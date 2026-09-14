"""Request replay protection and short-lived conversation state."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

from config import DB_PATH
from core.db import conn, db_lock

REQUEST_TTL_SECONDS = 86400
DIALOGUE_TTL_SECONDS = 1800
REQUEST_KEY_RE = re.compile(r"^([0-9]{13})\.([a-f0-9]{32})$")
current_operation: ContextVar[dict | None] = ContextVar("current_operation", default=None)
_thread_state = threading.local()


class UserBusyError(RuntimeError):
    pass


def init_operation_store() -> None:
    with db_lock:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS command_requests (
                user_id INTEGER NOT NULL,
                request_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                response_json TEXT,
                http_status INTEGER,
                effect_json TEXT,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY(user_id, request_key)
            );
            CREATE INDEX IF NOT EXISTS idx_command_requests_expiry ON command_requests(expires_at);
            CREATE TABLE IF NOT EXISTS conversation_states (
                user_id INTEGER PRIMARY KEY,
                state_json TEXT NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS web_sessions (
                session_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_web_sessions_user ON web_sessions(user_id);
        """)
        conn.commit()


@contextmanager
def user_operation(user_id: int, *, timeout: float = 2.0):
    """Serialize a user's operations across threads/processes on the same host."""
    identity = (str(Path(DB_PATH).resolve()), int(user_id))
    held = getattr(_thread_state, "held", None)
    if held is None:
        held = _thread_state.held = {}
    if identity in held:
        yield
        return

    directory = Path(DB_PATH).resolve().parent / ".user-locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    filename = hashlib.sha256(str(user_id).encode()).hexdigest() + ".lock"
    fd = os.open(directory / filename, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    acquired = False
    deadline = time.monotonic() + max(0, timeout)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise UserBusyError("Предыдущая операция ещё выполняется.")
                time.sleep(0.025)
        held[identity] = fd
        yield
    finally:
        if acquired:
            held.pop(identity, None)
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def validate_request_key(value: str, *, now: float | None = None) -> float:
    match = REQUEST_KEY_RE.fullmatch(value or "")
    if not match:
        raise ValueError("Нужен ключ запроса. Обнови страницу приложения.")
    created = int(match.group(1)) / 1000
    current = time.time() if now is None else now
    if created > current + 300 or current - created > REQUEST_TTL_SECONDS:
        raise ValueError("Срок безопасного повтора истёк. Сначала проверь результат в приложении.")
    return created


def find_request(user_id: int, key: str) -> dict | None:
    with db_lock:
        row = conn.execute(
            "SELECT fingerprint,status,response_json,http_status,effect_json "
            "FROM command_requests WHERE user_id=? AND request_key=?",
            (int(user_id), key),
        ).fetchone()
    if not row:
        return None
    return {
        "fingerprint": row[0], "status": row[1],
        "response": json.loads(row[2]) if row[2] else None, "http_status": row[3],
        "effect": json.loads(row[4]) if row[4] else None,
    }


def begin_request(user_id: int, key: str, fingerprint: str) -> None:
    created = validate_request_key(key)
    with db_lock:
        conn.execute("DELETE FROM command_requests WHERE expires_at<?", (time.time(),))
        conn.execute(
            "INSERT INTO command_requests "
            "(user_id,request_key,fingerprint,status,created_at,expires_at) VALUES (?,?,?,?,?,?)",
            (int(user_id), key, fingerprint, "running", created, created + REQUEST_TTL_SECONDS + 300),
        )
        conn.commit()


def finish_request(user_id: int, key: str, response: dict, http_status: int) -> None:
    with db_lock:
        conn.execute(
            "UPDATE command_requests SET response_json=?,http_status=?,status=? "
            "WHERE user_id=? AND request_key=?",
            (json.dumps(response, ensure_ascii=False), http_status,
             "done" if http_status < 500 else "uncertain", int(user_id), key),
        )
        conn.commit()


def remember_calendar_effect(user_id: int, event: dict) -> dict:
    operation = current_operation.get()
    if not operation or operation["user_id"] != int(user_id):
        return event
    # A request may create a primary event and a managed travel event.
    operation["effect_index"] = operation.get("effect_index", 0) + 1
    if operation["effect_index"] != 1:
        return event
    event_id = hashlib.sha256(f"{user_id}:{operation['key']}:calendar".encode()).hexdigest()
    result = dict(event)
    result["id"] = event_id
    properties = dict(result.get("extendedProperties") or {})
    private = dict(properties.get("private") or {})
    private["secretaryRequest"] = hashlib.sha256(operation["key"].encode()).hexdigest()
    properties["private"] = private
    result["extendedProperties"] = properties
    with db_lock:
        conn.execute(
            "UPDATE command_requests SET effect_json=? WHERE user_id=? AND request_key=?",
            (json.dumps({"kind": "calendar_create", "event_id": event_id, "event": result,
                         "confirmed": False}, ensure_ascii=False), int(user_id), operation["key"]),
        )
        conn.commit()
    return result


def confirm_calendar_effect(user_id: int, event: dict) -> None:
    operation = current_operation.get()
    if not operation or operation["user_id"] != int(user_id):
        return
    item = find_request(user_id, operation["key"])
    effect = item.get("effect") if item else None
    if not effect or effect["event_id"] != event.get("id"):
        return
    effect["confirmed"] = True
    effect["event"] = event
    with db_lock:
        conn.execute(
            "UPDATE command_requests SET effect_json=? WHERE user_id=? AND request_key=?",
            (json.dumps(effect, ensure_ascii=False), int(user_id), operation["key"]),
        )
        conn.commit()


def _json_default(value):
    if isinstance(value, datetime):
        return {"__secretary_datetime__": value.isoformat()}
    if isinstance(value, timedelta):
        return {"__secretary_timedelta__": value.total_seconds()}
    raise TypeError(f"Unsupported conversation value: {type(value).__name__}")


def _json_object(value):
    if set(value) == {"__secretary_datetime__"}:
        return datetime.fromisoformat(value["__secretary_datetime__"])
    if set(value) == {"__secretary_timedelta__"}:
        return timedelta(seconds=value["__secretary_timedelta__"])
    return value


def load_conversation(user_id: int) -> dict:
    with db_lock:
        row = conn.execute(
            "SELECT state_json,expires_at FROM conversation_states WHERE user_id=?", (int(user_id),),
        ).fetchone()
    if not row or row[1] <= time.time():
        return {}
    return json.loads(row[0], object_hook=_json_object)


def save_conversation(user_id: int, state: dict) -> None:
    payload = json.dumps(state, ensure_ascii=False, default=_json_default)
    if len(payload.encode()) > 262144:
        raise ValueError("Conversation state is too large")
    with db_lock:
        conn.execute("DELETE FROM conversation_states WHERE expires_at<?", (time.time(),))
        if state:
            conn.execute(
                "INSERT INTO conversation_states(user_id,state_json,expires_at) VALUES (?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET state_json=excluded.state_json,expires_at=excluded.expires_at",
                (int(user_id), payload, time.time() + DIALOGUE_TTL_SECONDS),
            )
        else:
            conn.execute("DELETE FROM conversation_states WHERE user_id=?", (int(user_id),))
        conn.commit()


def session_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_web_session(user_id: int, token: str) -> None:
    with db_lock:
        conn.execute("DELETE FROM web_sessions WHERE expires_at<?", (time.time(),))
        conn.execute(
            "INSERT INTO web_sessions(session_hash,user_id,expires_at) VALUES (?,?,?)",
            (session_hash(token), int(user_id), time.time() + 90 * 86400),
        )
        conn.commit()


def valid_web_session(user_id: int, token: str) -> bool:
    with db_lock:
        now = time.time()
        row = conn.execute(
            "UPDATE web_sessions SET expires_at=? "
            "WHERE session_hash=? AND user_id=? AND expires_at>? RETURNING session_hash",
            (now + 90 * 86400, session_hash(token), int(user_id), now),
        ).fetchone()
        conn.commit()
    return row is not None


def revoke_web_session(token: str) -> None:
    with db_lock:
        conn.execute("DELETE FROM web_sessions WHERE session_hash=?", (session_hash(token),))
        conn.commit()
