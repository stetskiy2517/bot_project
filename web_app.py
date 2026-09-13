"""Multi-user Google-authenticated web/PWA entrypoint."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from math import isfinite
import re
import threading
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, Response, jsonify, redirect, request, send_from_directory, session

from config import BASE_URL, WEB_HOST, WEB_PORT, WEB_SESSION_SECRET
from core.db import (
    DEFAULT_CATEGORY_COLORS,
    GOOGLE_EVENT_COLOR_IDS,
    get_google_account,
    get_onboarding_status,
    get_user_timezone,
    init_db,
    save_calendar_preferences,
    save_user_timezone,
)
from core.library_store import get_saved_reminder, list_saved_reminders
from core.note_store import delete_note, get_note, list_notes
from core.push_store import (
    delete_push_subscription,
    has_push_subscriptions,
    list_push_subscriptions,
    save_push_subscription,
)
from core.reminder_store import complete_reminder, delete_saved_reminder, reschedule_reminder
from core.web_transport import WebContext, WebPlannerResult, WebUpdate
from integrations.speech import normalize_time_format, transcribe_audio
from integrations.web_push import get_vapid_public_key
from modules.auth import build_web_signin_url, complete_web_signin
from modules.note_conversation import clear_active_note, remember_active_note
from modules.reminder_dispatcher import send_test_push_for_user, start_reminder_push_worker
from modules.reminders import claim_due_for_user
from modules.router import route_text

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
VOICE_MAX_BYTES = 25 * 1024 * 1024
VOICE_MIN_DURATION_MS = 400
MAX_MESSAGE_LENGTH = 10000
WEB_SESSION_LIFETIME_DAYS = 90
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
    if mimetype in VOICE_MIME_SUFFIXES:
        return True
    if mimetype and mimetype != "application/octet-stream":
        return False
    return suffix in VOICE_SUFFIXES


def _voice_duration_ms() -> float | None:
    raw = request.form.get("duration_ms")
    if raw in {None, ""}:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректная длительность аудио.") from exc
    if not isfinite(value) or value < 0:
        raise ValueError("Некорректная длительность аудио.")
    return value


def _with_due_reminders(user_id: int, replies: list[str]) -> list[str]:
    # If this user has at least one background Push subscription, the dispatcher
    # owns delivery. Foreground polling remains a fallback for unsubscribed devices.
    if has_push_subscriptions(user_id):
        return replies
    due = claim_due_for_user(user_id)
    if not due:
        return replies
    return [*(item["message"] for item in due), *replies]


def _library_note_payload(note: dict) -> dict:
    return {
        "id": int(note["note_id"]),
        "title": str(note.get("title") or "Без названия"),
        "text": str(note.get("text") or ""),
        "created_at": note.get("created_at"),
        "updated_at": note.get("updated_at"),
    }


def _library_reminder_payload(reminder: dict) -> dict:
    return {
        "id": int(reminder["reminder_id"]),
        "text": str(reminder.get("text") or ""),
        "remind_at": reminder.get("remind_at"),
        "status": reminder.get("status"),
        "created_at": reminder.get("created_at"),
        "delivered_at": reminder.get("delivered_at"),
        "completed_at": reminder.get("completed_at"),
    }


def _note_chat_text(note: dict) -> str:
    title = str(note.get("title") or "Без названия").strip()
    body = str(note.get("text") or "").strip()
    if not body or body.casefold() == title.casefold():
        return f"Заметка · {title}"
    return f"Заметка · {title}\n\n{body}"


def _reminder_chat_text(reminder: dict, timezone: str) -> str:
    when = str(reminder.get("remind_at") or "")
    try:
        due = datetime.fromisoformat(when.replace("Z", "+00:00"))
        when = due.astimezone(ZoneInfo(timezone)).strftime("%d.%m.%Y, %H:%M")
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        pass
    prefix = "Напоминание · Выполнено" if reminder.get("status") == "completed" else "Напоминание"
    return f"{prefix}\n{when}\n\n{reminder.get('text') or ''}".strip()


def _parse_future_reminder_time(value) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Нужно выбрать новое время")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Некорректная дата или время") from exc
    if parsed.tzinfo is None:
        raise ValueError("Время должно содержать часовой пояс")
    parsed = parsed.astimezone(timezone.utc)
    if parsed <= datetime.now(timezone.utc):
        raise ValueError("Новое время должно быть в будущем")
    return parsed


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
    start_reminder_push_worker()
    app = Flask("personal-secretary-web", static_folder=None)
    app.secret_key = WEB_SESSION_SECRET
    app.config.update(
        MAX_CONTENT_LENGTH=VOICE_MAX_BYTES,
        PERMANENT_SESSION_LIFETIME=timedelta(days=WEB_SESSION_LIFETIME_DAYS),
        SESSION_REFRESH_EACH_REQUEST=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(BASE_URL and BASE_URL.lower().startswith("https://")),
    )

    @app.before_request
    def protect_api():
        user_id = _current_user_id()
        # Older releases created browser-session cookies. Upgrade any still-valid
        # authenticated session in place so the user does not have to sign in again.
        if user_id is not None and not session.permanent:
            session.permanent = True
        if not request.path.startswith("/api/") or request.path in {"/api/health", "/api/google/login"}:
            return None
        if user_id is None:
            return jsonify({"error": "unauthorized"}), 401
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.is_json:
            if not isinstance(request.get_json(silent=True), dict):
                return jsonify({"error": "invalid_json", "message": "Ожидается JSON-объект."}), 400
        return None

    @app.after_request
    def prevent_content_sniffing(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify({"error": "audio_too_large", "message": "Аудиофайл слишком большой."}), 413

    @app.get("/")
    def index():
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        scripts = (
            '    <script src="/reminders.js"></script>\n'
            '    <script src="/library.js"></script>\n'
            '    <script src="/voice-gesture.js"></script>\n'
            "  </body>"
        )
        html = html.replace("</body>", scripts)
        return Response(html, mimetype="text/html")

    @app.get("/reminders.js")
    def reminders_js():
        return send_from_directory(WEB_DIR, "reminders.js", mimetype="application/javascript")

    @app.get("/library.js")
    def library_js():
        return send_from_directory(WEB_DIR, "library.js", mimetype="application/javascript")

    @app.get("/voice-gesture.js")
    def voice_gesture_js():
        return send_from_directory(WEB_DIR, "voice_gesture.js", mimetype="application/javascript")

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(WEB_DIR, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.get("/sw.js")
    def sw():
        response = send_from_directory(WEB_DIR, "sw.js", mimetype="application/javascript")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
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
        except Exception:
            logger.exception("Failed to build Google sign-in URL")
            return jsonify({"error": "google_oauth_not_configured", "message": "Вход через Google временно недоступен."}), 503

    @app.get("/oauth2callback")
    def oauth_callback():
        if request.args.get("error"):
            return Response(f"Google OAuth error: {request.args['error']}", status=400, mimetype="text/plain")
        try:
            user_id = complete_web_signin(request.args.get("state", ""), request.args.get("code", ""))
        except Exception:
            logger.exception("Web Google sign-in failed")
            return Response("Не удалось войти через Google. Попробуй войти ещё раз.", status=400, mimetype="text/plain")
        session.clear()
        session.permanent = True
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

    @app.get("/api/library")
    def library():
        user_id = _require_user_id()
        user_timezone = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
        return {
            "timezone": user_timezone,
            "notes": [_library_note_payload(item) for item in list_notes(user_id, limit=500)],
            "reminders": [
                _library_reminder_payload(item)
                for item in list_saved_reminders(user_id, limit=500)
            ],
        }

    @app.post("/api/library/open")
    def open_library_item():
        user_id = _require_user_id()
        payload = request.get_json(silent=True) or {}
        item_type = str(payload.get("type") or "").strip().lower()
        try:
            item_id = int(payload.get("id"))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid_library_item"}), 400
        if item_id <= 0 or item_type not in {"note", "reminder"}:
            return jsonify({"error": "invalid_library_item"}), 400

        state = _state_for(user_id)
        state.pop("smart_planner_pending", None)
        context = WebContext(state)

        if item_type == "note":
            note = get_note(user_id, item_id)
            if not note:
                return jsonify({"error": "library_item_not_found"}), 404
            remember_active_note(context, note)
            state.pop("smart_planner_active_reminder", None)
            return {
                "type": "note",
                "id": int(note["note_id"]),
                "label": str(note.get("title") or "Заметка"),
                "chat_text": _note_chat_text(note),
            }

        reminder = get_saved_reminder(user_id, item_id)
        if not reminder:
            return jsonify({"error": "library_item_not_found"}), 404
        clear_active_note(context)
        state["smart_planner_active_reminder"] = {
            "reminder_id": int(reminder["reminder_id"]),
            "text": str(reminder.get("text") or ""),
        }
        user_timezone = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
        return {
            "type": "reminder",
            "id": int(reminder["reminder_id"]),
            "label": "Напоминание",
            "chat_text": _reminder_chat_text(reminder, user_timezone),
        }

    @app.delete("/api/library/notes/<int:note_id>")
    def delete_library_note(note_id: int):
        user_id = _require_user_id()
        if not delete_note(user_id, note_id):
            return jsonify({"error": "library_item_not_found"}), 404
        state = _state_for(user_id)
        active = state.get("smart_planner_active_note") or {}
        if int(active.get("note_id") or 0) == note_id:
            clear_active_note(WebContext(state))
        return {"ok": True}

    @app.post("/api/library/reminders/<int:reminder_id>/complete")
    def complete_library_reminder(reminder_id: int):
        user_id = _require_user_id()
        payload = request.get_json(silent=True) or {}
        completed = payload.get("completed", True)
        if not isinstance(completed, bool):
            return jsonify({"error": "invalid_completion_state"}), 400
        reminder = complete_reminder(user_id, reminder_id, completed=completed)
        if not reminder:
            return jsonify({"error": "library_item_not_found"}), 404
        return {"ok": True, "reminder": _library_reminder_payload(reminder)}

    @app.post("/api/library/reminders/<int:reminder_id>/reschedule")
    def reschedule_library_reminder(reminder_id: int):
        user_id = _require_user_id()
        payload = request.get_json(silent=True) or {}
        try:
            remind_at = _parse_future_reminder_time(payload.get("remind_at"))
        except ValueError as exc:
            return jsonify({"error": "invalid_reminder_time", "message": str(exc)}), 400
        reminder = reschedule_reminder(user_id, reminder_id, remind_at)
        if not reminder:
            return jsonify({"error": "library_item_not_found"}), 404
        return {"ok": True, "reminder": _library_reminder_payload(reminder)}

    @app.delete("/api/library/reminders/<int:reminder_id>")
    def delete_library_reminder(reminder_id: int):
        user_id = _require_user_id()
        if not delete_saved_reminder(user_id, reminder_id):
            return jsonify({"error": "library_item_not_found"}), 404
        state = _state_for(user_id)
        active = state.get("smart_planner_active_reminder") or {}
        if int(active.get("reminder_id") or 0) == reminder_id:
            state.pop("smart_planner_active_reminder", None)
        return {"ok": True}

    @app.get("/api/push/config")
    def push_config():
        try:
            return {"public_key": get_vapid_public_key()}
        except Exception:
            logger.exception("Failed to prepare VAPID key")
            return jsonify({"error": "push_not_configured", "message": "Не удалось включить push-уведомления."}), 503

    @app.get("/api/push/status")
    def push_status():
        user_id = _require_user_id()
        subscriptions = list_push_subscriptions(user_id)
        devices = [
            {
                "updated_at": item.get("updated_at"),
                "last_success_at": item.get("last_success_at"),
                "last_error": item.get("last_error"),
            }
            for item in subscriptions
        ]
        return {
            "subscribed": bool(subscriptions),
            "subscriptions": len(subscriptions),
            "devices": devices,
        }

    @app.post("/api/push/test")
    def push_test():
        user_id = _require_user_id()
        result = send_test_push_for_user(user_id)
        if result["ok"]:
            result["message"] = "Тестовый push принят push-сервисом."
            return result
        errors = " | ".join(result.get("errors") or [])
        if "BadJwtToken" in errors:
            message = "Apple отклонил подпись push (BadJwtToken)."
        elif result.get("subscriptions") == 0:
            message = "Push-подписка не зарегистрирована на сервере."
        else:
            message = "Push-сервис не принял тестовое уведомление."
        result["message"] = message
        return jsonify(result), 502

    @app.post("/api/push/subscriptions")
    def create_push_subscription():
        user_id = _require_user_id()
        payload = request.get_json(silent=True) or {}
        keys = payload.get("keys") or {}
        if not isinstance(keys, dict):
            return jsonify({"error": "invalid_push_subscription"}), 400
        try:
            save_push_subscription(
                user_id,
                str(payload.get("endpoint") or ""),
                str(keys.get("p256dh") or ""),
                str(keys.get("auth") or ""),
                user_agent=request.headers.get("User-Agent"),
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"error": "invalid_push_subscription", "message": str(exc)}), 400
        return {"ok": True}

    @app.delete("/api/push/subscriptions")
    def remove_push_subscription():
        user_id = _require_user_id()
        payload = request.get_json(silent=True) or {}
        endpoint = str(payload.get("endpoint") or "").strip()
        if not endpoint:
            return jsonify({"error": "missing_push_endpoint"}), 400
        return {"ok": delete_push_subscription(user_id, endpoint)}

    @app.get("/api/reminders/due")
    def due_reminders():
        user_id = _require_user_id()
        if has_push_subscriptions(user_id):
            return {"reminders": []}
        return {"reminders": claim_due_for_user(user_id)}

    @app.post("/api/chat")
    def chat():
        user_id = _require_user_id()
        account = get_google_account(user_id)
        text = (request.get_json(silent=True) or {}).get("message", "")
        if not isinstance(text, str):
            return jsonify({"error": "invalid_message", "message": "Сообщение должно быть текстом."}), 400
        if len(text) > MAX_MESSAGE_LENGTH:
            return jsonify({"error": "message_too_long", "message": "Сообщение слишком длинное."}), 400
        text = text.strip()
        if not text:
            return jsonify({"error": "empty_message"}), 400
        try:
            result = asyncio.run(
                process_web_message(text, user_id, account.get("name") or account["email"])
            )
        except Exception:
            logger.exception("Web command request failed for user %s", user_id)
            return jsonify({"error": "command_failed", "replies": ["Не удалось обработать сообщение."]}), 500
        return {"handled": result.handled, "replies": _with_due_reminders(user_id, result.replies)}

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
            duration_ms = _voice_duration_ms()
        except ValueError as exc:
            return jsonify({"error": "invalid_audio_duration", "message": str(exc)}), 400
        if duration_ms is not None and duration_ms < VOICE_MIN_DURATION_MS:
            return jsonify({"error": "audio_too_short", "message": "Слишком короткая запись."}), 400

        try:
            text = normalize_time_format(transcribe_audio(audio.stream)).strip()
            if not text:
                return jsonify({"error": "empty_transcript", "message": "Не удалось распознать речь."}), 400
            if len(text) > MAX_MESSAGE_LENGTH:
                return jsonify({"error": "message_too_long", "message": "Голосовое сообщение слишком длинное."}), 400
            result = asyncio.run(
                process_web_message(text, user_id, account.get("name") or account["email"])
            )
        except Exception:
            logger.exception("Web voice processing failed for user %s", user_id)
            return jsonify({"error": "voice_failed", "message": "Не удалось обработать голосовое сообщение."}), 503

        return {
            "transcript": text,
            "handled": result.handled,
            "replies": _with_due_reminders(user_id, result.replies),
        }

    @app.post("/api/settings")
    def settings():
        user_id = _require_user_id()
        payload = request.get_json(silent=True) or {}
        preferences = {}
        user_timezone = None
        try:
            if "timezone" in payload:
                user_timezone = str(payload["timezone"]).strip()
                try:
                    ZoneInfo(user_timezone)
                except ZoneInfoNotFoundError as exc:
                    raise ValueError("Неизвестный часовой пояс") from exc

            work_start = payload.get("work_start")
            work_end = payload.get("work_end")
            if work_start is not None or work_end is not None:
                current = _status_payload(user_id)["preferences"]
                start = str(work_start or current["work_start"])
                end = str(work_end or current["work_end"])
                _validate_time_range(start, end)
                preferences.update(work_start=start, work_end=end)

            if "work_days" in payload:
                days = payload["work_days"]
                if not isinstance(days, list) or not days:
                    raise ValueError("Нужно выбрать хотя бы один рабочий день")
                if any(isinstance(day, (bool, float)) for day in days):
                    raise ValueError("Рабочие дни должны быть целыми числами от 0 до 6")
                parsed = sorted({int(day) for day in days})
                if any(day < 0 or day > 6 for day in parsed):
                    raise ValueError("Рабочие дни должны быть числами от 0 до 6")
                preferences["work_days"] = parsed

            if "buffer_minutes" in payload:
                if isinstance(payload["buffer_minutes"], (bool, float)):
                    raise ValueError("Буфер должен быть целым числом минут")
                value = int(payload["buffer_minutes"])
                if not 0 <= value <= 180:
                    raise ValueError("Буфер должен быть от 0 до 180 минут")
                preferences["buffer_minutes"] = value

            if "category_colors" in payload:
                colors = payload["category_colors"]
                if not isinstance(colors, dict) or not colors:
                    raise ValueError("Неверный набор категорий")
                if set(colors) - set(DEFAULT_CATEGORY_COLORS):
                    raise ValueError("Неверный набор категорий")
                parsed_colors = dict(_status_payload(user_id)["preferences"]["category_colors"])
                for category, color_id in colors.items():
                    if color_id in {None, ""}:
                        parsed_colors[category] = None
                    elif str(color_id) in GOOGLE_EVENT_COLOR_IDS:
                        parsed_colors[category] = str(color_id)
                    else:
                        raise ValueError("Неизвестный цвет категории")
                preferences["category_colors"] = parsed_colors
        except (TypeError, ValueError, OverflowError) as exc:
            return jsonify({"error": "invalid_settings", "message": str(exc)}), 400

        if user_timezone is not None:
            save_user_timezone(user_id, user_timezone)
        if preferences:
            save_calendar_preferences(user_id, **preferences)
        return _status_payload(user_id)

    return app


app = create_web_app()

if __name__ == "__main__":
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, threaded=True)
