"""Short-lived per-user diagnostics for support and beta debugging."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from core.db import conn, db_lock

RETENTION_DAYS = 7
MAX_DETAILS_BYTES = 16_384


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()


def init_user_activity_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                request_id TEXT,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                status INTEGER,
                phase TEXT,
                error_code TEXT,
                duration_ms INTEGER,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_activity_user_created "
            "ON user_activity_log(user_id, created_at DESC, activity_id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_activity_created "
            "ON user_activity_log(created_at DESC, activity_id DESC)"
        )
        conn.execute("DELETE FROM user_activity_log WHERE created_at<?", (_cutoff(),))
        conn.commit()


def record_user_activity(
    user_id: int,
    *,
    method: str,
    path: str,
    status: int | None = None,
    request_id: str | None = None,
    phase: str | None = None,
    error_code: str | None = None,
    duration_ms: int | None = None,
    details: dict | None = None,
) -> int:
    payload = json.dumps(details or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_DETAILS_BYTES:
        payload = json.dumps(
            {"truncated": True, "keys": sorted((details or {}).keys())[:100]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    with db_lock:
        conn.execute("DELETE FROM user_activity_log WHERE created_at<?", (_cutoff(),))
        cursor = conn.execute(
            "INSERT INTO user_activity_log "
            "(user_id,request_id,method,path,status,phase,error_code,duration_ms,details_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                int(user_id),
                str(request_id or "")[:100] or None,
                str(method or "")[:12],
                str(path or "")[:300],
                int(status) if status is not None else None,
                str(phase or "")[:40] or None,
                str(error_code or "")[:120] or None,
                max(0, int(duration_ms)) if duration_ms is not None else None,
                payload,
                _now(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_user_activity(user_id: int, *, limit: int = 200) -> list[dict]:
    safe_limit = max(1, min(int(limit), 500))
    with db_lock:
        conn.execute("DELETE FROM user_activity_log WHERE created_at<?", (_cutoff(),))
        rows = conn.execute(
            "SELECT activity_id,request_id,method,path,status,phase,error_code,duration_ms,"
            "details_json,created_at FROM user_activity_log "
            "WHERE user_id=? ORDER BY activity_id DESC LIMIT ?",
            (int(user_id), safe_limit),
        ).fetchall()
        conn.commit()
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
                "status": row[4],
                "phase": row[5],
                "error_code": row[6],
                "duration_ms": row[7],
                "details": details,
                "created_at": row[9],
            }
        )
    return result


init_user_activity_store()
