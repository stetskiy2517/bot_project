"""Дополнительные свойства событий Google Calendar."""

from __future__ import annotations

from datetime import datetime, timedelta
import re

from modules.calendar import _date_from_text, _detect_category, _extract_title
from modules.calendar_user import _user_zone

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
ALL_DAY_RE = re.compile(r"\b(?:весь\s+день|на\s+весь\s+день|целый\s+день)\b", re.IGNORECASE)
BIRTHDAY_RE = re.compile(r"\bдень\s+рождени[яе]\b", re.IGNORECASE)
LOCATION_RE = re.compile(
    r"\b(?:по\s+адресу|место\s*[:\-]|локация\s*[:\-])\s*(.+?)(?=$|\s+(?:напомни|пригласи|участники|кажд|весь\s+день))",
    re.IGNORECASE,
)
REMINDER_RE = re.compile(
    r"\bза\s+(?:(\d+)\s*(минут\w*|час\w*|дн\w*)|(полчаса)|(час)|(день))\b",
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
    """Явный all-day или день рождения без указанного времени."""
    if ALL_DAY_RE.search(text):
        return True
    if BIRTHDAY_RE.search(text):
        has_time = bool(re.search(r"\b(?:в|к)\s+\d{1,2}(?:(?::|\.|\s)\d{2})?\b", text, re.IGNORECASE))
        return not has_time
    return False


def build_all_day_event(text: str, timezone: str, now: datetime | None = None) -> dict | None:
    zone = _user_zone(timezone)
    local_now = now.astimezone(zone) if now and now.tzinfo else (now.replace(tzinfo=zone) if now else datetime.now(zone))
    event_date = _date_from_text(text, local_now.replace(tzinfo=None), 12, 0)
    if not event_date:
        return None
    category, color_id = _detect_category(text)
    event = {
        "summary": _clean_title(text),
        "description": f"AI Smart Planner category: {category}",
        "start": {"date": event_date.isoformat()},
        "end": {"date": (event_date + timedelta(days=1)).isoformat()},
    }
    if color_id:
        event["colorId"] = color_id
    return apply_event_features(event, text)


def _reminder_minutes(text: str) -> list[int]:
    values: list[int] = []
    for match in REMINDER_RE.finditer(text):
        if match.group(3):
            minutes = 30
        elif match.group(4):
            minutes = 60
        elif match.group(5):
            minutes = 1440
        else:
            amount = int(match.group(1))
            unit = match.group(2).lower()
            if unit.startswith("минут"):
                minutes = amount
            elif unit.startswith("час"):
                minutes = amount * 60
            else:
                minutes = amount * 1440
        if 0 <= minutes <= 40320 and minutes not in values:
            values.append(minutes)
    return sorted(values, reverse=True)[:5]


def _recurrence_rule(text: str) -> str | None:
    lower = text.lower().replace("ё", "е")
    if re.search(r"\bкажд(?:ый|ую|ое)\s+день\b|\bежедневно\b", lower):
        return "RRULE:FREQ=DAILY"
    if re.search(r"\bкажд(?:ую|ой)\s+недел\w*\b|\bеженедельно\b", lower):
        return "RRULE:FREQ=WEEKLY"
    if re.search(r"\bкажд(?:ый|ого)\s+месяц\w*\b|\bежемесячно\b", lower):
        return "RRULE:FREQ=MONTHLY"
    for word, code in WEEKDAY_BY_RE.items():
        if re.search(rf"\bкажд\w*\s+{word}\b", lower):
            return f"RRULE:FREQ=WEEKLY;BYDAY={code}"
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
    cleaned = re.sub(r"\b(?:напомни|напоминание)\s*(?:мне\s*)?", " ", cleaned, flags=re.IGNORECASE)
    cleaned = REMINDER_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\b(?:пригласи|участники\s*[:\-]?)\s*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = EMAIL_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\bкажд\w*\s+(?:день|недел\w*|месяц\w*|понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:ежедневно|еженедельно|ежемесячно)\b", " ", cleaned, flags=re.IGNORECASE)
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
