import json
import sqlite3

DEFAULT_TIMEZONE = "Europe/Moscow"

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()


def init_db():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        google_token TEXT,
        timezone TEXT
    )
    """)

    # Миграция для уже существующей БД.
    cursor.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in cursor.fetchall()}
    if "timezone" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN timezone TEXT")

    conn.commit()


def save_google_token(user_id, token_dict):
    cursor.execute("""
    INSERT INTO users (user_id, google_token)
    VALUES (?, ?)
    ON CONFLICT(user_id) DO UPDATE SET google_token=excluded.google_token
    """, (user_id, json.dumps(token_dict)))
    conn.commit()


def get_google_token(user_id):
    cursor.execute("SELECT google_token FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return json.loads(row[0]) if row and row[0] else None


def save_user_timezone(user_id: int, timezone: str) -> None:
    cursor.execute("""
    INSERT INTO users (user_id, timezone)
    VALUES (?, ?)
    ON CONFLICT(user_id) DO UPDATE SET timezone=excluded.timezone
    """, (user_id, timezone))
    conn.commit()


def get_user_timezone(user_id: int, default: str | None = DEFAULT_TIMEZONE) -> str | None:
    cursor.execute("SELECT timezone FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row and row[0]:
        return row[0]
    return default
