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

CALENDAR_TIMEZONE = "Europe/Moscow"
DEFAULT_EVENT_DURATION = timedelta(hours=1)

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
NUMERIC_DATE_RE = re.compile(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b")
NAMED_DATE_RE = re.compile(rf"\b\d{{1,2}}\s+(?:{MONTHS_PATTERN})(?:\s+\d{{4}})?\b", re.IGNORECASE)
CLOCK_TIME_RE = re.compile(
    r"(?<!\d)(?:(?:в|к)\s*)?(?P<hour>[01]?\d|2[0-3])"
    r"(?:\s*(?::|\.)\s*(?P<minute>[0-5]\d)|\s+(?P<space_minute>[0-5]\d))"
    r"(?:\s*(?:ч|час(?:а|ов)?))?(?!\d)", re.IGNORECASE,
)
SIMPLE_HOUR_RE = re.compile(r"\b(?:в|к)\s+(?P<hour>[01]?\d|2[0-3])(?:\s*(?:ч|час(?:а|ов)?))?\b", re.IGNORECASE)
DAYPART_HOUR_RE = re.compile(
    r"\b(?:в|к)\s+(?P<hour>\d{1,2})(?:\s*(?::|\.)\s*(?P<minute>[0-5]\d))?\s+(?P<part>утра|дня|вечера|ночи)\b",
    re.IGNORECASE,
)
RANGE_RE = re.compile(
    r"\bс\s+(\d{1,2})(?:(?::|\.|\s)(\d{2}))?\s+до\s+(\d{1,2})(?:(?::|\.|\s)(\d{2}))?\b",
    re.IGNORECASE,
)
HOUR_WORDS = {
    "один": 1, "час": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
}
HOUR_WORDS_GENITIVE = {
    "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "пятого": 5, "шестого": 6,
    "седьмого": 7, "восьмого": 8, "девятого": 9, "десятого": 10, "одиннадцатого": 11, "двенадцатого": 12,
}
EVENT_CATEGORIES = {
    "work": {"color_id": "3", "keywords": ("работ", "встреч", "созвон", "совещ", "клиент", "офис", "проект", "презентац", "отчет", "отчёт", "коммерчес", "переговор", "планерк")},
    "health": {"color_id": "6", "keywords": ("врач", "доктор", "невролог", "стоматолог", "клиник", "больниц", "анализ", "мрт", "узи", "массаж", "физиотерап", "здоров", "лекар")},
    "rest": {"color_id": "10", "keywords": ("отдых", "выходн", "кино", "театр", "ресторан", "кафе", "прогул", "сауна", "баня", "спорт", "трениров", "зал", "семь", "друз")},
    "travel": {"color_id": "7", "keywords": ("самолет", "самолёт", "рейс", "поезд", "вокзал", "аэропорт", "дорог", "такси", "перелет", "перелёт", "командиров", "отъезд", "прилет", "прилёт")},
    "personal": {"color_id": "5", "keywords": ("личн", "дом", "покуп", "магазин", "семья", "родител", "ребен", "ребён", "день рождения", "забрать", "отвезти")},
}


def _normalise(text: str) -> str:
    return text.lower().replace("ё", "е")


def _strip_explicit_dates(text: str) -> str:
    return NAMED_DATE_RE.sub(" ", NUMERIC_DATE_RE.sub(" ", text))


def _apply_daypart(hour: int, part: str) -> int | None:
    if not 1 <= hour <= 12:
        return None
    if part in {"вечера", "дня"} and hour < 12:
        return hour + 12
    if part in {"ночи", "утра"} and hour == 12:
        return 0
    return hour


def _extract_time(text: str) -> tuple[int, int] | None:
    lower = _normalise(_strip_explicit_dates(text))
    range_match = RANGE_RE.search(lower)
    if range_match:
        hour, minute = int(range_match.group(1)), int(range_match.group(2) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    if re.search(r"\bполдень\b", lower):
        return 12, 0
    if re.search(r"\bполночь\b", lower):
        return 0, 0
    daypart = DAYPART_HOUR_RE.search(lower)
    if daypart:
        hour = _apply_daypart(int(daypart.group("hour")), daypart.group("part"))
        if hour is not None:
            return hour, int(daypart.group("minute") or 0)
    half = re.search(r"\b(?:в\s+)?половин[аеуы]?\s+(\w+)\b", lower)
    if half and half.group(1) in HOUR_WORDS_GENITIVE:
        return (HOUR_WORDS_GENITIVE[half.group(1)] - 1) % 12, 30
    quarter_to = re.search(r"\bбез\s+четверти\s+(\w+)\b", lower)
    if quarter_to:
        target = HOUR_WORDS.get(quarter_to.group(1)) or HOUR_WORDS_GENITIVE.get(quarter_to.group(1))
        if target:
            return (target - 1) % 12, 45
    quarter_past = re.search(r"\bчетверть\s+(\w+)\b", lower)
    if quarter_past and quarter_past.group(1) in HOUR_WORDS_GENITIVE:
        return (HOUR_WORDS_GENITIVE[quarter_past.group(1)] - 1) % 12, 15
    matches = list(CLOCK_TIME_RE.finditer(lower))
    if matches:
        match = matches[-1]
        return int(match.group("hour")), int(match.group("minute") or match.group("space_minute") or 0)
    match = SIMPLE_HOUR_RE.search(lower)
    return (int(match.group("hour")), 0) if match else None


def _relative_offset(text: str) -> timedelta | None:
    lower = _normalise(text)
    if re.search(r"\bчерез\s+полчаса\b", lower):
        return timedelta(minutes=30)
    if re.search(r"\bчерез\s+полтора\s+часа\b", lower):
        return timedelta(minutes=90)
    match = re.search(r"\bчерез\s+(?:(\d+)\s+)?(минут\w*|час\w*|дн\w*|день|дня|недел\w*)\b", lower)
    if not match:
        return None
    amount, unit = int(match.group(1) or 1), match.group(2)
    if unit.startswith("минут"):
        return timedelta(minutes=amount)
    if unit.startswith("час"):
        return timedelta(hours=amount)
    if unit.startswith("дн") or unit in {"день", "дня"}:
        return timedelta(days=amount)
    return timedelta(weeks=amount) if unit.startswith("недел") else None


def _date_from_text(text: str, now: datetime, hour: int, minute: int):
    lower = _normalise(text)
    if re.search(r"\bпосле\s*завтра\b|\bпослезавтра\b", lower):
        return now.date() + timedelta(days=2)
    if re.search(r"\bзавтра\b|\bзавтро\b", lower):
        return now.date() + timedelta(days=1)
    if re.search(r"\bсегодня\b", lower):
        return now.date()
    relative = _relative_offset(lower)
    if relative and relative >= timedelta(days=1):
        return (now + relative).date()
    for word, weekday in WEEKDAYS.items():
        if re.search(rf"\b{word}\b", lower):
            days = (weekday - now.weekday()) % 7
            force_next = bool(re.search(r"\b(?:следующ\w*|след\.?)[^\n]{0,20}" + re.escape(word), lower))
            if force_next:
                days = days + 7 if days else 7
            elif days == 0:
                candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                days = 7 if candidate <= now else 0
            return now.date() + timedelta(days=days)
    date_match = NUMERIC_DATE_RE.search(lower) or NAMED_DATE_RE.search(lower)
    if date_match:
        parsed = dateparser.parse(date_match.group(0), languages=["ru"], settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": now, "DATE_ORDER": "DMY"})
        if parsed:
            return parsed.date()
    return None


def _parse_datetime(text: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now()
    parsed_time, relative = _extract_time(text), _relative_offset(text)
    if relative and relative < timedelta(days=1) and not parsed_time:
        return (now + relative).replace(second=0, microsecond=0)
    if not parsed_time:
        return None
    hour, minute = parsed_time
    base_date = _date_from_text(text, now, hour, minute)
    if base_date is None:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate + timedelta(days=1) if candidate <= now else candidate
    return datetime.combine(base_date, datetime.min.time()).replace(hour=hour, minute=minute)


def _extract_duration(text: str) -> timedelta:
    lower = _normalise(text)
    if re.search(r"\bна\s+полчаса\b", lower):
        return timedelta(minutes=30)
    if re.search(r"\bна\s+полтора\s+часа\b", lower):
        return timedelta(minutes=90)
    if re.search(r"\bна\s+час\b", lower):
        return timedelta(hours=1)
    match = re.search(r"\bна\s+(\d+)\s*(минут\w*|час\w*)\b", lower)
    if match:
        amount = int(match.group(1))
        return timedelta(minutes=amount) if match.group(2).startswith("минут") else timedelta(hours=amount)
    return DEFAULT_EVENT_DURATION


def _extract_range_end(text: str, start: datetime) -> datetime | None:
    match = RANGE_RE.search(_normalise(_strip_explicit_dates(text)))
    if not match:
        return None
    end_hour, end_minute = int(match.group(3)), int(match.group(4) or 0)
    if not (0 <= end_hour <= 23 and 0 <= end_minute <= 59):
        return None
    end = start.replace(hour=end_hour, minute=end_minute)
    return end + timedelta(days=1) if end <= start else end


def _parse_event_timing(text: str, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    start = _parse_datetime(text, now)
    if not start:
        return None
    return start, (_extract_range_end(text, start) or start + _extract_duration(text))


def _detect_category(text: str) -> tuple[str, str | None]:
    lower = _normalise(text)
    for category, config in EVENT_CATEGORIES.items():
        if any(keyword in lower for keyword in config["keywords"]):
            return category, config["color_id"]
    return "other", None


def _extract_title(text: str) -> str:
    title = text.strip()
    title = NAMED_DATE_RE.sub(" ", NUMERIC_DATE_RE.sub(" ", title))
    title = RANGE_RE.sub(" ", title)
    title = DAYPART_HOUR_RE.sub(" ", title)
    title = CLOCK_TIME_RE.sub(" ", title)
    title = SIMPLE_HOUR_RE.sub(" ", title)
    title = re.sub(r"\b(?:в\s+)?(?:полдень|полночь)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:в\s+)?половин[аеуы]?\s+\w+\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bбез\s+четверти\s+\w+\b|\bчетверть\s+\w+\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:сегодня|завтра|завтро|послезавтра|после\s*завтра|вчера)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bчерез\s+(?:полчаса|полтора\s+часа|(?:\d+\s+)?(?:минут\w*|час\w*|дн\w*|день|дня|недел\w*))\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:на\s+)?следующ\w*\s+(?:понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\b(?:в|во)?\s*(?:понедельник(?:а)?|вторник(?:а)?|среда|среду|среды|четверг(?:а)?|пятница|пятницу|пятницы|суббота|субботу|субботы|воскресенье|воскресенья)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\bна\s+(?:полчаса|полтора\s+часа|час|\d+\s*(?:минут\w*|час\w*))\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" ,.-")
    title = re.sub(r"^(?:добавь|добавить|создай|создать|поставь|запиши|запланируй|назначь)\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:в|на)\s+", "", title, flags=re.IGNORECASE).strip()
    return title[0].upper() + title[1:] if title else "Встреча"


def _build_event(text: str, start: datetime, end: datetime | None = None) -> dict:
    end = end or (start + _extract_duration(text))
    category, color_id = _detect_category(text)
    event = {
        "summary": _extract_title(text),
        "description": f"AI Smart Planner category: {category}",
        "start": {"dateTime": start.isoformat(), "timeZone": CALENDAR_TIMEZONE},
        "end": {"dateTime": end.isoformat(), "timeZone": CALENDAR_TIMEZONE},
    }
    if color_id:
        event["colorId"] = color_id
    return event


def _create_event(user_id: int, event: dict) -> None:
    token_dict = get_google_token(user_id)
    if not token_dict:
        raise PermissionError("GOOGLE_AUTH_REQUIRED")
    credentials = Credentials.from_authorized_user_info(token_dict)
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    service.events().insert(calendarId="primary", body=event).execute()


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.message or not update.message.text:
        return False
    text = update.message.text.strip()
    if not text:
        return False
    timing = _parse_event_timing(text)
    if not timing:
        return False
    start, end = timing
    user_id = update.effective_user.id
    try:
        event = _build_event(text, start, end)
        _create_event(user_id, event)
    except PermissionError:
        await update.message.reply_text("Сначала подключите Google Calendar: /start")
        return True
    except Exception:
        logger.exception("Calendar event creation failed for user %s", user_id)
        await update.message.reply_text("Не удалось добавить событие в Google Calendar. Попробуйте ещё раз.")
        return True
    await update.message.reply_text(f"Событие «{event['summary']}» добавлено: {start.strftime('%d.%m.%Y %H:%M')}–{end.strftime('%H:%M')}")
    return True
