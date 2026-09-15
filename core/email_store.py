from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from config import EMAIL_CREDENTIALS_KEY, WEB_SESSION_SECRET
from core.db import conn, db_lock

PROVIDERS = {
    "yandex": {"label": "Яндекс", "imap_host": "imap.yandex.ru", "imap_port": 993},
    "mailru": {"label": "Mail.ru", "imap_host": "imap.mail.ru", "imap_port": 993},
    "gmail": {"label": "Gmail", "imap_host": None, "imap_port": None},
}


def _fernet() -> Fernet:
    material = (EMAIL_CREDENTIALS_KEY or WEB_SESSION_SECRET or "").encode("utf-8")
    if len(material) < 16:
        raise RuntimeError("Email credential encryption is not configured")
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(key)


def _encrypt(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def _decrypt(value: str) -> dict:
    try:
        raw = _fernet().decrypt(value.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Email credentials cannot be decrypted") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid email credential payload")
    return payload


def init_email_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS email_accounts (
                account_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                email TEXT NOT NULL,
                display_name TEXT,
                credential_encrypted TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id,provider,email)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS email_oauth_states (
                state TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_email_accounts_user ON email_accounts(user_id,enabled)")
        conn.commit()


def save_email_account(user_id: int, provider: str, email: str, credentials: dict, *, display_name: str | None = None) -> dict:
    provider = str(provider or "").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError("Неподдерживаемый почтовый провайдер")
    email = str(email or "").strip()
    if "@" not in email or len(email) > 320:
        raise ValueError("Некорректный email")
    if not isinstance(credentials, dict) or not credentials:
        raise ValueError("Нет данных для подключения почты")
    now = datetime.now(timezone.utc).isoformat()
    encrypted = _encrypt(credentials)
    with db_lock:
        conn.execute(
            """INSERT INTO email_accounts (user_id,provider,email,display_name,credential_encrypted,enabled,created_at,updated_at)
               VALUES (?,?,?,?,?,1,?,?)
               ON CONFLICT(user_id,provider,email) DO UPDATE SET
                 display_name=excluded.display_name,
                 credential_encrypted=excluded.credential_encrypted,
                 enabled=1,
                 updated_at=excluded.updated_at""",
            (int(user_id), provider, email, display_name, encrypted, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT account_id,user_id,provider,email,display_name,enabled,created_at,updated_at FROM email_accounts WHERE user_id=? AND provider=? AND email=?",
            (int(user_id), provider, email),
        ).fetchone()
    return _account_from_row(row)


def _account_from_row(row) -> dict:
    return {
        "account_id": int(row[0]),
        "user_id": int(row[1]),
        "provider": row[2],
        "email": row[3],
        "display_name": row[4],
        "enabled": bool(row[5]),
        "created_at": row[6],
        "updated_at": row[7],
    }


def list_email_accounts(user_id: int) -> list[dict]:
    with db_lock:
        rows = conn.execute(
            "SELECT account_id,user_id,provider,email,display_name,enabled,created_at,updated_at FROM email_accounts WHERE user_id=? ORDER BY account_id",
            (int(user_id),),
        ).fetchall()
    return [_account_from_row(row) for row in rows]


def get_email_account(user_id: int, account_id: int, *, with_credentials: bool = False) -> dict | None:
    with db_lock:
        row = conn.execute(
            "SELECT account_id,user_id,provider,email,display_name,enabled,created_at,updated_at,credential_encrypted FROM email_accounts WHERE user_id=? AND account_id=?",
            (int(user_id), int(account_id)),
        ).fetchone()
    if not row:
        return None
    account = _account_from_row(row[:8])
    if with_credentials:
        account["credentials"] = _decrypt(row[8])
    return account


def delete_email_account(user_id: int, account_id: int) -> bool:
    with db_lock:
        cur = conn.execute("DELETE FROM email_accounts WHERE user_id=? AND account_id=?", (int(user_id), int(account_id)))
        conn.commit()
    return cur.rowcount > 0


def save_email_oauth_state(state: str, user_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        conn.execute("DELETE FROM email_oauth_states WHERE user_id=?", (int(user_id),))
        conn.execute("INSERT OR REPLACE INTO email_oauth_states (state,user_id,created_at) VALUES (?,?,?)", (state, int(user_id), now))
        conn.commit()


def consume_email_oauth_state(state: str, *, max_age_seconds: int = 900) -> int | None:
    with db_lock:
        row = conn.execute("SELECT user_id,created_at FROM email_oauth_states WHERE state=?", (state,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM email_oauth_states WHERE state=?", (state,))
        conn.commit()
    try:
        created = datetime.fromisoformat(row[1])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - created).total_seconds()
    except (TypeError, ValueError):
        return None
    if age < 0 or age > max_age_seconds:
        return None
    return int(row[0])


init_email_store()
