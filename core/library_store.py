"""Read models used by the web library screen.

The library keeps read concerns separate from the reminder mutation store. Saved
reminders include active, fired, and explicitly completed items, but never items
the user has deleted.
"""

from __future__ import annotations

from core.db import conn, db_lock

REMINDER_COLUMNS = (
    "reminder_id,user_id,text,remind_at,status,created_at,delivered_at,completed_at,"
    "lease_until,delivery_attempts,last_error,deleted_at,repeat_rule,repeat_timezone,next_remind_at"
)


def _reminder_from_row(row) -> dict:
    return {
        "reminder_id": int(row[0]),
        "user_id": int(row[1]),
        "text": row[2],
        "remind_at": row[3],
        "status": row[4],
        "created_at": row[5],
        "delivered_at": row[6],
        "completed_at": row[7],
        "lease_until": row[8],
        "delivery_attempts": int(row[9] or 0),
        "last_error": row[10],
        "deleted_at": row[11],
        "repeat_rule": row[12],
        "repeat_timezone": row[13],
        "next_remind_at": row[14],
    }


def list_saved_reminders(user_id: int, *, limit: int = 500) -> list[dict]:
    """Return the user's visible reminders with actionable items before history."""
    safe_limit = max(1, min(int(limit), 500))
    with db_lock:
        rows = conn.execute(
            f"SELECT {REMINDER_COLUMNS} FROM reminders "
            "WHERE user_id=? AND deleted_at IS NULL "
            "ORDER BY "
            "CASE status "
            "WHEN 'pending' THEN 0 WHEN 'delivering' THEN 1 "
            "WHEN 'delivered' THEN 2 WHEN 'completed' THEN 3 ELSE 4 END, "
            "CASE WHEN status IN ('pending','delivering') THEN remind_at END ASC, "
            "CASE WHEN status='delivered' THEN COALESCE(delivered_at,remind_at) END DESC, "
            "CASE WHEN status='completed' THEN COALESCE(completed_at,delivered_at,remind_at) END DESC, "
            "reminder_id DESC LIMIT ?",
            (int(user_id), safe_limit),
        ).fetchall()
    return [_reminder_from_row(row) for row in rows]


def get_saved_reminder(user_id: int, reminder_id: int) -> dict | None:
    """Load one visible reminder only when it belongs to the current user."""
    with db_lock:
        row = conn.execute(
            f"SELECT {REMINDER_COLUMNS} FROM reminders "
            "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
            (int(user_id), int(reminder_id)),
        ).fetchone()
    return _reminder_from_row(row) if row else None
