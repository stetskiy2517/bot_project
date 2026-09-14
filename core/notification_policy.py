"""Bounded notification repeats, independent from calendar recurrence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.db import conn, db_lock
from core.library_store import get_saved_reminder


def init_notification_policy():
    with db_lock:
        conn.execute("""CREATE TABLE IF NOT EXISTS reminder_push_policy (
            reminder_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
            interval_minutes INTEGER NOT NULL DEFAULT 15,
            max_repeats INTEGER NOT NULL DEFAULT 0,
            repeat_count INTEGER NOT NULL DEFAULT 0,
            occurrence TEXT, next_repeat_at TEXT,
            transport_not_before TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_repeat_due ON reminder_push_policy(user_id,next_repeat_at)")
        conn.commit()


def get_policy(user_id: int, reminder_id: int) -> dict:
    if not get_saved_reminder(user_id, reminder_id):
        raise ValueError("Напоминание не найдено")
    with db_lock:
        row = conn.execute(
            "SELECT interval_minutes,max_repeats,repeat_count,next_repeat_at FROM reminder_push_policy WHERE user_id=? AND reminder_id=?",
            (user_id, reminder_id),
        ).fetchone()
    return dict(zip(("interval_minutes", "max_repeats", "repeat_count", "next_repeat_at"), row or (15, 0, 0, None)))


def save_policy(user_id: int, reminder_id: int, data: dict) -> dict:
    reminder = get_saved_reminder(user_id, reminder_id)
    if not reminder:
        raise ValueError("Напоминание не найдено")
    if not isinstance(data, dict) or set(data) != {"interval_minutes", "max_repeats"}:
        raise ValueError("Нужны интервал и число повторных отправок")
    interval, count = data["interval_minutes"], data["max_repeats"]
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (interval, count)):
        raise ValueError("Интервал и число повторов должны быть целыми")
    if not 5 <= interval <= 1440 or not 0 <= count <= 5:
        raise ValueError("Интервал: 5–1440 минут. Повторы: от 0 до 5")
    next_at = (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat() if count and reminder["status"] == "delivered" else None
    with db_lock:
        conn.execute("""INSERT INTO reminder_push_policy
            (reminder_id,user_id,interval_minutes,max_repeats,occurrence,next_repeat_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(reminder_id) DO UPDATE SET
              interval_minutes=excluded.interval_minutes,max_repeats=excluded.max_repeats,
              repeat_count=0,occurrence=excluded.occurrence,next_repeat_at=excluded.next_repeat_at,
              transport_not_before=NULL WHERE reminder_push_policy.user_id=excluded.user_id""",
            (reminder_id, user_id, interval, count, reminder["remind_at"], next_at))
        conn.commit()
    return get_policy(user_id, reminder_id)


def activate_repeats(reminder: dict, now: datetime) -> None:
    with db_lock:
        row = conn.execute("SELECT interval_minutes,max_repeats FROM reminder_push_policy WHERE reminder_id=? AND user_id=?",
                           (reminder["reminder_id"], reminder["user_id"])).fetchone()
        if not row:
            return
        next_at = (now + timedelta(minutes=row[0])).isoformat() if row[1] else None
        conn.execute("UPDATE reminder_push_policy SET repeat_count=0,occurrence=?,next_repeat_at=?,transport_not_before=NULL WHERE reminder_id=?",
                     (reminder["remind_at"], next_at, reminder["reminder_id"]))
        conn.commit()


def postpone_transport(reminder: dict, now: datetime) -> None:
    attempts = reminder.get("delivery_attempts", 1)
    next_at = "9999-12-31T00:00:00+00:00" if attempts >= 5 else (now + timedelta(seconds=min(1800, 30 * 2 ** min(attempts, 6)))).isoformat()
    with db_lock:
        conn.execute("""INSERT INTO reminder_push_policy(reminder_id,user_id,transport_not_before) VALUES(?,?,?)
            ON CONFLICT(reminder_id) DO UPDATE SET transport_not_before=excluded.transport_not_before""",
            (reminder["reminder_id"], reminder["user_id"], next_at))
        conn.commit()


def claim_repeat_attempts(user_id: int, now: datetime, *, limit: int = 20) -> list[dict]:
    current = now.astimezone(timezone.utc).isoformat()
    with db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute("""SELECT p.reminder_id,p.interval_minutes,p.max_repeats,p.repeat_count
                FROM reminder_push_policy p JOIN reminders r USING(reminder_id)
                WHERE p.user_id=? AND r.user_id=? AND p.next_repeat_at<=? AND p.repeat_count<p.max_repeats
                  AND r.deleted_at IS NULL AND r.status='delivered' AND p.occurrence=r.remind_at
                  AND (r.next_remind_at IS NULL OR r.next_remind_at>?)
                ORDER BY p.next_repeat_at LIMIT ?""", (user_id, user_id, current, current, limit)).fetchall()
            result = []
            for reminder_id, interval, max_repeats, count in rows:
                next_at = (now + timedelta(minutes=interval)).isoformat() if count + 1 < max_repeats else None
                conn.execute("UPDATE reminder_push_policy SET repeat_count=?,next_repeat_at=? WHERE reminder_id=?",
                             (count + 1, next_at, reminder_id))
                result.append({"reminder_id": reminder_id, "attempt": count + 1})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return result


init_notification_policy()
