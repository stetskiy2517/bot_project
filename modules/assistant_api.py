"""Web endpoints for the approved deterministic assistant improvements."""

from __future__ import annotations

from pathlib import Path
import time
from flask import Blueprint, jsonify, request, send_from_directory, session

from core.assistant_preferences import get_assistant_preferences, save_assistant_preferences, review_history
from core.notification_policy import get_policy, save_policy
from core.undo_store import last_note_action, undo_note_action
from modules.account_privacy import create_erase_challenge, erase_account, export_account, privacy_policy
from modules.command_templates import list_templates, save_template, delete_template
from modules.daily_review import build_day_review
from modules.life_wheel import build_life_wheel_snapshot

assistant_api = Blueprint("assistant", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _user():
    return int(session["user_id"])


def _recent_login():
    stamp = session.get("auth_time")
    return isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and 0 <= time.time() - stamp <= 600


@assistant_api.after_app_request
def load_life_wheel_ui(response):
    if request.path != "/" or response.status_code != 200 or response.mimetype != "text/html":
        return response
    html = response.get_data(as_text=True)
    script = '<script src="/life-wheel.js"></script>'
    if script not in html and "</body>" in html:
        response.set_data(html.replace("</body>", f"    {script}\n  </body>", 1))
    return response


@assistant_api.get("/life-wheel.js")
def life_wheel_js():
    return send_from_directory(WEB_DIR, "life-wheel.js", mimetype="application/javascript")


@assistant_api.errorhandler(ValueError)
def invalid_request(error):
    return jsonify(error="invalid_assistant_request", message=str(error)), 400


@assistant_api.get("/api/assistant")
def assistant_status():
    return {
        "preferences": get_assistant_preferences(_user()),
        "templates": list_templates(_user()),
        "undo": last_note_action(_user()),
        "reviews": review_history(_user()),
        "privacy": privacy_policy(),
    }


@assistant_api.post("/api/assistant/preferences")
def assistant_preferences():
    return {"preferences": save_assistant_preferences(_user(), request.get_json(silent=True) or {})}


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
        return jsonify(error="reauth_required", message="Для удаления нужно заново войти через Google."), 403
    return {"challenge": create_erase_challenge(_user()), "expires_in": 300, "policy": privacy_policy()}


@assistant_api.post("/api/privacy/erase")
def account_erase():
    if not _recent_login():
        return jsonify(error="reauth_required", message="Для удаления нужно заново войти через Google."), 403
    payload = request.get_json(silent=True) or {}
    erase_account(_user(), payload.get("challenge"), payload.get("confirmation"))
    session.clear()
    return {"ok": True, "local_data_erased": True, "google_calendar_unchanged": True}