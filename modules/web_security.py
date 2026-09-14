"""Browser-bound sign-in, CSRF validation and replay-safe API requests."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from flask import g, jsonify, request, session

from core.user_operations import (
    UserBusyError, begin_request, confirm_calendar_effect, create_web_session,
    current_operation, find_request, finish_request, revoke_web_session,
    user_operation, valid_web_session, validate_request_key,
)

logger = logging.getLogger(__name__)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PUBLIC_API_PATHS = {"/api/health", "/api/google/login"}


def csrf_token() -> str:
    if not session.get("csrf_token"):
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def bind_oauth_url(url: str) -> None:
    state = parse_qs(urlsplit(url).query).get("state", [""])[0]
    if not state:
        raise ValueError("OAuth URL has no state")
    session["oauth_browser"] = {
        "state_hash": hashlib.sha256(state.encode()).hexdigest(), "created_at": time.time(),
    }


def verify_oauth_callback(state: str) -> None:
    binding = session.get("oauth_browser")
    if not isinstance(binding, dict) or not state:
        raise ValueError("Начни вход в этом браузере заново.")
    expected = binding.get("state_hash")
    actual = hashlib.sha256(state.encode()).hexdigest()
    age = time.time() - float(binding.get("created_at", 0))
    if (not isinstance(expected, str) or not hmac.compare_digest(expected, actual)
            or not 0 <= age <= 900):
        raise ValueError("Ссылка входа устарела или открыта в другом браузере.")
    session.pop("oauth_browser", None)


def establish_session(user_id: int) -> None:
    session.clear()
    session.permanent = True
    session["user_id"] = int(user_id)
    session["sid"] = secrets.token_urlsafe(32)
    create_web_session(user_id, session["sid"])
    csrf_token()


def close_session() -> None:
    token = session.get("sid")
    if isinstance(token, str):
        revoke_web_session(token)
    session.clear()


def _fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(f"{request.method}:{request.path}\n".encode())
    if request.mimetype == "multipart/form-data":
        digest.update(json.dumps(sorted(request.form.lists()), ensure_ascii=False).encode())
        for name in sorted(request.files):
            for upload in request.files.getlist(name):
                digest.update(json.dumps([name, upload.filename, upload.mimetype]).encode())
                upload.stream.seek(0)
                while chunk := upload.stream.read(65536):
                    digest.update(chunk)
                upload.stream.seek(0)
    else:
        payload = request.get_json(silent=True)
        digest.update(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
    return digest.hexdigest()


def _same_origin(origin: str, expected: str) -> bool:
    try:
        left, right = urlsplit(origin), urlsplit(expected)
        return (
            left.scheme in {"http", "https"}
            and (left.scheme, left.hostname, left.port or (443 if left.scheme == "https" else 80))
            == (right.scheme, right.hostname, right.port or (443 if right.scheme == "https" else 80))
            and not left.username and not left.password
        )
    except ValueError:
        return False


def _unknown_response() -> dict:
    return {
        "error": "request_outcome_unknown",
        "message": "Результат пока не подтверждён. Повтор не выполнит команду заново. Проверь календарь или сохранённое.",
        "replies": ["Результат пока не подтверждён. Повтор не выполнит команду заново. Проверь календарь или сохранённое."],
        "check_only": True,
    }


def _recover_calendar(user_id: int, key: str, saved: dict):
    effect = saved.get("effect") or {}
    if effect.get("kind") != "calendar_create":
        return _unknown_response(), 409
    try:
        from modules.calendar_user import _get_calendar_service

        event = _get_calendar_service(user_id).events().get(
            calendarId="primary", eventId=effect["event_id"],
        ).execute()
        marker = ((event.get("extendedProperties") or {}).get("private") or {}).get("secretaryRequest")
        expected = hashlib.sha256(key.encode()).hexdigest()
        if event.get("status") == "cancelled" or marker != expected:
            return _unknown_response(), 409
        operation_token = current_operation.set({"user_id": user_id, "key": key})
        try:
            confirm_calendar_effect(user_id, event)
        finally:
            current_operation.reset(operation_token)
        body = {"handled": True, "recovered": True,
                "replies": [f"Событие «{event.get('summary') or 'Без названия'}» уже сохранено. Повторную запись не создавал."]}
        finish_request(user_id, key, body, 200)
        return body, 200
    except Exception:
        logger.warning("Calendar outcome reconciliation failed for user %s", user_id)
        return _unknown_response(), 409


def install_web_security(app, *, account_lookup, public_url: str | None = None) -> None:
    @app.before_request
    def guard_request():
        if not request.path.startswith("/api/") or request.path in PUBLIC_API_PATHS:
            return None
        value = session.get("user_id")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return jsonify({"error": "unauthorized"}), 401
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify({"error": "cross_site_request"}), 403

        guard = user_operation(value)
        try:
            guard.__enter__()
        except UserBusyError:
            return jsonify({"error": "user_busy", "message": "Предыдущая команда ещё выполняется. Повтори позже."}), 409
        g.operation_guard = guard
        if not account_lookup(value):
            close_session()
            return jsonify({"error": "unauthorized"}), 401

        sid = session.get("sid")
        if not isinstance(sid, str) or not valid_web_session(value, sid):
            session.clear()
            return jsonify({"error": "unauthorized"}), 401
        csrf_token()

        mutates = request.method in UNSAFE_METHODS or request.path == "/api/reminders/due"
        if not mutates:
            return None
        origin = request.headers.get("Origin")
        if origin and not _same_origin(origin, public_url or request.host_url):
            return jsonify({"error": "invalid_origin"}), 403
        supplied = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(csrf_token(), supplied):
            return jsonify({"error": "invalid_csrf", "message": "Обнови страницу и повтори действие."}), 403

        if request.method not in UNSAFE_METHODS or request.path in {"/api/logout", "/api/privacy/delete-ticket"}:
            return None
        key = request.headers.get("Idempotency-Key", "")
        try:
            validate_request_key(key)
        except ValueError as exc:
            return jsonify({"error": "invalid_request_key", "message": str(exc)}), 400
        fingerprint = _fingerprint()
        saved = find_request(value, key)
        if saved:
            if saved["fingerprint"] != fingerprint:
                return jsonify({"error": "request_key_conflict", "message": "Этот ключ уже использован для другой команды."}), 409
            effect = saved.get("effect")
            if effect and not effect.get("confirmed"):
                body, status = _recover_calendar(value, key, saved)
            elif saved["status"] == "done":
                body, status = saved["response"], saved["http_status"]
            else:
                body, status = _unknown_response(), 409
            response = jsonify(body)
            response.status_code = status
            response.headers["Idempotency-Replayed"] = "true"
            return response
        begin_request(value, key, fingerprint)
        g.request_identity = (value, key)
        g.operation_token = current_operation.set({"user_id": value, "key": key, "effect_index": 0})
        return None

    @app.after_request
    def finish_guarded_request(response):
        if request.path.startswith("/api/") or request.path == "/oauth2callback":
            response.headers["Cache-Control"] = "no-store, private"
            response.vary.add("Cookie")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        identity = getattr(g, "request_identity", None)
        if identity:
            user_id, key = identity
            saved = find_request(user_id, key)
            if saved:
                effect = saved.get("effect")
                if effect and not effect.get("confirmed"):
                    response = jsonify(_unknown_response())
                    response.status_code = 409
                    response.headers.update({
                        "Cache-Control": "no-store, private", "Vary": "Cookie",
                        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                        "X-Frame-Options": "DENY",
                    })
                body = response.get_json(silent=True)
                if not isinstance(body, dict):
                    body = {"error": "request_failed", "message": "Не удалось подтвердить результат операции."}
                finish_request(user_id, key, body, response.status_code)
        return response

    @app.teardown_request
    def release_guard(_error):
        token = g.pop("operation_token", None)
        if token is not None:
            current_operation.reset(token)
        guard = g.pop("operation_guard", None)
        if guard is not None:
            guard.__exit__(None, None, None)
