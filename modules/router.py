from telegram import Update
from telegram.ext import ContextTypes

from modules.calendar import handle as handle_calendar


async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Главный маршрутизатор текстовых сообщений.

    На текущем этапе подключён только календарный модуль.
    Остальные сообщения намеренно не обрабатываются.
    """
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not text:
        return

    handled = await handle_calendar(update, context)

    if handled:
        return

    # Другие модули подключим позже.
    return
