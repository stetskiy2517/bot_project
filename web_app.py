"""Minimal web/PWA entrypoint for Personal Secretary.

This is a second transport beside Telegram. It reuses the existing Smart Planner
router and calendar modules through a lightweight adapter.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, jsonify, redirect, request, send_from_directory, session

from config import (
    WEB_HOST,
    WEB_PASSWORD,
    WEB_PORT,
    WEB_SESSION_SECRET,
    WEB_USER_ID,
    WEB_USER_NAME,
)
from core.db import (
    ensure_user,
    get_onboarding_status,
    init_db,
    save_calendar_preferences,
    save_user_timezone,
)
from core.web_transport import WebContext, WebPlannerResult, WebUpdate
from modules.auth import build_authorization_url, complete_authorization
from modules.router import route_text

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

_user_state: dict[int, dict] = {}
_state_lock = threading.RLock()


def _state_for(user_id: int) -> dict:
    with _state_lock:
        return _user_state.setdefault(user_id, {})


def _is_authenticated() -> bool:
    return not WEB_PASSWORD or bool(session.get("authenticated"))


def _validate_time_range(start: str, end: str) -> None:
    if not TIME_RE.fullmatch(start) or not TIME_RE.fullmatch(end):
        raise ValueError("Время должно быть в формате HH:MM")
    if start >= end:
        raise ValueError("Начало рабочего дня должно быть раньше окончания")


async def process_web_message(text: str, user_id: int = WEB_USER_ID) -> WebPlannerResult:
    update = WebUpdate(user_id, WEB_USER_NAME, text)
    context = WebContext(_state_for(user_id))
    handled = await route_text(update, context, text=text)
    replies = update.message.replies
    if not handled and not replies:
        replies.append("Не понял команду. Например: «поставь врача завтра в 19:00».")
    return WebPlannerResult(handled=handled, replies=replies)


def create_web_app() -> Flask:
    init_db()
    ensure_user(WEB_USER_ID, WEB_USER_NAME)

    app = Flask("personal-secretary-web", static_folder=None)
    app.secret_key = WEB_SESSION_SECRET

    @app.before_request
    def protect_api():
        if not request.path.startswith("/api/"):
            return None
        if request.path in {"/api/health", "/api/login"}:
            return None
        if not _is_authenticated():
            return jsonify({"error": "unauthorized"}), 401
        return None

    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(WEB_DIR, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        response = send_from_directory(WEB_DIR, "sw.js", mimetype="application/javascript")
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.get("/icon.svg")
    def icon():
        return send_from_directory(WEB_DIR, "icon.svg", mimetype="image/svg+xml")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "transport": "web"}

    @app.post("/api/login")
    def login():
        if not WEB_PASSWORD:
            session["authenticated"] = True
            return {"ok": True}
        supplied = str((request.get_json(silent=True) or {}).get("password", ""))
        if supplied != WEB_PASSWORD:
            return jsonify({"error": "invalid_password"}), 401
        session["authenticated"] = True
        return {"ok": True}

    @app.post("/api/logout")
    def logout():
        session.clear()
        return {"ok": True}

    @app.get("/api/status")
    def status():
        result = get_onboarding_status(WEB_USER_ID)
        result["user"] = {"id": WEB_USER_ID, "name": WEB_USER_NAME}
        result["password_required"] = bool(WEB_PASSWORD)
        return result

    @app.post("/api/chat")
    def chat():
        payload = request.get_json(silent=True) or {}
        text = str(payload.get("message", "")).strip()
        if not text:
            return jsonify({"error": "empty_message"}), 400
        try:
            result = asyncio.run(process_web_message(text))
        except Exception:
            logger.exception("Web planner request failed")
            return jsonify({"error": "planner_failed", "replies": ["Не удалось обработать сообщение."]}), 500
        return {"handled": result.handled, "replies": result.replies}

    @app.get("/api/google/auth")
    def google_auth():
        try:
            return {"url": build_authorization_url(WEB_USER_ID)}
        except Exception as exc:
            logger.exception("Failed to build Google authorization URL")
            return jsonify({"error": "google_oauth_not_configured", "message": str(exc)}), 503

    @app.get("/oauth2callback")
    def oauth_callback():
        error = request.args.get("error")
        if error:
            return f"Google OAuth error: {error}", 400
        try:
            complete_authorization(request.args.get("state", ""), request.args.get("code", ""))
        except Exception as exc:
            logger.exception("Web Google OAuth callback failed")
            return f"Не удалось подключить Google Calendar: {exc}", 400
        return redirect("/?google=connected")

    @app.post("/api/settings")
    def settings():
        payload = request.get_json(silent=True) or {}
        try:
            if "timezone" in payload:
                timezone = str(payload["timezone"]).strip()
                try:
                    ZoneInfo(timezone)
                except ZoneInfoNotFoundError as exc:
                    raise ValueError("Неизвестный часовой пояс") from exc
                save_user_timezone(WEB_USER_ID, timezone)

            work_start = payload.get("work_start")
            work_end = payload.get("work_end")
            if work_start is not None or work_end is not None:
                current = get_onboarding_status(WEB_USER_ID)["preferences"]
                start = str(work_start or current["work_start"])
                end = str(work_end or current["work_end"])
                _validate_time_range(start, end)
                save_calendar_preferences(WEB_USER_ID, work_start=start, work_end=end)

            if "work_days" in payload:
                days = payload["work_days"]
                if not isinstance(days, list) or not days:
                    raise ValueError("Нужно выбрать хотя бы один рабочий день")
                parsed_days = sorted({int(day) for day in days})
                if any(day < 0 or day > 6 for day in parsed_days):
                    raise ValueError("Рабочие дни должны быть числами от 0 до 6")
                save_calendar_preferences(WEB_USER_ID, work_days=parsed_days)

            if "buffer_minutes" in payload:
                buffer_minutes = int(payload["buffer_minutes"])
                if not 0 <= buffer_minutes <= 180:
                    raise ValueError("Буфер должен быть от 0 до 180 минут")
                save_calendar_preferences(WEB_USER_ID, buffer_minutes=buffer_minutes)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": "invalid_settings", "message": str(exc)}), 400
        return get_onboarding_status(WEB_USER_ID)

    return app


app = create_web_app()


if __name__ == "__main__":
    if WEB_HOST not in {"127.0.0.1", "localhost", "::1"} and not WEB_PASSWORD:
        logger.warning("WEB_PASSWORD is not set while web app is exposed beyond localhost")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, threaded=True)
