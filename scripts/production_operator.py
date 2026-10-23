"""Allowlisted encrypted operations over the existing GitHub Actions -> production SSH link.

The private key never leaves the VM. No shell commands or raw credentials are accepted.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_DIR = Path.home() / ".local" / "share" / "personal-secretary" / "operator"
KEY_PATH = KEY_DIR / "private.pem"
REQUEST_ID_RE = re.compile(r"^[a-z0-9_-]{8,64}$")


def _private_key():
    KEY_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    KEY_DIR.chmod(0o700)
    if not KEY_PATH.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        content = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        try:
            fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
    if (KEY_PATH.stat().st_mode & 0o077) != 0:
        raise PermissionError("operator private key permissions are unsafe")
    return serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)


def bootstrap() -> None:
    key = _private_key()
    public_der = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    print("VM_OPERATOR_READY")
    # Public RSA encryption key. This value is not a secret.
    print("VM_OPERATOR_PUBLIC_KEY=" + base64.b64encode(public_der).decode("ascii"))


def _decode(value: object, *, limit: int) -> bytes:
    if not isinstance(value, str) or len(value) > limit * 2:
        raise ValueError("invalid encrypted envelope")
    result = base64.b64decode(value, validate=True)
    if len(result) > limit:
        raise ValueError("oversized encrypted field")
    return result


def decrypt_request(path: Path) -> tuple[str, dict]:
    if not path.is_file() or path.stat().st_size > 16000:
        raise ValueError("invalid operator request file")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict) or set(envelope) != {
        "version", "request_id", "wrapped_key", "nonce", "ciphertext"
    } or envelope["version"] != 1:
        raise ValueError("invalid operator request schema")
    request_id = envelope["request_id"]
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        raise ValueError("invalid operator request id")
    wrapped_key = _decode(envelope["wrapped_key"], limit=512)
    nonce = _decode(envelope["nonce"], limit=12)
    ciphertext = _decode(envelope["ciphertext"], limit=8192)
    if len(nonce) != 12:
        raise ValueError("invalid nonce")
    secret = _private_key().decrypt(
        wrapped_key, padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(), label=None,
        ),
    )
    if len(secret) != 32:
        raise ValueError("invalid symmetric key")
    clear = AESGCM(secret).decrypt(nonce, ciphertext, request_id.encode("ascii"))
    payload = json.loads(clear)
    if not isinstance(payload, dict) or payload.get("request_id") != request_id:
        raise ValueError("mismatched operator request")
    return request_id, payload


def _resolve_dasha(expected_name: str) -> int:
    from core.db import conn, db_lock
    aliases = ("даша", "дарья", "dasha", "daria", "darya")
    expected = " ".join(str(expected_name or "").strip().casefold().split())
    if not expected or not any(expected == a or expected.startswith(a + " ") for a in aliases):
        raise ValueError("unrecognized recipient")
    with db_lock:
        rows = conn.execute("SELECT user_id,name FROM google_accounts").fetchall()
    matches = [
        int(user_id) for user_id, name in rows
        if " ".join(str(name or "").strip().casefold().split()) == expected
    ]
    if len(matches) != 1:
        raise ValueError("recipient is missing or ambiguous")
    return matches[0]


def _event_for_dasha(request_id: str, payload: dict) -> str:
    if set(payload) != {
        "request_id", "action", "target", "recipient_name", "date", "start", "duration_minutes", "title"
    } or payload["target"] != "dasha":
        raise ValueError("unsupported calendar operation")
    title = payload["title"]
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("invalid event title")
    duration = payload["duration_minutes"]
    if type(duration) is not int or not 5 <= duration <= 720:
        raise ValueError("invalid duration")
    local_date = payload["date"]
    start_time = payload["start"]
    if not isinstance(local_date, str) or not re.fullmatch(r"\d{4}-\d\d-\d\d", local_date):
        raise ValueError("invalid calendar date")
    if not isinstance(start_time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start_time):
        raise ValueError("invalid calendar time")
    user_id = _resolve_dasha(payload["recipient_name"])
    from core.db import get_user_timezone, get_category_colors
    from integrations.google_calendar_service import build_google_calendar_service
    from modules.calendar import _build_event, _create_event
    from googleapiclient.errors import HttpError

    user_tz = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    zone = ZoneInfo(user_tz)
    start = datetime.fromisoformat(local_date + "T" + start_time).replace(tzinfo=zone)
    end = start + timedelta(minutes=duration)
    now = datetime.now(zone)
    if start.date() < now.date() - timedelta(days=1) or start.date() > now.date() + timedelta(days=31):
        raise ValueError("calendar date outside allowed window")
    if end <= now:
        raise ValueError("calendar event has already finished")
    if end.date() != start.date():
        raise ValueError("cross-day event not allowed")
    digest = hashlib.sha256(("vm-operator:" + request_id + ":" + str(user_id)).encode()).hexdigest()
    event_id = "spop" + digest
    marker = digest[:40]
    service = build_google_calendar_service(user_id)
    try:
        existing = service.events().get(calendarId="primary", eventId=event_id).execute()
    except HttpError as exc:
        if getattr(exc.resp, "status", None) != 404:
            raise
    else:
        private = ((existing.get("extendedProperties") or {}).get("private") or {})
        if private.get("smartPlannerOperatorRequest") != marker or existing.get("status") == "cancelled":
            raise RuntimeError("existing event id does not match operator request")
        return "already_exists"

    colors = get_category_colors(user_id)
    event = _build_event(title, start, end, colors, user_id=user_id)
    event["id"] = event_id
    event["summary"] = title.strip()
    event["description"] = "AI Smart Planner category: rest"
    event["start"]["timeZone"] = user_tz
    event["end"]["timeZone"] = user_tz
    event.setdefault("extendedProperties", {}).setdefault("private", {})["smartPlannerOperatorRequest"] = marker
    if colors.get("rest"):
        event["colorId"] = colors["rest"]
    else:
        event.pop("colorId", None)
    try:
        _create_event(user_id, event)
    except HttpError as exc:
        if getattr(exc.resp, "status", None) != 409:
            raise
        existing = service.events().get(calendarId="primary", eventId=event_id).execute()
        private = ((existing.get("extendedProperties") or {}).get("private") or {})
        if private.get("smartPlannerOperatorRequest") != marker:
            raise RuntimeError("conflicting provider event")
        return "already_exists"
    return "created"


def run_request(path: Path) -> None:
    request_id, payload = decrypt_request(path)
    action = payload.get("action")
    if action == "health" and set(payload) == {"request_id", "action"}:
        from core.db import conn, db_lock
        with db_lock:
            if conn.execute("SELECT 1").fetchone()[0] != 1:
                raise RuntimeError("database unavailable")
        result = "ok"
    elif action == "calendar_create":
        result = _event_for_dasha(request_id, payload)
    else:
        raise ValueError("operation not allowlisted")
    print("VM_OPERATOR_RESULT=" + json.dumps(
        {"request_id": request_id, "action": action, "result": result},
        ensure_ascii=False, separators=(",", ":"),
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bootstrap", action="store_true")
    group.add_argument("--request", type=Path)
    args = parser.parse_args()
    try:
        if args.bootstrap:
            bootstrap()
        else:
            run_request(args.request)
    except Exception as exc:
        # Do not print email addresses, credentials, message contents or decrypted payloads.
        print("VM_OPERATOR_FAILED=" + type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
