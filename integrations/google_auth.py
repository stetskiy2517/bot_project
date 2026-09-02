"""Google OAuth flow for the polling bot."""

from __future__ import annotations

import logging
import secrets

from aiohttp import web
from google_auth_oauthlib.flow import Flow
from telegram import Update
from telegram.ext import ContextTypes

from config import (
    BASE_URL,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    OAUTH_SERVER_HOST,
    OAUTH_SERVER_PORT,
    REDIRECT_URI,
    SCOPES,
)
from core.db import consume_oauth_state, get_google_token, save_google_token, save_oauth_state


logger = logging.getLogger(__name__)


def oauth_enabled() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and (REDIRECT_URI or BASE_URL))


def redirect_uri() -> str:
    if REDIRECT_URI:
        return REDIRECT_URI
    if not BASE_URL:
        raise RuntimeError("REDIRECT_URI or BASE_URL is required for Google OAuth")
    return f"{BASE_URL.rstrip('/')}/auth/callback"


def _flow(state: str | None = None) -> Flow:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("Google OAuth credentials are not configured")
    return Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=redirect_uri(),
        state=state,
    )


def create_auth_url(user_id: int) -> str:
    state = secrets.token_urlsafe(32)
    save_oauth_state(state, user_id)
    url, _ = _flow(state).authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return url


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if get_google_token(update.effective_user.id):
        await update.message.reply_text(
            "Planner готов. Напишите, например: «Завтра в 15 встреча с Иваном»."
        )
        return
    if not oauth_enabled():
        await update.message.reply_text(
            "Planner запущен, но Google Calendar ещё не настроен на сервере. "
            "Задачи и Telegram-напоминания уже доступны."
        )
        return
    await update.message.reply_text(
        "Подключите Google Calendar по ссылке:\n" + create_auth_url(update.effective_user.id)
    )


async def oauth_callback(request: web.Request) -> web.Response:
    state = request.query.get("state", "")
    user_id = consume_oauth_state(state)
    if user_id is None:
        return web.Response(text="Ссылка устарела или уже использована.", status=400)
    try:
        flow = _flow(state)
        authorization_response = f"{redirect_uri()}?{request.query_string}"
        flow.fetch_token(authorization_response=authorization_response)
        save_google_token(user_id, _credentials_dict(flow.credentials))
    except Exception:
        logger.exception("Google OAuth callback failed for user %s", user_id)
        return web.Response(text="Не удалось подключить Google Calendar.", status=500)
    return web.Response(text="Google Calendar подключён. Можно вернуться в Telegram.")


async def health_check(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "ai-smart-planner"})


async def start_http_server() -> web.AppRunner:
    application = web.Application()
    application.router.add_get("/", health_check)
    application.router.add_get("/health", health_check)
    if oauth_enabled():
        application.router.add_get("/auth/callback", oauth_callback)
    else:
        logger.warning("Google OAuth callback is disabled: configuration is incomplete")
    runner = web.AppRunner(application)
    await runner.setup()
    await web.TCPSite(runner, OAUTH_SERVER_HOST, OAUTH_SERVER_PORT).start()
    logger.info("HTTP server started on port %s", OAUTH_SERVER_PORT)
    return runner


def _credentials_dict(credentials) -> dict:
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }
