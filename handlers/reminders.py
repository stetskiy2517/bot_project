from telegram.ext import MessageHandler, filters

async def handle_message(update, context):
    text = update.message.text.lower()

    # простая проверка на напоминания
    if not any(word in text for word in ["напомни", "напоминание", "напомнить"]):
        return

    await update.message.reply_text("⏰ Напоминание установлено (тест)")

# экспортируем handler
reminders_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
