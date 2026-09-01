from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ContextTypes

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    user_id = update.message.from_user.id

    # Простейший формат: "напомни <текст> в <время>"
    if not text.startswith("напомни"):
        return

    import re
    from dateparser import parse
    m = re.match(r"напомни (.+) в (.+)", text)
    if not m:
        await update.message.reply_text("❌ Формат: 'Напомни [текст] в [время]'")
        return

    remind_text, remind_time_str = m.groups()
    remind_time = parse(remind_time_str, languages=["ru"])
    if not remind_time:
        await update.message.reply_text("❌ Не удалось распознать время")
        return

    from telegram.ext import JobQueue
    msg = schedule_reminder(user_id, remind_text, remind_time, context.job_queue)
    await update.message.reply_text(msg)
