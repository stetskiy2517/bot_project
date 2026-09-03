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
SCOPES=["openid","https://www.googleapis.com/auth/userinfo.email","https://www.googleapis.com/auth/userinfo.profile","https://www.googleapis.com/auth/calendar.events"]
def validate_config():
    missing=[name for name,value in {"TG_TOKEN":TG_TOKEN}.items() if not value]
    if missing: raise RuntimeError("Не заданы обязательные переменные окружения: "+", ".join(missing))
