from telegram.ext import MessageHandler, filters
from telegram import Update
from telegram.ext import ContextTypes
from core.db import get_google_token
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import timedelta
import dateparser

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if "встреч" not in text:
        return

    user_id = update.effective_user.id
    token_dict = get_google_token(user_id)
    if not token_dict:
        await update.message.reply_text("Сначала авторизуйтесь: /start")
        return

    creds = Credentials.from_authorized_user_info(token_dict)
    service = build("calendar", "v3", credentials=creds)

    dt = dateparser.parse(text)
    if not dt:
        await update.message.reply_text("Не могу распознать дату")
        return

    event = {
        "summary": text,
        "start": {"dateTime": dt.isoformat()},
        "end": {"dateTime": (dt + timedelta(hours=1)).isoformat()}
    }
    service.events().insert(calendarId="primary", body=event).execute()
    await update.message.reply_text("Событие добавлено в Google Calendar ✅")

calendar_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
