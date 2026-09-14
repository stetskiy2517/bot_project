"""Reminder categorization aligned with calendar categories."""

from __future__ import annotations

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
    return detect_reminder_category(str(reminder.get("text") or ""))
