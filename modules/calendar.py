"""Календарный модуль: разбор команд и создание событий в Google Calendar."""

from datetime import datetime, timedelta
import logging
import re

import dateparser
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_google_token

logger = logging.getLogger(__name__)

WEEKDAYS = {
    "понедельник": 0, "понедельника": 0,
    "вторник": 1, "вторника": 1,
    "среда": 2, "среду": 2, "среды": 2,
    "четверг": 3, "четверга": 3,
    "пятница": 4, "пятницу": 4, "пятницы": 4,
    "суббота": 5, "субботу": 5, "субботы": 5,
    "воскресенье": 6, "воскресенья": 6,
}

MONTHS_PATTERN = (
    r"январ[ья]|феврал[ья]|март[ае]?|апрел[ья]|ма[йя]|июн[ья]|июл[ья]|"
    r"август[ае]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья]"
)

TIME_RE = re.compile(
    r"(?<!\d)(?:в\s*|к\s*)?(?P<hour>[01]?\d|2[0-3])"
    r"(?:\s*(?::|\.)\s*(?P<minute>[0-5]\d)|\s+(?P<space_minute>[0-5]\d))"
    r"(?:\s*(?:ч|час(?:а|ов)?))?(?!\d)",
    re.IGNORECASE,
)
SIMPLE_HOUR_RE = re.compile(
    r"\b(?:в|к)\s+(?P<hour>[01]?\d|2[0-3])(?:\s*(?:ч|час(?:а|ов)?))?\b",
    re.IGNORECASE,
)


def _extract_time(text: str) -> tuple[int, int] | None:
    """Извлечь время из разговорных форм: 19:00, 19.00, 19 00, в 19."""
    match = TIME_RE.search(text)
    if match:
        minute = match.group("minute") or match.group("space_minute") or "0"
        return int(match.group("hour")), int(minute)

    match = SIMPLE_HOUR_RE.search(text)
    if match:
        return int(match.group("hour")), 0
    return None


def _parse_datetime(text: str, now: datetime | None = None) -> datetime | None:
    """Распознать дату и время из естественной русской фразы.

    Если указано только время, выбирается сегодня, если оно ещё не прошло,
    иначе завтра. Дни недели всегда трактуются как ближайший будущий день.
    """
    now = now or datetime.now()
    lower = text.lower().replace("ё", "е")
    parsed_time = _extract_time(lower)
    if not parsed_time:
        return None
    hour, minute = parsed_time

    base_date = None
    if re.search(r"\bпосле\s*завтра\b|\bпослезавтра\b", lower):
        base_date = now.date() + timedelta(days=2)
    elif re.search(r"\bзавтра\b|\bзавтро\b", lower):
        base_date = now.date() + timedelta(days=1)
    elif re.search(r"\bсегодня\b", lower):
        base_date = now.date()

    if base_date is None:
        for word, weekday in WEEKDAYS.items():
            if re.search(rf"\b{word}\b", lower):
                days = (weekday - now.weekday()) % 7
                if days == 0:
                    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    days = 7 if candidate <= now else 0
                base_date = now.date() + timedelta(days=days)
                break

    if base_date is None:
        numeric = re.search(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", lower)
        named = re.search(rf"\b\d{{1,2}}\s+(?:{MONTHS_PATTERN})(?:\s+\d{{4}})?\b", lower)
        date_match = numeric or named
        if date_match:
            parsed = dateparser.parse(
                date_match.group(0), languages=["ru"],
                settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": now, "DATE_ORDER": "DMY"},
            )
            if parsed:
                base_date = parsed.date()

    if base_date is None:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    return datetime.combine(base_date, datetime.min.time()).replace(hour=hour, minute=minute)


def _extract_title(text: str) -> str:
    """Удалить служебные слова даты/времени, оставив название события."""
    title = text.strip()
    title = TIME_RE.sub(" ", title)
    title = SIMPLE_HOUR_RE.sub(" ", title)
    title = re.sub(r"\b(?:сегодня|завтра|завтро|послезавтра|после\s*завтра|вчера)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(
        r"\b(?:в|во)?\s*(?:понедельник(?:а)?|вторник(?:а)?|среда|среду|среды|четверг(?:а)?|"
        r"пятница|пятницу|пятницы|суббота|субботу|субботы|воскресенье|воскресенья)\b",
        " ", title, flags=re.IGNORECASE,
    )
    title = re.sub(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", " ", title)
    title = re.sub(rf"\b\d{{1,2}}\s+(?:{MONTHS_PATTERN})(?:\s+\d{{4}})?\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" ,.-")
    title = re.sub(r"^(?:добавь|добавить|создай|создать|поставь|запиши|запланируй)\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:в|на)\s+", "", title, flags=re.IGNORECASE).strip()
    if not title:
        return "Встреча"
    return title[0].upper() + title[1:]


def _build_event(text: str, start: datetime) -> dict:
    """Собрать событие продолжительностью один час."""
    end = start + timedelta(hours=1)
    return {
        "summary": _extract_title(text),
        "start": {"dateTime": start.isoformat(), "timeZone": "Europe/Moscow"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Europe/Moscow"},
    }


def _create_event(user_id: int, event: dict) -> None:
    """Создать событие через Google Calendar API."""
    token_dict = get_google_token(user_id)
    if not token_dict:
        raise PermissionError("GOOGLE_AUTH_REQUIRED")
    credentials = Credentials.from_authorized_user_info(token_dict)
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    service.events().insert(calendarId="primary", body=event).execute()


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Обработать текст как календарную команду."""
    if not update.message or not update.message.text:
        return False
    text = update.message.text.strip()
    if not text:
        return False
    start = _parse_datetime(text)
    if not start:
        return False
    user_id = update.effective_user.id
    try:
        event = _build_event(text, start)
        _create_event(user_id, event)
    except PermissionError:
        await update.message.reply_text("Сначала подключите Google Calendar: /start")
        return True
    except Exception:
        logger.exception("Calendar event creation failed for user %s", user_id)
        await update.message.reply_text("Не удалось добавить событие в Google Calendar. Попробуйте ещё раз.")
        return True
    await update.message.reply_text(f"Событие «{event['summary']}» добавлено: {start.strftime('%d.%m.%Y %H:%M')}")
    return True
