"""Restrict Web Push to known providers and pin the validated DNS address."""

from __future__ import annotations

import base64
import ipaddress
import re
import socket
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
import requests
from requests.structures import CaseInsensitiveDict
import urllib3

PROVIDER_HOSTS = {
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
}
WINDOWS_PUSH_HOST_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.notify\.windows\.com$")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


class InvalidPushEndpoint(ValueError):
    pass


def validate_push_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or not 1 <= len(endpoint) <= 4096:
        raise InvalidPushEndpoint("Некорректный адрес Push.")
    if any(ord(char) <= 32 or ord(char) >= 127 for char in endpoint) or "\\" in endpoint:
        raise InvalidPushEndpoint("Некорректный адрес Push.")
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname or ""
        windows_host = bool(WINDOWS_PUSH_HOST_RE.fullmatch(host))
        allowed = host in PROVIDER_HOSTS or windows_host
        has_destination = bool(parsed.path and parsed.path != "/") or (windows_host and bool(parsed.query))
        if (parsed.scheme != "https" or not allowed or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or parsed.fragment or not has_destination):
            raise InvalidPushEndpoint("Этот Push-сервис не поддерживается.")
        if host == "fcm.googleapis.com" and not parsed.path.startswith(("/fcm/send/", "/wp/")):
            raise InvalidPushEndpoint("Некорректный адрес Google Push.")
        if host == "updates.push.services.mozilla.com" and not parsed.path.startswith("/wpush/"):
            raise InvalidPushEndpoint("Некорректный адрес Mozilla Push.")
    except (ValueError, UnicodeError) as exc:
        raise InvalidPushEndpoint("Некорректный адрес Push.") from exc
    return host


def validate_push_keys(p256dh: str, auth: str) -> None:
    try:
        decoded = []
        for value in (p256dh, auth):
            if not isinstance(value, str) or not KEY_RE.fullmatch(value) or len(value) > 128:
                raise ValueError("Invalid key encoding")
            decoded.append(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if len(decoded[0]) != 65 or len(decoded[1]) != 16:
            raise ValueError("Invalid key length")
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decoded[0])
    except (ValueError, TypeError) as exc:
        raise ValueError("Некорректные ключи Push-подписки. Подключи уведомления заново.") from exc


def public_push_addresses(host: str) -> list[str]:
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise requests.ConnectionError("Не удалось определить адрес Push-сервиса.") from exc
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise InvalidPushEndpoint("Push-сервис вернул непубличный сетевой адрес.")
    return addresses


class SafePushTransport:
    def post(self, url: str, *, data, headers: dict, timeout: float):
        host = validate_push_endpoint(url)
        address = public_push_addresses(host)[0]
        parsed = urlsplit(url)
        target = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
        safe_headers = dict(headers)
        safe_headers["Host"] = host
        # The IP is fixed here; TLS still verifies the original provider hostname.
        with urllib3.HTTPSConnectionPool(
            address, port=443, assert_hostname=host, server_hostname=host,
            cert_reqs="CERT_REQUIRED", ca_certs=requests.certs.where(),
        ) as pool:
            raw = pool.urlopen(
                "POST", target, body=data, headers=safe_headers,
                timeout=urllib3.Timeout(connect=5, read=float(timeout)),
                retries=False, redirect=False, preload_content=False,
            )
            try:
                if 300 <= raw.status < 400:
                    raise InvalidPushEndpoint("Перенаправление Push-сервиса запрещено.")
                response = requests.Response()
                response.status_code = raw.status
                response.reason = raw.reason
                response.headers = CaseInsensitiveDict(raw.headers)
                response._content = raw.read(65536)
                response.encoding = "utf-8"
                response.url = url
                return response
            finally:
                raw.release_conn()
