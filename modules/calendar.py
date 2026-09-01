"""Календарный модуль.

Принимает обычный текст пользователя, пытается распознать дату/время
и создаёт событие в Google Calendar.
"""

from datetime import datetime, timedelta
import logging

import dateparser
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_google_token

logger = logging.getLogger(__name__)


def _parse_datetime(text: str) -> datetime | None:
    """Распознать дату/время из русской естественной фразы."""
    return dateparser.parse(
        text,
        languages=["ru"],
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )


def _build_event(text: str, start: datetime) -> dict:
    """Собрать событие продолжительностью один час."""
    end = start + timedelta(hours=1)
    return {
        "summary": text,
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
    """Обработать текст как календарную команду.

    Возвращает True, если сообщение было распознано и обработано.
    """
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
        f"Событие добавлено: {start.strftime('%d.%m.%Y %H:%M')}"
    )
    return True
