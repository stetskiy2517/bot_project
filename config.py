import os
from dotenv import load_dotenv
load_dotenv()
TG_TOKEN=os.getenv("TG_TOKEN") or os.getenv("BOT_TOKEN")
TELEGRAM_PROXY_URL=os.getenv("TELEGRAM_PROXY_URL")
TELEGRAM_API_BASE=os.getenv("TELEGRAM_API_BASE")
WEBHOOK_URL=os.getenv("WEBHOOK_URL")
BASE_URL=os.getenv("BASE_URL")
GOOGLE_CLIENT_ID=os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET=os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI=os.getenv("REDIRECT_URI")
WEB_HOST=os.getenv("WEB_HOST","127.0.0.1")
WEB_PORT=int(os.getenv("WEB_PORT","8080"))
WEB_USER_ID=int(os.getenv("WEB_USER_ID","1"))
WEB_USER_NAME=os.getenv("WEB_USER_NAME","Web User")
WEB_PASSWORD=os.getenv("WEB_PASSWORD")
WEB_SESSION_SECRET=os.getenv("WEB_SESSION_SECRET","dev-only-change-me")
DB_PATH=os.getenv("DB_PATH","bot.db")
WEB_PUSH_VAPID_PRIVATE_KEY=os.getenv("WEB_PUSH_VAPID_PRIVATE_KEY")
WEB_PUSH_SUBJECT=os.getenv("WEB_PUSH_SUBJECT")
WEB_PUSH_ENABLED=os.getenv("WEB_PUSH_ENABLED", "true").lower() in {"true", "1", "yes"}
WEB_PUSH_WORKER_INTERVAL_SECONDS=max(2,int(os.getenv("WEB_PUSH_WORKER_INTERVAL_SECONDS","5")))
NAVIGATION_PROVIDER=(os.getenv("NAVIGATION_PROVIDER") or "2gis").strip().lower()
DGIS_API_KEY=os.getenv("DGIS_API_KEY")
YANDEX_GEOCODER_API_KEY=os.getenv("YANDEX_GEOCODER_API_KEY")
YANDEX_ROUTING_API_KEY=os.getenv("YANDEX_ROUTING_API_KEY")
SCOPES=["openid","https://www.googleapis.com/auth/userinfo.email","https://www.googleapis.com/auth/userinfo.profile","https://www.googleapis.com/auth/calendar.events"]
def validate_config():
    missing=[name for name,value in {"TG_TOKEN":TG_TOKEN}.items() if not value]
    if missing: raise RuntimeError("Не заданы обязательные переменные окружения: "+", ".join(missing))