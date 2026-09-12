"""Дополнительные свойства событий Google Calendar."""

from __future__ import annotations

from datetime import datetime, timedelta
import re

import dateparser

from modules.calendar import MONTHS_PATTERN, _date_from_text, _detect_category, _extract_time, _extract_title
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
LOCATION_RE = re.compile(
    r"\b(?:по\s+адресу|место\s*[:\-]|локация\s*[:\-])\s*(.+?)(?=$|\s+(?:напомни|пригласи|участники|кажд|весь\s+день))",
    re.IGNORECASE,
)
REMINDER_RE = re.compile(
    r"\bза\s+(?:(?P<amount>\d+)\s*(?P<unit>мин(?:ут\w*)?|ч(?:ас\w*)?|дн\w*)|"
    r"(?P<half>полчаса)|(?P<hour>час)|(?P<day>день)|(?P<day_alias>сутки|суток))\b",
    re.IGNORECASE,
)
WEEKDAY_BY_RE = {
    "понедельник": "MO", "понедельникам": "MO",
    "вторник": "TU", "вторникам": "TU",
    "среду": "WE", "средам": "WE",
    "четверг": "TH", "четвергам": "TH",
    "пятницу": "FR", "пятницам": "FR",
    "субботу": "SA", "субботам": "SA",
    "воскресенье": "SU", "воскресеньям": "SU",
}


def is_all_day(text: str) -> bool:
    """Явный all-day, день рождения без времени или многодневный тип без часов."""
    if ALL_DAY_RE.search(text):
        return True
    if BIRTHDAY_RE.search(text):
        return _extract_time(text) is None
    if SPAN_EVENT_RE.search(text):
        return _extract_time(text) is None
    return False


def build_all_day_event(text: str, timezone: str, now: datetime | None = None, category_colors: dict[str, str | None] | None = None) -> dict | None:
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
        if cross:
            start_date = parse_fragment(f"{cross.group('start_day')} {cross.group('start_month')}")
            end_inclusive = parse_fragment(f"{cross.group('end_day')} {cross.group('end_month')}")
            if start_date and end_inclusive and end_inclusive < start_date:
                try:
                    end_inclusive = end_inclusive.replace(year=end_inclusive.year + 1)
                except ValueError:
                    return None
            end_date = end_inclusive + timedelta(days=1) if end_inclusive else None
        elif same:
            start_date = parse_fragment(f"{same.group('start_day')} {same.group('month')}")
            end_inclusive = parse_fragment(f"{same.group('end_day')} {same.group('month')}")
            if start_date and end_inclusive and end_inclusive < start_date:
                return None
            end_date = end_inclusive + timedelta(days=1) if end_inclusive else None
        else:
            start_date = _date_from_text(text, naive_now, 12, 0)
            end_date = start_date + timedelta(days=1) if start_date else None

    if not start_date or not end_date:
        return None
    category, color_id = _detect_category(text, category_colors)
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
        else:
            amount = int(match.group("amount"))
            unit = match.group("unit").lower()
            if unit.startswith("мин"):
                minutes = amount
            elif unit == "ч" or unit.startswith("час"):
                minutes = amount * 60
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

    interval = re.search(r"\bкажд\w*\s+(\d+)\s+недел\w*\b", lower)
    if interval:
        rule = f"RRULE:FREQ=WEEKLY;INTERVAL={int(interval.group(1))}"
        for word, code in WEEKDAY_BY_RE.items():
            if re.search(rf"\b(?:в\s+)?{word}\b", lower):
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


def _clean_title(text: str) -> str:
    cleaned = text
    cleaned = ALL_DAY_RE.sub(" ", cleaned)
    cleaned = LOCATION_RE.sub(" ", cleaned)
    cleaned = CROSS_MONTH_RANGE_RE.sub(" ", cleaned)
    cleaned = SAME_MONTH_RANGE_RE.sub(" ", cleaned)
    cleaned = NEXT_WEEK_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\b(?:напомни|напоминание)\s*(?:мне\s*)?", " ", cleaned, flags=re.IGNORECASE)
    cleaned = REMINDER_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\b(?:пригласи|участники\s*[:\-]?)\s*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = EMAIL_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\bкажд\w*\s+\d+\s+недел\w*\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bкажд\w*\s+(?:день|недел\w*|месяц\w*|понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bпо\s+(?:понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bраз\s+в\s+недел\w*\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:по\s+будням|по\s+выходным|кажд\w*\s+выходн\w*|ежегодно|кажд\w*\s+год)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:ежедневно|еженедельно|ежемесячно)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?:^|\s)(?:и|а)(?=\s*$)", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
    return _extract_title(cleaned)


def apply_event_features(event: dict, text: str) -> dict:
    """Добавить напоминания, повторение, место, участников и очистить title."""
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
        enriched["recurrence"] = [recurrence]

    location = _extract_location(text)
    if location:
        enriched["location"] = location

    attendees = _extract_attendees(text)
    if attendees:
        enriched["attendees"] = attendees

    return enriched
