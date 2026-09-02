"""Deterministic parser for conversational Russian planner commands."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

from .models import Intent, MissingField, PlannerCommand


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

MONTHS = {
    "января": 1, "январь": 1, "янв": 1,
    "февраля": 2, "февраль": 2, "фев": 2,
    "марта": 3, "март": 3, "мар": 3,
    "апреля": 4, "апрель": 4, "апр": 4,
    "мая": 5, "май": 5,
    "июня": 6, "июнь": 6, "июн": 6,
    "июля": 7, "июль": 7, "июл": 7,
    "августа": 8, "август": 8, "авг": 8,
    "сентября": 9, "сентябрь": 9, "сен": 9, "сент": 9,
    "октября": 10, "октябрь": 10, "окт": 10,
    "ноября": 11, "ноябрь": 11, "ноя": 11,
    "декабря": 12, "декабрь": 12, "дек": 12,
}

NUMBER_WORDS = {
    "один": 1, "одну": 1, "одна": 1,
    "два": 2, "две": 2,
    "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12,
}

TIME_WORDS = {
    "рано утром": (8, 0),
    "утром": (9, 0),
    "после обеда": (15, 0),
    "днём": (14, 0),
    "днем": (14, 0),
    "вечером": (18, 0),
    "ночью": (23, 0),
}

EVENT_MARKERS = (
    "встреч", "встрет", "созвон", "звонок", "совещан", "поездк", "вылет",
    "мероприят", "событи", "приём", "прием", "переговор",
)
TASK_MARKERS = (
    "задач", "нужно", "надо", "сделать", "позвонить", "отправить",
    "подготовить", "проверить", "купить", "заказать", "написать",
    "забрать", "сдать", "оплатить", "доделать", "выполнить",
)

_WEEKDAY_PATTERN = "|".join(sorted(map(re.escape, WEEKDAYS), key=len, reverse=True))
_MONTH_PATTERN = "|".join(sorted(map(re.escape, MONTHS), key=len, reverse=True))
_NUMBER_PATTERN = r"\d+|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))


class PlannerParser:
    def __init__(self, timezone: str = "Europe/Moscow") -> None:
        self.timezone = ZoneInfo(timezone)

    def parse(self, text: str, now: datetime | None = None) -> PlannerCommand:
        original = text.strip()
        normalized = self._normalize(original)
        reference = self._reference(now)
        intent = self._detect_intent(normalized)

        event_date, date_span, date_text, weekday_missing = self._extract_date(normalized, reference)
        event_time, time_span, time_text, relative_dt = self._extract_time(normalized, reference)
        if date_span and time_span and self._spans_overlap(date_span, time_span):
            event_time, time_span, time_text = None, None, None
        if relative_dt is not None:
            event_date = relative_dt.date()
            event_time = relative_dt.time().replace(second=0, microsecond=0)

        duration, duration_span = self._extract_duration(normalized)
        spans = [span for span in (date_span, time_span, duration_span) if span]
        title = self._extract_title(original, spans)

        missing: list[MissingField] = []
        if weekday_missing:
            missing.append(MissingField.WEEKDAY)
        if not title:
            missing.append(MissingField.TITLE)

        # Events and reminders require an exact moment. A task may have only a
        # due date or no due date at all, but a time without a date is unsafe.
        if intent in (Intent.CREATE_EVENT, Intent.CREATE_REMINDER):
            if event_date is None and not weekday_missing:
                missing.append(MissingField.DATE)
            if event_time is None:
                missing.append(MissingField.TIME)
        elif intent == Intent.CREATE_TASK and event_time is not None and event_date is None:
            missing.append(MissingField.DATE)

        return PlannerCommand(
            intent=intent,
            title=title,
            event_date=event_date,
            event_time=event_time,
            duration_minutes=duration,
            missing=self._unique(missing),
            date_text=date_text,
            time_text=time_text,
            confidence=0.0 if intent == Intent.UNKNOWN else 1.0,
        )

    def parse_clarification(
        self,
        text: str,
        pending: PlannerCommand,
        now: datetime | None = None,
    ) -> PlannerCommand:
        parsed = self.parse(text, now=now)
        if parsed.event_date is not None:
            pending.event_date = parsed.event_date
            pending.date_text = parsed.date_text
        if parsed.event_time is not None:
            pending.event_time = parsed.event_time
            pending.time_text = parsed.time_text
        if MissingField.TITLE in pending.missing and parsed.title:
            pending.title = parsed.title

        pending.missing = []
        if not pending.title:
            pending.missing.append(MissingField.TITLE)
        if pending.intent in (Intent.CREATE_EVENT, Intent.CREATE_REMINDER):
            if pending.event_date is None:
                pending.missing.append(MissingField.DATE)
            if pending.event_time is None:
                pending.missing.append(MissingField.TIME)
        elif pending.intent == Intent.CREATE_TASK and pending.event_time and not pending.event_date:
            pending.missing.append(MissingField.DATE)
        return pending

    def _reference(self, value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(self.timezone)
        if value.tzinfo is None:
            return value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone)

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()

    @staticmethod
    def _detect_intent(text: str) -> Intent:
        if re.search(r"\bнапомни(?:ть)?\b|\bнапоминание\b", text):
            return Intent.CREATE_REMINDER
        if any(marker in text for marker in EVENT_MARKERS):
            return Intent.CREATE_EVENT
        if any(marker in text for marker in TASK_MARKERS):
            return Intent.CREATE_TASK
        return Intent.UNKNOWN

    def _extract_date(
        self, text: str, now: datetime
    ) -> tuple[date | None, tuple[int, int] | None, str | None, bool]:
        relative = re.search(r"\b(послезавтра|после завтра|завтра|сегодня)\b", text)
        if relative:
            offsets = {"сегодня": 0, "завтра": 1, "послезавтра": 2, "после завтра": 2}
            return (now + timedelta(days=offsets[relative.group(1)])).date(), relative.span(), relative.group(), False

        rel_days = re.search(rf"\bчерез\s+({_NUMBER_PATTERN})\s+(день|дня|дней|сутки|суток)\b", text)
        if rel_days:
            amount = self._number(rel_days.group(1))
            return (now + timedelta(days=amount)).date(), rel_days.span(), rel_days.group(), False

        rel_weeks = re.search(rf"\bчерез\s+({_NUMBER_PATTERN})\s+(неделю|недели|недель)\b", text)
        if rel_weeks:
            amount = self._number(rel_weeks.group(1))
            return (now + timedelta(weeks=amount)).date(), rel_weeks.span(), rel_weeks.group(), False
        one_week = re.search(r"\bчерез\s+неделю\b", text)
        if one_week:
            return (now + timedelta(days=7)).date(), one_week.span(), one_week.group(), False

        vague_week = re.search(r"\b(?:на\s+)?следующей\s+неделе\b", text)
        if vague_week:
            return None, vague_week.span(), vague_week.group(), True

        weekday = re.search(
            rf"\b(?:(следующ(?:ий|ую|ая|ее)|ближайш(?:ий|ую|ая|ее))\s+)?({_WEEKDAY_PATTERN})\b",
            text,
        )
        if weekday:
            target = WEEKDAYS[weekday.group(2)]
            days_ahead = (target - now.weekday() + 7) % 7
            if days_ahead == 0:
                days_ahead = 7
            if weekday.group(1) and weekday.group(1).startswith("следующ"):
                days_ahead += 7
            return (now + timedelta(days=days_ahead)).date(), weekday.span(), weekday.group(), False

        numeric = re.search(r"(?<!\d)(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2}|\d{4}))?(?!\d)", text)
        if numeric:
            day, month = int(numeric.group(1)), int(numeric.group(2))
            year = self._year(numeric.group(3), now.year)
            parsed = self._safe_future_date(year, month, day, now.date(), numeric.group(3) is None)
            return parsed, numeric.span(), numeric.group(), False

        named = re.search(rf"\b(\d{{1,2}})\s+({_MONTH_PATTERN})(?:\s+(\d{{4}}))?\b", text)
        if named:
            day, month = int(named.group(1)), MONTHS[named.group(2)]
            year = int(named.group(3)) if named.group(3) else now.year
            parsed = self._safe_future_date(year, month, day, now.date(), named.group(3) is None)
            return parsed, named.span(), named.group(), False

        return None, None, None, False

    def _extract_time(
        self, text: str, now: datetime
    ) -> tuple[time | None, tuple[int, int] | None, str | None, datetime | None]:
        half_hour = re.search(r"\bчерез\s+полчаса\b", text)
        if half_hour:
            result = now + timedelta(minutes=30)
            return result.time(), half_hour.span(), half_hour.group(), result

        rel = re.search(rf"\bчерез\s+({_NUMBER_PATTERN})\s+(минуту|минуты|минут|час|часа|часов)\b", text)
        if rel:
            amount = self._number(rel.group(1))
            delta = timedelta(minutes=amount) if rel.group(2).startswith("минут") else timedelta(hours=amount)
            result = now + delta
            return result.time(), rel.span(), rel.group(), result

        time_range = re.search(
            r"\bс\s*(\d{1,2})(?:\s*[:.;-]\s*(\d{2}))?\s+до\s+\d{1,2}(?:\s*[:.;-]\s*\d{2})?\b",
            text,
        )
        if time_range:
            hour, minute = int(time_range.group(1)), int(time_range.group(2) or 0)
            if self._valid_time(hour, minute):
                return time(hour, minute), time_range.span(), time_range.group(), None

        clock = re.search(
            r"\b(?:в|к|на)\s*(?:пол)?(\d{1,2})(?:(?:\s*[:.;-]\s*|\s+)(\d{2}))?\s*(утра|дня|вечера|ночи)?\b",
            text,
        )
        if clock:
            hour = int(clock.group(1))
            minute = int(clock.group(2) or 0)
            hour = self._apply_day_part(hour, clock.group(3))
            if self._valid_time(hour, minute):
                return time(hour, minute), clock.span(), clock.group(), None

        # A colon/dot time is unambiguous even without a preposition.
        clock = re.search(r"(?<!\d)([01]?\d|2[0-3])\s*[:.;-]\s*([0-5]\d)(?!\d)", text)
        if clock:
            return time(int(clock.group(1)), int(clock.group(2))), clock.span(), clock.group(), None

        clock_words = re.search(r"\b(\d{1,2})\s*(утра|дня|вечера|ночи)\b", text)
        if clock_words:
            hour = self._apply_day_part(int(clock_words.group(1)), clock_words.group(2))
            if self._valid_time(hour, 0):
                return time(hour, 0), clock_words.span(), clock_words.group(), None

        for phrase, (hour, minute) in TIME_WORDS.items():
            match = re.search(rf"\b{re.escape(phrase)}\b", text)
            if match:
                return time(hour, minute), match.span(), match.group(), None
        return None, None, None, None

    @staticmethod
    def _extract_duration(text: str) -> tuple[int, tuple[int, int] | None]:
        match = re.search(r"\bна\s+(\d+)\s*(минут|минуты|час|часа|часов)\b", text)
        if match:
            amount = int(match.group(1))
            minutes = amount if match.group(2).startswith("минут") else amount * 60
            return max(1, minutes), match.span()
        time_range = re.search(
            r"\bс\s*(\d{1,2})(?:\s*[:.;-]\s*(\d{2}))?\s+до\s+(\d{1,2})(?:\s*[:.;-]\s*(\d{2}))?\b",
            text,
        )
        if time_range:
            start = int(time_range.group(1)) * 60 + int(time_range.group(2) or 0)
            end = int(time_range.group(3)) * 60 + int(time_range.group(4) or 0)
            if end > start:
                return end - start, time_range.span()
        return 60, None

    @staticmethod
    def _extract_title(original: str, spans: list[tuple[int, int]]) -> str:
        chars = list(original)
        for start, end in spans:
            for index in range(start, min(end, len(chars))):
                chars[index] = " "
        title = "".join(chars)
        title = re.sub(r"\b(?:напомни(?:ть)?|создай|добавь|запланируй|поставь)\b", " ", title, flags=re.I)
        title = re.sub(r"\b(?:событие|задачу|задача|напоминание)\b", " ", title, flags=re.I)
        title = re.sub(r"\b(?:на\s+следующей\s+неделе)\b", " ", title, flags=re.I)
        title = re.sub(r"\s+", " ", title).strip(" ,.!?-–—")
        title = re.sub(r"^(?:в|во|на|к|до)\s+", "", title, flags=re.I).strip()
        return title[:1].upper() + title[1:] if title else ""

    @staticmethod
    def _apply_day_part(hour: int, part: str | None) -> int:
        if part in ("вечера", "дня") and hour < 12:
            return hour + 12
        if part == "ночи" and hour == 12:
            return 0
        return hour

    @staticmethod
    def _valid_time(hour: int, minute: int) -> bool:
        return 0 <= hour <= 23 and 0 <= minute <= 59

    @staticmethod
    def _number(value: str) -> int:
        return int(value) if value.isdigit() else NUMBER_WORDS[value]

    @staticmethod
    def _year(value: str | None, current: int) -> int:
        if value is None:
            return current
        year = int(value)
        return year + 2000 if year < 100 else year

    @staticmethod
    def _safe_future_date(year: int, month: int, day: int, today: date, roll_year: bool) -> date | None:
        try:
            result = date(year, month, day)
        except ValueError:
            return None
        if roll_year and result < today:
            next_year = year + 1
            if day <= monthrange(next_year, month)[1]:
                result = date(next_year, month, day)
        return result

    @staticmethod
    def _unique(values: list[MissingField]) -> list[MissingField]:
        return list(dict.fromkeys(values))

    @staticmethod
    def _spans_overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
        return max(first[0], second[0]) < min(first[1], second[1])
