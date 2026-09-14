"""Persistent storage for standalone Smart Planner reminders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.ai_memory_store import record_ai_memory_event
from core.db import conn, db_lock
from core.reminder_recurrence import next_repeat_at, next_repeat_after, validate_repeat_rule

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
    "lease_until,delivery_attempts,last_error,deleted_at,repeat_rule,repeat_timezone,next_remind_at"
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
                deleted_at TEXT,
                repeat_rule TEXT,
                repeat_timezone TEXT,
                next_remind_at TEXT
            )"""
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(reminders)").fetchall()}
        migrations = {
            "completed_at": "TEXT",
            "lease_until": "TEXT",
            "delivery_attempts": "INTEGER NOT NULL DEFAULT 0",
            "last_error": "TEXT",
            "deleted_at": "TEXT",
            "repeat_rule": "TEXT",
            "repeat_timezone": "TEXT",
            "next_remind_at": "TEXT",
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_repeat_due "
            "ON reminders(deleted_at, repeat_rule, next_remind_at)"
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
        "repeat_rule": row[12],
        "repeat_timezone": row[13],
        "next_remind_at": row[14],
    }


def _parse_utc(value: str) -> datetime:
    return _to_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def create_reminder(
    user_id: int,
    text: str,
    remind_at: datetime,
    *,
    repeat_rule: str | None = None,
    repeat_timezone: str | None = None,
) -> dict:
    text = " ".join(text.split()).strip()
    if not text:
        raise ValueError("Reminder text is required")
    normalized_repeat = validate_repeat_rule(repeat_rule)
    remind_dt = _to_utc(remind_at)
    remind_value = remind_dt.isoformat()
    repeat_tz = str(repeat_timezone or "UTC") if normalized_repeat else None
    next_value = (
        next_repeat_at(remind_dt, normalized_repeat, repeat_tz).isoformat()
        if normalized_repeat
        else None
    )
    created_at = datetime.now(timezone.utc).isoformat()
    with db_lock:
        try:
            cur = conn.execute(
                "INSERT INTO reminders "
                "(user_id,text,remind_at,status,created_at,repeat_rule,repeat_timezone,next_remind_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    int(user_id),
                    text,
                    remind_value,
                    REMINDER_PENDING,
                    created_at,
                    normalized_repeat,
                    repeat_tz,
                    next_value,
                ),
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


def list_active_reminders(user_id: int, *, limit: int = 100) -> list[dict]:
    """Return reminders that are still actionable, including recurring schedules."""
    safe_limit = max(1, min(int(limit), 500))
    with db_lock:
        rows = conn.execute(
            f"SELECT {SELECT_COLUMNS} FROM reminders "
            "WHERE user_id=? AND deleted_at IS NULL AND ("
            "status IN (?,?) OR (repeat_rule IS NOT NULL AND next_remind_at IS NOT NULL)"
            ") ORDER BY CASE WHEN status IN (?,?) THEN remind_at ELSE next_remind_at END, reminder_id LIMIT ?",
            (
                int(user_id),
                REMINDER_PENDING,
                REMINDER_DELIVERING,
                REMINDER_PENDING,
                REMINDER_DELIVERING,
                safe_limit,
            ),
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
                query += " AND (status=? OR repeat_rule IS NOT NULL)"
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
                update += " AND (status=? OR repeat_rule IS NOT NULL)"
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
    """Hide an active reminder or recurring schedule while retaining it for AI memory."""
    return _soft_delete_reminder(user_id, reminder_id, pending_only=True)


def delete_saved_reminder(user_id: int, reminder_id: int) -> bool:
    """Hide one saved reminder regardless of history state while retaining it internally."""
    return _soft_delete_reminder(user_id, reminder_id, pending_only=False)


def complete_reminder(
    user_id: int,
    reminder_id: int,
    *,
    completed: bool = True,
) -> dict | None:
    """Set or clear the user's explicit completion mark.

    A recurring reminder keeps its next occurrence in ``next_remind_at``. Marking
    the current occurrence complete never cancels the recurrence; when the next
    occurrence becomes due it replaces the current occurrence automatically.
    """
    if not isinstance(completed, bool):
        raise ValueError("completed must be boolean")

    now = datetime.now(timezone.utc)
    with db_lock:
        try:
            row = conn.execute(
                f"SELECT {SELECT_COLUMNS} FROM reminders "
                "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                (int(user_id), int(reminder_id)),
            ).fetchone()
            if not row:
                return None
            current = _from_row(row)

            if completed:
                if current["status"] == REMINDER_COMPLETED:
                    conn.commit()
                    return current
                completed_at = now.isoformat()
                conn.execute(
                    "UPDATE reminders SET status=?,completed_at=?,lease_until=NULL,last_error=NULL "
                    "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                    (REMINDER_COMPLETED, completed_at, int(user_id), int(reminder_id)),
                )
                event_type = "completed"
            else:
                if current["status"] != REMINDER_COMPLETED:
                    conn.commit()
                    return current
                remind_at = _parse_utc(current["remind_at"])
                target_status = REMINDER_PENDING if remind_at > now else REMINDER_DELIVERED
                if target_status == REMINDER_PENDING:
                    conn.execute(
                        "UPDATE reminders SET status=?,completed_at=NULL,delivered_at=NULL,"
                        "lease_until=NULL,last_error=NULL "
                        "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                        (target_status, int(user_id), int(reminder_id)),
                    )
                else:
                    conn.execute(
                        "UPDATE reminders SET status=?,completed_at=NULL,lease_until=NULL,last_error=NULL "
                        "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                        (target_status, int(user_id), int(reminder_id)),
                    )
                event_type = "reopened"

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
                    event_type,
                    reminder,
                    commit=False,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return reminder


def reschedule_reminder(user_id: int, reminder_id: int, remind_at: datetime) -> dict | None:
    """Move a saved reminder to a new time and keep its recurrence, if any."""
    remind_dt = _to_utc(remind_at)
    remind_value = remind_dt.isoformat()
    with db_lock:
        try:
            row = conn.execute(
                f"SELECT {SELECT_COLUMNS} FROM reminders "
                "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                (int(user_id), int(reminder_id)),
            ).fetchone()
            if not row:
                return None
            current = _from_row(row)
            next_value = None
            if current.get("repeat_rule"):
                next_value = next_repeat_at(
                    remind_dt,
                    current["repeat_rule"],
                    current.get("repeat_timezone"),
                ).isoformat()
            conn.execute(
                "UPDATE reminders SET remind_at=?,status=?,delivered_at=NULL,completed_at=NULL,"
                "lease_until=NULL,delivery_attempts=0,last_error=NULL,next_remind_at=? "
                "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                (
                    remind_value,
                    REMINDER_PENDING,
                    next_value,
                    int(user_id),
                    int(reminder_id),
                ),
            )
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


def _select_due_rows(user_ids: list[int], current_iso: str, limit: int, *, push: bool = False):
    delay_filter = (
        " AND NOT EXISTS (SELECT 1 FROM reminder_push_policy p WHERE p.reminder_id=reminders.reminder_id "
        "AND p.transport_not_before>?)"
    ) if push else ""
    placeholders = ",".join("?" for _ in user_ids)
    return conn.execute(
        f"SELECT {SELECT_COLUMNS} FROM reminders "
        f"WHERE deleted_at IS NULL AND user_id IN ({placeholders}) AND ("
        "(status=? AND remind_at<=?) OR "
        "(repeat_rule IS NOT NULL AND next_remind_at IS NOT NULL "
        "AND status IN (?,?) AND next_remind_at<=?)"
        ")" + delay_filter + " ORDER BY CASE WHEN status=? THEN remind_at ELSE next_remind_at END, reminder_id LIMIT ?",
        [
            *user_ids,
            REMINDER_PENDING,
            current_iso,
            REMINDER_DELIVERED,
            REMINDER_COMPLETED,
            current_iso,
            *([current_iso] if push else []),
            REMINDER_PENDING,
            int(limit),
        ],
    ).fetchall()


def _due_occurrence(item: dict, current: datetime) -> datetime | None:
    remind_at = _parse_utc(item["remind_at"])
    if item["status"] == REMINDER_PENDING and remind_at <= current:
        return remind_at
    if (
        item.get("repeat_rule")
        and item["status"] in {REMINDER_DELIVERED, REMINDER_COMPLETED}
        and item.get("next_remind_at")
    ):
        next_at = _parse_utc(item["next_remind_at"])
        if next_at <= current:
            return next_at
    return None


def _advance_repeat(item: dict, occurrence: datetime, current: datetime) -> str | None:
    if not item.get("repeat_rule"):
        return None
    return next_repeat_after(
        occurrence, item["repeat_rule"], item.get("repeat_timezone"), current,
    ).isoformat()


def claim_due_reminders(
    user_id: int,
    *,
    now: datetime | None = None,
    limit: int = 20,
) -> list[dict]:
    """Claim due reminders for an active foreground client exactly once."""
    current_dt = _to_utc(now or datetime.now(timezone.utc))
    current = current_dt.isoformat()
    safe_limit = max(1, min(int(limit), 100))
    delivered_at = current_dt.isoformat()
    with db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _recover_expired_leases(current)
            rows = _select_due_rows([int(user_id)], current, safe_limit)
            result = []
            for row in rows:
                item = _from_row(row)
                occurrence = _due_occurrence(item, current_dt)
                if occurrence is None:
                    continue
                next_value = _advance_repeat(item, occurrence, current_dt)
                conn.execute(
                    "UPDATE reminders SET remind_at=?,status=?,delivered_at=?,completed_at=NULL,"
                    "lease_until=NULL,last_error=NULL,next_remind_at=? "
                    "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                    (
                        occurrence.isoformat(),
                        REMINDER_DELIVERED,
                        delivered_at,
                        next_value,
                        item["user_id"],
                        item["reminder_id"],
                    ),
                )
                item["remind_at"] = occurrence.isoformat()
                item["status"] = REMINDER_DELIVERED
                item["delivered_at"] = delivered_at
                item["completed_at"] = None
                item["lease_until"] = None
                item["last_error"] = None
                item["next_remind_at"] = next_value
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

    with db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _recover_expired_leases(current)
            rows = _select_due_rows(normalized_users, current, safe_limit, push=True)
            result = []
            for row in rows:
                item = _from_row(row)
                occurrence = _due_occurrence(item, current_dt)
                if occurrence is None:
                    continue
                is_new_occurrence = item["status"] != REMINDER_PENDING
                next_value = _advance_repeat(item, occurrence, current_dt)
                attempts = 1 if is_new_occurrence else item["delivery_attempts"] + 1
                conn.execute(
                    "UPDATE reminders SET remind_at=?,status=?,delivered_at=NULL,completed_at=NULL,"
                    "lease_until=?,delivery_attempts=?,last_error=NULL,next_remind_at=? "
                    "WHERE reminder_id=? AND deleted_at IS NULL",
                    (
                        occurrence.isoformat(),
                        REMINDER_DELIVERING,
                        lease_until,
                        attempts,
                        next_value,
                        item["reminder_id"],
                    ),
                )
                item["remind_at"] = occurrence.isoformat()
                item["status"] = REMINDER_DELIVERING
                item["delivered_at"] = None
                item["completed_at"] = None
                item["lease_until"] = lease_until
                item["delivery_attempts"] = attempts
                item["last_error"] = None
                item["next_remind_at"] = next_value
                result.append(item)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
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
            (
                REMINDER_PENDING,
                str(error or "")[:1000] or None,
                int(reminder_id),
                REMINDER_DELIVERING,
            ),
        )
        conn.commit()
    return cur.rowcount > 0


init_reminder_store()

from core.notification_policy import init_notification_policy
init_notification_policy()
