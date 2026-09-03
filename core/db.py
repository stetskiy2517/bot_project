import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from werkzeug.security import check_password_hash, generate_password_hash

DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_WORK_START = "09:00"
DEFAULT_WORK_END = "18:00"
DEFAULT_WORK_DAYS = [0, 1, 2, 3, 4]
DEFAULT_BUFFER_MINUTES = 15
OAUTH_STATE_TTL_MINUTES = 15

conn = sqlite3.connect("bot.db", check_same_thread=False)
db_lock = threading.RLock()


def init_db():
    with db_lock:
        conn.execute("""
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
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        migrations = {"timezone": "TEXT", "work_start": "TEXT", "work_end": "TEXT", "work_days": "TEXT", "buffer_minutes": "INTEGER"}
        for column, sql_type in migrations.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column} {sql_type}")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_states (
            state TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS web_accounts (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)
        conn.commit()


def ensure_user(user_id: int, name: str | None = None) -> None:
    with db_lock:
        conn.execute("INSERT OR IGNORE INTO users (user_id, name) VALUES (?, ?)", (user_id, name))
        if name:
            conn.execute("UPDATE users SET name=? WHERE user_id=?", (name, user_id))
        conn.commit()


def create_web_account(email: str, password: str, name: str) -> int:
    email = email.strip().lower()
    name = name.strip()
    if not email or "@" not in email:
        raise ValueError("Укажите корректный email")
    if len(password) < 8:
        raise ValueError("Пароль должен содержать минимум 8 символов")
    if not name:
        raise ValueError("Укажите имя")
    with db_lock:
        try:
            cursor = conn.execute(
                "INSERT INTO web_accounts (email, password_hash, name, created_at) VALUES (?, ?, ?, ?)",
                (email, generate_password_hash(password), name, datetime.now(timezone.utc).isoformat()),
            )
            user_id = int(cursor.lastrowid)
            conn.execute("INSERT OR IGNORE INTO users (user_id, name) VALUES (?, ?)", (user_id, name))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("Пользователь с таким email уже существует") from exc
    return user_id


def authenticate_web_account(email: str, password: str) -> dict | None:
    with db_lock:
        row = conn.execute(
            "SELECT user_id, email, password_hash, name FROM web_accounts WHERE email=? COLLATE NOCASE",
            (email.strip(),),
        ).fetchone()
    if not row or not check_password_hash(row[2], password):
        return None
    return {"user_id": int(row[0]), "email": row[1], "name": row[3]}


def get_web_account(user_id: int) -> dict | None:
    with db_lock:
        row = conn.execute("SELECT user_id, email, name FROM web_accounts WHERE user_id=?", (user_id,)).fetchone()
    return {"user_id": int(row[0]), "email": row[1], "name": row[2]} if row else None


def save_google_token(user_id, token_dict):
    with db_lock:
        conn.execute("INSERT INTO users (user_id, google_token) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET google_token=excluded.google_token", (user_id, json.dumps(token_dict)))
        conn.commit()


def get_google_token(user_id):
    with db_lock:
        row = conn.execute("SELECT google_token FROM users WHERE user_id=?", (user_id,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def clear_google_token(user_id: int) -> None:
    with db_lock:
        conn.execute("UPDATE users SET google_token=NULL WHERE user_id=?", (user_id,))
        conn.commit()


def save_oauth_state(state: str, user_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        conn.execute("DELETE FROM oauth_states WHERE user_id=?", (user_id,))
        conn.execute("INSERT OR REPLACE INTO oauth_states (state, user_id, created_at) VALUES (?, ?, ?)", (state, user_id, now))
        conn.commit()


def consume_oauth_state(state: str) -> int | None:
    with db_lock:
        row = conn.execute("SELECT user_id, created_at FROM oauth_states WHERE state=?", (state,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM oauth_states WHERE state=?", (state,))
        conn.commit()
    user_id, created_at = int(row[0]), row[1]
    try:
        created = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created > timedelta(minutes=OAUTH_STATE_TTL_MINUTES):
        return None
    return user_id


def save_user_timezone(user_id: int, timezone: str) -> None:
    with db_lock:
        conn.execute("INSERT INTO users (user_id, timezone) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET timezone=excluded.timezone", (user_id, timezone))
        conn.commit()


def get_user_timezone(user_id: int, default: str | None = DEFAULT_TIMEZONE) -> str | None:
    with db_lock:
        row = conn.execute("SELECT timezone FROM users WHERE user_id=?", (user_id,)).fetchone()
    return row[0] if row and row[0] else default


def save_calendar_preferences(user_id: int, *, work_start: str | None = None, work_end: str | None = None, work_days: list[int] | None = None, buffer_minutes: int | None = None) -> None:
    with db_lock:
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        updates, values = [], []
        if work_start is not None: updates.append("work_start=?"); values.append(work_start)
        if work_end is not None: updates.append("work_end=?"); values.append(work_end)
        if work_days is not None: updates.append("work_days=?"); values.append(json.dumps(sorted(set(work_days))))
        if buffer_minutes is not None: updates.append("buffer_minutes=?"); values.append(int(buffer_minutes))
        if not updates: return
        values.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id=?", values)
        conn.commit()


def get_calendar_preferences(user_id: int) -> dict:
    with db_lock:
        row = conn.execute("SELECT work_start, work_end, work_days, buffer_minutes FROM users WHERE user_id=?", (user_id,)).fetchone()
    work_start = row[0] if row and row[0] else DEFAULT_WORK_START
    work_end = row[1] if row and row[1] else DEFAULT_WORK_END
    if row and row[2]:
        try: work_days = [int(day) for day in json.loads(row[2])]
        except (TypeError, ValueError, json.JSONDecodeError): work_days = DEFAULT_WORK_DAYS.copy()
    else: work_days = DEFAULT_WORK_DAYS.copy()
    buffer_minutes = row[3] if row and row[3] is not None else DEFAULT_BUFFER_MINUTES
    return {"work_start": work_start, "work_end": work_end, "work_days": work_days, "buffer_minutes": int(buffer_minutes)}


def get_onboarding_status(user_id: int) -> dict:
    return {"google_connected": get_google_token(user_id) is not None, "timezone_set": get_user_timezone(user_id, default=None) is not None, "preferences": get_calendar_preferences(user_id)}


init_db()
