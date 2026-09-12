"""Persistent storage for standalone Smart Planner reminders."""

from __future__ import annotations

from datetime import datetime, timezone

from core.db import conn, db_lock

REMINDER_PENDING = "pending"
REMINDER_DELIVERED = "delivered"
REMINDER_STATUSES = {REMINDER_PENDING, REMINDER_DELIVERED}


def init_reminder_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS reminders (
                reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                delivered_at TEXT
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_user_status_time "
            "ON reminders(user_id, status, remind_at)"
        )
        conn.commit()


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Reminder datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def _from_row(row) -> dict:
    return {
        "reminder_id": int(row[0]),
        "user_id": int(row[1]),
        "text": row[2],
        "remind_at": row[3],
        "status": row[4],
        "created_at": row[5],
        "delivered_at": row[6],
    }


def create_reminder(user_id: int, text: str, remind_at: datetime) -> dict:
    text = " ".join(text.split()).strip()
    if not text:
        raise ValueError("Reminder text is required")
    remind_value = _to_utc(remind_at).isoformat()
    created_at = datetime.now(timezone.utc).isoformat()
    with db_lock:
        cur = conn.execute(
            "INSERT INTO reminders (user_id,text,remind_at,status,created_at) VALUES (?,?,?,?,?)",
            (int(user_id), text, remind_value, REMINDER_PENDING, created_at),
        )
        conn.commit()
        row = conn.execute(
            "SELECT reminder_id,user_id,text,remind_at,status,created_at,delivered_at "
            "FROM reminders WHERE reminder_id=?",
            (cur.lastrowid,),
        ).fetchone()
    return _from_row(row)


def list_reminders(
    user_id: int,
    *,
    status: str = REMINDER_PENDING,
    limit: int = 100,
) -> list[dict]:
    if status not in REMINDER_STATUSES:
        raise ValueError("Unknown reminder status")
    safe_limit = max(1, min(int(limit), 500))
    with db_lock:
        rows = conn.execute(
            "SELECT reminder_id,user_id,text,remind_at,status,created_at,delivered_at "
            "FROM reminders WHERE user_id=? AND status=? ORDER BY remind_at, reminder_id LIMIT ?",
            (int(user_id), status, safe_limit),
        ).fetchall()
    return [_from_row(row) for row in rows]


def delete_reminder(user_id: int, reminder_id: int) -> bool:
    with db_lock:
        cur = conn.execute(
            "DELETE FROM reminders WHERE user_id=? AND reminder_id=? AND status=?",
            (int(user_id), int(reminder_id), REMINDER_PENDING),
        )
        conn.commit()
    return cur.rowcount > 0


def claim_due_reminders(
    user_id: int,
    *,
    now: datetime | None = None,
    limit: int = 20,
) -> list[dict]:
    """Atomically claim due reminders so the same reminder is delivered once."""
    current = _to_utc(now or datetime.now(timezone.utc)).isoformat()
    safe_limit = max(1, min(int(limit), 100))
    delivered_at = datetime.now(timezone.utc).isoformat()
    with db_lock:
        rows = conn.execute(
            "SELECT reminder_id,user_id,text,remind_at,status,created_at,delivered_at "
            "FROM reminders WHERE user_id=? AND status=? AND remind_at<=? "
            "ORDER BY remind_at, reminder_id LIMIT ?",
            (int(user_id), REMINDER_PENDING, current, safe_limit),
        ).fetchall()
        if not rows:
            return []
        ids = [int(row[0]) for row in rows]
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE reminders SET status=?, delivered_at=? "
            f"WHERE user_id=? AND status=? AND reminder_id IN ({placeholders})",
            [REMINDER_DELIVERED, delivered_at, int(user_id), REMINDER_PENDING, *ids],
        )
        conn.commit()
    result = []
    for row in rows:
        item = _from_row(row)
        item["status"] = REMINDER_DELIVERED
        item["delivered_at"] = delivered_at
        result.append(item)
    return result


init_reminder_store()
