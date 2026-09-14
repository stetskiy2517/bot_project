"""Life wheel snapshot built from categorized calendar and reminder activity."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

from core.db import get_category_colors, get_user_timezone
from core.library_store import list_saved_reminders
from modules.calendar import _detect_category
from modules.calendar_user import _event_start, _list_events
from modules.reminder_categories import reminder_category

LIFE_WHEEL_PERIODS = {7, 30, 90}
CATEGORY_ORDER = ("work", "health", "rest", "travel", "family", "personal", "other")
CATEGORY_LABELS = {
    "work": "Работа",
    "health": "Здоровье",
    "rest": "Отдых",
    "travel": "Поездки",
    "family": "Семья",
    "personal": "Личное",
    "other": "Прочее",
}
CATEGORY_MARKER_RE = re.compile(
    r"(?:^|\n)AI Smart Planner category:\s*(work|health|rest|travel|family|personal|other)\b",
    re.IGNORECASE,
)


def _local_now(timezone_name: str, now: datetime | None = None) -> datetime:
    zone = ZoneInfo(timezone_name)
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None:
        return now.replace(tzinfo=zone)
    return now.astimezone(zone)


def _period_bounds(timezone_name: str, days: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    if days not in LIFE_WHEEL_PERIODS:
        raise ValueError("Период колеса жизни должен быть 7, 30 или 90 дней")
    local_now = _local_now(timezone_name, now)
    end = datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=local_now.tzinfo)
    return end - timedelta(days=days), end


def _event_end(event: dict, timezone_name: str) -> datetime | None:
    end = event.get("end") or {}
    zone = ZoneInfo(timezone_name)
    if end.get("dateTime"):
        value = datetime.fromisoformat(str(end["dateTime"]).replace("Z", "+00:00"))
        return value.astimezone(zone) if value.tzinfo else value.replace(tzinfo=zone)
    if end.get("date"):
        return datetime.fromisoformat(str(end["date"])).replace(tzinfo=zone)
    return None


def event_category(event: dict) -> str:
    description = str(event.get("description") or "")
    marker = CATEGORY_MARKER_RE.search(description)
    if marker:
        return marker.group(1).lower()
    text = "\n".join(
        part for part in (str(event.get("summary") or ""), description) if part.strip()
    )
    return _detect_category(text)[0] if text else "other"


def _event_overlap(
    event: dict,
    timezone_name: str,
    period_start: datetime,
    period_end: datetime,
) -> tuple[datetime, datetime, bool] | None:
    start, all_day = _event_start(event, timezone_name)
    end = _event_end(event, timezone_name)
    if not start or not end or end <= start:
        return None
    overlap_start = max(start, period_start)
    overlap_end = min(end, period_end)
    if overlap_end <= overlap_start:
        return None
    return overlap_start, overlap_end, all_day


def _covered_dates(start: datetime, end: datetime) -> set[str]:
    dates: set[str] = set()
    cursor = start.date()
    last = (end - timedelta(microseconds=1)).date()
    while cursor <= last:
        dates.add(cursor.isoformat())
        cursor += timedelta(days=1)
    return dates


def _event_units(start: datetime, end: datetime, all_day: bool) -> tuple[float, float, int]:
    seconds = max(0.0, (end - start).total_seconds())
    if all_day:
        days = max(1, int(round(seconds / 86400)))
        return float(days), 0.0, days
    hours = seconds / 3600
    return 1.0 + min(hours, 4.0) / 4.0, hours, 0


def _reminder_due(reminder: dict, timezone_name: str) -> datetime | None:
    raw = str(reminder.get("remind_at") or "").strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    zone = ZoneInfo(timezone_name)
    return value.astimezone(zone) if value.tzinfo else value.replace(tzinfo=zone)


def build_life_wheel_snapshot(
    user_id: int,
    *,
    days: int = 30,
    now: datetime | None = None,
    events: list[dict] | None = None,
    reminders: list[dict] | None = None,
) -> dict:
    timezone_name = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    period_start, period_end = _period_bounds(timezone_name, int(days), now)
    calendar_events = events if events is not None else _list_events(user_id, period_start, period_end)
    saved_reminders = reminders if reminders is not None else list_saved_reminders(user_id, limit=500)
    colors = get_category_colors(user_id)

    stats = {
        key: {
            "key": key,
            "label": CATEGORY_LABELS[key],
            "color_id": colors.get(key),
            "events": 0,
            "reminders": 0,
            "hours": 0.0,
            "all_day_days": 0,
            "active_days": set(),
            "activity_units": 0.0,
        }
        for key in CATEGORY_ORDER
    }

    total_dates: set[str] = set()
    counted_events = 0
    counted_reminders = 0
    skipped_events = 0
    for event in calendar_events:
        if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
            skipped_events += 1
            continue
        overlap = _event_overlap(event, timezone_name, period_start, period_end)
        if not overlap:
            skipped_events += 1
            continue
        start, end, all_day = overlap
        category = event_category(event)
        if category not in stats:
            category = "other"
        units, hours, all_day_days = _event_units(start, end, all_day)
        dates = _covered_dates(start, end)
        current = stats[category]
        current["events"] += 1
        current["hours"] += hours
        current["all_day_days"] += all_day_days
        current["active_days"].update(dates)
        current["activity_units"] += units
        total_dates.update(dates)
        counted_events += 1

    for reminder in saved_reminders:
        due = _reminder_due(reminder, timezone_name)
        if due is None or not period_start <= due < period_end:
            continue
        category = reminder_category(reminder)
        if category not in stats:
            category = "other"
        day = due.date().isoformat()
        current = stats[category]
        current["reminders"] += 1
        current["active_days"].add(day)
        current["activity_units"] += 0.75
        total_dates.add(day)
        counted_reminders += 1

    activity_values = {
        key: len(item["active_days"]) + item["activity_units"] * 0.5
        for key, item in stats.items()
    }
    max_activity = max(activity_values.values(), default=0.0)

    categories = []
    for key in CATEGORY_ORDER:
        item = stats[key]
        activity = activity_values[key]
        score = round((activity / max_activity) * 10, 1) if max_activity > 0 else 0.0
        categories.append(
            {
                "key": key,
                "label": item["label"],
                "color_id": item["color_id"],
                "score": score,
                "events": int(item["events"]),
                "reminders": int(item["reminders"]),
                "hours": round(float(item["hours"]), 1),
                "all_day_days": int(item["all_day_days"]),
                "active_days": len(item["active_days"]),
            }
        )

    return {
        "period": {
            "days": int(days),
            "start": period_start.date().isoformat(),
            "end": (period_end - timedelta(days=1)).date().isoformat(),
            "timezone": timezone_name,
        },
        "categories": categories,
        "totals": {
            "events": counted_events,
            "reminders": counted_reminders,
            "items": counted_events + counted_reminders,
            "active_days": len(total_dates),
            "skipped_events": skipped_events,
        },
        "source": "calendar_and_reminders",
        "reminders_included": True,
        "metric": "relative_activity",
        "metric_help": "10 — самая активная категория за выбранный период, а не оценка качества жизни.",
    }
