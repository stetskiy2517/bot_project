"""Свободные окна и альтернативы времени для AI Smart Planner."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import re

from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_user_timezone
from modules.calendar import _extract_duration
from modules.calendar_user import (
    _event_start,
    _list_events,
    _parse_view_period,
    _user_zone,
)

DEFAULT_WORK_START = time(9, 0)
DEFAULT_WORK_END = time(20, 0)
DEFAULT_SLOT_DURATION = timedelta(hours=1)
SLOT_STEP = timedelta(minutes=30)
MAX_SUGGESTIONS = 5


def _event_end(event: dict, timezone: str) -> datetime | None:
    end = event.get("end") or {}
    zone = _user_zone(timezone)
    if end.get("dateTime"):
        return datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00")).astimezone(zone)
    if end.get("date"):
        return datetime.fromisoformat(end["date"]).replace(tzinfo=zone)
    return None


def _requested_duration(text: str) -> timedelta:
    duration = _extract_duration(text)
    return duration if duration > timedelta(0) else DEFAULT_SLOT_DURATION


def _period_has_explicit_day(text: str) -> bool:
    lower = text.lower().replace("ё", "е")
    return bool(re.search(
        r"\b(?:сегодня|завтра|послезавтра|понедельник\w*|вторник\w*|сред\w*|"
        r"четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)\b",
        lower,
    ))


def _availability_period(
    text: str,
    timezone: str,
    now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    zone = _user_zone(timezone)
    local_now = now.astimezone(zone) if now and now.tzinfo else (now.replace(tzinfo=zone) if now else datetime.now(zone))
    if _period_has_explicit_day(text):
        return _parse_view_period(text, timezone, local_now)

    # Без даты ищем сегодня, если рабочий день ещё не закончился; иначе завтра.
    today_start = datetime.combine(local_now.date(), time.min, tzinfo=zone)
    if local_now.time() < DEFAULT_WORK_END:
        return today_start, today_start + timedelta(days=1), "сегодня"
    start = today_start + timedelta(days=1)
    return start, start + timedelta(days=1), "завтра"


def _busy_intervals(events: list[dict], timezone: str) -> list[tuple[datetime, datetime]]:
    intervals: list[tuple[datetime, datetime]] = []
    for event in events:
        if event.get("transparency") == "transparent":
            continue
        start, _ = _event_start(event, timezone)
        end = _event_end(event, timezone)
        if start and end and end > start:
            intervals.append((start, end))
    intervals.sort(key=lambda item: item[0])
    return intervals


def _overlaps(start: datetime, end: datetime, busy: list[tuple[datetime, datetime]]) -> bool:
    return any(busy_start < end and busy_end > start for busy_start, busy_end in busy)


def find_free_slots(
    events: list[dict],
    timezone: str,
    period_start: datetime,
    period_end: datetime,
    duration: timedelta,
    *,
    work_start: time = DEFAULT_WORK_START,
    work_end: time = DEFAULT_WORK_END,
    step: timedelta = SLOT_STEP,
    now: datetime | None = None,
    limit: int = MAX_SUGGESTIONS,
) -> list[tuple[datetime, datetime]]:
    """Найти свободные интервалы заданной длины в пределах рабочего окна."""
    zone = _user_zone(timezone)
    busy = _busy_intervals(events, timezone)
    local_now = now.astimezone(zone) if now and now.tzinfo else (now.replace(tzinfo=zone) if now else datetime.now(zone))
    slots: list[tuple[datetime, datetime]] = []

    day = period_start.date()
    last_day = (period_end - timedelta(microseconds=1)).date()
    while day <= last_day and len(slots) < limit:
        day_start = datetime.combine(day, work_start, tzinfo=zone)
        day_end = datetime.combine(day, work_end, tzinfo=zone)
        window_start = max(day_start, period_start)
        window_end = min(day_end, period_end)
        if day == local_now.date():
            rounded = local_now.replace(second=0, microsecond=0)
            remainder = rounded.minute % 30
            if remainder:
                rounded += timedelta(minutes=30 - remainder)
            window_start = max(window_start, rounded)

        cursor = window_start
        while cursor + duration <= window_end and len(slots) < limit:
            candidate_end = cursor + duration
            if not _overlaps(cursor, candidate_end, busy):
                slots.append((cursor, candidate_end))
            cursor += step
        day += timedelta(days=1)

    return slots


def suggest_alternatives(
    user_id: int,
    timezone: str,
    desired_start: datetime,
    duration: timedelta,
    *,
    limit: int = 3,
) -> list[tuple[datetime, datetime]]:
    """Предложить ближайшие свободные слоты после конфликтного времени."""
    zone = _user_zone(timezone)
    start = desired_start.astimezone(zone) if desired_start.tzinfo else desired_start.replace(tzinfo=zone)
    end_of_search = datetime.combine(start.date(), DEFAULT_WORK_END, tzinfo=zone)
    if start >= end_of_search:
        next_day = start.date() + timedelta(days=1)
        start = datetime.combine(next_day, DEFAULT_WORK_START, tzinfo=zone)
        end_of_search = datetime.combine(next_day, DEFAULT_WORK_END, tzinfo=zone)

    events = _list_events(user_id, start, end_of_search)
    return find_free_slots(
        events,
        timezone,
        start,
        end_of_search,
        duration,
        now=start,
        limit=limit,
    )


def _format_slot(slot: tuple[datetime, datetime], include_date: bool = False) -> str:
    start, end = slot
    if include_date:
        return f"{start.strftime('%d.%m %H:%M')}–{end.strftime('%H:%M')}"
    return f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"


def format_alternatives(slots: list[tuple[datetime, datetime]]) -> str:
    if not slots:
        return "Свободных вариантов рядом не нашёл."
    return "Могу предложить: " + ", ".join(_format_slot(slot) for slot in slots)


async def free_slots_from_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default=None)
    if not timezone:
        await update.message.reply_text("Сначала выбери часовой пояс для календаря: /timezone")
        return True

    try:
        start, end, label = _availability_period(text, timezone)
        duration = _requested_duration(text)
        events = _list_events(user_id, start, end)
        slots = find_free_slots(events, timezone, start, end, duration)
    except PermissionError:
        await update.message.reply_text("Сначала подключите Google Calendar: /start")
        return True
    except Exception:
        await update.message.reply_text("Не удалось проверить свободное время в Google Calendar.")
        return True

    if not slots:
        await update.message.reply_text(
            f"На {label} не нашёл свободного окна длительностью {int(duration.total_seconds() // 60)} минут."
        )
        return True

    include_date = (end - start) > timedelta(days=1)
    lines = [f"• {_format_slot(slot, include_date=include_date)}" for slot in slots]
    await update.message.reply_text(
        f"Свободные окна {label} на {int(duration.total_seconds() // 60)} минут:\n" + "\n".join(lines)
    )
    return True
