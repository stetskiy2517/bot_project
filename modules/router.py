"""Центральная маршрутизация сообщений."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from modules.planner.handlers import handle as handle_planner

logger = logging.getLogger(__name__)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Передать текстовое сообщение подключённым модулям.

    На этапе MVP подключён AI Smart Planner.
    """
    try:
        handled = await handle_planner(update, context)
        if handled:
            return

        if update.message:
            await update.message.reply_text(
                "Не понял поручение. Напишите действие, дату или время."
            )
    except Exception:
        logger.exception("Unhandled error in text router")
        if update.message:
            await update.message.reply_text(
                "Не удалось обработать сообщение. Попробуйте ещё раз."
            )
