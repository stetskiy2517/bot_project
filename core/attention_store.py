"""Persistent deduplicated attention queue for proactive assistant signals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
import json

from core.db import conn, db_lock

PRIORITIES = {"high", "normal", "info"}
ACTION_TYPES = {"none", "email_action", "task", "navigation", "proactive"}
RETENTION_DAYS = 60
PUSH_FRESHNESS_HOURS = 6
PUSH_BATCH_INTERVAL_MINUTES = 5
TRANSIENT_SOURCE_HOURS = {
    "navigation_leave_now": 6,
    "navigation_route_change": 6,
    "daily_review": 36,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_attention_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS attention_items (
                attention_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                source_key TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                action_type TEXT NOT NULL DEFAULT 'none',
                action_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                seen_at TEXT,
                dismissed_at TEXT,
                pushed_at TEXT,
                push_attempts INTEGER NOT NULL DEFAULT 0,
                push_error TEXT,
                UNIQUE(user_id,source_type,source_key)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attention_user_active "
            "ON attention_items(user_id,dismissed_at,priority,updated_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attention_push "
            "ON attention_items(user_id,pushed_at,push_attempts,dismissed_at)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS email_attention_feedback (
                user_id INTEGER NOT NULL,
                sender TEXT NOT NULL,
                ignored_count INTEGER NOT NULL DEFAULT 0,
                last_ignored_at TEXT NOT NULL,
                PRIMARY KEY(user_id, sender)
            )"""
        )
        conn.commit()


def _sender_key(value: object) -> str:
    """Normalize email addresses for per-user, per-sender feedback."""
    raw = " ".join(str(value or "").split()).strip().casefold()
    address = parseaddr(raw)[1].casefold()
    return (address or raw)[:320]


def email_sender_ignore_count(user_id: int, sender: object, *, days: int = 30) -> int:
    key = _sender_key(sender)
    if not key:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with db_lock:
        row = conn.execute(
            "SELECT ignored_count FROM email_attention_feedback "
            "WHERE user_id=? AND sender=? AND last_ignored_at>=?",
            (int(user_id), key, cutoff),
        ).fetchone()
    return int(row[0]) if row else 0


def _clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _action_json(payload: object) -> str | None:
    if payload is None or payload == "":
        return None
    if not isinstance(payload, dict):
        raise ValueError("attention action payload must be an object")
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > 24 * 1024:
        raise ValueError("attention action payload is too large")
    return raw


def upsert_attention_item(
    user_id: int,
    *,
    source_type: str,
    source_key: str,
    category: str,
    priority: str,
    title: str,
    body: str = "",
    action_type: str = "none",
    action: dict | None = None,
) -> dict:
    source_type = _clean(source_type, 80)
    source_key = _clean(source_key, 300)
    category = _clean(category, 80) or "assistant"
    priority = str(priority or "normal").strip().lower()
    action_type = str(action_type or "none").strip().lower()
    title = _clean(title, 300)
    body = _clean(body, 1600)
    if not source_type or not source_key or not title:
        raise ValueError("attention source and title are required")
    if priority not in PRIORITIES:
        raise ValueError("unknown attention priority")
    if action_type not in ACTION_TYPES:
        raise ValueError("unknown attention action type")
    raw_action = _action_json(action)
    now = _now()
    with db_lock:
        conn.execute(
            """INSERT INTO attention_items
               (user_id,source_type,source_key,category,priority,title,body,action_type,action_json,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id,source_type,source_key) DO UPDATE SET
                 category=excluded.category,priority=excluded.priority,title=excluded.title,body=excluded.body,
                 action_type=excluded.action_type,action_json=excluded.action_json,updated_at=excluded.updated_at""",
            (
                int(user_id), source_type, source_key, category, priority, title, body,
                action_type, raw_action, now, now,
            ),
        )
        row = conn.execute(
            """SELECT attention_id,user_id,source_type,source_key,category,priority,title,body,action_type,
                      action_json,created_at,updated_at,seen_at,dismissed_at,pushed_at,push_attempts,push_error
               FROM attention_items WHERE user_id=? AND source_type=? AND source_key=?""",
            (int(user_id), source_type, source_key),
        ).fetchone()
        conn.commit()
    return _row_payload(row) if row else {}


def _row_payload(row) -> dict:
    names = (
        "attention_id", "user_id", "source_type", "source_key", "category", "priority", "title", "body",
        "action_type", "action_json", "created_at", "updated_at", "seen_at", "dismissed_at", "pushed_at",
        "push_attempts", "push_error",
    )
    item = dict(zip(names, row))
    raw = item.pop("action_json", None)
    try:
        item["action"] = json.loads(raw) if raw else None
    except (TypeError, json.JSONDecodeError):
        item["action"] = None
    item["attention_id"] = int(item["attention_id"])
    item["user_id"] = int(item["user_id"])
    item["push_attempts"] = int(item.get("push_attempts") or 0)
    item["unseen"] = item.get("seen_at") is None
    return item


def get_attention_item(user_id: int, attention_id: int) -> dict | None:
    with db_lock:
        row = conn.execute(
            """SELECT attention_id,user_id,source_type,source_key,category,priority,title,body,action_type,
                      action_json,created_at,updated_at,seen_at,dismissed_at,pushed_at,push_attempts,push_error
               FROM attention_items WHERE user_id=? AND attention_id=?""",
            (int(user_id), int(attention_id)),
        ).fetchone()
    return _row_payload(row) if row else None


def list_attention_items(user_id: int, *, limit: int = 20) -> list[dict]:
    safe_limit = max(1, min(int(limit), 100))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    with db_lock:
        rows = conn.execute(
            """SELECT attention_id,user_id,source_type,source_key,category,priority,title,body,action_type,
                      action_json,created_at,updated_at,seen_at,dismissed_at,pushed_at,push_attempts,push_error
               FROM attention_items
               WHERE user_id=? AND dismissed_at IS NULL AND updated_at>=?
               ORDER BY CASE WHEN seen_at IS NULL THEN 0 ELSE 1 END,
                        CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                        updated_at DESC,attention_id DESC LIMIT ?""",
            (int(user_id), cutoff, safe_limit),
        ).fetchall()
    return [_row_payload(row) for row in rows]


def pending_attention_pushes(user_id: int, *, limit: int = 3) -> list[dict]:
    safe_limit = max(1, min(int(limit), 10))
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=PUSH_FRESHNESS_HOURS)).isoformat()
    batch_cutoff = (now - timedelta(minutes=PUSH_BATCH_INTERVAL_MINUTES)).isoformat()
    with db_lock:
        recently_pushed = conn.execute(
            "SELECT 1 FROM attention_items WHERE user_id=? AND pushed_at>=? LIMIT 1",
            (int(user_id), batch_cutoff),
        ).fetchone()
        if recently_pushed:
            return []
        rows = conn.execute(
            """SELECT attention_id,user_id,source_type,source_key,category,priority,title,body,action_type,
                      action_json,created_at,updated_at,seen_at,dismissed_at,pushed_at,push_attempts,push_error
               FROM attention_items
               WHERE user_id=? AND dismissed_at IS NULL AND pushed_at IS NULL
                 AND priority IN ('high','normal') AND push_attempts<2 AND created_at>=?
                 AND (push_attempts=0 OR updated_at<=?)
               ORDER BY CASE priority WHEN 'high' THEN 0 ELSE 1 END,created_at ASC,attention_id ASC LIMIT ?""",
            (int(user_id), cutoff, batch_cutoff, safe_limit),
        ).fetchall()
    return [_row_payload(row) for row in rows]


def mark_attention_seen(user_id: int, attention_id: int) -> bool:
    now = _now()
    with db_lock:
        cur = conn.execute(
            "UPDATE attention_items SET seen_at=COALESCE(seen_at,?),updated_at=? WHERE user_id=? AND attention_id=? AND dismissed_at IS NULL",
            (now, now, int(user_id), int(attention_id)),
        )
        conn.commit()
    return bool(cur.rowcount)


def dismiss_attention_item(user_id: int, attention_id: int, *, feedback: bool = True) -> bool:
    """Only explicit individual dismissal teaches email preferences.

    Completing an email action or bulk-clearing the screen is not negative feedback.
    """
    now = _now()
    with db_lock:
        row = conn.execute(
            "SELECT source_type,action_json FROM attention_items "
            "WHERE user_id=? AND attention_id=? AND dismissed_at IS NULL",
            (int(user_id), int(attention_id)),
        ).fetchone()
        if row is None:
            return False
        cur = conn.execute(
            "UPDATE attention_items SET dismissed_at=?,seen_at=COALESCE(seen_at,?),updated_at=? "
            "WHERE user_id=? AND attention_id=? AND dismissed_at IS NULL",
            (now, now, now, int(user_id), int(attention_id)),
        )
        if feedback and cur.rowcount and row[0] == "email_action" and row[1]:
            try:
                action = json.loads(row[1])
                source = action.get("source") if isinstance(action, dict) else None
                sender = _sender_key(source.get("from")) if isinstance(source, dict) else ""
            except (TypeError, ValueError, json.JSONDecodeError):
                sender = ""
            if sender:
                conn.execute(
                    """INSERT INTO email_attention_feedback(user_id,sender,ignored_count,last_ignored_at)
                       VALUES (?,?,1,?)
                       ON CONFLICT(user_id,sender) DO UPDATE SET
                          ignored_count=ignored_count+1,last_ignored_at=excluded.last_ignored_at""",
                    (int(user_id), sender, now),
                )
        conn.commit()
    return bool(cur.rowcount)

def dismiss_all_attention_items(user_id: int) -> int:
    """Dismiss all currently active attention cards for one user, atomically."""
    now = _now()
    with db_lock:
        cur = conn.execute(
            """UPDATE attention_items
               SET dismissed_at=?,seen_at=COALESCE(seen_at,?),updated_at=?
               WHERE user_id=? AND dismissed_at IS NULL""",
            (now, now, now, int(user_id)),
        )
        conn.commit()
        return int(cur.rowcount)


def dismiss_attention_ids(user_id: int, ids: list[int]) -> int:
    """Silently prune stale automatic suggestions without learning from them."""
    unique = sorted({int(value) for value in ids if int(value) > 0})
    if not unique:
        return 0
    now = _now()
    placeholders = ",".join("?" for _ in unique)
    with db_lock:
        cur = conn.execute(
            f"UPDATE attention_items SET dismissed_at=?,seen_at=COALESCE(seen_at,?),updated_at=? "
            f"WHERE user_id=? AND dismissed_at IS NULL AND attention_id IN ({placeholders})",
            (now, now, int(user_id), *unique),
        )
        conn.commit()
        return int(cur.rowcount)


def count_active_attention_items(user_id: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    with db_lock:
        row = conn.execute(
            "SELECT COUNT(*) FROM attention_items "
            "WHERE user_id=? AND dismissed_at IS NULL AND updated_at>=?",
            (int(user_id), cutoff),
        ).fetchone()
    return int(row[0]) if row else 0


def dismiss_attention_source(user_id: int, source_type: str, source_key: str) -> bool:
    source_type = _clean(source_type, 80)
    source_key = _clean(source_key, 300)
    if not source_type or not source_key:
        return False
    now = _now()
    with db_lock:
        cur = conn.execute(
            """UPDATE attention_items
               SET dismissed_at=?,seen_at=COALESCE(seen_at,?),updated_at=?
               WHERE user_id=? AND source_type=? AND source_key=? AND dismissed_at IS NULL""",
            (now, now, now, int(user_id), source_type, source_key),
        )
        conn.commit()
    return bool(cur.rowcount)


def dismiss_missing_source_keys(user_id: int, source_type: str, active_keys: set[str]) -> None:
    source_type = _clean(source_type, 80)
    now = _now()
    with db_lock:
        rows = conn.execute(
            "SELECT attention_id,source_key FROM attention_items WHERE user_id=? AND source_type=? AND dismissed_at IS NULL",
            (int(user_id), source_type),
        ).fetchall()
        stale = [int(row[0]) for row in rows if str(row[1]) not in active_keys]
        if stale:
            placeholders = ",".join("?" for _ in stale)
            conn.execute(
                f"UPDATE attention_items SET dismissed_at=?,seen_at=COALESCE(seen_at,?),updated_at=? WHERE user_id=? AND attention_id IN ({placeholders})",
                (now, now, now, int(user_id), *stale),
            )
        conn.commit()


def mark_attention_push_result(user_id: int, attention_id: int, *, success: bool, error: str = "") -> None:
    now = _now()
    with db_lock:
        if success:
            conn.execute(
                "UPDATE attention_items SET pushed_at=?,push_attempts=push_attempts+1,push_error=NULL,updated_at=? WHERE user_id=? AND attention_id=?",
                (now, now, int(user_id), int(attention_id)),
            )
        else:
            conn.execute(
                "UPDATE attention_items SET push_attempts=push_attempts+1,push_error=?,updated_at=? WHERE user_id=? AND attention_id=?",
                (_clean(error, 500) or "push_failed", now, int(user_id), int(attention_id)),
            )
        conn.commit()


def cleanup_attention_store() -> None:
    current = datetime.now(timezone.utc)
    cutoff = (current - timedelta(days=RETENTION_DAYS)).isoformat()
    now = current.isoformat()
    with db_lock:
        for source_type, hours in TRANSIENT_SOURCE_HOURS.items():
            transient_cutoff = (current - timedelta(hours=hours)).isoformat()
            conn.execute(
                """UPDATE attention_items
                   SET dismissed_at=?,seen_at=COALESCE(seen_at,?),updated_at=?
                   WHERE source_type=? AND dismissed_at IS NULL AND updated_at<?""",
                (now, now, now, source_type, transient_cutoff),
            )
        conn.execute("DELETE FROM attention_items WHERE updated_at<?", (cutoff,))
        conn.commit()


init_attention_store()
