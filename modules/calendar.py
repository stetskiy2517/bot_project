"""Календарный модуль: разбор команд и создание событий в Google Calendar."""

from datetime import datetime, timedelta
import logging
import re

import dateparser
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_category_colors, get_google_token

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
NUMERIC_DATE_RE = re.compile(
    r"\b(?:0?[1-9]|[12]\d|3[01])[./-](?:0?[1-9]|1[0-2])(?:[./-]\d{2,4})?\b"
)
NAMED_DATE_RE = re.compile(
    rf"\b\d{{1,2}}\s+(?:{MONTHS_PATTERN})(?:\s+\d{{4}})?\b",
    re.IGNORECASE,
)
CLOCK_TIME_RE = re.compile(
    r"(?<!\d)(?:(?:в|к)\s*)?(?P<hour>[01]?\d|2[0-3])"
    r"(?:\s*(?::|\.|-)\s*(?P<minute>[0-5]\d)|\s+(?P<space_minute>[0-5]\d))"
    r"(?:\s*(?:ч|час(?:а|ов)?))?(?!\d)",
    re.IGNORECASE,
)
SIMPLE_HOUR_RE = re.compile(
    r"\b(?:в|к)\s+(?P<hour>[01]?\d|2[0-3])(?:\s*(?:ч|час(?:а|ов)?))?\b",
    re.IGNORECASE,
)
DAYPART_HOUR_RE = re.compile(
    r"\b(?:в|к)\s+(?P<hour>\d{1,2})(?:\s*(?::|\.)\s*(?P<minute>[0-5]\d))?\s+"
    r"(?P<part>утра|дня|вечера|ночи)\b",
    re.IGNORECASE,
)
RANGE_RE = re.compile(
    r"\bс\s+(\d{1,2})(?:(?::|\.|-|\s)(\d{2}))?\s+до\s+"
    r"(\d{1,2})(?:(?::|\.|-|\s)(\d{2}))?\b",
    re.IGNORECASE,
)
COMPACT_RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2})(?::|\.|-)([0-5]\d)\s*[-–—]\s*"
    r"(\d{1,2})(?::|\.|-)([0-5]\d)(?!\d)",
    re.IGNORECASE,
)
DATE_LIKE_RE = re.compile(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b")
SPOKEN_CLOCK_RE = re.compile(
    r"\b(?:в|к)\s+(?P<hour>[01]?\d|2[0-3])\s*(?:ч|час(?:а|ов)?)\s+"
    r"(?P<minute>[0-5]?\d)\s*(?:мин|минут\w*)\b",
    re.IGNORECASE,
)

HOUR_WORDS = {
    "один": 1, "час": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20,
}
HOUR_WORDS_GENITIVE = {
    "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4,
    "пятого": 5, "шестого": 6, "седьмого": 7, "восьмого": 8,
    "девятого": 9, "десятого": 10, "одиннадцатого": 11, "двенадцатого": 12,
}
HOUR_WORD_PATTERN = "|".join(sorted((re.escape(word) for word in HOUR_WORDS), key=len, reverse=True))
WORD_HOUR_RE = re.compile(
    rf"\b(?:в|к)\s+(?P<word>{HOUR_WORD_PATTERN})(?:\s+(?P<part>утра|дня|вечера|ночи))?\b",
    re.IGNORECASE,
)
PREFIX_DAYPART_RE = re.compile(
    rf"\b(?P<part>утром|днем|вечером|ночью)\s+(?:в\s+)?(?P<hour>\d{{1,2}}|{HOUR_WORD_PATTERN})\b",
    re.IGNORECASE,
)
COMMON_TEXT_REPLACEMENTS = {
    "сегодя": "сегодня",
    "севодня": "сегодня",
    "завтро": "завтра",
    "послезавтро": "послезавтра",
    "понеделник": "понедельник",
    "вторниик": "вторник",
    "четврг": "четверг",
    "пятнца": "пятница",
    "пятнцу": "пятницу",
    "пятнитца": "пятница",
    "субота": "суббота",
    "суботу": "субботу",
    "воскрсенье": "воскресенье",
}
EVENT_CATEGORIES = {
    "work": {"color_id": "3", "keywords": ("работ", "встреч", "созвон", "совещ", "клиент", "офис", "проект", "презентац", "отчет", "отчёт", "коммерчес", "переговор", "планерк")},
    "health": {"color_id": "6", "keywords": ("врач", "доктор", "невролог", "стоматолог", "клиник", "больниц", "анализ", "мрт", "узи", "массаж", "физиотерап", "здоров", "лекар")},
    "rest": {"color_id": "10", "keywords": ("отдых", "отпуск", "выходн", "кино", "театр", "ресторан", "кафе", "прогул", "сауна", "баня", "спорт", "трениров", "зал", "друз")},
    "travel": {"color_id": "7", "keywords": ("самолет", "самолёт", "рейс", "поезд", "вокзал", "аэропорт", "дорог", "такси", "перелет", "перелёт", "командиров", "отъезд", "прилет", "прилёт")},
    "family": {"color_id": "4", "keywords": ()},
    "personal": {"color_id": "5", "keywords": ("личн", "дом", "покуп", "купить", "куплю", "купи", "магазин", "день рождения", "забрать", "отвезти")},
}
FAMILY_RE = re.compile(
    r"\b(?:семь(?:я|и|е|ю|ей|ям|ями|ях)|семейн\w*|родител\w*|ребен\w*|"
    r"дет(?:и|ей|ям|ьми|ях)|сын\w*|доч\w*|мам\w*|пап\w*)\b",
    re.IGNORECASE,
)


def _normalise(text: str) -> str:
    normal = text.lower().replace("ё", "е")
    for wrong, right in COMMON_TEXT_REPLACEMENTS.items():
        normal = re.sub(rf"\b{re.escape(wrong)}\b", right, normal)
    return normal


def _strip_explicit_dates(text: str) -> str:
    def replace_numeric(match: re.Match) -> str:
        prefix = text[max(0, match.start() - 4):match.start()].lower()
        if re.search(r"(?:в|к|с|до)\s+$", prefix):
            return match.group(0)
        return " "

    without_numeric = NUMERIC_DATE_RE.sub(replace_numeric, text)
    return NAMED_DATE_RE.sub(" ", without_numeric)


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

    compact_range = COMPACT_RANGE_RE.search(lower)
    if compact_range:
        hour, minute = int(compact_range.group(1)), int(compact_range.group(2))
        if 0 <= hour <= 23:
            return hour, minute

    if re.search(r"\bполдень\b", lower):
        return 12, 0
    if re.search(r"\bполночь\b", lower):
        return 0, 0

    spoken = SPOKEN_CLOCK_RE.search(lower)
    if spoken:
        return int(spoken.group("hour")), int(spoken.group("minute"))

    def apply_part(hour: int, part: str | None) -> int | None:
        if not part:
            return hour if 0 <= hour <= 23 else None
        aliases = {"утром": "утра", "днем": "дня", "вечером": "вечера", "ночью": "ночи"}
        normalized_part = aliases.get(part, part)
        if hour > 12:
            return hour if hour <= 23 else None
        return _apply_daypart(hour, normalized_part)

    prefix_part = PREFIX_DAYPART_RE.search(lower)
    if prefix_part:
        raw = prefix_part.group("hour")
        hour = int(raw) if raw.isdigit() else HOUR_WORDS.get(raw)
        if hour is not None:
            resolved = apply_part(hour, prefix_part.group("part"))
            if resolved is not None:
                return resolved, 0

    daypart = DAYPART_HOUR_RE.search(lower)
    if daypart:
        hour = _apply_daypart(int(daypart.group("hour")), daypart.group("part"))
        if hour is not None:
            return hour, int(daypart.group("minute") or 0)

    half = re.search(
        r"\b(?:в\s+)?(?:половин[аеуы]?|пол)\s+(\w+)(?:\s+(утра|дня|вечера|ночи))?\b",
        lower,
    )
    if half and half.group(1) in HOUR_WORDS_GENITIVE:
        base = (HOUR_WORDS_GENITIVE[half.group(1)] - 1) % 12
        if half.group(2):
            base = 12 if base == 0 else base
            base = _apply_daypart(base, half.group(2))
        if base is not None:
            return base, 30

    quarter_to = re.search(
        r"\bбез\s+четверти\s+(\w+)(?:\s+(утра|дня|вечера|ночи))?\b",
        lower,
    )
    if quarter_to:
        target = HOUR_WORDS.get(quarter_to.group(1)) or HOUR_WORDS_GENITIVE.get(quarter_to.group(1))
        if target:
            base = (target - 1) % 12
            if quarter_to.group(2):
                base = 12 if base == 0 else base
                base = _apply_daypart(base, quarter_to.group(2))
            if base is not None:
                return base, 45

    quarter_past = re.search(
        r"\bчетверть\s+(\w+)(?:\s+(утра|дня|вечера|ночи))?\b",
        lower,
    )
    if quarter_past and quarter_past.group(1) in HOUR_WORDS_GENITIVE:
        base = (HOUR_WORDS_GENITIVE[quarter_past.group(1)] - 1) % 12
        if quarter_past.group(2):
            base = 12 if base == 0 else base
            base = _apply_daypart(base, quarter_past.group(2))
        if base is not None:
            return base, 15

    word_hour = WORD_HOUR_RE.search(lower)
    if word_hour:
        hour = HOUR_WORDS[word_hour.group("word")]
        resolved = apply_part(hour, word_hour.group("part"))
        if resolved is not None:
            return resolved, 0

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
    if re.search(r"\bчерез\s+пару\s+час\w*\b", lower):
        return timedelta(hours=2)

    word_amounts = {
        "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
        "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    }
    amount_pattern = "|".join(word_amounts)
    match = re.search(
        rf"\bчерез\s+(?:(?P<amount>\d+|{amount_pattern})\s*)?"
        r"(?P<unit>мин(?:ут\w*)?|ч(?:ас\w*)?|дн\w*|день|дня|недел\w*)\b",
        lower,
    )
    if not match:
        return None
    raw_amount = match.group("amount")
    amount = 1 if not raw_amount else (int(raw_amount) if raw_amount.isdigit() else word_amounts[raw_amount])
    unit = match.group("unit")
    if unit.startswith("мин"):
        return timedelta(minutes=amount)
    if unit == "ч" or unit.startswith("час"):
        return timedelta(hours=amount)
    if unit.startswith("дн") or unit in {"день", "дня"}:
        return timedelta(days=amount)
    if unit.startswith("недел"):
        return timedelta(weeks=amount)
    return None


def _parse_numeric_date(value: str, now: datetime):
    parts = re.split(r"[./-]", value)
    if len(parts) not in {2, 3}:
        return None
    day, month = int(parts[0]), int(parts[1])
    year = now.year
    if len(parts) == 3:
        raw_year = int(parts[2])
        year = raw_year + 2000 if raw_year < 100 else raw_year
    try:
        candidate = datetime(year, month, day).date()
    except ValueError:
        return None
    if len(parts) == 2 and candidate < now.date():
        try:
            candidate = datetime(year + 1, month, day).date()
        except ValueError:
            return None
    return candidate


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
        if not re.search(rf"\b{word}\b", lower):
            continue
        days = (weekday - now.weekday()) % 7
        force_next = bool(re.search(r"\b(?:следующ\w*|след\.?)[^\n]{0,20}" + re.escape(word), lower))
        if force_next:
            days = days or 7
        elif days == 0:
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            days = 7 if candidate <= now else 0
        return now.date() + timedelta(days=days)

    numeric_match = NUMERIC_DATE_RE.search(lower)
    if numeric_match:
        return _parse_numeric_date(numeric_match.group(0), now)
    named_match = NAMED_DATE_RE.search(lower)
    if named_match:
        parsed = dateparser.parse(
            named_match.group(0),
            languages=["ru"],
            settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": now, "DATE_ORDER": "DMY"},
        )
        if parsed:
            return parsed.date()
    return None


def _parse_datetime(text: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now()
    lower = _normalise(text)

    # Явная невозможная дата должна быть ошибкой, а не молчаливым переносом
    # на ближайшее время. При этом диапазоны времени вроде 14:00-16:00
    # не являются датами и исключаются из этой проверки.
    compact_range = COMPACT_RANGE_RE.search(lower)
    for match in DATE_LIKE_RE.finditer(lower):
        if compact_range and compact_range.start() <= match.start() and match.end() <= compact_range.end():
            continue
        token = match.group(0)
        parts = re.split(r"[./-]", token)
        day, month = int(parts[0]), int(parts[1])
        has_year = len(parts) == 3
        delimiter = next((char for char in token if char in "./-"), ".")
        prefix = lower[max(0, match.start() - 4):match.start()]
        looks_like_time = bool(re.search(r"(?:в|к|с|до)\s+$", prefix)) and not has_year and day <= 23
        looks_like_date = has_year or day > 23 or delimiter in "/-" or month <= 12
        if looks_like_date and not looks_like_time and _parse_numeric_date(token, now) is None:
            return None

    parsed_time = _extract_time(text)
    relative = _relative_offset(text)
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
    match = re.search(
        r"\bна\s+(\d+(?:[.,]\d+)?)\s*(мин(?:ут\w*)?|ч(?:ас\w*)?)\b",
        lower,
    )
    if match:
        amount = float(match.group(1).replace(",", "."))
        unit = match.group(2)
        if unit.startswith("мин"):
            return timedelta(minutes=amount)
        return timedelta(hours=amount)
    return DEFAULT_EVENT_DURATION


def _extract_range_end(text: str, start: datetime) -> datetime | None:
    lower = _normalise(_strip_explicit_dates(text))
    match = RANGE_RE.search(lower)
    if match:
        end_hour, end_minute = int(match.group(3)), int(match.group(4) or 0)
    else:
        compact = COMPACT_RANGE_RE.search(lower)
        if not compact:
            return None
        end_hour, end_minute = int(compact.group(3)), int(compact.group(4))
    if not (0 <= end_hour <= 23 and 0 <= end_minute <= 59):
        return None
    end = start.replace(hour=end_hour, minute=end_minute)
    return end + timedelta(days=1) if end <= start else end


def _parse_event_timing(text: str, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    start = _parse_datetime(text, now)
    if not start:
        return None
    return start, (_extract_range_end(text, start) or start + _extract_duration(text))


def _detect_category(text: str, category_colors: dict[str, str | None] | None = None) -> tuple[str, str | None]:
    lower = _normalise(text)
    colors = category_colors or {name: config["color_id"] for name, config in EVENT_CATEGORIES.items()}
    if FAMILY_RE.search(lower):
        return "family", colors.get("family")
    for category, config in EVENT_CATEGORIES.items():
        if any(keyword in lower for keyword in config["keywords"]):
            return category, colors.get(category)
    return "other", colors.get("other")


def _extract_title(text: str) -> str:
    title = NAMED_DATE_RE.sub(" ", NUMERIC_DATE_RE.sub(" ", text.strip()))
    title = RANGE_RE.sub(" ", title)
    title = COMPACT_RANGE_RE.sub(" ", title)
    title = SPOKEN_CLOCK_RE.sub(" ", title)
    title = PREFIX_DAYPART_RE.sub(" ", title)
    title = DAYPART_HOUR_RE.sub(" ", title)
    title = WORD_HOUR_RE.sub(" ", title)
    title = CLOCK_TIME_RE.sub(" ", title)
    title = SIMPLE_HOUR_RE.sub(" ", title)
    title = re.sub(r"\b(?:в\s+)?(?:полдень|полночь)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(
        r"\b(?:в\s+)?(?:половин[аеуы]?|пол)\s+\w+(?:\s+(?:утра|дня|вечера|ночи))?\b",
        " ", title, flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\bбез\s+четверти\s+\w+(?:\s+(?:утра|дня|вечера|ночи))?\b|"
        r"\bчетверть\s+\w+(?:\s+(?:утра|дня|вечера|ночи))?\b",
        " ", title, flags=re.IGNORECASE,
    )
    title = re.sub(r"\b(?:сегодня|завтра|завтро|послезавтра|после\s*завтра|вчера)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(
        r"\bчерез\s+(?:полчаса|полтора\s+часа|пару\s+час\w*|"
        r"(?:\d+|один|одну|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять)\s*"
        r"(?:мин(?:ут\w*)?|ч(?:ас\w*)?|дн\w*|день|дня|недел\w*))\b",
        " ", title, flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\b(?:на\s+)?следующ\w*\s+(?:понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)\b",
        " ", title, flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\b(?:в|во)?\s*(?:понедельник(?:а)?|вторник(?:а)?|среда|среду|среды|четверг(?:а)?|"
        r"пятница|пятницу|пятницы|суббота|субботу|субботы|воскресенье|воскресенья)\b",
        " ", title, flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\bна\s+(?:полчаса|полтора\s+часа|час|\d+(?:[.,]\d+)?\s*(?:мин(?:ут\w*)?|ч(?:ас\w*)?))\b",
        " ", title, flags=re.IGNORECASE,
    )
    title = re.sub(r"\s+", " ", title).strip(" ,.-")
    title = re.sub(r"^(?:(?:пожалуйста|плиз)\s*,?\s*)+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:можешь|можно|давай)\s+(?:мне\s+)?", "", title, flags=re.IGNORECASE)
    title = re.sub(
        r"^(?:добавь|добавить|создай|создать|поставь|поставить|запиши|записать|"
        r"запланируй|запланировать|назначь|назначить|внеси|напомни|напомнить)\s+",
        "", title, flags=re.IGNORECASE,
    )
    title = re.sub(r"^(?:пожалуйста|плиз)\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:в|на)\s+", "", title, flags=re.IGNORECASE).strip()
    return title[0].upper() + title[1:] if title else "Встреча"


def _build_event(text: str, start: datetime, end: datetime | None = None, category_colors: dict[str, str | None] | None = None) -> dict:
    end = end or start + _extract_duration(text)
    category, color_id = _detect_category(text, category_colors)
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
    insert_kwargs = {"calendarId": "primary", "body": event}
    if event.get("attendees"):
        insert_kwargs["sendUpdates"] = "all"
    service.events().insert(**insert_kwargs).execute()


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
        event = _build_event(text, start, end, get_category_colors(user_id))
        _create_event(user_id, event)
    except PermissionError:
        await update.message.reply_text("Сначала подключите Google Calendar: /start")
        return True
    except Exception:
        logger.exception("Calendar event creation failed for user %s", user_id)
        await update.message.reply_text("Не удалось добавить событие в Google Calendar. Попробуйте ещё раз.")
        return True
    await update.message.reply_text(
        f"Событие «{event['summary']}» добавлено: {start.strftime('%d.%m.%Y %H:%M')}–{end.strftime('%H:%M')}"
    )
    return True
