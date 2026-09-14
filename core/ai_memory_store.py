"""Append-only behavioural memory events for future AI processing.

This store is intentionally independent from any model. It preserves a compact
history of user actions so an AI layer can analyse behaviour later without
changing the current deterministic planner.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from core.db import conn, db_lock


ENTITY_TYPES = {"note", "reminder", "voice_transcript"}
EVENT_TYPES = {
    "created",
    "updated",
    "delivered",
    "completed",
    "reopened",
    "rescheduled",
    "deleted",
    "recognized",
}


def init_ai_memory_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS ai_memory_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_memory_user_time "
            "ON ai_memory_events(user_id, created_at DESC, event_id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_memory_entity "
            "ON ai_memory_events(user_id, entity_type, entity_id, event_id)"
        )
        conn.commit()


def record_ai_memory_event(
    user_id: int,
    entity_type: str,
    entity_id: int,
    event_type: str,
    snapshot: dict[str, Any],
    *,
    commit: bool = True,
) -> int:
    """Append one immutable event and return its id.

    Callers that already own a database transaction can pass ``commit=False``;
    ``db_lock`` is re-entrant, so the insert stays in the caller's transaction.
    """
    entity_type = str(entity_type).strip().lower()
    event_type = str(event_type).strip().lower()
    if entity_type not in ENTITY_TYPES:
        raise ValueError("Unknown AI memory entity type")
    if event_type not in EVENT_TYPES:
        raise ValueError("Unknown AI memory event type")

    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    created_at = datetime.now(timezone.utc).isoformat()
    with db_lock:
        cur = conn.execute(
            "INSERT INTO ai_memory_events "
            "(user_id,entity_type,entity_id,event_type,snapshot_json,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (int(user_id), entity_type, int(entity_id), event_type, payload, created_at),
        )
        if commit:
            conn.commit()
    return int(cur.lastrowid)


def list_ai_memory_events(user_id: int, *, limit: int = 200) -> list[dict]:
    """Internal read helper for tests and the future AI layer."""
    safe_limit = max(1, min(int(limit), 2000))
    with db_lock:
        rows = conn.execute(
            "SELECT event_id,user_id,entity_type,entity_id,event_type,snapshot_json,created_at "
            "FROM ai_memory_events WHERE user_id=? "
            "ORDER BY event_id DESC LIMIT ?",
            (int(user_id), safe_limit),
        ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "event_id": int(row[0]),
                "user_id": int(row[1]),
                "entity_type": row[2],
                "entity_id": int(row[3]),
                "event_type": row[4],
                "snapshot": json.loads(row[5]),
                "created_at": row[6],
            }
        )
    return result


init_ai_memory_store()
