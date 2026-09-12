"""Persistent storage for standalone Smart Planner reminders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.db import conn, db_lock

REMINDER_PENDING = "pending"
REMINDER_DELIVERING = "delivering"
REMINDER_DELIVERED = "delivered"
REMINDER_STATUSES = {REMINDER_PENDING, REMINDER_DELIVERING, REMINDER_DELIVERED}
SELECT_COLUMNS = (
    "reminder_id,user_id,text,remind_at,status,created_at,delivered_at,"
    "lease_until,delivery_attempts,last_error"
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
                lease_until TEXT,
                delivery_attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )"""
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(reminders)").fetchall()}
        migrations = {
            "lease_until": "TEXT",
            "delivery_attempts": "INTEGER NOT NULL DEFAULT 0",
            "last_error": "TEXT",
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
        "lease_until": row[7],
        "delivery_attempts": int(row[8] or 0),
        "last_error": row[9],
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
            f"SELECT {SELECT_COLUMNS} FROM reminders WHERE reminder_id=?",
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
            f"SELECT {SELECT_COLUMNS} FROM reminders "
            "WHERE user_id=? AND status=? ORDER BY remind_at, reminder_id LIMIT ?",
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


def _recover_expired_leases(current_iso: str) -> None:
    conn.execute(
        "UPDATE reminders SET status=?,lease_until=NULL "
        "WHERE status=? AND lease_until IS NOT NULL AND lease_until<=?",
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
                "WHERE user_id=? AND status=? AND remind_at<=? "
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
                f"WHERE user_id=? AND status=? AND reminder_id IN ({placeholders})",
                [REMINDER_DELIVERED, delivered_at, int(user_id), REMINDER_PENDING, *ids],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    result = []
    for row in rows:
        item = _from_row(row)
        item["status"] = REMINDER_DELIVERED
        item["delivered_at"] = delivered_at
        item["lease_until"] = None
        result.append(item)
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
                f"WHERE status=? AND remind_at<=? AND user_id IN ({user_placeholders}) "
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
                f"WHERE status=? AND reminder_id IN ({placeholders})",
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
        cur = conn.execute(
            "UPDATE reminders SET status=?,delivered_at=?,lease_until=NULL,last_error=NULL "
            "WHERE reminder_id=? AND status=?",
            (REMINDER_DELIVERED, delivered_at, int(reminder_id), REMINDER_DELIVERING),
        )
        conn.commit()
    return cur.rowcount > 0


def release_push_delivery(reminder_id: int, error: str | None = None) -> bool:
    with db_lock:
        cur = conn.execute(
            "UPDATE reminders SET status=?,lease_until=NULL,last_error=? "
            "WHERE reminder_id=? AND status=?",
            (REMINDER_PENDING, str(error or "")[:1000] or None, int(reminder_id), REMINDER_DELIVERING),
        )
        conn.commit()
    return cur.rowcount > 0


init_reminder_store()
