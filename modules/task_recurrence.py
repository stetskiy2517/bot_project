"""Deterministic recurrence for planner tasks."""

from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.db import get_user_timezone
from core.task_planner_store import TASK_REPEAT_RULES, create_planner_task

SUPPORTED_TASK_REPEAT_RULES = set(TASK_REPEAT_RULES)


def _parse_due(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _add_month(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _zone(name: str | None):
    try:
        return ZoneInfo(str(name or "UTC"))
    except ZoneInfoNotFoundError:
        return timezone.utc


def next_task_due(
    due_at: object,
    repeat_rule: object,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> datetime | None:
    due = _parse_due(due_at)
    rule = str(repeat_rule or "").strip().lower()
    if due is None or rule not in SUPPORTED_TASK_REPEAT_RULES:
        return None
    zone = _zone(timezone_name)
    current = (now or datetime.now(timezone.utc)).astimezone(zone)
    candidate = due.astimezone(zone)
    for _ in range(500):
        if rule == "daily":
            candidate += timedelta(days=1)
        elif rule == "weekly":
            candidate += timedelta(days=7)
        else:
            candidate = _add_month(candidate)
        if candidate > current:
            return candidate.astimezone(timezone.utc)
    return None


def create_next_recurring_task(user_id: int, completed_task: dict, *, now: datetime | None = None) -> dict | None:
    timezone_name = get_user_timezone(user_id, default="UTC") or "UTC"
    next_due = next_task_due(
        completed_task.get("due_at"),
        completed_task.get("repeat_rule"),
        now=now,
        timezone_name=timezone_name,
    )
    if next_due is None:
        return None
    return create_planner_task(
        user_id,
        completed_task.get("title"),
        description=completed_task.get("description") or "",
        due_at=next_due,
        priority=completed_task.get("priority") or "normal",
        category=completed_task.get("category") or "other",
        estimate_minutes=completed_task.get("estimate_minutes"),
        flexible=bool(completed_task.get("flexible", True)),
        parent_task_id=completed_task.get("parent_task_id"),
        repeat_rule=str(completed_task.get("repeat_rule") or "").strip().lower(),
    )
