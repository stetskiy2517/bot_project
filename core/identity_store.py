"""Provider-neutral application identities.

`google_accounts` remains as a legacy compatibility profile table for the current web shell.
Authentication identity is stored here and is no longer conceptually tied to Google Calendar.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets

from core.db import conn, db_lock

STATE_TTL = timedelta(minutes=15)


def init_identity_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS identity_accounts (
                provider TEXT NOT NULL,
                subject TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                email TEXT,
                name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(provider,subject),
                UNIQUE(provider,user_id)
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_identity_user ON identity_accounts(user_id)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS identity_oauth_states (
                state TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                code_verifier TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        # Backfill existing Google identities once, preserving current numeric user ids.
        rows = conn.execute("SELECT user_id,google_sub,email,name,created_at FROM google_accounts").fetchall()
        now = datetime.now(timezone.utc).isoformat()
        for user_id, subject, email, name, created_at in rows:
            if str(subject or "").startswith("identity:"):
                continue
            conn.execute(
                "INSERT OR IGNORE INTO identity_accounts(provider,subject,user_id,email,name,created_at,updated_at) "
                "VALUES ('google',?,?,?,?,?,?)",
                (str(subject), int(user_id), email, name, created_at or now, now),
            )
        conn.commit()


def _clean(value: object, limit: int) -> str | None:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit] if text else None


def get_identity_account(user_id: int) -> dict | None:
    init_identity_store()
    with db_lock:
        row = conn.execute(
            "SELECT provider,subject,user_id,email,name,created_at,updated_at "
            "FROM identity_accounts WHERE user_id=? ORDER BY CASE provider WHEN 'google' THEN 0 ELSE 1 END LIMIT 1",
            (int(user_id),),
        ).fetchone()
    if not row:
        return None
    return dict(zip(("provider", "subject", "user_id", "email", "name", "created_at", "updated_at"), row))


def get_or_create_identity_user(provider: str, subject: object, email: object = None, name: object = None) -> int:
    init_identity_store()
    provider = str(provider or "").strip().lower()
    subject_value = _clean(subject, 300)
    email_value = _clean(email, 320)
    name_value = _clean(name, 300)
    if provider not in {"google", "yandex"} or not subject_value:
        raise ValueError("Некорректная учётная запись входа")
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT user_id FROM identity_accounts WHERE provider=? AND subject=?",
                (provider, subject_value),
            ).fetchone()
            if row:
                user_id = int(row[0])
                conn.execute(
                    "UPDATE identity_accounts SET email=?,name=?,updated_at=? WHERE provider=? AND subject=?",
                    (email_value, name_value, now, provider, subject_value),
                )
            else:
                max_user = conn.execute("SELECT COALESCE(MAX(user_id),0) FROM users").fetchone()[0]
                max_profile = conn.execute("SELECT COALESCE(MAX(user_id),0) FROM google_accounts").fetchone()[0]
                user_id = max(int(max_user or 0), int(max_profile or 0)) + 1
                if user_id <= 0 or user_id >= 2**63:
                    raise RuntimeError("Не удалось выделить идентификатор пользователя")
                conn.execute(
                    "INSERT INTO identity_accounts(provider,subject,user_id,email,name,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (provider, subject_value, user_id, email_value, name_value, now, now),
                )
                conn.execute("INSERT OR IGNORE INTO users(user_id,name) VALUES (?,?)", (user_id, name_value))
            if name_value:
                conn.execute("UPDATE users SET name=? WHERE user_id=?", (name_value, user_id))
            # Transitional profile compatibility for existing UI/privacy code. This does NOT create a Google token.
            legacy_subject = subject_value if provider == "google" else f"identity:{provider}:{subject_value}"
            existing = conn.execute("SELECT google_sub FROM google_accounts WHERE user_id=?", (user_id,)).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO google_accounts(user_id,google_sub,email,name,created_at) VALUES (?,?,?,?,?)",
                    (user_id, legacy_subject, email_value or f"{provider}-{user_id}@local.invalid", name_value, now),
                )
            else:
                conn.execute(
                    "UPDATE google_accounts SET email=?,name=? WHERE user_id=?",
                    (email_value or f"{provider}-{user_id}@local.invalid", name_value, user_id),
                )
            conn.commit()
            return user_id
        except Exception:
            conn.rollback()
            raise


def create_identity_oauth_state(provider: str, code_verifier: str | None = None) -> str:
    init_identity_store()
    state = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        conn.execute("DELETE FROM identity_oauth_states WHERE created_at<?", ((datetime.now(timezone.utc) - STATE_TTL).isoformat(),))
        conn.execute(
            "INSERT INTO identity_oauth_states(state,provider,code_verifier,created_at) VALUES (?,?,?,?)",
            (state, str(provider), code_verifier, now),
        )
        conn.commit()
    return state


def consume_identity_oauth_state(state: str, provider: str) -> dict | None:
    init_identity_store()
    with db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT provider,code_verifier,created_at FROM identity_oauth_states WHERE state=?",
                (str(state),),
            ).fetchone()
            conn.execute("DELETE FROM identity_oauth_states WHERE state=?", (str(state),))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if not row or row[0] != provider:
        return None
    try:
        created = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    age = datetime.now(timezone.utc) - created
    if age < timedelta(0) or age > STATE_TTL:
        return None
    return {"provider": row[0], "code_verifier": row[1], "created_at": row[2]}


init_identity_store()
