"""Multi-user Google-authenticated web/PWA entrypoint."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, jsonify, redirect, request, send_from_directory, session

from config import WEB_HOST, WEB_PORT, WEB_SESSION_SECRET
from core.db import (
    get_google_account,
    get_onboarding_status,
    get_user_timezone,
    init_db,
    save_calendar_preferences,
    save_user_timezone,
)
from core.web_transport import WebContext, WebPlannerResult, WebUpdate
from integrations.speech import normalize_time_format, transcribe_audio
from modules.auth import build_web_signin_url, complete_web_signin
from modules.router import route_text

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
VOICE_MAX_BYTES = 25 * 1024 * 1024
VOICE_MIME_SUFFIXES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}
VOICE_SUFFIXES = frozenset(VOICE_MIME_SUFFIXES.values())

_user_state: dict[int, dict] = {}
_state_lock = threading.RLock()


def _state_for(user_id: int) -> dict:
    with _state_lock:
        return _user_state.setdefault(user_id, {})


def _current_user_id() -> int | None:
    value = session.get("user_id")
    return int(value) if value is not None else None


def _require_user_id() -> int:
    user_id = _current_user_id()
    if user_id is None:
        raise RuntimeError("unauthorized")
    return user_id


def _validate_time_range(start: str, end: str) -> None:
    if not TIME_RE.fullmatch(start) or not TIME_RE.fullmatch(end):
        raise ValueError("Время должно быть в формате HH:MM")
    if start >= end:
        raise ValueError("Начало рабочего дня должно быть раньше окончания")


def _status_payload(user_id: int) -> dict:
    result = get_onboarding_status(user_id)
    result["timezone"] = get_user_timezone(user_id, default=None)
    return result


def _valid_voice_upload(audio) -> bool:
    mimetype = (audio.mimetype or "").lower()
    suffix = Path(audio.filename or "").suffix.lower()
    return mimetype in VOICE_MIME_SUFFIXES or suffix in VOICE_SUFFIXES


async def process_web_message(text: str, user_id: int, user_name: str) -> WebPlannerResult:
    """Route text from any web input channel through the shared command router."""
    update = WebUpdate(user_id, user_name, text)
    context = WebContext(_state_for(user_id))
    handled = await route_text(update, context, text=text)
    replies = update.message.replies
    if not handled and not replies:
        replies.append("Не понял команду. Сформулируй её иначе или уточни, что нужно сделать.")
    return WebPlannerResult(handled=handled, replies=replies)


def create_web_app() -> Flask:
    init_db()
    app = Flask("personal-secretary-web", static_folder=None)
    app.secret_key = WEB_SESSION_SECRET
    app.config["MAX_CONTENT_LENGTH"] = VOICE_MAX_BYTES

    @app.before_request
    def protect_api():
        if not request.path.startswith("/api/") or request.path in {"/api/health", "/api/google/login"}:
            return None
        if _current_user_id() is None:
            return jsonify({"error": "unauthorized"}), 401
        return None

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify({"error": "audio_too_large", "message": "Аудиофайл слишком большой."}), 413

    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(WEB_DIR, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.get("/sw.js")
    def sw():
        response = send_from_directory(WEB_DIR, "sw.js", mimetype="application/javascript")
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.get("/icon.svg")
    def icon():
        return send_from_directory(WEB_DIR, "icon.svg", mimetype="image/svg+xml")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "transport": "web"}

    @app.get("/api/google/login")
    def google_login():
        try:
            return {"url": build_web_signin_url()}
        except Exception as exc:
            logger.exception("Failed to build Google sign-in URL")
            return jsonify({"error": "google_oauth_not_configured", "message": str(exc)}), 503

    @app.get("/oauth2callback")
    def oauth_callback():
        if request.args.get("error"):
            return f"Google OAuth error: {request.args['error']}", 400
        try:
            user_id = complete_web_signin(request.args.get("state", ""), request.args.get("code", ""))
        except Exception as exc:
            logger.exception("Web Google sign-in failed")
            return f"Не удалось войти через Google: {exc}", 400
        session.clear()
        session["user_id"] = user_id
        return redirect("/?google=connected")

    @app.post("/api/logout")
    def logout():
        session.clear()
        return {"ok": True}

    @app.get("/api/status")
    def status():
        user_id = _require_user_id()
        account = get_google_account(user_id)
        result = _status_payload(user_id)
        result["user"] = {
            "id": user_id,
            "email": account["email"],
            "name": account["name"],
        }
        return result

    @app.post("/api/chat")
    def chat():
        user_id = _require_user_id()
        account = get_google_account(user_id)
        text = str((request.get_json(silent=True) or {}).get("message", "")).strip()
        if not text:
            return jsonify({"error": "empty_message"}), 400
        try:
            result = asyncio.run(
                process_web_message(text, user_id, account.get("name") or account["email"])
            )
        except Exception:
            logger.exception("Web command request failed for user %s", user_id)
            return jsonify({"error": "command_failed", "replies": ["Не удалось обработать сообщение."]}), 500
        return {"handled": result.handled, "replies": result.replies}

    @app.post("/api/voice")
    def voice():
        user_id = _require_user_id()
        account = get_google_account(user_id)
        audio = request.files.get("audio")
        if audio is None or not audio.filename:
            return jsonify({"error": "empty_audio"}), 400
        if not _valid_voice_upload(audio):
            return jsonify({"error": "unsupported_audio", "message": "Неподдерживаемый формат аудио."}), 415

        try:
            text = normalize_time_format(transcribe_audio(audio.stream))
            if not text:
                return jsonify({"error": "empty_transcript", "message": "Не удалось распознать речь."}), 400
            result = asyncio.run(
                process_web_message(text, user_id, account.get("name") or account["email"])
            )
        except Exception:
            logger.exception("Web voice processing failed for user %s", user_id)
            return jsonify({"error": "voice_failed", "message": "Не удалось обработать голосовое сообщение."}), 503

        return {
            "transcript": text,
            "handled": result.handled,
            "replies": result.replies,
        }

    @app.post("/api/settings")
    def settings():
        user_id = _require_user_id()
        payload = request.get_json(silent=True) or {}
        try:
            if "timezone" in payload:
                timezone = str(payload["timezone"]).strip()
                try:
                    ZoneInfo(timezone)
                except ZoneInfoNotFoundError as exc:
                    raise ValueError("Неизвестный часовой пояс") from exc
                save_user_timezone(user_id, timezone)

            work_start = payload.get("work_start")
            work_end = payload.get("work_end")
            if work_start is not None or work_end is not None:
                current = _status_payload(user_id)["preferences"]
                start = str(work_start or current["work_start"])
                end = str(work_end or current["work_end"])
                _validate_time_range(start, end)
                save_calendar_preferences(user_id, work_start=start, work_end=end)

            if "work_days" in payload:
                days = payload["work_days"]
                if not isinstance(days, list) or not days:
                    raise ValueError("Нужно выбрать хотя бы один рабочий день")
                parsed = sorted({int(day) for day in days})
                if any(day < 0 or day > 6 for day in parsed):
                    raise ValueError("Рабочие дни должны быть числами от 0 до 6")
                save_calendar_preferences(user_id, work_days=parsed)

            if "buffer_minutes" in payload:
                value = int(payload["buffer_minutes"])
                if not 0 <= value <= 180:
                    raise ValueError("Буфер должен быть от 0 до 180 минут")
                save_calendar_preferences(user_id, buffer_minutes=value)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": "invalid_settings", "message": str(exc)}), 400
        return _status_payload(user_id)

    return app


app = create_web_app()

if __name__ == "__main__":
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, threaded=True)
