"""Session, CSRF and durable request guards for Flask endpoints."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from urllib.parse import urlsplit

from flask import g, jsonify, request, session

from config import BASE_URL
from core.command_store import (
    UserBusyError, begin_request, enter_request, finish_request, leave_request,
    load_conversation, request_effects, save_conversation, set_phase, user_operation,
)
from core.db import get_google_account

logger = logging.getLogger(__name__)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
UNKNOWN_MESSAGE = (
    "Ответ потерялся. Повторное выполнение остановлено, чтобы не создать дубль. "
    "Проверь календарь или сохранённое перед новой командой."
)


def csrf_token() -> str:
    if not isinstance(session.get("csrf_token"), str):
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    return parsed.scheme, parsed.hostname or "", parsed.port or (443 if parsed.scheme == "https" else 80)


def _same_origin_request() -> bool:
    """Return True only when browser metadata proves this request came from our own origin.

    This is intentionally narrower than same-site. It exists only as a rolling-upgrade
    compatibility path for tabs that were opened before the CSRF/request-id frontend
    shipped. New clients still use the explicit headers below.
    """
    fetch_site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if fetch_site == "cross-site":
        return False

    origin = request.headers.get("Origin")
    if origin:
        try:
            return _origin(origin) == _origin(BASE_URL or request.host_url)
        except ValueError:
            return False

    return fetch_site == "same-origin"


def _legacy_request_id() -> str:
    return f"{int(time.time() * 1000)}-{secrets.token_hex(16)}"


def _fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(f"{request.method}:{request.path}:".encode())
    if request.path == "/api/voice":
        audio = request.files.get("audio")
        if audio:
            digest.update((audio.mimetype or "").encode())
            while chunk := audio.stream.read(65536):
                digest.update(chunk)
            audio.stream.seek(0)
        digest.update(json.dumps(sorted(request.form.items()), ensure_ascii=False).encode())
    else:
        data = request.get_json(silent=True)
        if isinstance(data, dict):
            digest.update(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
        else:
            digest.update(request.get_data(cache=True))
    return digest.hexdigest()


def mark_executing() -> None:
    key = getattr(g, "command_key", None)
    if key:
        set_phase(*key, "executing")
        g.command_executing = True


def _recover_calendar(user_id: int, request_id: str) -> dict | None:
    effects = request_effects(user_id, request_id)
    if len(effects) != 1 or effects[0]["kind"] != "calendar_create":
        return None
    from modules.calendar_user import _get_calendar_service

    effect = effects[0]
    try:
        event = _get_calendar_service(user_id).events().get(
            calendarId="primary", eventId=effect["provider_id"],
        ).execute()
    except Exception as exc:
        logger.warning("Calendar recovery failed for user %s (%s)", user_id, type(exc).__name__)
        return None
    expected = effect["payload"].get("extendedProperties", {}).get("private", {}).get("smartPlannerRequest")
    actual = event.get("extendedProperties", {}).get("private", {}).get("smartPlannerRequest")
    if not expected or actual != expected or event.get("status") == "cancelled":
        return None
    when = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date", "")
    return {
        "handled": True, "recovered": True, "request_id": request_id,
        "replies": [f"Запись уже есть в календаре: «{event.get('summary', 'Событие')}», {when}. Дубль не создавал."],
    }


def install_web_security(app, states: dict[int, dict]) -> None:
    @app.before_request
    def guard_web_request():
        if request.path == "/api/google/login":
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                return jsonify(error="cross_site_request"), 403
            return None
        if not request.path.startswith("/api/") or request.path == "/api/health":
            return None
        user_id = session.get("user_id")
        if not isinstance(user_id, int) or isinstance(user_id, bool) or not get_google_account(user_id):
            session.clear()
            return jsonify(error="unauthorized"), 401
        if request.path == "/api/status":
            csrf_token()
        writes = request.method not in SAFE_METHODS
        if not writes and request.path != "/api/reminders/due":
            return None

        legacy_client = False
        if writes:
            expected = session.get("csrf_token")
            provided = request.headers.get("X-CSRF-Token", "")
            request_id_header = request.headers.get("X-Request-ID", "")
            csrf_ok = bool(expected and provided and secrets.compare_digest(str(expected), provided))

            # Rolling-upgrade compatibility: an already-open old tab has neither
            # security header. Permit it only when browser metadata proves exact
            # same-origin. Any partially upgraded/malformed request still fails.
            legacy_client = not provided and not request_id_header and _same_origin_request()
            if not csrf_ok and not legacy_client:
                return jsonify(error="csrf_failed", message="Обнови страницу и повтори действие."), 403

            origin = request.headers.get("Origin")
            try:
                invalid_origin = origin and _origin(origin) != _origin(BASE_URL or request.host_url)
            except ValueError:
                invalid_origin = True
            if invalid_origin or request.headers.get("Sec-Fetch-Site") == "cross-site":
                return jsonify(error="cross_site_request"), 403
            g.legacy_client = legacy_client

        operation = user_operation(user_id, blocking=False)
        try:
            operation.__enter__()
        except UserBusyError:
            return jsonify(error="user_busy", message="Предыдущая команда ещё обрабатывается."), 409
        g.user_operation = operation
        # Recheck after acquiring the lock: an account could have been erased while waiting.
        if not get_google_account(user_id):
            session.clear()
            return jsonify(error="unauthorized"), 401
        states[user_id] = load_conversation(user_id)
        if not writes:
            return None

        request_id = request.headers.get("X-Request-ID", "")
        if not request_id:
            if legacy_client:
                request_id = _legacy_request_id()
            else:
                return jsonify(error="request_id_required", message="Обнови страницу приложения."), 428
        try:
            previous = begin_request(user_id, request_id, _fingerprint())
        except ValueError as exc:
            code = str(exc)
            return jsonify(error=code), (410 if code == "request_expired" else 409 if code == "request_id_conflict" else 400)
        if previous:
            if previous["phase"] == "done":
                response = jsonify(previous["response"])
                response.status_code = previous["status"]
                response.headers["X-Request-Replayed"] = "true"
                return response
            recovered = _recover_calendar(user_id, request_id)
            if recovered:
                finish_request(user_id, request_id, recovered, 200)
                return jsonify(recovered)
            return jsonify(error="outcome_unknown", request_id=request_id, message=UNKNOWN_MESSAGE), 409
        g.command_key = (user_id, request_id)
        g.command_tokens = enter_request(user_id, request_id)
        g.command_executing = request.path != "/api/voice"
        set_phase(user_id, request_id, "executing" if g.command_executing else "transcribing")
        return None

    @app.after_request
    def finish_web_request(response):
        if request.path.startswith("/api/") or request.path == "/oauth2callback":
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if getattr(g, "legacy_client", False):
            response.headers["X-Legacy-Client"] = "true"
            response.headers["X-Client-Upgrade"] = "reload"
        key = getattr(g, "command_key", None)
        if not key:
            return response
        user_id, request_id = key
        if not get_google_account(user_id):
            states.pop(user_id, None)
            return response
        data = response.get_json(silent=True)
        if not isinstance(data, dict):
            data = {"error": "request_failed"}
        phase = "done"
        effects = request_effects(user_id, request_id)
        if (response.status_code >= 500 and g.command_executing and request.path in {"/api/chat", "/api/voice"}) or any(not item["completed"] for item in effects):
            phase = "uncertain"
            data = {"error": "outcome_unknown", "message": UNKNOWN_MESSAGE}
            response = jsonify(data)
            response.status_code = 409
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            if getattr(g, "legacy_client", False):
                response.headers["X-Legacy-Client"] = "true"
                response.headers["X-Client-Upgrade"] = "reload"
        elif response.status_code >= 500 and not g.command_executing:
            phase = "retryable"
        if request.path != "/api/logout":
            save_conversation(user_id, states.get(user_id, {}))
        data["request_id"] = request_id
        finish_request(user_id, request_id, data, response.status_code, phase=phase)
        response.set_data(app.json.dumps(data))
        response.mimetype = "application/json"
        return response

    @app.teardown_request
    def release_web_request(_error):
        tokens = getattr(g, "command_tokens", None)
        if tokens:
            leave_request(tokens)
            g.command_tokens = None
        operation = getattr(g, "user_operation", None)
        if operation:
            operation.__exit__(None, None, None)
            g.user_operation = None
