from __future__ import annotations

from datetime import datetime
import hashlib
import json
import logging
import re
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from flask import Blueprint, jsonify, redirect, request, send_from_directory, session
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from config import BASE_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
from core.chat_context import append_chat_exchange
from core.email_auto_store import email_auto_enabled, email_auto_status, set_email_auto_enabled
from core.email_store import (
    PROVIDERS,
    consume_email_oauth_state,
    delete_email_account,
    list_email_accounts,
    save_email_account,
    save_email_oauth_state,
)
from core.feature_access import has_ai_access
from integrations.email_gmail import GMAIL_SCOPE
from integrations.email_imap import EmailAuthenticationError, EmailTransportError, test_connection
from modules.email import answer_email_query, detect_email_intent
from modules.email_actions import build_email_plan
from modules.email_actions_api import apply_high_confidence_attachment_actions
from modules.email_auto import start_email_auto_worker

logger = logging.getLogger(__name__)
email_api = Blueprint("email", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
GMAIL_SCOPES = [GMAIL_SCOPE]

EMAIL_PLAN_REQUEST_RE = re.compile(
    r"\b(?:разбер\w*|проанализ\w*|анализ\w*|что\s+важн\w*|что\s+учесть|"
    r"проверь\s+(?:мою\s+)?почт\w*|почт\w*\s+(?:для\s+)?планирован\w*)\b",
    re.IGNORECASE,
)


def _user() -> int:
    return int(session["user_id"])


def _redirect_uri() -> str:
    if BASE_URL:
        return BASE_URL.rstrip("/") + "/api/email/google/callback"
    raise RuntimeError("Public BASE_URL is required for Gmail OAuth")


def _client_config() -> dict:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("Google OAuth is not configured")
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [_redirect_uri()],
        }
    }


def _is_email_plan_request(text: str) -> bool:
    clean = " ".join(str(text or "").split()).strip()
    if not clean or not detect_email_intent(clean):
        return False
    return bool(EMAIL_PLAN_REQUEST_RE.search(clean))


def _format_plan_time(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        return parsed.strftime("%d.%m.%Y, %H:%M")
    return parsed.strftime("%d.%m.%Y, %H:%M %z")


def _format_email_plan_for_chat(plan: dict) -> str:
    actions = [item for item in (plan.get("actions") or []) if isinstance(item, dict)]
    attachment_actions = [
        item for item in actions if isinstance(item.get("attachment_event"), dict)
    ]
    attachment_info = plan.get("attachment_analysis") if isinstance(plan.get("attachment_analysis"), dict) else {}
    detected = int(attachment_info.get("detected") or 0)
    supported = int(attachment_info.get("supported_found") or 0)
    analyzed = int(attachment_info.get("analyzed") or 0)
    warnings = [str(item).strip() for item in (attachment_info.get("warnings") or []) if str(item).strip()]

    if attachment_actions:
        lines = [
            "Почта → вложения",
            f"Вложения: найдено {detected} · поддерживается {supported} · разобрано {analyzed}",
        ]
        for index, action in enumerate(attachment_actions, 1):
            event = action["attachment_event"]
            source = action.get("source") if isinstance(action.get("source"), dict) else {}
            title = str(event.get("title") or action.get("title") or "Событие из вложения").strip()
            block = [f"{index}. {title}"]
            attachment_name = str(source.get("attachment") or "").strip()
            subject = str(source.get("subject") or "").strip()
            if attachment_name:
                block.append(f"Вложение: {attachment_name}")
            if subject:
                block.append(f"Письмо: {subject}")
            start = _format_plan_time(event.get("start") or action.get("due_at"))
            end = _format_plan_time(event.get("end"))
            if start and end:
                block.append(f"Время: {start} → {end}")
            elif start:
                block.append(f"Время: {start}")
            start_location = str(event.get("start_location") or "").strip()
            end_location = str(event.get("end_location") or "").strip()
            location = str(event.get("location") or "").strip()
            if start_location and end_location:
                block.append(f"Маршрут: {start_location} → {end_location}")
            elif location:
                block.append(f"Место: {location}")
            try:
                confidence = float(action.get("confidence") or event.get("confidence") or 0)
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence:
                block.append(f"Уверенность: {round(max(0.0, min(1.0, confidence)) * 100)}%")
            if action.get("auto_created"):
                block.append("✓ Добавлено в календарь автоматически.")
            elif action.get("already_in_calendar"):
                block.append("✓ Уже есть в календаре.")
            elif action.get("auto_create_error"):
                block.append("⚠ Автодобавление не сработало — можно добавить кнопкой ниже.")
            elif action.get("ready") is False or event.get("ready") is False:
                block.append("⚠ Нужна проверка данных перед добавлением в календарь.")
            for warning in action.get("warnings") or []:
                clean = str(warning).strip()
                if clean:
                    block.append(f"⚠ {clean}")
            lines.append("\n".join(block))
        for warning in warnings:
            lines.append(f"⚠ {warning}")
        auto = plan.get("auto_calendar") if isinstance(plan.get("auto_calendar"), dict) else {}
        if int(auto.get("created") or 0):
            lines.append("Транспортные билеты с уверенностью 99%+ добавляю автоматически. Для остальных событий ниже доступны кнопки.")
        else:
            lines.append("Для событий, которые не прошли порог автодобавления, используй кнопку «Добавить в календарь».")
        return "\n\n".join(lines)

    lines = ["Почта → планирование"]
    summary = str(plan.get("summary") or "").strip()
    if summary:
        lines.append(summary)
    if attachment_info.get("enabled"):
        lines.append(
            f"Вложения: найдено {detected} · поддерживается {supported} · разобрано {analyzed}"
        )
    for warning in warnings:
        lines.append(f"⚠ {warning}")
    for index, action in enumerate(actions[:5], 1):
        title = str(action.get("title") or "Действие").strip()
        when = _format_plan_time(action.get("due_at"))
        line = f"{index}. {title}"
        if when:
            line += f" · {when}"
        lines.append(line)
    return "\n\n".join(lines)


def answer_email_chat_request_details(user_id: int, text: str) -> tuple[str, dict | None]:
    if _is_email_plan_request(text):
        plan = build_email_plan(user_id, text, include_attachments=True)
        apply_high_confidence_attachment_actions(user_id, plan)
        return _format_email_plan_for_chat(plan), plan
    return answer_email_query(user_id, text), None


def answer_email_chat_request(user_id: int, text: str) -> str:
    return answer_email_chat_request_details(user_id, text)[0]


def build_gmail_authorization_url(user_id: int) -> str:
    flow = Flow.from_client_config(_client_config(), scopes=GMAIL_SCOPES)
    flow.redirect_uri = _redirect_uri()
    url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    save_email_oauth_state(state, user_id)
    return url


def complete_gmail_authorization(state: str, code: str) -> int | None:
    user_id = consume_email_oauth_state(state)
    if user_id is None:
        return None
    flow = Flow.from_client_config(_client_config(), scopes=GMAIL_SCOPES, state=state)
    flow.redirect_uri = _redirect_uri()
    flow.fetch_token(code=code)
    credentials = flow.credentials
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    address = str(profile.get("emailAddress") or "").strip()
    if not address:
        raise ValueError("Google не вернул адрес Gmail")
    save_email_account(
        user_id,
        "gmail",
        address,
        {"token": json.loads(credentials.to_json())},
        display_name="Gmail",
    )
    return user_id


@email_api.after_app_request
def email_response_hooks(response):
    if request.path == "/" and response.status_code == 200 and response.mimetype == "text/html":
        html = response.get_data(as_text=True)
        script = '<script src="/email.js"></script>'
        if script not in html and "</body>" in html:
            response.set_data(html.replace("</body>", f"    {script}\n  </body>", 1))
        return response

    if (
        request.path in {"/api/chat", "/api/voice"}
        and response.status_code == 200
        and response.mimetype == "application/json"
        and session.get("user_id")
    ):
        payload = response.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get("handled") is not False:
            return response
        if request.path == "/api/chat":
            incoming = request.get_json(silent=True) or {}
            text = incoming.get("message") if isinstance(incoming, dict) else None
        else:
            text = payload.get("transcript")
        if isinstance(text, str) and detect_email_intent(text):
            user_id = _user()
            try:
                answer, plan = answer_email_chat_request_details(user_id, text)
                payload["handled"] = True
                payload["replies"] = [answer]
                if plan is not None:
                    payload["email_plan"] = plan
                append_chat_exchange(user_id, text, answer)
                response.set_data(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            except Exception:
                logger.exception("Email query failed for user %s", user_id)
                payload["handled"] = True
                payload["replies"] = ["Не удалось прочитать почту. Проверь подключение ящика в настройках."]
                response.set_data(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return response


@email_api.get("/email.js")
def email_js():
    return send_from_directory(WEB_DIR, "email.js", mimetype="application/javascript")


@email_api.get("/api/email/accounts")
def accounts():
    return {
        "accounts": list_email_accounts(_user()),
        "providers": [
            {"id": key, "label": value["label"], "oauth": key == "gmail"}
            for key, value in PROVIDERS.items()
        ],
        "read_only": True,
    }


@email_api.get("/api/email/auto")
def automatic_email_status():
    return email_auto_status(_user())


@email_api.post("/api/email/auto")
def automatic_email_settings():
    payload = request.get_json(silent=True) or {}
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify(error="invalid_email_auto_setting", message="enabled должен быть true или false."), 400
    user_id = _user()
    if enabled:
        if not has_ai_access(user_id):
            return jsonify(error="ai_access_required", message="Для автоматического разбора почты нужен доступ к ИИ."), 403
        if not any(account.get("enabled") for account in list_email_accounts(user_id)):
            return jsonify(error="email_account_required", message="Сначала подключи почтовый ящик."), 400
    set_email_auto_enabled(user_id, enabled)
    status = email_auto_status(user_id)
    status["note"] = (
        "Авторазбор включён. Первый фоновый проход создаёт baseline без ИИ; дальше анализируются только новые подходящие письма."
        if enabled
        else "Авторазбор выключен."
    )
    return status


@email_api.post("/api/email/accounts/imap")
def connect_imap():
    payload = request.get_json(silent=True) or {}
    provider = str(payload.get("provider") or "").strip().lower()
    if provider not in {"yandex", "mailru"}:
        return jsonify(error="unsupported_email_provider", message="Сейчас через IMAP поддерживаются Яндекс и Mail.ru."), 400
    address = str(payload.get("email") or "").strip()
    password = str(payload.get("app_password") or "").strip()
    if "@" not in address or not password or len(password) > 512:
        return jsonify(error="invalid_email_credentials", message="Укажи email и пароль приложения."), 400
    try:
        test_connection(provider, address, password)
        account = save_email_account(
            _user(),
            provider,
            address,
            {"app_password": password},
            display_name=PROVIDERS[provider]["label"],
        )
    except EmailAuthenticationError as exc:
        logger.warning("Email authentication rejected for provider=%s user=%s", provider, _user())
        return jsonify(error="email_authentication_failed", message=str(exc)), 400
    except EmailTransportError as exc:
        logger.warning("Email server unavailable for provider=%s user=%s", provider, _user())
        return jsonify(error="email_server_unavailable", message=str(exc)), 503
    except Exception:
        logger.exception("Failed to connect %s email for user %s", provider, _user())
        return jsonify(error="email_connection_failed", message="Не удалось подключить почту. Попробуй ещё раз позже."), 400
    return {"ok": True, "account": account}


@email_api.get("/api/email/google/connect")
def connect_gmail():
    try:
        url = build_gmail_authorization_url(_user())
        states = parse_qs(urlsplit(url).query).get("state", [])
        if len(states) != 1 or not states[0]:
            raise RuntimeError("Missing OAuth state")
        session["email_oauth_binding"] = {
            "digest": hashlib.sha256(states[0].encode()).hexdigest(),
            "issued_at": time.time(),
        }
        return {"url": url}
    except Exception:
        logger.exception("Failed to build Gmail OAuth URL")
        return jsonify(error="gmail_oauth_unavailable", message="Подключение Gmail временно недоступно."), 503


@email_api.get("/api/email/google/callback")
def gmail_callback():
    if request.args.get("error"):
        return redirect("/?email=error")
    state = str(request.args.get("state") or "")
    code = str(request.args.get("code") or "")
    binding = session.get("email_oauth_binding")
    valid = (
        isinstance(binding, dict)
        and isinstance(binding.get("issued_at"), (int, float))
        and 0 <= time.time() - binding["issued_at"] <= 900
        and state
        and code
        and secrets.compare_digest(str(binding.get("digest", "")), hashlib.sha256(state.encode()).hexdigest())
    )
    if not valid:
        return redirect("/?email=stale")
    session.pop("email_oauth_binding", None)
    try:
        user_id = complete_gmail_authorization(state, code)
        if user_id is None or user_id != _user():
            raise ValueError("OAuth account mismatch")
    except Exception:
        logger.exception("Gmail OAuth callback failed")
        return redirect("/?email=error")
    return redirect("/?email=connected")


@email_api.delete("/api/email/accounts/<int:account_id>")
def disconnect(account_id: int):
    if not delete_email_account(_user(), account_id):
        return jsonify(error="email_account_not_found"), 404
    return {"ok": True}


start_email_auto_worker()
