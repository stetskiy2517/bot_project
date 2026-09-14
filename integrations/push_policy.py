"""Validate browser push destinations before storing or contacting them."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import re
import socket
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
import requests

PUSH_HOSTS = frozenset({
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
})
WINDOWS_HOST_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.notify\.windows\.com$")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def validate_push_endpoint(endpoint: str, *, resolve: bool = False) -> str:
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > 4096:
        raise ValueError("Неверный адрес push-сервиса.")
    if any(ord(char) <= 32 or ord(char) >= 127 for char in endpoint) or "\\" in endpoint:
        raise ValueError("Неверный адрес push-сервиса.")
    try:
        parsed = urlsplit(endpoint)
        hostname = parsed.hostname or ""
        valid_port = parsed.port in {None, 443}
    except ValueError as exc:
        raise ValueError("Неверный адрес push-сервиса.") from exc
    if (
        parsed.scheme != "https" or not valid_port or parsed.username is not None
        or parsed.password is not None or parsed.fragment or not parsed.path.startswith("/")
        or not (hostname in PUSH_HOSTS or WINDOWS_HOST_RE.fullmatch(hostname))
    ):
        raise ValueError("Этот push-сервис не поддерживается.")
    if resolve:
        try:
            addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise requests.ConnectionError("Push-сервис временно недоступен.") from exc
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError("Push-сервис вернул недопустимый сетевой адрес.")
    return endpoint


def _decode_key(value: str, expected_length: int) -> bytes:
    if not isinstance(value, str) or len(value) > 128 or not KEY_RE.fullmatch(value):
        raise ValueError("Неверный ключ push-подписки.")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Неверный ключ push-подписки.") from exc
    if len(decoded) != expected_length:
        raise ValueError("Неверный ключ push-подписки.")
    return decoded


def validate_push_keys(p256dh: str, auth: str) -> None:
    point = _decode_key(p256dh, 65)
    _decode_key(auth, 16)
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    except ValueError as exc:
        raise ValueError("Неверный открытый ключ push-подписки.") from exc


class TrustedPushSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.trust_env = False

    def send(self, request, **kwargs):
        validate_push_endpoint(request.url, resolve=True)
        kwargs["allow_redirects"] = False
        return super().send(request, **kwargs)
