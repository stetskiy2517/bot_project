"""Durable request receipts and per-user serialization for the web transport."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time

from config import DB_PATH
from core.db import conn, db_lock

logger = logging.getLogger(__name__)
REQUEST_TTL_SECONDS = 86400
CONTEXT_TTL_SECONDS = 1800
KEY_RE = re.compile(r"^(\d{13})-([0-9a-f]{32})$")
_current_request: ContextVar[tuple[int, str] | None] = ContextVar("web_request", default=None)
_effect_number: ContextVar[int] = ContextVar("web_effect_number", default=0)
_held_locks = threading.local()


class UserBusyError(Exception):
    pass


def init_command_store() -> None:
    with db_lock:
        conn.execute("""CREATE TABLE IF NOT EXISTS command_requests (
            user_id INTEGER NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            phase TEXT NOT NULL, response_json TEXT, http_status INTEGER,
            created_at REAL NOT NULL, PRIMARY KEY(user_id,request_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS command_effects (
            user_id INTEGER NOT NULL, request_id TEXT NOT NULL, sequence INTEGER NOT NULL,
            kind TEXT NOT NULL, provider_id TEXT NOT NULL, payload_json TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id,request_id,sequence)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS conversation_state (
            user_id INTEGER PRIMARY KEY, state_json TEXT NOT NULL, expires_at REAL NOT NULL
        )""")
        conn.commit()


@contextmanager
def user_operation(user_id: int, *, blocking: bool = True):
    """One operation per user across threads and local server processes."""
    user_id = int(user_id)
    if getattr(_held_locks, "pid", None) != os.getpid():
        _held_locks.users = set()
        _held_locks.pid = os.getpid()
    held = getattr(_held_locks, "users", None)
    if held is None:
        held = _held_locks.users = set()
    if user_id in held:
        yield
        return
    directory = Path(DB_PATH).resolve().parent / ".user-locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(directory / f"{user_id}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise UserBusyError from exc
        acquired = True
        held.add(user_id)
        yield
    finally:
        if acquired:
            held.remove(user_id)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def validate_request_id(value: str, *, now: float | None = None) -> None:
    match = KEY_RE.fullmatch(value)
    if not match:
        raise ValueError("invalid_request_id")
    current = time.time() if now is None else now
    created = int(match.group(1)) / 1000
    if current - created > REQUEST_TTL_SECONDS:
        raise ValueError("request_expired")
    if created > current + 300:
        raise ValueError("invalid_request_id")


def begin_request(user_id: int, request_id: str, fingerprint: str) -> dict | None:
    validate_request_id(request_id)
    now = time.time()
    with db_lock:
        cutoff = now - REQUEST_TTL_SECONDS - 300
        conn.execute(
            "DELETE FROM command_effects WHERE EXISTS (SELECT 1 FROM command_requests r "
            "WHERE r.user_id=command_effects.user_id AND r.request_id=command_effects.request_id "
            "AND r.created_at<?)", (cutoff,),
        )
        conn.execute("DELETE FROM command_requests WHERE created_at<?", (cutoff,))
        row = conn.execute(
            "SELECT fingerprint,phase,response_json,http_status FROM command_requests "
            "WHERE user_id=? AND request_id=?", (user_id, request_id),
        ).fetchone()
        if row and row[0] != fingerprint:
            conn.commit()
            raise ValueError("request_id_conflict")
        if row and row[1] not in {"received", "transcribing", "retryable"}:
            conn.commit()
            return {
                "phase": row[1],
                "response": json.loads(row[2]) if row[2] else None,
                "status": row[3],
            }
        conn.execute(
            "INSERT INTO command_requests(user_id,request_id,fingerprint,phase,created_at) "
            "VALUES (?,?,?,'received',?) ON CONFLICT(user_id,request_id) "
            "DO UPDATE SET phase='received',response_json=NULL,http_status=NULL",
            (user_id, request_id, fingerprint, now),
        )
        conn.commit()
    return None


def set_phase(user_id: int, request_id: str, phase: str) -> None:
    if phase not in {"transcribing", "executing", "retryable", "uncertain"}:
        raise ValueError("invalid_command_phase")
    with db_lock:
        conn.execute(
            "UPDATE command_requests SET phase=? WHERE user_id=? AND request_id=?",
            (phase, user_id, request_id),
        )
        conn.commit()


def finish_request(user_id: int, request_id: str, response: dict, status: int, *, phase: str = "done") -> None:
    with db_lock:
        conn.execute(
            "UPDATE command_requests SET phase=?,response_json=?,http_status=? "
            "WHERE user_id=? AND request_id=?",
            (phase, json.dumps(response, ensure_ascii=False), status, user_id, request_id),
        )
        conn.commit()


def enter_request(user_id: int, request_id: str):
    return (_current_request.set((user_id, request_id)), _effect_number.set(0))


def leave_request(tokens) -> None:
    _effect_number.reset(tokens[1])
    _current_request.reset(tokens[0])


def current_request_id() -> str | None:
    current = _current_request.get()
    return current[1] if current else None


def prepare_calendar_create(user_id: int, event: dict) -> tuple[str, str] | None:
    current = _current_request.get()
    if not current or current[0] != user_id:
        return None
    sequence = _effect_number.get() + 1
    _effect_number.set(sequence)
    digest = hashlib.sha256(f"{user_id}:{current[1]}:{sequence}".encode()).hexdigest()
    provider_id = "sp" + digest
    event["id"] = provider_id
    event.setdefault("extendedProperties", {}).setdefault("private", {})["smartPlannerRequest"] = digest
    with db_lock:
        conn.execute(
            "INSERT INTO command_effects(user_id,request_id,sequence,kind,provider_id,payload_json) "
            "VALUES (?,?,?,'calendar_create',?,?)",
            (user_id, current[1], sequence, provider_id, json.dumps(event, ensure_ascii=False)),
        )
        conn.commit()
    return provider_id, digest


def complete_calendar_create(user_id: int, provider_id: str) -> None:
    current = _current_request.get()
    if current and current[0] == user_id:
        with db_lock:
            conn.execute(
                "UPDATE command_effects SET completed=1 WHERE user_id=? AND request_id=? AND provider_id=?",
                (user_id, current[1], provider_id),
            )
            conn.commit()


def request_effects(user_id: int, request_id: str) -> list[dict]:
    with db_lock:
        rows = conn.execute(
            "SELECT kind,provider_id,payload_json,completed FROM command_effects "
            "WHERE user_id=? AND request_id=? ORDER BY sequence", (user_id, request_id),
        ).fetchall()
    return [
        {"kind": row[0], "provider_id": row[1], "payload": json.loads(row[2]), "completed": bool(row[3])}
        for row in rows
    ]


def _json_default(value):
    if isinstance(value, datetime):
        return {"__planner_datetime__": value.isoformat()}
    raise TypeError(f"Unsupported conversation value: {type(value).__name__}")


def _json_object(value):
    if set(value) == {"__planner_datetime__"}:
        return datetime.fromisoformat(value["__planner_datetime__"])
    return value


def load_conversation(user_id: int) -> dict:
    with db_lock:
        row = conn.execute(
            "SELECT state_json,expires_at FROM conversation_state WHERE user_id=?", (user_id,),
        ).fetchone()
    if not row or row[1] <= time.time():
        return {}
    try:
        data = json.loads(row[0], object_hook=_json_object)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        logger.warning("Discarded invalid conversation state for user %s", user_id)
        return {}


def save_conversation(user_id: int, state: dict) -> None:
    payload = json.dumps(state, default=_json_default, ensure_ascii=False, allow_nan=False)
    if len(payload.encode()) > 512_000:
        raise ValueError("Conversation state is too large")
    with db_lock:
        conn.execute("DELETE FROM conversation_state WHERE expires_at<=?", (time.time(),))
        conn.execute(
            "INSERT INTO conversation_state(user_id,state_json,expires_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET state_json=excluded.state_json,expires_at=excluded.expires_at",
            (user_id, payload, time.time() + CONTEXT_TTL_SECONDS),
        )
        conn.commit()


init_command_store()
