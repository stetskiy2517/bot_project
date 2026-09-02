"""Пользовательский слой календаря с настройками профиля."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_user_timezone
from modules.calendar import _build_event, _create_event, _parse_event_timing

logger = logging.getLogger(__name__)


async def create_from_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> bool:
    """Создать календарное событие из уже классифицированной команды."""
    timing = _parse_event_timing(text)
    if not timing:
        return False

    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default=None)
    if not timezone:
        await update.message.reply_text(
            "Сначала выбери часовой пояс для календаря: /timezone"
        )
        return True

    start, end = timing

    try:
        event = _build_event(text, start, end)
        event["start"]["timeZone"] = timezone
        event["end"]["timeZone"] = timezone
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
        f"Событие «{event['summary']}» добавлено: "
        f"{start.strftime('%d.%m.%Y %H:%M')}–{end.strftime('%H:%M')} ({timezone})"
    )
    return True


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Совместимость со старым роутером. Новое ядро использует modules.planner."""
    if not update.message or not update.message.text:
        return False
    text = update.message.text.strip()
    if not text:
        return False
    return await create_from_text(update, context, text)
