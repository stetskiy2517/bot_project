import os
from dotenv import load_dotenv

load_dotenv()

TG_TOKEN = os.getenv("TG_TOKEN") or os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
BASE_URL = os.getenv("BASE_URL")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY")
REDIRECT_URI = os.getenv("REDIRECT_URI")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
OAUTH_SERVER_HOST = os.getenv("OAUTH_SERVER_HOST", "0.0.0.0")
OAUTH_SERVER_PORT = int(os.getenv("PORT", os.getenv("OAUTH_SERVER_PORT", "8080")))

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events"
]


def validate_config():
    required = {
        "TG_TOKEN": TG_TOKEN,
    }

    missing = [
        name for name, value in required.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Не заданы обязательные переменные окружения: "
            + ", ".join(missing)
        )

    google_values = {
        "GOOGLE_CLIENT_ID": GOOGLE_CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": GOOGLE_CLIENT_SECRET,
        "BASE_URL или REDIRECT_URI": BASE_URL or REDIRECT_URI,
        "TOKEN_ENCRYPTION_KEY": TOKEN_ENCRYPTION_KEY,
    }
    google_requested = bool(
        GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET or TOKEN_ENCRYPTION_KEY
    )
    if google_requested and not all(google_values.values()):
        missing_google = [name for name, value in google_values.items() if not value]
        raise RuntimeError(
            "Google OAuth настроен не полностью. Не заданы: "
            + ", ".join(missing_google)
        )
