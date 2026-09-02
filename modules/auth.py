"""Google OAuth и первый запуск пользователя."""

from __future__ import annotations

import json
import logging
import threading
from html import escape

import requests
from flask import Flask, request
from google_auth_oauthlib.flow import Flow
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from werkzeug.serving import make_server

from config import (
    BASE_URL,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    REDIRECT_URI,
    SCOPES,
    TG_TOKEN,
)
from core.db import (
    consume_oauth_state,
    ensure_user,
    get_google_token,
    get_onboarding_status,
    save_google_token,
    save_oauth_state,
)
from modules.settings import timezone_command

logger = logging.getLogger(__name__)

OAUTH_CALLBACK_PATH = "/oauth2callback"
HEALTH_PATH = "/health"


def _redirect_uri() -> str | None:
    if REDIRECT_URI:
        return REDIRECT_URI.rstrip("/")
    if BASE_URL:
        return BASE_URL.rstrip("/") + OAUTH_CALLBACK_PATH
    return None


def _oauth_ready() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and _redirect_uri())


def _client_config() -> dict:
    if not _oauth_ready():
        raise RuntimeError("Google OAuth не настроен")
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [_redirect_uri()],
        }
    }


def build_authorization_url(user_id: int) -> str:
    """Создать одноразовую ссылку Google OAuth и связать state с Telegram user_id."""
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = _redirect_uri()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    save_oauth_state(state, user_id)
    return authorization_url


def _auth_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Подключить Google Calendar", url=build_authorization_url(user_id))]
    ])


def _telegram_notify(user_id: int, text: str) -> None:
    """Уведомить Telegram после HTTP callback, не смешивая Flask и PTB event loop."""
    if not TG_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": user_id, "text": text},
            timeout=10,
        ).raise_for_status()
    except requests.RequestException:
        logger.exception("Failed to notify Telegram user %s after OAuth", user_id)


def _success_html() -> str:
    return """
    <!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Google Calendar подключён</title></head>
    <body style="font-family: sans-serif; max-width: 560px; margin: 48px auto; padding: 0 16px;">
      <h2>Google Calendar подключён</h2>
      <p>Вернись в Telegram. Бот продолжит настройку календаря.</p>
    </body></html>
    """


def _error_html(message: str) -> tuple[str, int]:
    safe = escape(message)
    return (
        f"<!doctype html><html lang='ru'><meta charset='utf-8'><body>"
        f"<h2>Не удалось подключить Google Calendar</h2><p>{safe}</p>"
        f"<p>Вернись в Telegram и выполни /start ещё раз.</p></body></html>",
        400,
    )


def create_oauth_web_app() -> Flask:
    app = Flask("personal-secretary-oauth")

    @app.get(HEALTH_PATH)
    def health():
        return {"status": "ok"}

    @app.get(OAUTH_CALLBACK_PATH)
    def oauth_callback():
        error = request.args.get("error")
        if error:
            return _error_html(f"Google вернул ошибку: {error}")

        state = request.args.get("state", "")
        code = request.args.get("code", "")
        if not state or not code:
            return _error_html("В ответе Google нет code или state.")

        user_id = consume_oauth_state(state)
        if user_id is None:
            return _error_html("Ссылка авторизации устарела или уже была использована.")

        try:
            flow = Flow.from_client_config(_client_config(), scopes=SCOPES, state=state)
            flow.redirect_uri = _redirect_uri()
            flow.fetch_token(code=code)
            credentials = flow.credentials
            token_dict = json.loads(credentials.to_json())
            save_google_token(user_id, token_dict)
        except Exception:
            logger.exception("Google OAuth callback failed for user %s", user_id)
            return _error_html("Не удалось обменять код Google на токен.")

        _telegram_notify(
            user_id,
            "Google Calendar подключён. Следующий шаг — выбери часовой пояс: /timezone",
        )
        return _success_html()

    return app


class OAuthServer:
    """Небольшой HTTP-сервер только для OAuth callback и health-check."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self._server = make_server(host, port, create_oauth_web_app(), threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()
        logger.info("OAuth callback server started")

    def stop(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    user = update.effective_user
    ensure_user(user.id, user.full_name)
    status = get_onboarding_status(user.id)

    if not status["google_connected"]:
        if not _oauth_ready():
            await update.message.reply_text(
                "Google Calendar пока не настроен на сервере. Нужны GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET и REDIRECT_URI (или BASE_URL)."
            )
            return
        await update.message.reply_text(
            "Сначала подключи Google Calendar. После авторизации вернись сюда.",
            reply_markup=_auth_keyboard(user.id),
        )
        return

    if not status["timezone_set"]:
        await update.message.reply_text("Google Calendar уже подключён. Теперь выбери часовой пояс.")
        await timezone_command(update, context)
        return

    prefs = status["preferences"]
    await update.message.reply_text(
        "Календарь готов к работе.\n"
        f"Рабочие часы: {prefs['work_start']}–{prefs['work_end']}\n"
        f"Буфер: {prefs['buffer_minutes']} мин\n\n"
        "Проверить или изменить настройки: /calendar_settings"
    )


async def reconnect_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Явно переавторизовать Google Calendar и получить новый refresh token."""
    if not update.message or not update.effective_user:
        return
    if not _oauth_ready():
        await update.message.reply_text("Google OAuth не настроен на сервере.")
        return
    await update.message.reply_text(
        "Открой Google и заново разреши доступ к календарю.",
        reply_markup=_auth_keyboard(update.effective_user.id),
    )
