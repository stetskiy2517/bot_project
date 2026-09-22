"""Дополнительные свойства событий Google Calendar."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone as dt_timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import dateparser

from modules.calendar import MONTHS_PATTERN, _date_from_text, _extract_time, _extract_title, _resolve_category
from modules.calendar_user import _user_zone

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
ALL_DAY_RE = re.compile(r"\b(?:весь\s+день|на\s+весь\s+день|целый\s+день)\b", re.IGNORECASE)
BIRTHDAY_RE = re.compile(r"\bдень\s+рождени[яе]\b", re.IGNORECASE)
SPAN_EVENT_RE = re.compile(r"\b(?:отпуск|командировк\w*)\b", re.IGNORECASE)
NEXT_WEEK_RE = re.compile(r"\b(?:на\s+)?следующ\w*\s+недел\w*\b", re.IGNORECASE)
CROSS_MONTH_RANGE_RE = re.compile(
    rf"\bс\s+(?P<start_day>\d{{1,2}})\s+(?P<start_month>(?:{MONTHS_PATTERN}))\s+"
    rf"по\s+(?P<end_day>\d{{1,2}})\s+(?P<end_month>(?:{MONTHS_PATTERN}))\b",
    re.IGNORECASE,
)
SAME_MONTH_RANGE_RE = re.compile(
    rf"\bс\s+(?P<start_day>\d{{1,2}})\s+по\s+(?P<end_day>\d{{1,2}})\s+"
    rf"(?P<month>(?:{MONTHS_PATTERN}))\b",
    re.IGNORECASE,
)
SAME_MONTH_DASH_RANGE_RE = re.compile(
    rf"\b(?P<start_day>\d{{1,2}})\s*[-–—]\s*(?P<end_day>\d{{1,2}})\s+"
    rf"(?P<month>(?:{MONTHS_PATTERN}))\b",
    re.IGNORECASE,
)
LOCATION_RE = re.compile(
    r"\b(?:по\s+адресу|место\s*[:\-]|локация\s*[:\-])\s*(.+?)(?=$|\s+(?:напомни|пригласи|участники|кажд|весь\s+день|приоритет))",
    re.IGNORECASE,
)
REMINDER_RE = re.compile(
    r"\bза\s+(?:(?P<amount>\d+)\s*(?P<unit>мин(?:ут\w*)?|ч(?:ас\w*)?|день|дня|дн\w*|недел\w*)|"
    r"(?P<half>полчаса)|(?P<hour>час)|(?P<day>день)|(?P<day_alias>сутки|суток)|(?P<week>недел\w*))\b",
    re.IGNORECASE,
)
RECURRENCE_UNTIL_RE = re.compile(
    rf"\bдо\s+(?P<date>(?:\d{{1,2}}(?:-?го)?\s+(?:{MONTHS_PATTERN})(?:\s+\d{{4}})?)|(?:\d{{1,2}}[./-]\d{{1,2}}(?:[./-]\d{{2,4}})?))\b",
    re.IGNORECASE,
)
HIGH_PRIORITY_RE = re.compile(r"\b(?:высок\w*\s+приоритет\w*|приоритет\w*\s+высок\w*|срочн\w*|важн\w*)\b", re.IGNORECASE)
LOW_PRIORITY_RE = re.compile(r"\b(?:низк\w*\s+приоритет\w*|приоритет\w*\s+низк\w*|не\s+срочн\w*)\b", re.IGNORECASE)
NORMAL_PRIORITY_RE = re.compile(r"\b(?:(?:обычн|средн|нормальн)\w*\s+приоритет\w*|приоритет\w*\s+(?:обычн|средн|нормальн)\w*)\b", re.IGNORECASE)
WEEKDAY_BY_RE = {
    "понедельник": "MO", "понедельникам": "MO",
    "вторник": "TU", "вторникам": "TU",
    "среду": "WE", "средам": "WE",
    "четверг": "TH", "четвергам": "TH",
    "пятницу": "FR", "пятницам": "FR",
    "субботу": "SA", "субботам": "SA",
    "воскресенье": "SU", "воскресеньям": "SU",
}
RECURRENCE_INTERVAL_WORDS = {"два": 2, "две": 2, "три": 3, "четыре": 4}


def is_all_day(text: str) -> bool:
    """Явный all-day, день рождения без времени или многодневный тип без часов."""
    if ALL_DAY_RE.search(text):
        return True
    if BIRTHDAY_RE.search(text):
        return _extract_time(text) is None
    if SPAN_EVENT_RE.search(text):
        return _extract_time(text) is None
    return False


def build_all_day_event(
    text: str,
    timezone: str,
    now: datetime | None = None,
    category_colors: dict[str, str | None] | None = None,
    *,
    user_id: int | None = None,
) -> dict | None:
    zone = _user_zone(timezone)
    local_now = now.astimezone(zone) if now and now.tzinfo else (now.replace(tzinfo=zone) if now else datetime.now(zone))
    naive_now = local_now.replace(tzinfo=None)

    def parse_fragment(value: str):
        parsed = dateparser.parse(
            value,
            languages=["ru"],
            settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": naive_now, "DATE_ORDER": "DMY"},
        )
        return parsed.date() if parsed else None

    start_date = None
    end_date = None
    if NEXT_WEEK_RE.search(text):
        days_to_next_monday = 7 - local_now.weekday()
        start_date = local_now.date() + timedelta(days=days_to_next_monday)
        end_date = start_date + timedelta(days=7)
    else:
        cross = CROSS_MONTH_RANGE_RE.search(text)
        same = SAME_MONTH_RANGE_RE.search(text)
        dash = SAME_MONTH_DASH_RANGE_RE.search(text)
        if cross:
            start_date = parse_fragment(f"{cross.group('start_day')} {cross.group('start_month')}")
            end_inclusive = parse_fragment(f"{cross.group('end_day')} {cross.group('end_month')}")
            if start_date and end_inclusive and end_inclusive < start_date:
                try:
                    end_inclusive = end_inclusive.replace(year=end_inclusive.year + 1)
                except ValueError:
                    return None
            end_date = end_inclusive + timedelta(days=1) if end_inclusive else None
        elif same or dash:
            match = same or dash
            start_date = parse_fragment(f"{match.group('start_day')} {match.group('month')}")
            end_inclusive = parse_fragment(f"{match.group('end_day')} {match.group('month')}")
            if start_date and end_inclusive and end_inclusive < start_date:
                return None
            end_date = end_inclusive + timedelta(days=1) if end_inclusive else None
        else:
            start_date = _date_from_text(text, naive_now, 12, 0)
            end_date = start_date + timedelta(days=1) if start_date else None

    if not start_date or not end_date:
        return None
    category, color_id = _resolve_category(text, category_colors, user_id=user_id)
    event = {
        "summary": _clean_title(text),
        "description": f"AI Smart Planner category: {category}",
        "start": {"date": start_date.isoformat()},
        "end": {"date": end_date.isoformat()},
    }
    if color_id:
        event["colorId"] = color_id
    return apply_event_features(event, text)


def _reminder_minutes(text: str) -> list[int]:
    values: list[int] = []
    for match in REMINDER_RE.finditer(text):
        if match.group("half"):
            minutes = 30
        elif match.group("hour"):
            minutes = 60
        elif match.group("day") or match.group("day_alias"):
            minutes = 1440
        elif match.group("week"):
            minutes = 10080
        else:
            amount = int(match.group("amount"))
            unit = match.group("unit").lower()
            if unit.startswith("мин"):
                minutes = amount
            elif unit == "ч" or unit.startswith("час"):
                minutes = amount * 60
            elif unit.startswith("недел"):
                minutes = amount * 10080
            else:
                minutes = amount * 1440
        if 0 <= minutes <= 40320 and minutes not in values:
            values.append(minutes)
    return sorted(values, reverse=True)[:5]


def _recurrence_rule(text: str) -> str | None:
    lower = text.lower().replace("ё", "е")
    if re.search(r"\b(?:по\s+будням|кажд\w*\s+будн\w*\s+день)\b", lower):
        return "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    if re.search(r"\b(?:кажд\w*\s+выходн\w*|по\s+выходным)\b", lower):
        return "RRULE:FREQ=WEEKLY;BYDAY=SA,SU"
    if re.search(r"\b(?:ежегодно|кажд\w*\s+год)\b", lower):
        return "RRULE:FREQ=YEARLY"
    if re.search(r"\bкажд(?:ый|ую|ое)\s+день\b|\bежедневно\b", lower):
        return "RRULE:FREQ=DAILY"

    interval = re.search(
        r"\b(?:кажд\w*|раз\s+в)\s+(?P<amount>\d+|два|две|три|четыре)\s+недел\w*\b",
        lower,
    )
    if interval:
        raw_amount = interval.group("amount")
        amount = int(raw_amount) if raw_amount.isdigit() else RECURRENCE_INTERVAL_WORDS[raw_amount]
        rule = f"RRULE:FREQ=WEEKLY;INTERVAL={amount}"
        for word, code in WEEKDAY_BY_RE.items():
            if re.search(rf"\b(?:в\s+|по\s+)?{word}\b", lower):
                return rule + f";BYDAY={code}"
        return rule

    for word, code in WEEKDAY_BY_RE.items():
        if re.search(rf"\bкажд\w*\s+{word}\b", lower):
            return f"RRULE:FREQ=WEEKLY;BYDAY={code}"
        if re.search(rf"\bпо\s+{word}\b", lower):
            return f"RRULE:FREQ=WEEKLY;BYDAY={code}"

    if re.search(r"\bкажд(?:ую|ой)\s+недел\w*\b|\bеженедельно\b|\bраз\s+в\s+недел\w*\b", lower):
        return "RRULE:FREQ=WEEKLY"
    if re.search(r"\bкажд(?:ый|ого)\s+месяц\w*\b|\bежемесячно\b", lower):
        return "RRULE:FREQ=MONTHLY"
    return None


def _priority_value(text: str) -> str | None:
    if LOW_PRIORITY_RE.search(text):
        return "low"
    if HIGH_PRIORITY_RE.search(text):
        return "high"
    if NORMAL_PRIORITY_RE.search(text):
        return "normal"
    return None


def _extract_location(text: str) -> str | None:
    match = LOCATION_RE.search(text)
    if not match:
        return None
    location = re.sub(r"\s+", " ", match.group(1)).strip(" ,.;")
    return location[:500] if location else None


def _extract_attendees(text: str) -> list[dict]:
    emails = []
    for email in EMAIL_RE.findall(text):
        normalized = email.lower()
        if normalized not in emails:
            emails.append(normalized)
    return [{"email": email} for email in emails[:20]]


def _recurrence_until_date(text: str, relative_base: datetime | None = None):
    match = RECURRENCE_UNTIL_RE.search(text)
    if not match:
        return None
    value = re.sub(r"(\d{1,2})-?го\b", r"\1", match.group("date"), flags=re.IGNORECASE)
    parsed = dateparser.parse(
        value,
        languages=["ru"],
        settings={
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": (relative_base or datetime.now()).replace(tzinfo=None),
            "DATE_ORDER": "DMY",
        },
    )
    return parsed.date() if parsed else None


def _append_until(rule: str, text: str, event: dict) -> str:
    start = event.get("start") or {}
    base = None
    if start.get("dateTime"):
        try:
            base = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        except ValueError:
            base = None
    until_date = _recurrence_until_date(text, base)
    if not until_date:
        return rule
    if start.get("date"):
        return rule + f";UNTIL={until_date.strftime('%Y%m%d')}"

    timezone_name = start.get("timeZone")
    try:
        zone = ZoneInfo(timezone_name) if timezone_name else (base.tzinfo if base and base.tzinfo else dt_timezone.utc)
    except ZoneInfoNotFoundError:
        zone = base.tzinfo if base and base.tzinfo else dt_timezone.utc
    local_until = datetime.combine(until_date, time(23, 59, 59), tzinfo=zone)
    utc_until = local_until.astimezone(dt_timezone.utc)
    return rule + f";UNTIL={utc_until.strftime('%Y%m%dT%H%M%SZ')}"


def _clean_title(text: str) -> str:
    cleaned = text
    cleaned = ALL_DAY_RE.sub(" ", cleaned)
    cleaned = LOCATION_RE.sub(" ", cleaned)
    cleaned = CROSS_MONTH_RANGE_RE.sub(" ", cleaned)
    cleaned = SAME_MONTH_RANGE_RE.sub(" ", cleaned)
    cleaned = SAME_MONTH_DASH_RANGE_RE.sub(" ", cleaned)
    cleaned = NEXT_WEEK_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\b(?:напомни|напоминание)\s*(?:мне\s*)?", " ", cleaned, flags=re.IGNORECASE)
    cleaned = REMINDER_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\b(?:пригласи|участники\s*[:\-]?)\s*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = EMAIL_RE.sub(" ", cleaned)
    cleaned = re.sub(
        r"\b(?:кажд\w*|раз\s+в)\s+(?:\d+|два|две|три|четыре)\s+недел\w*\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bкажд\w*\s+(?:день|недел\w*|месяц\w*|понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bпо\s+(?:понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bраз\s+в\s+недел\w*\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:по\s+будням|по\s+выходным|кажд\w*\s+выходн\w*|ежегодно|кажд\w*\s+год)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:ежедневно|еженедельно|ежемесячно)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = RECURRENCE_UNTIL_RE.sub(" ", cleaned)
    cleaned = HIGH_PRIORITY_RE.sub(" ", cleaned)
    cleaned = LOW_PRIORITY_RE.sub(" ", cleaned)
    cleaned = NORMAL_PRIORITY_RE.sub(" ", cleaned)
    cleaned = re.sub(r"(?:^|\s)(?:и|а)(?=\s*$)", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
    return _extract_title(cleaned)


def apply_event_features(event: dict, text: str) -> dict:
    """Добавить напоминания, повторение, место, участников, приоритет и очистить title."""
    enriched = dict(event)
    enriched["summary"] = _clean_title(text)

    reminders = _reminder_minutes(text)
    if reminders:
        enriched["reminders"] = {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": minutes} for minutes in reminders],
        }

    recurrence = _recurrence_rule(text)
    if recurrence:
        enriched["recurrence"] = [_append_until(recurrence, text, enriched)]

    location = _extract_location(text)
    if location:
        enriched["location"] = location

    attendees = _extract_attendees(text)
    if attendees:
        enriched["attendees"] = attendees

    priority = _priority_value(text)
    if priority:
        extended = dict(enriched.get("extendedProperties") or {})
        private = dict(extended.get("private") or {})
        private["smartPlannerPriority"] = priority
        extended["private"] = private
        enriched["extendedProperties"] = extended

    return enriched