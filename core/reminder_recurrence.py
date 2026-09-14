"""Deterministic recurrence helpers for standalone reminders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SIMPLE_REPEAT_RULES = {"daily", "weekdays", "weekends", "weekly"}
WEEKLY_DAY_RE = re.compile(r"^weekly:(?P<weekday>[0-6])$")


def validate_repeat_rule(rule: str | None) -> str | None:
    """Return a normalized supported repeat rule or raise for invalid input."""
    if rule is None:
        return None
    normalized = str(rule).strip().lower()
    if normalized in SIMPLE_REPEAT_RULES or WEEKLY_DAY_RE.fullmatch(normalized):
        return normalized
    raise ValueError("Unsupported reminder repeat rule")


def _zone(name: str | None):
    try:
        return ZoneInfo(str(name or "UTC"))
    except ZoneInfoNotFoundError:
        return timezone.utc


def next_repeat_at(value: datetime, rule: str, timezone_name: str | None) -> datetime:
    """Calculate the next occurrence while preserving the user's local wall-clock time."""
    normalized = validate_repeat_rule(rule)
    if normalized is None:
        raise ValueError("Repeat rule is required")
    if value.tzinfo is None:
        raise ValueError("Reminder datetime must be timezone-aware")

    zone = _zone(timezone_name)
    local = value.astimezone(zone)

    if normalized == "daily":
        days = 1
    elif normalized == "weekly":
        days = 7
    elif normalized == "weekdays":
        days = 1
        while (local + timedelta(days=days)).weekday() >= 5:
            days += 1
    elif normalized == "weekends":
        days = 1
        while (local + timedelta(days=days)).weekday() < 5:
            days += 1
    else:
        target = int(WEEKLY_DAY_RE.fullmatch(normalized).group("weekday"))
        days = (target - local.weekday()) % 7
        if days == 0:
            days = 7

    next_local = local + timedelta(days=days)
    return next_local.astimezone(timezone.utc)


def repeat_label(rule: str | None) -> str | None:
    normalized = validate_repeat_rule(rule)
    if normalized is None:
        return None
    labels = {
        "daily": "каждый день",
        "weekdays": "по будням",
        "weekends": "по выходным",
        "weekly": "каждую неделю",
    }
    if normalized in labels:
        return labels[normalized]
    weekday = int(WEEKLY_DAY_RE.fullmatch(normalized).group("weekday"))
    weekday_labels = {
        0: "каждый понедельник",
        1: "каждый вторник",
        2: "каждую среду",
        3: "каждый четверг",
        4: "каждую пятницу",
        5: "каждую субботу",
        6: "каждое воскресенье",
    }
    return weekday_labels[weekday]
