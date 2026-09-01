from telegram import Update
from telegram.ext import ContextTypes


async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Главный маршрутизатор текстовых сообщений."""

    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()

    if not text:
        return

    await update.message.reply_text(
        f"Получил: {text}"
    )
