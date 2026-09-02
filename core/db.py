import json
import sqlite3

DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_WORK_START = "09:00"
DEFAULT_WORK_END = "18:00"
DEFAULT_WORK_DAYS = [0, 1, 2, 3, 4]
DEFAULT_BUFFER_MINUTES = 15

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()


def init_db():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        google_token TEXT,
        timezone TEXT,
        work_start TEXT,
        work_end TEXT,
        work_days TEXT,
        buffer_minutes INTEGER
    )
    """)

    cursor.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in cursor.fetchall()}
    migrations = {
        "timezone": "TEXT",
        "work_start": "TEXT",
        "work_end": "TEXT",
        "work_days": "TEXT",
        "buffer_minutes": "INTEGER",
    }
    for column, sql_type in migrations.items():
        if column not in columns:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {column} {sql_type}")
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


def save_calendar_preferences(
    user_id: int,
    *,
    work_start: str | None = None,
    work_end: str | None = None,
    work_days: list[int] | None = None,
    buffer_minutes: int | None = None,
) -> None:
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    updates = []
    values = []
    if work_start is not None:
        updates.append("work_start=?")
        values.append(work_start)
    if work_end is not None:
        updates.append("work_end=?")
        values.append(work_end)
    if work_days is not None:
        updates.append("work_days=?")
        values.append(json.dumps(sorted(set(work_days))))
    if buffer_minutes is not None:
        updates.append("buffer_minutes=?")
        values.append(int(buffer_minutes))
    if not updates:
        return
    values.append(user_id)
    cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id=?", values)
    conn.commit()


def get_calendar_preferences(user_id: int) -> dict:
    cursor.execute(
        "SELECT work_start, work_end, work_days, buffer_minutes FROM users WHERE user_id=?",
        (user_id,),
    )
    row = cursor.fetchone()
    work_start = row[0] if row and row[0] else DEFAULT_WORK_START
    work_end = row[1] if row and row[1] else DEFAULT_WORK_END
    if row and row[2]:
        try:
            work_days = [int(day) for day in json.loads(row[2])]
        except (TypeError, ValueError, json.JSONDecodeError):
            work_days = DEFAULT_WORK_DAYS.copy()
    else:
        work_days = DEFAULT_WORK_DAYS.copy()
    buffer_minutes = row[3] if row and row[3] is not None else DEFAULT_BUFFER_MINUTES
    return {
        "work_start": work_start,
        "work_end": work_end,
        "work_days": work_days,
        "buffer_minutes": int(buffer_minutes),
    }
