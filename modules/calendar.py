"""Календарный модуль: разбор команды и создание события в Google Calendar."""

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


def _parse_datetime(text: str) -> datetime | None:
    """Распознать дату и время из русской естественной фразы."""
    return dateparser.parse(
        text,
        languages=["ru"],
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )


def _extract_title(text: str) -> str:
    """Удалить из команды дату/время и оставить понятное название события."""
    title = text.strip()

    # Удаляем время: 16:00, 16 00, 16.00, 16ч и т.п.
    title = re.sub(r"\b\d{1,2}\s*(?::|\.|\s)\s*\d{2}\b", " ", title)
    title = re.sub(r"\b\d{1,2}\s*(?:ч|час|часа|часов)\b", " ", title, flags=re.IGNORECASE)

    # Удаляем относительные даты и дни недели.
    title = re.sub(
        r"\b(?:сегодня|завтра|послезавтра|после\s+завтра|вчера)\b",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\b(?:в|во)\s+(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)\b",
        " ",
        title,
        flags=re.IGNORECASE,
    )

    # Убираем лишние связки, оставшиеся после даты/времени.
    title = re.sub(r"\b(?:в|на)\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" ,.-")

    # Если после очистки ничего не осталось, используем понятное название.
    if not title:
        return "Встреча"

    # Нормализуем типичный ввод "встреча".
    return title[0].upper() + title[1:]


def _build_event(text: str, start: datetime) -> dict:
    """Собрать событие продолжительностью один час."""
    end = start + timedelta(hours=1)
    return {
        "summary": _extract_title(text),
        "start": {
            "dateTime": start.isoformat(),
            "timeZone": "Europe/Moscow",
        },
        "end": {
            "dateTime": end.isoformat(),
            "timeZone": "Europe/Moscow",
        },
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
        await update.message.reply_text(
            "Не удалось добавить событие в Google Calendar. Попробуйте ещё раз."
        )
        return True

    await update.message.reply_text(
        f"Событие «{event['summary']}» добавлено: {start.strftime('%d.%m.%Y %H:%M')}"
    )
    return True
