"""Reminder categorization aligned with calendar categories."""

from __future__ import annotations

from core.reminder_detail_store import get_reminder_category_override
from modules.calendar import _detect_category

REMINDER_CATEGORY_LABELS = {
    "work": "Работа",
    "health": "Здоровье",
    "rest": "Отдых",
    "travel": "Поездки",
    "family": "Семья",
    "personal": "Личное",
    "other": "Прочее",
}


def detect_reminder_category(text: str) -> str:
    value = str(text or "").strip()
    category = _detect_category(value)[0] if value else "other"
    return category if category in REMINDER_CATEGORY_LABELS else "other"


def reminder_category(reminder: dict) -> str:
    stored = str(reminder.get("category") or "").strip().lower()
    if stored in REMINDER_CATEGORY_LABELS:
        return stored

    user_id = reminder.get("user_id")
    reminder_id = reminder.get("reminder_id") or reminder.get("id")
    if user_id is not None and reminder_id is not None:
        override = get_reminder_category_override(int(user_id), int(reminder_id))
        if override in REMINDER_CATEGORY_LABELS:
            return override

    return detect_reminder_category(str(reminder.get("text") or ""))
