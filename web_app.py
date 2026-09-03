"""Multi-user web/PWA entrypoint for Personal Secretary."""
from __future__ import annotations
import asyncio, logging, re, threading
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import Flask, jsonify, redirect, request, send_from_directory, session
from config import WEB_HOST, WEB_PORT, WEB_SESSION_SECRET
from core.db import (authenticate_web_account, create_web_account, get_onboarding_status, get_user_timezone, get_web_account, init_db, save_calendar_preferences, save_user_timezone)
from core.web_transport import WebContext, WebPlannerResult, WebUpdate
from modules.auth import build_authorization_url, complete_authorization
from modules.router import route_text

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_user_state: dict[int, dict] = {}
_state_lock = threading.RLock()


def _state_for(user_id: int) -> dict:
    with _state_lock: return _user_state.setdefault(user_id, {})

def _current_user_id() -> int | None:
    value = session.get("user_id")
    return int(value) if value is not None else None

def _require_user_id() -> int:
    user_id = _current_user_id()
    if user_id is None: raise RuntimeError("unauthorized")
    return user_id

def _validate_time_range(start: str, end: str) -> None:
    if not TIME_RE.fullmatch(start) or not TIME_RE.fullmatch(end): raise ValueError("Время должно быть в формате HH:MM")
    if start >= end: raise ValueError("Начало рабочего дня должно быть раньше окончания")

def _status_payload(user_id: int) -> dict:
    result = get_onboarding_status(user_id); result["timezone"] = get_user_timezone(user_id, default=None); return result

async def process_web_message(text: str, user_id: int, user_name: str) -> WebPlannerResult:
    update = WebUpdate(user_id, user_name, text); context = WebContext(_state_for(user_id)); handled = await route_text(update, context, text=text)
    replies = update.message.replies
    if not handled and not replies: replies.append("Не понял команду. Например: «поставь врача завтра в 19:00».")
    return WebPlannerResult(handled=handled, replies=replies)


def create_web_app() -> Flask:
    init_db(); app = Flask("personal-secretary-web", static_folder=None); app.secret_key = WEB_SESSION_SECRET

    @app.before_request
    def protect_api():
        if not request.path.startswith("/api/") or request.path in {"/api/health", "/api/login", "/api/register"}: return None
        if _current_user_id() is None: return jsonify({"error": "unauthorized"}), 401
        return None

    @app.get("/")
    def index(): return send_from_directory(WEB_DIR, "index.html")
    @app.get("/manifest.webmanifest")
    def manifest(): return send_from_directory(WEB_DIR, "manifest.webmanifest", mimetype="application/manifest+json")
    @app.get("/sw.js")
    def service_worker():
        response = send_from_directory(WEB_DIR, "sw.js", mimetype="application/javascript"); response.headers["Service-Worker-Allowed"] = "/"; return response
    @app.get("/icon.svg")
    def icon(): return send_from_directory(WEB_DIR, "icon.svg", mimetype="image/svg+xml")
    @app.get("/api/health")
    def health(): return {"status": "ok", "transport": "web"}

    @app.post("/api/register")
    def register():
        payload = request.get_json(silent=True) or {}
        try: user_id = create_web_account(str(payload.get("email", "")), str(payload.get("password", "")), str(payload.get("name", "")))
        except ValueError as exc: return jsonify({"error": "registration_failed", "message": str(exc)}), 400
        session.clear(); session["user_id"] = user_id; return {"ok": True, "user_id": user_id}, 201

    @app.post("/api/login")
    def login():
        payload = request.get_json(silent=True) or {}; account = authenticate_web_account(str(payload.get("email", "")), str(payload.get("password", "")))
        if not account: return jsonify({"error": "invalid_credentials"}), 401
        session.clear(); session["user_id"] = account["user_id"]; return {"ok": True, "user": {"id": account["user_id"], "email": account["email"], "name": account["name"]}}

    @app.post("/api/logout")
    def logout(): session.clear(); return {"ok": True}

    @app.get("/api/status")
    def status():
        user_id = _require_user_id(); account = get_web_account(user_id); result = _status_payload(user_id); result["user"] = {"id": user_id, "email": account["email"], "name": account["name"]}; return result

    @app.post("/api/chat")
    def chat():
        user_id = _require_user_id(); account = get_web_account(user_id); payload = request.get_json(silent=True) or {}; text = str(payload.get("message", "")).strip()
        if not text: return jsonify({"error": "empty_message"}), 400
        try: result = asyncio.run(process_web_message(text, user_id, account["name"]))
        except Exception: logger.exception("Web planner request failed for user %s", user_id); return jsonify({"error": "planner_failed", "replies": ["Не удалось обработать сообщение."]}), 500
        return {"handled": result.handled, "replies": result.replies}

    @app.get("/api/google/auth")
    def google_auth():
        try: return {"url": build_authorization_url(_require_user_id())}
        except Exception as exc: logger.exception("Failed to build Google authorization URL"); return jsonify({"error": "google_oauth_not_configured", "message": str(exc)}), 503

    @app.get("/oauth2callback")
    def oauth_callback():
        if request.args.get("error"): return f"Google OAuth error: {request.args['error']}", 400
        try: authorized_user_id = complete_authorization(request.args.get("state", ""), request.args.get("code", ""))
        except Exception as exc: logger.exception("Web Google OAuth callback failed"); return f"Не удалось подключить Google Calendar: {exc}", 400
        current = _current_user_id()
        if current is not None and current != authorized_user_id: logger.warning("OAuth callback user mismatch: session=%s state=%s", current, authorized_user_id); session.clear(); return "Сессия пользователя не совпадает с авторизацией Google.", 403
        session["user_id"] = authorized_user_id; return redirect("/?google=connected")

    @app.post("/api/settings")
    def settings():
        user_id = _require_user_id(); payload = request.get_json(silent=True) or {}
        try:
            if "timezone" in payload:
                timezone = str(payload["timezone"]).strip()
                try: ZoneInfo(timezone)
                except ZoneInfoNotFoundError as exc: raise ValueError("Неизвестный часовой пояс") from exc
                save_user_timezone(user_id, timezone)
            work_start, work_end = payload.get("work_start"), payload.get("work_end")
            if work_start is not None or work_end is not None:
                current = _status_payload(user_id)["preferences"]; start = str(work_start or current["work_start"]); end = str(work_end or current["work_end"]); _validate_time_range(start, end); save_calendar_preferences(user_id, work_start=start, work_end=end)
            if "work_days" in payload:
                days = payload["work_days"]
                if not isinstance(days, list) or not days: raise ValueError("Нужно выбрать хотя бы один рабочий день")
                parsed = sorted({int(day) for day in days})
                if any(day < 0 or day > 6 for day in parsed): raise ValueError("Рабочие дни должны быть числами от 0 до 6")
                save_calendar_preferences(user_id, work_days=parsed)
            if "buffer_minutes" in payload:
                value = int(payload["buffer_minutes"])
                if not 0 <= value <= 180: raise ValueError("Буфер должен быть от 0 до 180 минут")
                save_calendar_preferences(user_id, buffer_minutes=value)
        except (TypeError, ValueError) as exc: return jsonify({"error": "invalid_settings", "message": str(exc)}), 400
        return _status_payload(user_id)
    return app

app = create_web_app()
if __name__ == "__main__": app.run(host=WEB_HOST, port=WEB_PORT, debug=False, threaded=True)
