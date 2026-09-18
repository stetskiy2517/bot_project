"""Safe flexible task scheduling on top of the existing calendar engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from zoneinfo import ZoneInfo

from core.db import get_calendar_preferences, get_category_colors, get_user_timezone
from core.task_planner_store import get_planner_task, link_task_calendar, list_planner_tasks
from modules.calendar import _create_event
from modules.calendar_availability import _busy_intervals, _overlaps, _parse_hhmm, find_free_slots
from modules.calendar_user import _get_calendar_service, _list_events

logger = logging.getLogger(__name__)
MAX_AUTO_PLAN_DAYS = 30
MAX_AUTO_PLAN_TASKS = 20
PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


def _zone(user_id: int) -> ZoneInfo:
    return ZoneInfo(get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow")


def _parse_due(task: dict, zone: ZoneInfo) -> datetime | None:
    raw = task.get("due_at")
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return value.astimezone(zone)


def _task_sort_key(task: dict, zone: ZoneInfo) -> tuple:
    due = _parse_due(task, zone)
    return (
        PRIORITY_ORDER.get(str(task.get("priority") or "normal"), 1),
        due or datetime.max.replace(tzinfo=zone),
        int(task.get("task_id") or 0),
    )


def preview_flexible_schedule(user_id: int, *, now: datetime | None = None) -> dict:
    """Suggest calendar slots without writing anything."""
    zone = _zone(user_id)
    local_now = (now or datetime.now(timezone.utc)).astimezone(zone)
    horizon = local_now + timedelta(days=MAX_AUTO_PLAN_DAYS)
    prefs = get_calendar_preferences(user_id)
    work_start = _parse_hhmm(prefs["work_start"])
    work_end = _parse_hhmm(prefs["work_end"])
    work_days = prefs["work_days"]
    buffer = timedelta(minutes=prefs["buffer_minutes"])

    candidates = []
    skipped = []
    for task in list_planner_tasks(user_id, status="open", limit=500):
        if not task.get("flexible"):
            skipped.append({"task_id": task["task_id"], "reason": "fixed"})
            continue
        if task.get("calendar_event_id"):
            skipped.append({"task_id": task["task_id"], "reason": "already_scheduled"})
            continue
        estimate = task.get("estimate_minutes")
        if not estimate:
            skipped.append({"task_id": task["task_id"], "reason": "missing_estimate"})
            continue
        due = _parse_due(task, zone)
        if due is None:
            skipped.append({"task_id": task["task_id"], "reason": "missing_deadline"})
            continue
        if due <= local_now:
            skipped.append({"task_id": task["task_id"], "reason": "overdue"})
            continue
        candidates.append(task)

    candidates.sort(key=lambda item: _task_sort_key(item, zone))
    candidates = candidates[:MAX_AUTO_PLAN_TASKS]
    if not candidates:
        return {"proposals": [], "skipped": skipped, "generated_at": local_now.isoformat()}

    latest_due = min(
        horizon,
        max((_parse_due(task, zone) or local_now) for task in candidates),
    )
    events = _list_events(user_id, local_now, latest_due)
    simulated_events = list(events)
    proposals = []

    for task in candidates:
        due = min(_parse_due(task, zone) or horizon, horizon)
        duration = timedelta(minutes=int(task["estimate_minutes"]))
        slots = find_free_slots(
            simulated_events,
            str(zone),
            local_now,
            due,
            duration,
            work_start=work_start,
            work_end=work_end,
            work_days=work_days,
            buffer=buffer,
            now=local_now,
            limit=1,
        )
        if not slots:
            skipped.append({"task_id": task["task_id"], "reason": "no_slot_before_deadline"})
            continue
        start, end = slots[0]
        proposals.append(
            {
                "task_id": task["task_id"],
                "title": task["title"],
                "priority": task["priority"],
                "category": task.get("category") or "other",
                "estimate_minutes": task["estimate_minutes"],
                "due_at": task["due_at"],
                "start": start.isoformat(),
                "end": end.isoformat(),
            }
        )
        simulated_events.append(
            {
                "summary": task["title"],
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
                "transparency": "opaque",
            }
        )

    return {"proposals": proposals, "skipped": skipped, "generated_at": local_now.isoformat()}


def _validate_requested_slot(user_id: int, task: dict, start: datetime, end: datetime) -> None:
    zone = _zone(user_id)
    local_start = start.astimezone(zone)
    local_end = end.astimezone(zone)
    now = datetime.now(zone)
    prefs = get_calendar_preferences(user_id)
    if start <= datetime.now(timezone.utc) or end <= start:
        raise ValueError("Предложенное время уже недоступно")
    if local_start.date() != local_end.date():
        raise ValueError("Гибкая задача должна помещаться в один рабочий день")
    if local_start.weekday() not in prefs["work_days"]:
        raise ValueError("Время вне выбранных рабочих дней")
    if local_start.time() < _parse_hhmm(prefs["work_start"]) or local_end.time() > _parse_hhmm(prefs["work_end"]):
        raise ValueError("Время вне рабочих часов")
    due = _parse_due(task, zone)
    if due and local_end > due:
        raise ValueError("Задача не помещается до дедлайна")
    if local_start <= now:
        raise ValueError("Предложенное время уже прошло")
    buffer = timedelta(minutes=prefs["buffer_minutes"])
    events = _list_events(user_id, local_start - buffer, local_end + buffer)
    if _overlaps(local_start, local_end, _busy_intervals(events, str(zone), buffer=buffer)):
        raise ValueError("Это окно уже занято. Обнови план")


def apply_task_slot(user_id: int, task_id: int, start_value: str) -> dict:
    """Apply one user-approved proposal after rechecking the calendar."""
    task = get_planner_task(user_id, task_id)
    if not task or task.get("status") != "open":
        raise ValueError("Открытая задача не найдена")
    if not task.get("flexible"):
        raise ValueError("Эта задача отмечена как фиксированная")
    if task.get("calendar_event_id"):
        raise ValueError("Для задачи уже выделено время")
    estimate = task.get("estimate_minutes")
    if not estimate:
        raise ValueError("Сначала укажи длительность задачи")
    try:
        start = datetime.fromisoformat(str(start_value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Некорректное время задачи") from exc
    if start.tzinfo is None:
        raise ValueError("Время задачи должно содержать часовой пояс")
    start = start.astimezone(timezone.utc)
    end = start + timedelta(minutes=int(estimate))
    _validate_requested_slot(user_id, task, start, end)

    zone = _zone(user_id)
    local_start = start.astimezone(zone)
    local_end = end.astimezone(zone)
    category = task.get("category") or "other"
    details = str(task.get("description") or "").strip()
    event_description = f"AI Smart Planner category: {category}\nГибкая задача #{task_id}"
    if details:
        event_description += f"\n\n{details[:4000]}"
    event = {
        "summary": task["title"][:200],
        "description": event_description,
        "start": {"dateTime": local_start.isoformat(), "timeZone": str(zone)},
        "end": {"dateTime": local_end.isoformat(), "timeZone": str(zone)},
        "extendedProperties": {
            "private": {
                "smartPlannerType": "task",
                "smartPlannerManaged": "1",
                "smartPlannerTaskId": str(task_id),
            }
        },
        "transparency": "opaque",
    }
    color = get_category_colors(user_id).get(category)
    if color:
        event["colorId"] = color
    created = _create_event(user_id, event)
    event_id = str(created.get("id") or "").strip()
    if not event_id:
        raise RuntimeError("Calendar did not return an event id")
    return link_task_calendar(
        user_id,
        task_id,
        calendar_event_id=event_id,
        scheduled_start=start,
    )


def remove_future_task_block(user_id: int, task: dict) -> None:
    """Best-effort cleanup for a managed future calendar block."""
    event_id = str(task.get("calendar_event_id") or "").strip()
    if not event_id:
        return
    scheduled = task.get("scheduled_start")
    if scheduled:
        try:
            value = datetime.fromisoformat(str(scheduled).replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            if value.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                return
        except ValueError:
            pass
    try:
        _get_calendar_service(user_id).events().delete(calendarId="primary", eventId=event_id).execute()
    except Exception:
        logger.exception("Could not delete task calendar block user=%s task=%s", user_id, task.get("task_id"))
