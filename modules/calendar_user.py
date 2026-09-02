"""Пользовательский слой календаря с настройками профиля."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import logging
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_google_token, get_user_timezone
from modules.calendar import _build_event, _create_event, _parse_event_timing

logger = logging.getLogger(__name__)

WEEKDAYS = {
    "понедельник": 0,
    "понедельника": 0,
    "вторник": 1,
    "вторника": 1,
    "среда": 2,
    "среду": 2,
    "среды": 2,
    "четверг": 3,
    "четверга": 3,
    "пятница": 4,
    "пятницу": 4,
    "пятницы": 4,
    "суббота": 5,
    "субботу": 5,
    "субботы": 5,
    "воскресенье": 6,
    "воскресенья": 6,
}

WEEKDAY_LABELS = {
    0: "понедельник",
    1: "вторник",
    2: "среду",
    3: "четверг",
    4: "пятницу",
    5: "субботу",
    6: "воскресенье",
}


def _get_calendar_service(user_id: int):
    token_dict = get_google_token(user_id)
    if not token_dict:
        raise PermissionError("GOOGLE_AUTH_REQUIRED")
    credentials = Credentials.from_authorized_user_info(token_dict)
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _user_zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone}") from exc


def _start_of_day(value: datetime) -> datetime:
    return datetime.combine(value.date(), time.min, tzinfo=value.tzinfo)


def _parse_view_period(
    text: str,
    timezone: str,
    now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    """Определить период просмотра календаря из русской команды."""
    zone = _user_zone(timezone)
    now = now.astimezone(zone) if now and now.tzinfo else (now.replace(tzinfo=zone) if now else datetime.now(zone))
    lower = text.lower().replace("ё", "е")
    today = _start_of_day(now)

    if re.search(r"\bпослезавтра\b|\bпосле\s+завтра\b", lower):
        start = today + timedelta(days=2)
        return start, start + timedelta(days=1), "послезавтра"

    if re.search(r"\bзавтра\b", lower):
        start = today + timedelta(days=1)
        return start, start + timedelta(days=1), "завтра"

    if re.search(r"\bсегодня\b", lower):
        return today, today + timedelta(days=1), "сегодня"

    if re.search(r"\bследующ\w*\s+недел\w*\b|\bна\s+следующ\w*\s+недел\w*\b", lower):
        current_monday = today - timedelta(days=today.weekday())
        start = current_monday + timedelta(days=7)
        return start, start + timedelta(days=7), "на следующей неделе"

    if re.search(r"\b(?:эта|эту|текущ\w*|на)\s+недел\w*\b|\bнедел\w*\b", lower):
        # Для текущей недели показываем оставшуюся часть недели, а не уже прошедшие дни.
        start = today
        end = today + timedelta(days=7 - today.weekday())
        return start, end, "на этой неделе"

    for word, weekday in WEEKDAYS.items():
        if not re.search(rf"\b{word}\b", lower):
            continue
        days = (weekday - today.weekday()) % 7
        force_next = bool(re.search(r"\bследующ\w*[^\n]{0,20}" + re.escape(word), lower))
        if force_next:
            days = days + 7 if days else 7
        start = today + timedelta(days=days)
        return start, start + timedelta(days=1), WEEKDAY_LABELS[weekday]

    # Если период не указан, просмотр календаря означает сегодня.
    return today, today + timedelta(days=1), "сегодня"


def _list_events(user_id: int, start: datetime, end: datetime) -> list[dict]:
    service = _get_calendar_service(user_id)
    response = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=100,
        )
        .execute()
    )
    return response.get("items", [])


def _event_start(event: dict, timezone: str) -> tuple[datetime | None, bool]:
    start = event.get("start") or {}
    if start.get("dateTime"):
        value = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        return value.astimezone(_user_zone(timezone)), False
    if start.get("date"):
        value = datetime.fromisoformat(start["date"]).replace(tzinfo=_user_zone(timezone))
        return value, True
    return None, False


def _format_event_line(event: dict, timezone: str, include_date: bool) -> str:
    summary = (event.get("summary") or "Без названия").strip()
    start, all_day = _event_start(event, timezone)
    if not start:
        return f"• {summary}"
    if all_day:
        prefix = start.strftime("%d.%m") + " · весь день" if include_date else "весь день"
    else:
        prefix = start.strftime("%d.%m · %H:%M") if include_date else start.strftime("%H:%M")
    return f"• {prefix} — {summary}"


def _format_events(events: list[dict], timezone: str, start: datetime, end: datetime, label: str) -> str:
    if not events:
        return f"На {label} событий нет."

    include_date = (end - start) > timedelta(days=1)
    lines = [_format_event_line(event, timezone, include_date) for event in events]
    return f"Календарь {label}:\n" + "\n".join(lines)


async def create_from_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> bool:
    """Создать календарное событие из уже классифицированной команды."""
    timing = _parse_event_timing(text)
    if not timing:
        return False

    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default=None)
    if not timezone:
        await update.message.reply_text(
            "Сначала выбери часовой пояс для календаря: /timezone"
        )
        return True

    start, end = timing

    try:
        event = _build_event(text, start, end)
        event["start"]["timeZone"] = timezone
        event["end"]["timeZone"] = timezone
        _create_event(user_id, event)
    except PermissionError:
        await update.message.reply_text("Сначала подключите Google Calendar: /start")
        return True
    except Exception:
        logger.exception("Calendar event creation failed for user %s", user_id)
        await update.message.reply_text(
            "Не удалось добавить событие в Google Calendar. Попробуйте ещё раз."
        )
        return True

    await update.message.reply_text(
        f"Событие «{event['summary']}» добавлено: "
        f"{start.strftime('%d.%m.%Y %H:%M')}–{end.strftime('%H:%M')} ({timezone})"
    )
    return True


async def view_from_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> bool:
    """Показать события пользователя за день или неделю."""
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default=None)
    if not timezone:
        await update.message.reply_text("Сначала выбери часовой пояс для календаря: /timezone")
        return True

    try:
        start, end, label = _parse_view_period(text, timezone)
        events = _list_events(user_id, start, end)
    except PermissionError:
        await update.message.reply_text("Сначала подключите Google Calendar: /start")
        return True
    except Exception:
        logger.exception("Calendar event listing failed for user %s", user_id)
        await update.message.reply_text("Не удалось прочитать Google Calendar. Попробуй ещё раз.")
        return True

    await update.message.reply_text(_format_events(events, timezone, start, end, label))
    return True


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Совместимость: прямой вызов трактуется как создание события."""
    if not update.message or not update.message.text:
        return False
    text = update.message.text.strip()
    if not text:
        return False
    return await create_from_text(update, context, text)
