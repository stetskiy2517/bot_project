import os
from telegram import Update
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
from google_auth_oauthlib.flow import Flow

ASK_NAME = 1
USERS = {}  # временно, потом заменим на БД
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# Redirect URI должен точно совпадать с Google Cloud
REDIRECT_URI = "https://bot-project-bdub.onrender.com/auth/callback"


def get_auth_url(user_id: int):
    """
    Создаёт ссылку для OAuth авторизации Google для конкретного пользователя
    """
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise RuntimeError("GOOGLE_CLIENT_ID и GOOGLE_CLIENT_SECRET должны быть заданы в окружении")

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=str(user_id)
    )
    return auth_url


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id in USERS:
        await update.message.reply_text("👋 С возвращением!")
        return ConversationHandler.END

    await update.message.reply_text(
        "Привет! Я твой личный помощник.\n"
        "Как тебя называть?"
    )
    return ASK_NAME


async def save_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.message.text.strip()

    USERS[user_id] = {"name": name}

    # Генерация ссылки OAuth
    auth_url = get_auth_url(user_id)

    await update.message.reply_text(
        f"Отлично, {name}! ✅\n\n"
        "Для работы с календарём нужно подключить Google:\n"
        f"{auth_url}"
    )

    return ConversationHandler.END


auth_conv = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        ASK_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, save_name)
        ],
    },
    fallbacks=[],
)


# --- Функция для обработки callback от Google ---
from google.oauth2.credentials import Credentials

def fetch_token_from_url(full_url: str):
    """
    Обрабатывает redirect от Google OAuth
    и возвращает Credentials
    """
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    flow.fetch_token(authorization_response=full_url)
    return flow.credentials
