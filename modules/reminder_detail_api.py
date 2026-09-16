"""Web API for viewing and editing reminder details."""

from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from core.db import get_user_timezone
from core.library_store import get_saved_reminder, list_saved_reminders
from core.reminder_detail_store import edit_saved_reminder, get_reminder_category_override
from modules.reminder_categories import REMINDER_CATEGORY_LABELS, reminder_category

reminder_detail_api = Blueprint("reminder_details", __name__)


def _user() -> int:
    return int(session["user_id"])


def _payload(reminder: dict) -> dict:
    category = reminder_category(reminder)
    manual = get_reminder_category_override(int(reminder["user_id"]), int(reminder["reminder_id"]))
    return {
        "id": int(reminder["reminder_id"]),
        "text": str(reminder.get("text") or ""),
        "remind_at": reminder.get("remind_at"),
        "status": reminder.get("status"),
        "repeat_rule": reminder.get("repeat_rule"),
        "repeat_timezone": reminder.get("repeat_timezone"),
        "category": category,
        "category_source": "manual" if manual else "auto",
        "category_label": REMINDER_CATEGORY_LABELS.get(category, REMINDER_CATEGORY_LABELS["other"]),
    }


@reminder_detail_api.get("/api/mobile/reminders/details")
def reminder_details_list():
    user_id = _user()
    items = list_saved_reminders(user_id, limit=500)
    return {
        "items": [_payload(item) for item in items],
        "categories": REMINDER_CATEGORY_LABELS,
    }


@reminder_detail_api.get("/api/mobile/reminders/<int:reminder_id>/details")
def reminder_details(reminder_id: int):
    reminder = get_saved_reminder(_user(), reminder_id)
    if not reminder:
        return jsonify(error="reminder_not_found"), 404
    return {"reminder": _payload(reminder), "categories": REMINDER_CATEGORY_LABELS}


@reminder_detail_api.patch("/api/mobile/reminders/<int:reminder_id>/details")
def edit_reminder_details(reminder_id: int):
    user_id = _user()
    payload = request.get_json(silent=True) or {}
    allowed = {"text", "category", "remind_at", "repeat_rule"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        return jsonify(error="invalid_reminder_update", message="Неизвестные поля напоминания"), 400
    if not payload:
        return jsonify(error="invalid_reminder_update", message="Нет изменений"), 400

    kwargs = {}
    if "text" in payload:
        kwargs["text"] = payload.get("text")
    if "category" in payload:
        value = payload.get("category")
        kwargs["category"] = None if value in {None, "", "auto"} else value
    if "remind_at" in payload:
        kwargs["remind_at"] = payload.get("remind_at")
    if "repeat_rule" in payload:
        value = payload.get("repeat_rule")
        kwargs["repeat_rule"] = None if value in {None, "", "none"} else value
        kwargs["repeat_timezone"] = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"

    try:
        reminder = edit_saved_reminder(user_id, reminder_id, **kwargs)
    except (TypeError, ValueError) as exc:
        return jsonify(error="invalid_reminder_update", message=str(exc)), 400
    if not reminder:
        return jsonify(error="reminder_not_found"), 404
    return {"ok": True, "reminder": _payload(reminder)}
