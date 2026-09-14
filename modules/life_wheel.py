"""Life wheel snapshot built from categorized calendar activity."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

from core.db import get_category_colors, get_user_timezone
from modules.calendar import _detect_category
from modules.calendar_user import _event_start, _list_events

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


def _covered_dates(start: datetime, end: datetime, all_day: bool) -> set[str]:
    dates: set[str] = set()
    cursor = start.date()
    if all_day:
        last = (end - timedelta(microseconds=1)).date()
    else:
        last = end.date()
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
    # One scheduled item counts as activity, while duration matters only up to four hours.
    # This keeps a long meeting from dominating an entire category by itself.
    return 1.0 + min(hours, 4.0) / 4.0, hours, 0


def build_life_wheel_snapshot(
    user_id: int,
    *,
    days: int = 30,
    now: datetime | None = None,
    events: list[dict] | None = None,
) -> dict:
    timezone_name = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    period_start, period_end = _period_bounds(timezone_name, int(days), now)
    calendar_events = events if events is not None else _list_events(user_id, period_start, period_end)
    colors = get_category_colors(user_id)

    stats = {
        key: {
            "key": key,
            "label": CATEGORY_LABELS[key],
            "color_id": colors.get(key),
            "events": 0,
            "hours": 0.0,
            "all_day_days": 0,
            "active_days": set(),
            "event_units": 0.0,
        }
        for key in CATEGORY_ORDER
    }

    total_dates: set[str] = set()
    counted_events = 0
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
        dates = _covered_dates(start, end, all_day)
        current = stats[category]
        current["events"] += 1
        current["hours"] += hours
        current["all_day_days"] += all_day_days
        current["active_days"].update(dates)
        current["event_units"] += units
        total_dates.update(dates)
        counted_events += 1

    activity_values: dict[str, float] = {}
    for key, item in stats.items():
        # Regular presence matters more than packing many small items into one day.
        activity_values[key] = len(item["active_days"]) + item["event_units"] * 0.5
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
            "active_days": len(total_dates),
            "skipped_events": skipped_events,
        },
        "source": "google_calendar",
        "reminders_included": False,
        "metric": "relative_activity",
        "metric_help": "10 — самая активная категория за выбранный период, а не оценка качества жизни.",
    }
