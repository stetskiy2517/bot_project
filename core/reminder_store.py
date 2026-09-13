"""Persistent storage for standalone Smart Planner reminders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.ai_memory_store import record_ai_memory_event
from core.db import conn, db_lock

REMINDER_PENDING = "pending"
REMINDER_DELIVERING = "delivering"
REMINDER_DELIVERED = "delivered"
REMINDER_COMPLETED = "completed"
REMINDER_STATUSES = {
    REMINDER_PENDING,
    REMINDER_DELIVERING,
    REMINDER_DELIVERED,
    REMINDER_COMPLETED,
}
SELECT_COLUMNS = (
    "reminder_id,user_id,text,remind_at,status,created_at,delivered_at,completed_at,"
    "lease_until,delivery_attempts,last_error,deleted_at"
)


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
                delivered_at TEXT,
                completed_at TEXT,
                lease_until TEXT,
                delivery_attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                deleted_at TEXT
            )"""
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(reminders)").fetchall()}
        migrations = {
            "completed_at": "TEXT",
            "lease_until": "TEXT",
            "delivery_attempts": "INTEGER NOT NULL DEFAULT 0",
            "last_error": "TEXT",
            "deleted_at": "TEXT",
        }
        for column, sql_type in migrations.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE reminders ADD COLUMN {column} {sql_type}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_user_status_time "
            "ON reminders(user_id, status, remind_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_status_time "
            "ON reminders(status, remind_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_user_deleted_status_time "
            "ON reminders(user_id, deleted_at, status, remind_at)"
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
        "completed_at": row[7],
        "lease_until": row[8],
        "delivery_attempts": int(row[9] or 0),
        "last_error": row[10],
        "deleted_at": row[11],
    }


def create_reminder(user_id: int, text: str, remind_at: datetime) -> dict:
    text = " ".join(text.split()).strip()
    if not text:
        raise ValueError("Reminder text is required")
    remind_value = _to_utc(remind_at).isoformat()
    created_at = datetime.now(timezone.utc).isoformat()
    with db_lock:
        try:
            cur = conn.execute(
                "INSERT INTO reminders (user_id,text,remind_at,status,created_at) VALUES (?,?,?,?,?)",
                (int(user_id), text, remind_value, REMINDER_PENDING, created_at),
            )
            row = conn.execute(
                f"SELECT {SELECT_COLUMNS} FROM reminders WHERE reminder_id=?",
                (cur.lastrowid,),
            ).fetchone()
            reminder = _from_row(row)
            record_ai_memory_event(
                user_id,
                "reminder",
                reminder["reminder_id"],
                "created",
                reminder,
                commit=False,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return reminder


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
            f"SELECT {SELECT_COLUMNS} FROM reminders "
            "WHERE user_id=? AND status=? AND deleted_at IS NULL "
            "ORDER BY remind_at, reminder_id LIMIT ?",
            (int(user_id), status, safe_limit),
        ).fetchall()
    return [_from_row(row) for row in rows]


def _soft_delete_reminder(user_id: int, reminder_id: int, *, pending_only: bool) -> bool:
    deleted_at = datetime.now(timezone.utc).isoformat()
    with db_lock:
        try:
            query = (
                f"SELECT {SELECT_COLUMNS} FROM reminders "
                "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL"
            )
            params: list[object] = [int(user_id), int(reminder_id)]
            if pending_only:
                query += " AND status=?"
                params.append(REMINDER_PENDING)
            row = conn.execute(query, params).fetchone()
            if not row:
                return False
            reminder = _from_row(row)
            update = (
                "UPDATE reminders SET deleted_at=?,lease_until=NULL "
                "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL"
            )
            update_params: list[object] = [deleted_at, int(user_id), int(reminder_id)]
            if pending_only:
                update += " AND status=?"
                update_params.append(REMINDER_PENDING)
            cur = conn.execute(update, update_params)
            if cur.rowcount <= 0:
                conn.rollback()
                return False
            snapshot = {**reminder, "deleted_at": deleted_at}
            record_ai_memory_event(
                user_id,
                "reminder",
                reminder["reminder_id"],
                "deleted",
                snapshot,
                commit=False,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return True


def delete_reminder(user_id: int, reminder_id: int) -> bool:
    """Hide a still-pending reminder while retaining it for AI memory."""
    return _soft_delete_reminder(user_id, reminder_id, pending_only=True)


def delete_saved_reminder(user_id: int, reminder_id: int) -> bool:
    """Hide one saved reminder regardless of history state while retaining it internally."""
    return _soft_delete_reminder(user_id, reminder_id, pending_only=False)


def complete_reminder(user_id: int, reminder_id: int) -> dict | None:
    """Mark a reminder as explicitly completed by the user."""
    completed_at = datetime.now(timezone.utc).isoformat()
    with db_lock:
        try:
            cur = conn.execute(
                "UPDATE reminders SET status=?,completed_at=COALESCE(completed_at,?),"
                "lease_until=NULL,last_error=NULL "
                "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                (REMINDER_COMPLETED, completed_at, int(user_id), int(reminder_id)),
            )
            if cur.rowcount <= 0:
                conn.commit()
                return None
            row = conn.execute(
                f"SELECT {SELECT_COLUMNS} FROM reminders "
                "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                (int(user_id), int(reminder_id)),
            ).fetchone()
            reminder = _from_row(row) if row else None
            if reminder:
                record_ai_memory_event(
                    user_id,
                    "reminder",
                    reminder["reminder_id"],
                    "completed",
                    reminder,
                    commit=False,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return reminder


def reschedule_reminder(user_id: int, reminder_id: int, remind_at: datetime) -> dict | None:
    """Move a saved reminder to a new time and make it active again."""
    remind_value = _to_utc(remind_at).isoformat()
    with db_lock:
        try:
            cur = conn.execute(
                "UPDATE reminders SET remind_at=?,status=?,delivered_at=NULL,completed_at=NULL,"
                "lease_until=NULL,delivery_attempts=0,last_error=NULL "
                "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                (remind_value, REMINDER_PENDING, int(user_id), int(reminder_id)),
            )
            if cur.rowcount <= 0:
                conn.commit()
                return None
            row = conn.execute(
                f"SELECT {SELECT_COLUMNS} FROM reminders "
                "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                (int(user_id), int(reminder_id)),
            ).fetchone()
            reminder = _from_row(row) if row else None
            if reminder:
                record_ai_memory_event(
                    user_id,
                    "reminder",
                    reminder["reminder_id"],
                    "rescheduled",
                    reminder,
                    commit=False,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return reminder


def _recover_expired_leases(current_iso: str) -> None:
    conn.execute(
        "UPDATE reminders SET status=?,lease_until=NULL "
        "WHERE status=? AND lease_until IS NOT NULL AND lease_until<=? AND deleted_at IS NULL",
        (REMINDER_PENDING, REMINDER_DELIVERING, current_iso),
    )


def claim_due_reminders(
    user_id: int,
    *,
    now: datetime | None = None,
    limit: int = 20,
) -> list[dict]:
    """Claim due reminders for an active foreground client exactly once."""
    current = _to_utc(now or datetime.now(timezone.utc)).isoformat()
    safe_limit = max(1, min(int(limit), 100))
    delivered_at = datetime.now(timezone.utc).isoformat()
    with db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _recover_expired_leases(current)
            rows = conn.execute(
                f"SELECT {SELECT_COLUMNS} FROM reminders "
                "WHERE user_id=? AND status=? AND remind_at<=? AND deleted_at IS NULL "
                "ORDER BY remind_at, reminder_id LIMIT ?",
                (int(user_id), REMINDER_PENDING, current, safe_limit),
            ).fetchall()
            if not rows:
                conn.commit()
                return []
            ids = [int(row[0]) for row in rows]
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE reminders SET status=?,delivered_at=?,lease_until=NULL,last_error=NULL "
                f"WHERE user_id=? AND status=? AND deleted_at IS NULL "
                f"AND reminder_id IN ({placeholders})",
                [REMINDER_DELIVERED, delivered_at, int(user_id), REMINDER_PENDING, *ids],
            )
            result = []
            for row in rows:
                item = _from_row(row)
                item["status"] = REMINDER_DELIVERED
                item["delivered_at"] = delivered_at
                item["lease_until"] = None
                record_ai_memory_event(
                    item["user_id"],
                    "reminder",
                    item["reminder_id"],
                    "delivered",
                    item,
                    commit=False,
                )
                result.append(item)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return result


def claim_due_for_push(
    user_ids: list[int],
    *,
    now: datetime | None = None,
    limit: int = 50,
    lease_seconds: int = 90,
) -> list[dict]:
    """Lease due reminders for users that currently have Web Push subscriptions."""
    normalized_users = sorted({int(user_id) for user_id in user_ids})
    if not normalized_users:
        return []
    current_dt = _to_utc(now or datetime.now(timezone.utc))
    current = current_dt.isoformat()
    lease_until = (current_dt + timedelta(seconds=max(30, int(lease_seconds)))).isoformat()
    safe_limit = max(1, min(int(limit), 200))
    user_placeholders = ",".join("?" for _ in normalized_users)

    with db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _recover_expired_leases(current)
            rows = conn.execute(
                f"SELECT {SELECT_COLUMNS} FROM reminders "
                f"WHERE status=? AND remind_at<=? AND deleted_at IS NULL "
                f"AND user_id IN ({user_placeholders}) "
                "ORDER BY remind_at, reminder_id LIMIT ?",
                [REMINDER_PENDING, current, *normalized_users, safe_limit],
            ).fetchall()
            if not rows:
                conn.commit()
                return []
            ids = [int(row[0]) for row in rows]
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE reminders SET status=?,lease_until=?,delivery_attempts=delivery_attempts+1,last_error=NULL "
                f"WHERE status=? AND deleted_at IS NULL AND reminder_id IN ({placeholders})",
                [REMINDER_DELIVERING, lease_until, REMINDER_PENDING, *ids],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    result = []
    for row in rows:
        item = _from_row(row)
        item["status"] = REMINDER_DELIVERING
        item["lease_until"] = lease_until
        item["delivery_attempts"] += 1
        item["last_error"] = None
        result.append(item)
    return result


def complete_push_delivery(reminder_id: int) -> bool:
    delivered_at = datetime.now(timezone.utc).isoformat()
    with db_lock:
        try:
            cur = conn.execute(
                "UPDATE reminders SET status=?,delivered_at=?,lease_until=NULL,last_error=NULL "
                "WHERE reminder_id=? AND status=? AND deleted_at IS NULL",
                (REMINDER_DELIVERED, delivered_at, int(reminder_id), REMINDER_DELIVERING),
            )
            if cur.rowcount <= 0:
                conn.commit()
                return False
            row = conn.execute(
                f"SELECT {SELECT_COLUMNS} FROM reminders "
                "WHERE reminder_id=? AND deleted_at IS NULL",
                (int(reminder_id),),
            ).fetchone()
            if row:
                reminder = _from_row(row)
                record_ai_memory_event(
                    reminder["user_id"],
                    "reminder",
                    reminder["reminder_id"],
                    "delivered",
                    reminder,
                    commit=False,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return True


def release_push_delivery(reminder_id: int, error: str | None = None) -> bool:
    with db_lock:
        cur = conn.execute(
            "UPDATE reminders SET status=?,lease_until=NULL,last_error=? "
            "WHERE reminder_id=? AND status=? AND deleted_at IS NULL",
            (REMINDER_PENDING, str(error or "")[:1000] or None, int(reminder_id), REMINDER_DELIVERING),
        )
        conn.commit()
    return cur.rowcount > 0


init_reminder_store()
