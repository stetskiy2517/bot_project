"""Web endpoints for the approved deterministic assistant improvements."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import time
from flask import Blueprint, jsonify, request, send_from_directory, session

from core.ai_memory_store import record_ai_memory_event
from core.assistant_preferences import get_assistant_preferences, save_assistant_preferences, review_history
from core.chat_context import append_chat_exchange, clear_chat_context, recent_chat_messages
from core.feature_access import ai_access_status, has_ai_access
from core.memory_store import list_memories, memory_status, suppress_memory
from core.notification_policy import get_policy, save_policy
from core.proactive_store import list_proactive_actions
from core.undo_store import last_note_action, undo_note_action
from modules.account_privacy import create_erase_challenge, erase_account, export_account, privacy_policy
from modules.admin_api import admin_api
from modules.admin_metrics_api import admin_metrics_api
from modules.ai_assistant import ai_status, answer_unhandled, is_unhandled_reply, replace_unhandled_reply
from modules.command_templates import list_templates, save_template, delete_template
from modules.daily_review import build_day_review
from modules.email import detect_email_intent
from modules.email_actions_api import email_actions_api
from modules.email_api import email_api
from modules.life_balance_api import life_balance_api
from modules.life_wheel import build_life_wheel_snapshot
from modules.memory import start_memory_worker
from modules.memory_controls_api import memory_controls_api
from modules.mobile_ui_api import mobile_ui_api
from modules.navigation_access import navigation_access_api
from modules.navigation_extra_api import navigation_extra_api
from modules.navigation_recurring import start_navigation_recurring_worker
from modules.note_tools_api import note_tools_api
from modules.proactive import proactive_status, start_proactive_worker
from modules.task_api import task_api
from modules.yandex_auth import yandex_auth_api

assistant_api = Blueprint("assistant", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
logger = logging.getLogger(__name__)
PROACTIVE_AI_SETTINGS = {"proactive_reminders_enabled", "proactive_calendar_events_enabled"}


def _user():
    return int(session["user_id"])


def _recent_login():
    stamp = session.get("auth_time")
    return isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and 0 <= time.time() - stamp <= 600


def _journal_user_utterance(user_id: int, text: str, channel: str) -> None:
    clean = str(text or "").strip()
    if not clean:
        return
    entity_id = time.time_ns() & ((1 << 63) - 1)
    record_ai_memory_event(
        user_id,
        "voice_transcript",
        entity_id or 1,
        "recognized" if channel == "voice" else "created",
        {"text": clean, "channel": channel},
    )


@assistant_api.after_app_request
def load_assistant_ui(response):
    if request.path != "/" or response.status_code != 200 or response.mimetype != "text/html":
        return response
    html = response.get_data(as_text=True)
    scripts = (
        '<script src="/life-wheel.js"></script>',
        '<script src="/proactive.js"></script>',
    )
    if "</body>" in html:
        for script in scripts:
            if script not in html:
                html = html.replace("</body>", f"    {script}\n  </body>", 1)
        response.set_data(html)
    return response


@assistant_api.after_app_request
def use_ai_for_unhandled_chat(response):
    if (
        request.path not in {"/api/chat", "/api/voice"}
        or response.status_code != 200
        or response.mimetype != "application/json"
    ):
        return response
    try:
        user_id = _user()
        if not has_ai_access(user_id):
            return response
        payload = response.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get("handled") is not False:
            return response
        replies = payload.get("replies")
        if not isinstance(replies, list) or not any(is_unhandled_reply(item) for item in replies):
            return response
        if request.path == "/api/chat":
            request_payload = request.get_json(silent=True) or {}
            text = request_payload.get("message") if isinstance(request_payload, dict) else None
            channel = "chat"
        else:
            text = payload.get("transcript")
            channel = "voice"
        if not isinstance(text, str) or not text.strip():
            return response
        if detect_email_intent(text):
            return response
        try:
            _journal_user_utterance(user_id, text, channel)
        except Exception:
            logger.exception("Failed to journal user utterance for AI memory")
        history = recent_chat_messages(user_id)
        answer = answer_unhandled(text, user_id=user_id, history=history)
        if not answer:
            return response
        append_chat_exchange(user_id, text, answer)
        payload["handled"] = True
        payload["replies"] = replace_unhandled_reply(replies, answer)
        response.set_data(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return response
    except Exception:
        logger.exception("Failed to apply AI fallback response")
        return response


@assistant_api.get("/life-wheel.js")
def life_wheel_js():
    return send_from_directory(WEB_DIR, "life-wheel.js", mimetype="application/javascript")


@assistant_api.get("/proactive.js")
def proactive_js():
    return send_from_directory(WEB_DIR, "proactive.js", mimetype="application/javascript")


@assistant_api.errorhandler(ValueError)
def invalid_request(error):
    return jsonify(error="invalid_assistant_request", message=str(error)), 400


@assistant_api.get("/api/assistant")
def assistant_status():
    user_id = _user()
    return {
        "preferences": get_assistant_preferences(user_id),
        "templates": list_templates(user_id),
        "undo": last_note_action(user_id),
        "reviews": review_history(user_id),
        "privacy": privacy_policy(),
        "ai": ai_status(),
        "access": ai_access_status(user_id),
        "memory": memory_status(user_id),
        "proactive": proactive_status(user_id),
    }


@assistant_api.get("/api/assistant/memory")
def assistant_memory():
    return {"memories": list_memories(_user(), limit=200), "status": memory_status(_user())}


@assistant_api.delete("/api/assistant/memory/<int:memory_id>")
def remove_assistant_memory(memory_id: int):
    if not suppress_memory(_user(), memory_id):
        return jsonify(error="memory_not_found"), 404
    return {"ok": True, "memory_id": memory_id}


@assistant_api.get("/api/assistant/proactive")
def assistant_proactive():
    user_id = _user()
    return {"status": proactive_status(user_id), "actions": list_proactive_actions(user_id, limit=50)}


@assistant_api.post("/api/assistant/preferences")
def assistant_preferences():
    user_id = _user()
    payload = request.get_json(silent=True) or {}
    if (
        not has_ai_access(user_id)
        and any(payload.get(key) is True for key in PROACTIVE_AI_SETTINGS)
    ):
        return jsonify(
            error="ai_access_required",
            message="Для проактивных ИИ-функций нужен доступ к ИИ. Календарь и обычные напоминания работают без него.",
        ), 403
    return {"preferences": save_assistant_preferences(user_id, payload)}


@assistant_api.get("/api/assistant/review")
def day_review():
    return build_day_review(_user(), request.args.get("kind", "morning"))


@assistant_api.get("/api/assistant/life-wheel")
def life_wheel():
    raw_days = request.args.get("days", "30")
    try:
        days = int(raw_days)
    except (TypeError, ValueError) as exc:
        raise ValueError("Период колеса жизни должен быть числом") from exc
    return build_life_wheel_snapshot(_user(), days=days)


@assistant_api.post("/api/assistant/undo/<int:action_id>")
def undo_action(action_id):
    return undo_note_action(_user(), action_id)


@assistant_api.post("/api/assistant/templates")
def create_template():
    return {"template": save_template(_user(), request.get_json(silent=True) or {})}


@assistant_api.put("/api/assistant/templates/<int:template_id>")
def update_template(template_id):
    return {"template": save_template(_user(), request.get_json(silent=True) or {}, template_id)}


@assistant_api.delete("/api/assistant/templates/<int:template_id>")
def remove_template(template_id):
    return {"ok": delete_template(_user(), template_id)}


@assistant_api.get("/api/assistant/reminders/<int:reminder_id>/notifications")
def notification_policy(reminder_id):
    return {"policy": get_policy(_user(), reminder_id)}


@assistant_api.post("/api/assistant/reminders/<int:reminder_id>/notifications")
def update_notification_policy(reminder_id):
    return {"policy": save_policy(_user(), reminder_id, request.get_json(silent=True) or {})}


@assistant_api.get("/api/privacy/export")
def account_export():
    response = jsonify(export_account(_user()))
    response.headers["Content-Disposition"] = 'attachment; filename="personal-secretary-export.json"'
    return response


@assistant_api.post("/api/privacy/challenge")
def erase_challenge():
    if not _recent_login():
        return jsonify(error="reauth_required", message="Для удаления нужно заново войти в аккаунт."), 403
    return {"challenge": create_erase_challenge(_user()), "expires_in": 300, "policy": privacy_policy()}


@assistant_api.post("/api/privacy/erase")
def account_erase():
    if not _recent_login():
        return jsonify(error="reauth_required", message="Для удаления нужно заново войти в аккаунт."), 403
    payload = request.get_json(silent=True) or {}
    user_id = _user()
    erase_account(user_id, payload.get("challenge"), payload.get("confirmation"))
    clear_chat_context(user_id)
    session.clear()
    return {"ok": True, "local_data_erased": True, "google_calendar_unchanged": True}


assistant_api.register_blueprint(admin_api)
assistant_api.register_blueprint(admin_metrics_api)
assistant_api.register_blueprint(email_api)
assistant_api.register_blueprint(email_actions_api)
assistant_api.register_blueprint(task_api)
assistant_api.register_blueprint(memory_controls_api)
assistant_api.register_blueprint(life_balance_api)
assistant_api.register_blueprint(navigation_access_api)
assistant_api.register_blueprint(navigation_extra_api)
assistant_api.register_blueprint(note_tools_api)
assistant_api.register_blueprint(yandex_auth_api)
assistant_api.register_blueprint(mobile_ui_api)
start_memory_worker()
start_proactive_worker()
start_navigation_recurring_worker()
