"""Read models used by the web library screen.

The library is intentionally read-only at this layer. Existing note/reminder
modules keep ownership of mutations and conversational behavior.
"""

from __future__ import annotations

from core.db import conn, db_lock

REMINDER_COLUMNS = (
    "reminder_id,user_id,text,remind_at,status,created_at,delivered_at,"
    "lease_until,delivery_attempts,last_error"
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
        "lease_until": row[7],
        "delivery_attempts": int(row[8] or 0),
        "last_error": row[9],
    }


def list_saved_reminders(user_id: int, *, limit: int = 500) -> list[dict]:
    """Return the user's reminders with active items first and history after them."""
    safe_limit = max(1, min(int(limit), 500))
    with db_lock:
        rows = conn.execute(
            f"SELECT {REMINDER_COLUMNS} FROM reminders WHERE user_id=? "
            "ORDER BY "
            "CASE status WHEN 'pending' THEN 0 WHEN 'delivering' THEN 1 ELSE 2 END, "
            "CASE WHEN status IN ('pending','delivering') THEN remind_at END ASC, "
            "CASE WHEN status='delivered' THEN COALESCE(delivered_at,remind_at) END DESC, "
            "reminder_id DESC LIMIT ?",
            (int(user_id), safe_limit),
        ).fetchall()
    return [_reminder_from_row(row) for row in rows]


def get_saved_reminder(user_id: int, reminder_id: int) -> dict | None:
    """Load one reminder only when it belongs to the current user."""
    with db_lock:
        row = conn.execute(
            f"SELECT {REMINDER_COLUMNS} FROM reminders WHERE user_id=? AND reminder_id=?",
            (int(user_id), int(reminder_id)),
        ).fetchone()
    return _reminder_from_row(row) if row else None
