"""Standards-based Web Push delivery for iOS Home Screen PWAs and desktop browsers."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import webpush

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


def _vapid_subject() -> str:
    configured = str(WEB_PUSH_SUBJECT or "").strip()
    if configured:
        return configured
    base_url = str(BASE_URL or "").strip()
    if base_url.startswith("https://"):
        return base_url
    return "mailto:personal-secretary@localhost"


def send_web_push(subscription: dict, payload: dict) -> None:
    subscription_info = {
        "endpoint": subscription["endpoint"],
        "keys": {
            "p256dh": subscription["p256dh"],
            "auth": subscription["auth"],
        },
    }
    webpush(
        subscription_info=subscription_info,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        vapid_private_key=str(ensure_vapid_private_key()),
        vapid_claims={"sub": _vapid_subject()},
        ttl=86400,
        timeout=10,
    )
