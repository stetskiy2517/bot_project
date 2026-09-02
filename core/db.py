"""Small SQLite repository used by the bot."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
import sqlite3
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


DB_PATH = os.getenv("DB_PATH", "bot.db")
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY")


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                google_token TEXT
            );

            CREATE TABLE IF NOT EXISTS planner_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                due_at TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS planner_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'scheduled',
                delivered_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def save_user_name(user_id: int, name: str) -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO users (user_id, name) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET name=excluded.name
            """,
            (user_id, name),
        )


def save_google_token(user_id: int, token_dict: dict[str, Any]) -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO users (user_id, google_token) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET google_token=excluded.google_token
            """,
            (user_id, _encode_token(token_dict)),
        )


def get_google_token(user_id: int) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT google_token FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
    return _decode_token(row["google_token"]) if row and row["google_token"] else None


def create_task(user_id: int, title: str, due_at: date | datetime | None) -> int:
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO planner_tasks (user_id, title, due_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, title, due_at.isoformat() if due_at else None, _utc_now()),
        )
        return int(cursor.lastrowid)


def create_reminder(user_id: int, chat_id: int, title: str, remind_at: datetime) -> int:
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO planner_reminders
                (user_id, chat_id, title, remind_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, chat_id, title, remind_at.isoformat(), _utc_now()),
        )
        return int(cursor.lastrowid)


def mark_reminder_delivered(reminder_id: int) -> None:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE planner_reminders
            SET status='delivered', delivered_at=?
            WHERE id=?
            """,
            (_utc_now(), reminder_id),
        )


def pending_reminders() -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, user_id, chat_id, title, remind_at
            FROM planner_reminders
            WHERE status='scheduled'
            ORDER BY remind_at
            """
        ).fetchall()
    return [dict(row) for row in rows]


def save_oauth_state(state: str, user_id: int) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO oauth_states (state, user_id, created_at) VALUES (?, ?, ?)",
            (state, user_id, _utc_now()),
        )


def consume_oauth_state(state: str, max_age_minutes: int = 15) -> int | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT user_id, created_at FROM oauth_states WHERE state=?", (state,)
        ).fetchone()
        connection.execute("DELETE FROM oauth_states WHERE state=?", (state,))
    if not row:
        return None
    created = datetime.fromisoformat(row["created_at"])
    age = datetime.now(timezone.utc) - created
    return int(row["user_id"]) if age.total_seconds() <= max_age_minutes * 60 else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode_token(value: dict[str, Any]) -> str:
    serialized = json.dumps(value).encode("utf-8")
    if not TOKEN_ENCRYPTION_KEY:
        # Allows local task-only development and reading legacy databases.
        # Production OAuth configuration is rejected without the key.
        return serialized.decode("utf-8")
    encrypted = Fernet(TOKEN_ENCRYPTION_KEY.encode("utf-8")).encrypt(serialized)
    return "fernet:" + encrypted.decode("ascii")


def _decode_token(value: str) -> dict[str, Any]:
    if not value.startswith("fernet:"):
        return json.loads(value)
    if not TOKEN_ENCRYPTION_KEY:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is required to read Google credentials")
    try:
        payload = Fernet(TOKEN_ENCRYPTION_KEY.encode("utf-8")).decrypt(
            value.removeprefix("fernet:").encode("ascii")
        )
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError("Could not decrypt Google credentials") from exc
    return json.loads(payload)
