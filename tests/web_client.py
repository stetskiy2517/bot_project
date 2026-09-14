"""Authenticated browser fixtures. Negative security tests use raw FlaskClient."""
from __future__ import annotations

import base64
import hashlib
import secrets
import time

from flask.testing import FlaskClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from core.user_operations import create_web_session


def request_key():
    return f"{int(time.time() * 1000)}.{secrets.token_hex(16)}"


def set_test_session(stored, user_id):
    stored["user_id"] = int(user_id)
    stored["sid"] = secrets.token_urlsafe(32)
    stored["csrf_token"] = secrets.token_urlsafe(32)
    stored.permanent = True
    create_web_session(user_id, stored["sid"])


def bind_test_oauth(client, state="test-state"):
    with client.session_transaction() as stored:
        stored["oauth_browser"] = {
            "state_hash": hashlib.sha256(state.encode()).hexdigest(),
            "created_at": time.time(),
        }


def push_keys():
    public = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
    )
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    return {"p256dh": encode(public), "auth": encode(b"0123456789abcdef")}


class CsrfClient(FlaskClient):
    def open(self, *args, **kwargs):
        path = str(args[0]) if args else kwargs.get("path", "")
        method = kwargs.get("method", "GET").upper()
        if path.startswith("/api/") and (
            method in {"POST", "PUT", "PATCH", "DELETE"} or path == "/api/reminders/due"
        ):
            with self.session_transaction() as stored:
                token = stored.get("csrf_token")
            if token:
                headers = dict(kwargs.get("headers") or {})
                headers.setdefault("X-CSRF-Token", token)
                headers.setdefault("Idempotency-Key", request_key())
                kwargs["headers"] = headers
        return super().open(*args, **kwargs)


def create_test_app():
    import web_app
    app = web_app.create_web_app()
    app.test_client_class = CsrfClient
    return app
