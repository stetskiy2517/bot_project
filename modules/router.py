"""Центральная маршрутизация сообщений."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from modules.calendar_user import handle as handle_calendar

logger = logging.getLogger(__name__)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Передать текстовое сообщение подключённым модулям.

    На этапе MVP подключён только календарь.
    """
    try:
        handled = await handle_calendar(update, context)
        if handled:
            return

        if update.message:
            await update.message.reply_text(
                "Пока я умею работать только с календарём."
            )
    except Exception:
        logger.exception("Unhandled error in text router")
        if update.message:
            await update.message.reply_text(
                "Не удалось обработать сообщение. Попробуйте ещё раз."
            )
