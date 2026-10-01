"""Privacy-safe per-user activity diagnostics for support and beta testing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from core.db import conn, db_lock

RETENTION_DAYS = 14
MAX_DETAILS_BYTES = 4096


def _now() -> datetime:
    return datetime.now(timezone.utc)


def init_user_activity_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                request_id TEXT,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                endpoint TEXT,
                status INTEGER NOT NULL,
                error_code TEXT,
                duration_ms INTEGER,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_activity_user_created "
            "ON user_activity_log(user_id, activity_id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_activity_created "
            "ON user_activity_log(created_at)"
        )
        cutoff = (_now() - timedelta(days=RETENTION_DAYS)).isoformat()
        conn.execute("DELETE FROM user_activity_log WHERE created_at<?", (cutoff,))
        conn.commit()


def purge_user_activity(*, commit: bool = True) -> int:
    cutoff = (_now() - timedelta(days=RETENTION_DAYS)).isoformat()
    with db_lock:
        cursor = conn.execute("DELETE FROM user_activity_log WHERE created_at<?", (cutoff,))
        if commit:
            conn.commit()
        return int(cursor.rowcount or 0)


def record_user_activity(
    user_id: int,
    *,
    method: str,
    path: str,
    status: int,
    request_id: str | None = None,
    endpoint: str | None = None,
    error_code: str | None = None,
    duration_ms: int | None = None,
    details: dict | None = None,
) -> int:
    payload = json.dumps(details or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_DETAILS_BYTES:
        payload = json.dumps({"truncated": True}, separators=(",", ":"))
    with db_lock:
        cursor = conn.execute(
            "INSERT INTO user_activity_log "
            "(user_id,request_id,method,path,endpoint,status,error_code,duration_ms,details_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                int(user_id),
                str(request_id or "")[:120] or None,
                str(method or "")[:12],
                str(path or "")[:300],
                str(endpoint or "")[:160] or None,
                int(status),
                str(error_code or "")[:120] or None,
                max(0, int(duration_ms)) if duration_ms is not None else None,
                payload,
                _now().isoformat(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_user_activity(user_id: int, *, limit: int = 200) -> list[dict]:
    safe_limit = max(1, min(int(limit), 1000))
    purge_user_activity()
    with db_lock:
        rows = conn.execute(
            "SELECT activity_id,request_id,method,path,endpoint,status,error_code,duration_ms,"
            "details_json,created_at FROM user_activity_log "
            "WHERE user_id=? ORDER BY activity_id DESC LIMIT ?",
            (int(user_id), safe_limit),
        ).fetchall()
    result = []
    for row in rows:
        try:
            details = json.loads(row[8]) if row[8] else {}
        except (TypeError, json.JSONDecodeError):
            details = {}
        result.append(
            {
                "id": int(row[0]),
                "request_id": row[1],
                "method": row[2],
                "path": row[3],
                "endpoint": row[4],
                "status": int(row[5]),
                "error_code": row[6],
                "duration_ms": int(row[7]) if row[7] is not None else None,
                "details": details,
                "created_at": row[9],
            }
        )
    return result


init_user_activity_store()
