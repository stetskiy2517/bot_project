"""Bounded undo for local deletion, completion and rescheduling only."""
from __future__ import annotations

import hashlib
import json
import time

from core.db import conn, db_lock

TABLES = {"note": ("notes", "note_id"), "reminder": ("reminders", "reminder_id")}
UNDO_TTL_SECONDS = 300


def init_undo_store() -> None:
    with db_lock:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS undo_actions (
                action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL, label TEXT NOT NULL,
                before_json TEXT NOT NULL, after_hash TEXT NOT NULL,
                expires_at REAL NOT NULL, undone_at REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_undo_user ON undo_actions(user_id,action_id)")
        conn.commit()


def entity_snapshot(kind: str, user_id: int, entity_id: int) -> dict | None:
    table, key = TABLES[kind]
    with db_lock:
        cursor = conn.execute(
            f"SELECT * FROM {table} WHERE user_id=? AND {key}=?", (int(user_id), int(entity_id)),
        )
        row = cursor.fetchone()
        return dict(zip([column[0] for column in cursor.description], row)) if row else None


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def record_undo(kind: str, user_id: int, entity_id: int, before: dict, label: str) -> None:
    """Called inside the same transaction as the mutation; never commits it."""
    after = entity_snapshot(kind, user_id, entity_id)
    if after is None or after == before:
        return
    with db_lock:
        conn.execute("DELETE FROM undo_actions WHERE expires_at<?", (time.time(),))
        conn.execute(
            "INSERT INTO undo_actions(user_id,entity_type,entity_id,label,before_json,after_hash,expires_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (int(user_id), kind, int(entity_id), label,
             json.dumps(before, ensure_ascii=False), _digest(after), time.time() + UNDO_TTL_SECONDS),
        )
        conn.execute(
            "DELETE FROM undo_actions WHERE user_id=? AND action_id NOT IN "
            "(SELECT action_id FROM undo_actions WHERE user_id=? ORDER BY action_id DESC LIMIT 20)",
            (int(user_id), int(user_id)),
        )


def last_undo(user_id: int) -> dict | None:
    with db_lock:
        row = conn.execute(
            "SELECT action_id,label,expires_at FROM undo_actions "
            "WHERE user_id=? AND expires_at>? AND undone_at IS NULL ORDER BY action_id DESC LIMIT 1",
            (int(user_id), time.time()),
        ).fetchone()
    return dict(zip(("id", "label", "expires_at"), row)) if row else None


def undo_action(user_id: int, action_id: int) -> dict:
    from core.ai_memory_store import record_ai_memory_event

    with db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT entity_type,entity_id,before_json,after_hash,expires_at,undone_at "
                "FROM undo_actions WHERE user_id=? AND action_id=?",
                (int(user_id), int(action_id)),
            ).fetchone()
            if row is None:
                raise LookupError("Действие не найдено.")
            kind, entity_id, payload, expected, expires_at, undone_at = row
            if undone_at is not None:
                conn.commit()
                return {"ok": True, "already_undone": True}
            if expires_at <= time.time():
                raise ValueError("Срок отмены истёк.")
            current = entity_snapshot(kind, user_id, entity_id)
            if current is None or _digest(current) != expected:
                raise ValueError("Запись уже изменилась. Отмена не будет затирать новые данные.")
            before = json.loads(payload)
            table, key = TABLES[kind]
            columns = [name for name in before if name not in {"user_id", key}]
            # Columns originate from our SQLite schema, not from a request.
            conn.execute(
                f"UPDATE {table} SET " + ",".join(f"{name}=?" for name in columns)
                + f" WHERE user_id=? AND {key}=?",
                [*(before[name] for name in columns), int(user_id), int(entity_id)],
            )
            conn.execute(
                "UPDATE undo_actions SET undone_at=? WHERE user_id=? AND action_id=?",
                (time.time(), int(user_id), int(action_id)),
            )
            record_ai_memory_event(user_id, kind, entity_id, "updated", before, commit=False)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"ok": True, "entity_type": kind, "entity_id": entity_id}


init_undo_store()
