"""Модуль работы с Google Calendar.

На первом этапе модуль переиспользует существующую календарную логику
из handlers.calendar. В дальнейшем всю календарную логику перенесём сюда.
"""

from telegram import Update
from telegram.ext import ContextTypes

from handlers.calendar import handle_message


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Обрабатывает календарное сообщение.

    Возвращает True, если сообщение было распознано как календарное.
    """
    if not update.message or not update.message.text:
        return False

    text = update.message.text.strip().lower()
    if "встреч" not in text:
        return False

    await handle_message(update, context)
    return True
