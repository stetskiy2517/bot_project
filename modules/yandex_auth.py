"""Optional Yandex ID login for the web application."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from flask import Blueprint, Response, redirect, request, send_from_directory, session

from config import BASE_URL
from core.identity_store import consume_identity_oauth_state, create_identity_oauth_state, get_or_create_identity_user
from core.web_security import csrf_token

logger = logging.getLogger(__name__)
yandex_auth_api = Blueprint("yandex_auth", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"
USERINFO_URL = "https://login.yandex.ru/info"


def _settings() -> tuple[str, str, str]:
    client_id = str(os.environ.get("YANDEX_ID_CLIENT_ID") or "").strip()
    client_secret = str(os.environ.get("YANDEX_ID_CLIENT_SECRET") or "").strip()
    redirect_uri = str(os.environ.get("YANDEX_ID_REDIRECT_URI") or "").strip()
    if not redirect_uri and BASE_URL:
        redirect_uri = BASE_URL.rstrip("/") + "/auth/yandex/callback"
    if not client_id or not client_secret or not redirect_uri:
        raise RuntimeError("Yandex ID OAuth is not configured")
    return client_id, client_secret, redirect_uri


def configured() -> bool:
    try:
        _settings()
        return True
    except RuntimeError:
        return False


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _exchange_code(code: str, verifier: str) -> str:
    client_id, client_secret, redirect_uri = _settings()
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        timeout=(5, 30),
    )
    if response.status_code != 200:
        raise RuntimeError(f"Yandex token exchange failed: HTTP {response.status_code}")
    payload = response.json()
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Yandex token exchange returned no access token")
    return token


def _userinfo(token: str) -> dict:
    response = requests.get(
        USERINFO_URL,
        params={"format": "json"},
        headers={"Authorization": f"OAuth {token}"},
        timeout=(5, 20),
    )
    if response.status_code != 200:
        raise RuntimeError(f"Yandex userinfo failed: HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("id"):
        raise RuntimeError("Yandex userinfo returned no user id")
    return payload


@yandex_auth_api.after_app_request
def yandex_auth_ui_hook(response):
    if request.path == "/" and response.status_code == 200 and response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        script = '<script src="/yandex-auth.js"></script>'
        if script not in html and "</body>" in html:
            response.set_data(html.replace("</body>", f"    {script}\n  </body>", 1))
    return response


@yandex_auth_api.get("/yandex-auth.js")
def yandex_auth_js():
    return send_from_directory(WEB_DIR, "yandex-auth.js", mimetype="application/javascript")


@yandex_auth_api.get("/auth/yandex/start")
def yandex_start():
    try:
        client_id, _client_secret, redirect_uri = _settings()
    except RuntimeError:
        return Response("Вход через Яндекс пока не настроен на сервере.", status=503, mimetype="text/plain")
    verifier, challenge = _pkce_pair()
    state = create_identity_oauth_state("yandex", verifier)
    session["yandex_oauth_binding"] = {
        "digest": hashlib.sha256(state.encode()).hexdigest(),
        "issued_at": time.time(),
    }
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "login:info login:email",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return redirect(AUTHORIZE_URL + "?" + urlencode(params))


@yandex_auth_api.get("/auth/yandex/callback")
def yandex_callback():
    if request.args.get("error"):
        return Response("Яндекс не разрешил вход.", status=400, mimetype="text/plain")
    state = str(request.args.get("state") or "")
    code = str(request.args.get("code") or "")
    binding = session.get("yandex_oauth_binding")
    valid_binding = isinstance(binding, dict) and isinstance(binding.get("issued_at"), (int, float))
    if (
        not valid_binding
        or not state
        or not code
        or not 0 <= time.time() - float(binding["issued_at"]) <= 900
        or not secrets.compare_digest(str(binding.get("digest") or ""), hashlib.sha256(state.encode()).hexdigest())
    ):
        return Response("Ссылка входа устарела или открыта в другом браузере.", status=400, mimetype="text/plain")
    state_data = consume_identity_oauth_state(state, "yandex")
    session.pop("yandex_oauth_binding", None)
    if not state_data or not state_data.get("code_verifier"):
        return Response("Ссылка входа устарела. Начни вход заново.", status=400, mimetype="text/plain")
    try:
        token = _exchange_code(code, str(state_data["code_verifier"]))
        profile = _userinfo(token)
        subject = str(profile["id"])
        email = profile.get("default_email") or profile.get("emails", [None])[0]
        name = profile.get("real_name") or profile.get("display_name") or profile.get("login")
        user_id = get_or_create_identity_user("yandex", subject, email, name)
    except Exception:
        logger.exception("Yandex ID sign-in failed")
        return Response("Не удалось войти через Яндекс. Попробуй ещё раз.", status=400, mimetype="text/plain")
    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["auth_time"] = time.time()
    session["identity_provider"] = "yandex"
    csrf_token()
    return redirect("/?yandex=connected")
