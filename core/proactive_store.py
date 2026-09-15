"""Persistent audit log for proactive assistant decisions and actions."""

from __future__ import annotations

from datetime import datetime, timezone

from core.db import conn, db_lock

TERMINAL_STATUSES = {"created", "covered"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_proactive_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS proactive_actions (
                action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memory_id INTEGER NOT NULL,
                memory_updated_at TEXT NOT NULL,
                action_type TEXT NOT NULL,
                status TEXT NOT NULL,
                reminder_id INTEGER,
                reason TEXT NOT NULL,
                confidence REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, memory_id, action_type)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proactive_actions_user_updated "
            "ON proactive_actions(user_id, updated_at DESC, action_id DESC)"
        )
        conn.commit()


def get_proactive_decision(user_id: int, memory_id: int, action_type: str = "reminder") -> dict | None:
    with db_lock:
        row = conn.execute(
            "SELECT action_id,user_id,memory_id,memory_updated_at,action_type,status,reminder_id,"
            "reason,confidence,created_at,updated_at FROM proactive_actions "
            "WHERE user_id=? AND memory_id=? AND action_type=?",
            (int(user_id), int(memory_id), str(action_type)),
        ).fetchone()
    if not row:
        return None
    names = (
        "action_id", "user_id", "memory_id", "memory_updated_at", "action_type", "status",
        "reminder_id", "reason", "confidence", "created_at", "updated_at",
    )
    return dict(zip(names, row))


def record_proactive_decision(
    user_id: int,
    memory_id: int,
    memory_updated_at: str,
    *,
    status: str,
    reason: str,
    confidence: float,
    reminder_id: int | None = None,
    action_type: str = "reminder",
) -> dict:
    clean_reason = " ".join(str(reason or "").split()).strip()[:1000] or "Без пояснения"
    confidence = max(0.0, min(1.0, float(confidence)))
    now = _now()
    with db_lock:
        conn.execute(
            "INSERT INTO proactive_actions "
            "(user_id,memory_id,memory_updated_at,action_type,status,reminder_id,reason,confidence,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id,memory_id,action_type) DO UPDATE SET "
            "memory_updated_at=excluded.memory_updated_at,status=excluded.status,reminder_id=excluded.reminder_id,"
            "reason=excluded.reason,confidence=excluded.confidence,updated_at=excluded.updated_at",
            (
                int(user_id), int(memory_id), str(memory_updated_at), str(action_type), str(status),
                int(reminder_id) if reminder_id is not None else None,
                clean_reason, confidence, now, now,
            ),
        )
        conn.commit()
    return get_proactive_decision(user_id, memory_id, action_type) or {}


def should_evaluate_memory(user_id: int, memory: dict, action_type: str = "reminder") -> bool:
    decision = get_proactive_decision(user_id, int(memory["memory_id"]), action_type)
    if not decision:
        return True
    if decision["status"] in TERMINAL_STATUSES:
        return False
    return str(decision.get("memory_updated_at") or "") != str(memory.get("updated_at") or "")


def list_proactive_actions(user_id: int, *, limit: int = 20) -> list[dict]:
    safe_limit = max(1, min(int(limit), 100))
    with db_lock:
        rows = conn.execute(
            "SELECT action_id,memory_id,action_type,status,reminder_id,reason,confidence,created_at,updated_at "
            "FROM proactive_actions WHERE user_id=? ORDER BY updated_at DESC,action_id DESC LIMIT ?",
            (int(user_id), safe_limit),
        ).fetchall()
    names = (
        "action_id", "memory_id", "action_type", "status", "reminder_id", "reason",
        "confidence", "created_at", "updated_at",
    )
    return [dict(zip(names, row)) for row in rows]


def proactive_status(user_id: int) -> dict:
    actions = list_proactive_actions(user_id, limit=1)
    with db_lock:
        created = conn.execute(
            "SELECT COUNT(*) FROM proactive_actions WHERE user_id=? AND status='created'",
            (int(user_id),),
        ).fetchone()[0]
    return {"created_reminders": int(created), "last_action": actions[0] if actions else None}


init_proactive_store()
