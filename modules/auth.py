"""Google OAuth for Telegram calendar linking and web Google sign-in."""
from __future__ import annotations
import json, logging, threading
from html import escape
import requests
from flask import Flask, request
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from google_auth_oauthlib.flow import Flow
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from werkzeug.serving import make_server
from config import BASE_URL,GOOGLE_CLIENT_ID,GOOGLE_CLIENT_SECRET,REDIRECT_URI,SCOPES,TG_TOKEN
from core.db import consume_oauth_state,ensure_user,get_onboarding_status,get_or_create_google_user,save_google_token,save_oauth_state
from modules.settings import timezone_command
logger=logging.getLogger(__name__);OAUTH_CALLBACK_PATH="/oauth2callback";HEALTH_PATH="/health"
def _redirect_uri(): return REDIRECT_URI.rstrip("/") if REDIRECT_URI else (BASE_URL.rstrip("/")+OAUTH_CALLBACK_PATH if BASE_URL else None)
def _oauth_ready(): return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and _redirect_uri())
def _client_config():
    if not _oauth_ready(): raise RuntimeError("Google OAuth не настроен")
    return {"web":{"client_id":GOOGLE_CLIENT_ID,"client_secret":GOOGLE_CLIENT_SECRET,"auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","redirect_uris":[_redirect_uri()]}}
def _authorization_url(user_id:int|None):
    flow=Flow.from_client_config(_client_config(),scopes=SCOPES);flow.redirect_uri=_redirect_uri();url,state=flow.authorization_url(access_type="offline",include_granted_scopes="true",prompt="consent");save_oauth_state(state,user_id);return url
def build_authorization_url(user_id:int)->str:return _authorization_url(user_id)
def build_web_signin_url()->str:return _authorization_url(None)
def _exchange(state:str,code:str):
    if not state or not code:raise ValueError("В ответе Google нет code или state.")
    linked=consume_oauth_state(state)
    if linked is None:raise ValueError("Ссылка авторизации устарела или уже была использована.")
    flow=Flow.from_client_config(_client_config(),scopes=SCOPES,state=state);flow.redirect_uri=_redirect_uri();flow.fetch_token(code=code);return linked,flow.credentials
def complete_authorization(state:str,code:str)->int:
    user_id,credentials=_exchange(state,code)
    if user_id==0:raise ValueError("Эта ссылка предназначена для входа через веб-приложение.")
    save_google_token(user_id,json.loads(credentials.to_json()));return user_id
def complete_web_signin(state:str,code:str)->int:
    linked,credentials=_exchange(state,code)
    if linked!=0:raise ValueError("Эта ссылка предназначена для подключения календаря существующего пользователя.")
    raw=id_token.verify_oauth2_token(credentials.id_token,google_requests.Request(),GOOGLE_CLIENT_ID)
    if not raw.get("email_verified"):raise ValueError("Google email не подтверждён")
    user_id=get_or_create_google_user(str(raw["sub"]),str(raw["email"]),raw.get("name"));save_google_token(user_id,json.loads(credentials.to_json()));return user_id
def _auth_keyboard(user_id):return InlineKeyboardMarkup([[InlineKeyboardButton("Подключить Google Calendar",url=build_authorization_url(user_id))]])
def _telegram_notify(user_id,text):
    if not TG_TOKEN:return
    try:requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",json={"chat_id":user_id,"text":text},timeout=10).raise_for_status()
    except requests.RequestException:logger.exception("Failed to notify Telegram user %s after OAuth",user_id)
def _success_html():return '<!doctype html><html lang="ru"><meta charset="utf-8"><body><h2>Google Calendar подключён</h2><p>Можно вернуться в приложение.</p></body></html>'
def _error_html(message):return f"<!doctype html><html lang='ru'><meta charset='utf-8'><body><h2>Не удалось подключить Google Calendar</h2><p>{escape(message)}</p></body></html>",400
def create_oauth_web_app():
    app=Flask("personal-secretary-oauth")
    @app.get(HEALTH_PATH)
    def health():return {"status":"ok"}
    @app.get(OAUTH_CALLBACK_PATH)
    def oauth_callback():
        if request.args.get("error"):return _error_html(f"Google вернул ошибку: {request.args['error']}")
        try:user_id=complete_authorization(request.args.get("state",""),request.args.get("code",""))
        except ValueError as exc:return _error_html(str(exc))
        except Exception:logger.exception("Google OAuth callback failed");return _error_html("Не удалось обменять код Google на токен.")
        _telegram_notify(user_id,"Google Calendar подключён. Следующий шаг — выбери часовой пояс: /timezone");return _success_html()
    return app
class OAuthServer:
    def __init__(self,host="0.0.0.0",port=8080):self._server=make_server(host,port,create_oauth_web_app(),threaded=True);self._thread=threading.Thread(target=self._server.serve_forever,daemon=True)
    def start(self):self._thread.start();logger.info("OAuth callback server started")
    def stop(self):self._server.shutdown();self._thread.join(timeout=5)
async def start_command(update:Update,context:ContextTypes.DEFAULT_TYPE)->None:
    if not update.message or not update.effective_user:return
    user=update.effective_user;ensure_user(user.id,user.full_name);status=get_onboarding_status(user.id)
    if not status["google_connected"]:
        if not _oauth_ready():await update.message.reply_text("Google Calendar пока не настроен на сервере.");return
        await update.message.reply_text("Сначала подключи Google Calendar. После авторизации вернись сюда.",reply_markup=_auth_keyboard(user.id));return
    if not status["timezone_set"]:await update.message.reply_text("Google Calendar уже подключён. Теперь выбери часовой пояс.");await timezone_command(update,context);return
    p=status["preferences"];await update.message.reply_text(f"Календарь готов к работе.\nРабочие часы: {p['work_start']}–{p['work_end']}\nБуфер: {p['buffer_minutes']} мин")
async def reconnect_command(update:Update,context:ContextTypes.DEFAULT_TYPE)->None:
    if not update.message or not update.effective_user:return
    if not _oauth_ready():await update.message.reply_text("Google OAuth не настроен на сервере.");return
    await update.message.reply_text("Открой Google и заново разреши доступ к календарю.",reply_markup=_auth_keyboard(update.effective_user.id))
