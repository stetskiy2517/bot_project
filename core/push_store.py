"""Persistent Web Push subscriptions for browser/PWA devices."""

from __future__ import annotations

from datetime import datetime, timezone

from core.db import conn, db_lock


def init_push_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS push_subscriptions (
                subscription_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                user_agent TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_success_at TEXT,
                last_error TEXT
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user "
            "ON push_subscriptions(user_id)"
        )
        conn.commit()


def _from_row(row) -> dict:
    return {
        "subscription_id": int(row[0]),
        "user_id": int(row[1]),
        "endpoint": row[2],
        "p256dh": row[3],
        "auth": row[4],
        "user_agent": row[5],
        "created_at": row[6],
        "updated_at": row[7],
        "last_success_at": row[8],
        "last_error": row[9],
    }


def save_push_subscription(
    user_id: int,
    endpoint: str,
    p256dh: str,
    auth: str,
    *,
    user_agent: str | None = None,
) -> dict:
    endpoint = str(endpoint or "").strip()
    p256dh = str(p256dh or "").strip()
    auth = str(auth or "").strip()
    if not endpoint.startswith("https://"):
        raise ValueError("Push endpoint must use HTTPS")
    if not p256dh or not auth:
        raise ValueError("Push subscription keys are required")
    if len(endpoint) > 4096 or len(p256dh) > 1024 or len(auth) > 1024:
        raise ValueError("Push subscription is too large")

    now = datetime.now(timezone.utc).isoformat()
    safe_agent = str(user_agent or "")[:1000] or None
    with db_lock:
        conn.execute(
            """INSERT INTO push_subscriptions
               (user_id,endpoint,p256dh,auth,user_agent,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(endpoint) DO UPDATE SET
                 user_id=excluded.user_id,
                 p256dh=excluded.p256dh,
                 auth=excluded.auth,
                 user_agent=excluded.user_agent,
                 updated_at=excluded.updated_at,
                 last_error=NULL""",
            (int(user_id), endpoint, p256dh, auth, safe_agent, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT subscription_id,user_id,endpoint,p256dh,auth,user_agent,created_at,updated_at,last_success_at,last_error "
            "FROM push_subscriptions WHERE endpoint=?",
            (endpoint,),
        ).fetchone()
    return _from_row(row)


def list_push_subscriptions(user_id: int) -> list[dict]:
    with db_lock:
        rows = conn.execute(
            "SELECT subscription_id,user_id,endpoint,p256dh,auth,user_agent,created_at,updated_at,last_success_at,last_error "
            "FROM push_subscriptions WHERE user_id=? ORDER BY subscription_id",
            (int(user_id),),
        ).fetchall()
    return [_from_row(row) for row in rows]


def list_push_user_ids() -> list[int]:
    with db_lock:
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM push_subscriptions ORDER BY user_id"
        ).fetchall()
    return [int(row[0]) for row in rows]


def has_push_subscriptions(user_id: int) -> bool:
    with db_lock:
        row = conn.execute(
            "SELECT 1 FROM push_subscriptions WHERE user_id=? LIMIT 1",
            (int(user_id),),
        ).fetchone()
    return row is not None


def delete_push_subscription(user_id: int, endpoint: str) -> bool:
    with db_lock:
        cur = conn.execute(
            "DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?",
            (int(user_id), str(endpoint)),
        )
        conn.commit()
    return cur.rowcount > 0


def delete_push_subscription_by_id(subscription_id: int) -> bool:
    with db_lock:
        cur = conn.execute(
            "DELETE FROM push_subscriptions WHERE subscription_id=?",
            (int(subscription_id),),
        )
        conn.commit()
    return cur.rowcount > 0


def mark_push_success(subscription_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        conn.execute(
            "UPDATE push_subscriptions SET last_success_at=?,last_error=NULL,updated_at=? WHERE subscription_id=?",
            (now, now, int(subscription_id)),
        )
        conn.commit()


def mark_push_error(subscription_id: int, error: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        conn.execute(
            "UPDATE push_subscriptions SET last_error=?,updated_at=? WHERE subscription_id=?",
            (str(error)[:1000], now, int(subscription_id)),
        )
        conn.commit()


init_push_store()
