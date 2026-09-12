"""Web transport adapter for the existing Smart Planner router.

The calendar/router layer still consumes a small Telegram-like interface.
This adapter lets web/PWA requests use the same tested core without calling
Telegram API. Deeper transport decoupling can happen incrementally later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from types import SimpleNamespace
from typing import Any


CREATED_TIMED_RE = re.compile(
    r"^Событие «(?P<title>.+?)» добавлено: "
    r"(?P<date>\d{2}\.\d{2}\.\d{4}) "
    r"(?P<start>\d{2}:\d{2})–(?P<end>\d{2}:\d{2}) "
    r"\([^)]+\)(?P<extras>.*)$"
)
CREATED_ALL_DAY_RE = re.compile(
    r"^Событие «(?P<title>.+?)» добавлено на весь день: "
    r"(?P<date>\d{4}-\d{2}-\d{2})$"
)


def _request_day_label(request_text: str, fallback_date: str) -> str:
    lower = request_text.lower().replace("ё", "е")
    if re.search(r"\bпослезавтра\b", lower):
        return "Послезавтра"
    if re.search(r"\bзавтра\b", lower):
        return "Завтра"
    if re.search(r"\bсегодня\b", lower):
        return "Сегодня"
    return fallback_date


def _short_iso_date(value: str) -> str:
    year, month, day = value.split("-")
    return f"{day}.{month}"


def _compact_extras(value: str) -> str:
    extras = value.strip().lstrip("·").strip()
    if not extras:
        return ""
    extras = extras.replace("с напоминанием", "напоминание")
    extras = extras.replace(", ", " · ")
    return extras[:1].upper() + extras[1:]


def _compact_calendar_reply(text: str, request_text: str) -> str:
    timed = CREATED_TIMED_RE.fullmatch(text)
    if timed:
        date_label = _request_day_label(request_text, timed.group("date")[:5])
        result = (
            f"Готово · «{timed.group('title')}»\n"
            f"{date_label}, {timed.group('start')}–{timed.group('end')}"
        )
        extras = _compact_extras(timed.group("extras"))
        return f"{result}\n{extras}" if extras else result

    all_day = CREATED_ALL_DAY_RE.fullmatch(text)
    if all_day:
        date_label = _request_day_label(request_text, _short_iso_date(all_day.group("date")))
        return f"Готово · «{all_day.group('title')}»\n{date_label}, весь день"

    return text


def _web_text(text: str, request_text: str = "") -> str:
    """Adapt shared planner replies to concise web/PWA copy."""
    value = str(text)
    value = value.replace("Сначала подключите Google Calendar: /start", "Сначала подключи Google Calendar в приложении.")
    value = value.replace("Сначала выбери часовой пояс для календаря: /timezone", "Сначала выбери часовой пояс в настройках календаря.")
    return _compact_calendar_reply(value, request_text)


@dataclass
class WebMessage:
    text: str
    replies: list[str] = field(default_factory=list)

    async def reply_text(self, text: str, **_: Any) -> None:
        self.replies.append(_web_text(text, self.text))


class WebUpdate:
    def __init__(self, user_id: int, user_name: str, text: str):
        self.message = WebMessage(text=text)
        self.effective_user = SimpleNamespace(id=user_id, full_name=user_name)
        self.callback_query = None


class WebContext:
    def __init__(self, user_data: dict[str, Any]):
        self.user_data = user_data
        self.args: list[str] = []


@dataclass
class WebPlannerResult:
    handled: bool
    replies: list[str]
