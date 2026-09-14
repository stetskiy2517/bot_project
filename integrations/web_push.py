"""Standards-based Web Push delivery for iOS Home Screen PWAs and desktop browsers."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush

from config import BASE_URL, DB_PATH, WEB_PUSH_SUBJECT, WEB_PUSH_VAPID_PRIVATE_KEY


def _private_key_path() -> Path:
    configured = str(WEB_PUSH_VAPID_PRIVATE_KEY or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    db_path = Path(DB_PATH).expanduser()
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()
    return db_path.parent / "webpush_vapid_private.pem"


def ensure_vapid_private_key() -> Path:
    """Create one persistent P-256 VAPID key on first use without storing it in git."""
    path = _private_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path

    private_key = ec.generate_private_key(ec.SECP256R1())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(pem)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def get_vapid_public_key() -> str:
    path = ensure_vapid_private_key()
    private_key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise ValueError("VAPID private key is not an EC key")
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _valid_vapid_subject(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme == "https":
        return bool(parsed.hostname and parsed.hostname != "localhost")
    if parsed.scheme == "mailto":
        address = parsed.path.strip()
        if "@" not in address:
            return False
        domain = address.rsplit("@", 1)[-1].lower()
        return bool(domain and domain != "localhost")
    return False


def _vapid_subject() -> str:
    configured = str(WEB_PUSH_SUBJECT or "").strip()
    if configured:
        if not _valid_vapid_subject(configured):
            raise RuntimeError("WEB_PUSH_SUBJECT must be a public https:// URL or mailto: address")
        return configured

    base_url = str(BASE_URL or "").strip()
    if _valid_vapid_subject(base_url):
        parsed = urlparse(base_url)
        return f"https://{parsed.netloc}"

    # Apple rejects placeholder/localhost VAPID subjects with BadJwtToken.
    # Failing loudly is safer than accepting a subscription that can never receive pushes.
    raise RuntimeError("Web Push requires a public BASE_URL or WEB_PUSH_SUBJECT")


def web_push_error_details(exc: WebPushException) -> tuple[int | None, str | None]:
    response = getattr(exc, "response", None)
    status_value = getattr(response, "status_code", None)
    try:
        status = int(status_value) if status_value is not None else None
    except (TypeError, ValueError):
        status = None
    body = str(getattr(response, "text", "") or "").strip()
    return status, body[:500] or None


def send_web_push(subscription: dict, payload: dict):
    subscription_info = {
        "endpoint": subscription["endpoint"],
        "keys": {
            "p256dh": subscription["p256dh"],
            "auth": subscription["auth"],
        },
    }
    return webpush(
        subscription_info=subscription_info,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        vapid_private_key=str(ensure_vapid_private_key()),
        vapid_claims={"sub": _vapid_subject()},
        content_encoding="aes128gcm",
        headers={"Urgency": "high"},
        ttl=86400,
        timeout=10,
    )
