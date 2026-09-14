"""Authenticated browser requests for endpoint tests; security tests use raw clients."""

import base64
import hashlib
import secrets
import time
import uuid
from unittest.mock import patch
from flask.testing import FlaskClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import web_app


def request_id():
    return f"{int(time.time() * 1000)}-{uuid.uuid4().hex}"


class BrowserClient(FlaskClient):
    def open(self, *args, **kwargs):
        method = str(kwargs.get("method", "GET")).upper()
        if method not in {"GET", "HEAD", "OPTIONS"}:
            headers = dict(kwargs.get("headers") or {})
            with self.session_transaction() as stored:
                stored.setdefault("csrf_token", secrets.token_urlsafe(32))
                headers.setdefault("X-CSRF-Token", stored["csrf_token"])
            headers.setdefault("X-Request-ID", request_id())
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


def web_test_app():
    with patch.object(web_app, "WEB_SESSION_SECRET", "test-only-secret-for-browser-client-32chars"):
        app = web_app.create_web_app()
    app.test_client_class = BrowserClient
    return app


def bind_oauth(client, state="test-state"):
    with client.session_transaction() as stored:
        stored["oauth_binding"] = {
            "digest": hashlib.sha256(state.encode()).hexdigest(), "issued_at": time.time(),
        }


def push_keys():
    key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
    )
    return {
        "p256dh": base64.urlsafe_b64encode(key).rstrip(b"=").decode(),
        "auth": base64.urlsafe_b64encode(b"0123456789abcdef").rstrip(b"=").decode(),
    }
