"""Opt-in briefings and bounded reminder follow-ups, run by the existing worker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import time

from core.assistant_store import assistant_preferences
from core.db import conn, db_lock
from core.push_store import list_push_subscriptions, list_push_user_ids, mark_push_error, mark_push_success
from core.user_operations import UserBusyError, user_operation
from modules.assistant_commands import daily_overview, quiet_now, user_zone

logger = logging.getLogger(__name__)


def _attempt(user_id: int, key: str, title: str, body: str, reminder_id: int | None = None) -> bool:
    from modules.reminder_dispatcher import _notification_payload
    from integrations.web_push import send_web_push

    subscriptions = list_push_subscriptions(user_id)
    if not subscriptions:
        return False
    with db_lock:
        conn.execute("DELETE FROM notification_attempts WHERE attempted_at<?", (time.time() - 30 * 86400,))
        cur = conn.execute(
            "INSERT OR IGNORE INTO notification_attempts(user_id,delivery_key,status,attempted_at) "
            "VALUES (?,?,?,?)", (int(user_id), key, "attempted", time.time()),
        )
        conn.commit()
        if not cur.rowcount:
            return False
    # Persist before sending: an interrupted briefing is reported as uncertain, not resent after restart.
    accepted = 0
    payload = _notification_payload(title=title, body=body[:1200], tag=key, reminder_id=reminder_id)
    for subscription in subscriptions:
        try:
            send_web_push(subscription, payload)
            mark_push_success(subscription["subscription_id"])
            accepted += 1
        except Exception as exc:
            mark_push_error(subscription["subscription_id"], type(exc).__name__)
            logger.warning("Assistant notification failed for user %s: %s", user_id, type(exc).__name__)
    with db_lock:
        conn.execute(
            "UPDATE notification_attempts SET status=?,accepted=? WHERE user_id=? AND delivery_key=?",
            ("accepted" if accepted else "failed", accepted, int(user_id), key),
        )
        conn.commit()
    return bool(accepted)


def _dispatch_user(user_id: int, now: datetime) -> int:
    if quiet_now(user_id, now):
        return 0
    accepted = 0
    local = now.astimezone(user_zone(user_id))
    prefs = assistant_preferences(user_id)
    for kind in ("morning", "evening"):
        if not prefs[f"{kind}_enabled"]:
            continue
        hour, minute = map(int, prefs[f"{kind}_time"].split(":"))
        due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if not timedelta(0) <= local - due < timedelta(hours=2):
            continue
        key = f"briefing:{kind}:{local.date().isoformat()}"
        with db_lock:
            already = conn.execute(
                "SELECT 1 FROM notification_attempts WHERE user_id=? AND delivery_key=?", (user_id, key),
            ).fetchone()
        if already:
            continue
        overview = daily_overview(user_id, evening=kind == "evening", now=now)
        accepted += int(_attempt(user_id, key, "Личный секретарь", overview["text"]))

    with db_lock:
        rows = conn.execute(
            "SELECT a.reminder_id,a.interval_minutes,a.max_repeats,a.occurrence,a.attempts,a.next_at,"
            "r.remind_at,r.delivered_at,r.status,r.text,r.deleted_at FROM reminder_alerts a "
            "JOIN reminders r ON r.reminder_id=a.reminder_id AND r.user_id=a.user_id "
            "WHERE a.user_id=? AND a.max_repeats>0", (int(user_id),),
        ).fetchall()
    for row in rows:
        rid, interval, limit, occurrence, attempts, next_at, remind_at, delivered_at, status, text, deleted = row
        if deleted or status != "delivered":
            with db_lock:
                conn.execute("UPDATE reminder_alerts SET next_at=NULL WHERE user_id=? AND reminder_id=?",
                             (user_id, rid))
                conn.commit()
            continue
        if occurrence != remind_at:
            attempts = 0
            next_at = None
        if attempts >= limit:
            continue
        if next_at is None:
            reference = datetime.fromisoformat(delivered_at).timestamp() if delivered_at else now.timestamp()
            next_at = max(reference + interval * 60, now.timestamp())
            with db_lock:
                conn.execute(
                    "UPDATE reminder_alerts SET occurrence=?,attempts=?,next_at=? WHERE user_id=? AND reminder_id=?",
                    (remind_at, attempts, next_at, user_id, rid),
                )
                conn.commit()
        if next_at > now.timestamp():
            continue
        attempts += 1
        with db_lock:
            conn.execute(
                "UPDATE reminder_alerts SET attempts=?,next_at=?,occurrence=? WHERE user_id=? AND reminder_id=?",
                (attempts, now.timestamp() + interval * 60 if attempts < limit else None,
                 remind_at, user_id, rid),
            )
            conn.commit()
        key = f"followup:{rid}:{remind_at}:{attempts}"
        accepted += int(_attempt(
            user_id, key, f"Напоминание · повтор {attempts}/{limit}", text, reminder_id=rid,
        ))
    return accepted


def dispatch_assistant_notifications(now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    accepted = 0
    for user_id in list_push_user_ids():
        try:
            with user_operation(user_id, timeout=0):
                # Re-read subscriptions after acquiring the lock; deletion may have completed meanwhile.
                if list_push_subscriptions(user_id):
                    accepted += _dispatch_user(user_id, current)
        except UserBusyError:
            continue
        except Exception:
            logger.exception("Assistant notification iteration failed for user %s", user_id)
    return accepted
