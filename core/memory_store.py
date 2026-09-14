"""Persistent, model-independent learned memory for the personal secretary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any

from core.db import conn, db_lock

MEMORY_KINDS = {"fact", "preference", "habit", "relationship", "goal", "observation"}
MEMORY_STATUSES = {"active", "suppressed"}
MAX_MEMORY_KEY_LENGTH = 120
MAX_MEMORY_EVIDENCE_LENGTH = 500
MAX_MEMORY_VALUE_BYTES = 8000
MAX_PROCESSING_ATTEMPTS = 5
SELECT_COLUMNS = (
    "memory_id,user_id,kind,memory_key,value_json,confidence,source_type,source_id,"
    "evidence,status,created_at,updated_at"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_key(value: str) -> str:
    key = str(value or "").strip().casefold().replace("ё", "е")
    key = re.sub(r"[^\w-]+", "_", key, flags=re.UNICODE).strip("_-")
    return key[:MAX_MEMORY_KEY_LENGTH]


def _clean_text(value: str, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _normalize_confidence(value: float) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Memory confidence must be numeric") from exc
    return max(0.0, min(1.0, confidence))


def _encode_value(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    if len(payload.encode("utf-8")) > MAX_MEMORY_VALUE_BYTES:
        raise ValueError("Memory value is too large")
    return payload


def _decode_value(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def init_memory_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_memories (
                memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                memory_key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                evidence TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, kind, memory_key)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_memories_active "
            "ON user_memories(user_id, status, updated_at DESC, memory_id DESC)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS ai_memory_event_processing (
                event_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                lease_until TEXT,
                processed_at TEXT
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_memory_processing_user "
            "ON ai_memory_event_processing(user_id, processed_at, lease_until)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS ai_calendar_sync (
                user_id INTEGER NOT NULL,
                google_event_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY(user_id, google_event_id)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_calendar_sync_user_seen "
            "ON ai_calendar_sync(user_id, last_seen_at DESC)"
        )
        conn.commit()


def _from_row(row) -> dict:
    return {
        "memory_id": int(row[0]),
        "user_id": int(row[1]),
        "kind": row[2],
        "key": row[3],
        "value": _decode_value(row[4]),
        "confidence": float(row[5]),
        "source_type": row[6],
        "source_id": row[7],
        "evidence": row[8],
        "status": row[9],
        "created_at": row[10],
        "updated_at": row[11],
    }


def upsert_memory(
    user_id: int,
    kind: str,
    key: str,
    value: Any,
    confidence: float,
    *,
    source_type: str,
    source_id: str | int,
    evidence: str = "",
    commit: bool = True,
) -> dict:
    kind = str(kind or "").strip().lower()
    if kind not in MEMORY_KINDS:
        raise ValueError("Unknown memory kind")
    memory_key = _clean_key(key)
    if not memory_key:
        raise ValueError("Memory key is required")
    value_json = _encode_value(value)
    confidence = _normalize_confidence(confidence)
    source_type = _clean_text(source_type, 64)
    source_id = _clean_text(str(source_id), 200)
    evidence = _clean_text(evidence, MAX_MEMORY_EVIDENCE_LENGTH)
    if not source_type or not source_id:
        raise ValueError("Memory source is required")

    now = _now().isoformat()
    with db_lock:
        existing = conn.execute(
            f"SELECT {SELECT_COLUMNS} FROM user_memories "
            "WHERE user_id=? AND kind=? AND memory_key=?",
            (int(user_id), kind, memory_key),
        ).fetchone()
        if existing:
            previous = _from_row(existing)
            previous_value_json = _encode_value(previous["value"])
            if previous_value_json != value_json and confidence + 0.10 < previous["confidence"]:
                return previous
            resolved_confidence = (
                max(previous["confidence"], confidence)
                if previous_value_json == value_json
                else confidence
            )
            conn.execute(
                "UPDATE user_memories SET value_json=?,confidence=?,source_type=?,source_id=?,"
                "evidence=?,status='active',updated_at=? WHERE memory_id=?",
                (
                    value_json,
                    resolved_confidence,
                    source_type,
                    source_id,
                    evidence,
                    now,
                    previous["memory_id"],
                ),
            )
            memory_id = previous["memory_id"]
        else:
            cur = conn.execute(
                "INSERT INTO user_memories "
                "(user_id,kind,memory_key,value_json,confidence,source_type,source_id,evidence,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    int(user_id),
                    kind,
                    memory_key,
                    value_json,
                    confidence,
                    source_type,
                    source_id,
                    evidence,
                    "active",
                    now,
                    now,
                ),
            )
            memory_id = int(cur.lastrowid)
        if commit:
            conn.commit()
        row = conn.execute(
            f"SELECT {SELECT_COLUMNS} FROM user_memories WHERE memory_id=?",
            (memory_id,),
        ).fetchone()
    return _from_row(row)


def list_memories(
    user_id: int,
    *,
    status: str = "active",
    limit: int = 50,
) -> list[dict]:
    if status not in MEMORY_STATUSES:
        raise ValueError("Unknown memory status")
    safe_limit = max(1, min(int(limit), 500))
    with db_lock:
        rows = conn.execute(
            f"SELECT {SELECT_COLUMNS} FROM user_memories "
            "WHERE user_id=? AND status=? "
            "ORDER BY confidence DESC,updated_at DESC,memory_id DESC LIMIT ?",
            (int(user_id), status, safe_limit),
        ).fetchall()
    return [_from_row(row) for row in rows]


def suppress_memory(user_id: int, memory_id: int) -> bool:
    with db_lock:
        cur = conn.execute(
            "UPDATE user_memories SET status='suppressed',updated_at=? WHERE user_id=? AND memory_id=?",
            (_now().isoformat(), int(user_id), int(memory_id)),
        )
        conn.commit()
    return cur.rowcount > 0


def memory_prompt_context(user_id: int, *, limit: int = 24) -> str:
    memories = list_memories(user_id, limit=limit)
    compact = [
        {
            "kind": item["kind"],
            "key": item["key"],
            "value": item["value"],
            "confidence": round(item["confidence"], 2),
        }
        for item in memories
        if item["confidence"] >= 0.55
    ]
    if not compact:
        return ""
    payload = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    return payload[:7000]


def claim_memory_events(*, limit: int = 3, lease_seconds: int = 180) -> list[dict]:
    safe_limit = max(1, min(int(limit), 20))
    now = _now()
    now_iso = now.isoformat()
    lease_until = (now + timedelta(seconds=max(30, int(lease_seconds)))).isoformat()
    with db_lock:
        rows = conn.execute(
            "SELECT e.event_id,e.user_id,e.entity_type,e.entity_id,e.event_type,e.snapshot_json,e.created_at,"
            "COALESCE(p.attempts,0) "
            "FROM ai_memory_events e "
            "LEFT JOIN ai_memory_event_processing p ON p.event_id=e.event_id "
            "WHERE p.processed_at IS NULL "
            "AND (p.lease_until IS NULL OR p.lease_until<=?) "
            "AND COALESCE(p.attempts,0)<? "
            "ORDER BY e.event_id LIMIT ?",
            (now_iso, MAX_PROCESSING_ATTEMPTS, safe_limit),
        ).fetchall()
        claimed = []
        for row in rows:
            attempts = int(row[7] or 0) + 1
            conn.execute(
                "INSERT INTO ai_memory_event_processing "
                "(event_id,user_id,attempts,last_error,lease_until,processed_at) VALUES (?,?,?,?,?,NULL) "
                "ON CONFLICT(event_id) DO UPDATE SET attempts=excluded.attempts,lease_until=excluded.lease_until",
                (int(row[0]), int(row[1]), attempts, None, lease_until),
            )
            try:
                snapshot = json.loads(row[5])
            except (TypeError, json.JSONDecodeError):
                snapshot = {}
            claimed.append(
                {
                    "event_id": int(row[0]),
                    "user_id": int(row[1]),
                    "entity_type": row[2],
                    "entity_id": row[3],
                    "event_type": row[4],
                    "snapshot": snapshot,
                    "created_at": row[6],
                    "attempts": attempts,
                }
            )
        conn.commit()
    return claimed


def complete_memory_event(event_id: int) -> None:
    with db_lock:
        conn.execute(
            "UPDATE ai_memory_event_processing SET processed_at=?,lease_until=NULL,last_error=NULL WHERE event_id=?",
            (_now().isoformat(), int(event_id)),
        )
        conn.commit()


def fail_memory_event(event_id: int, error: str, *, attempts: int) -> None:
    error = _clean_text(error, 1000) or "unknown error"
    now = _now()
    with db_lock:
        if int(attempts) >= MAX_PROCESSING_ATTEMPTS:
            conn.execute(
                "UPDATE ai_memory_event_processing SET processed_at=?,lease_until=NULL,last_error=? WHERE event_id=?",
                (now.isoformat(), f"abandoned: {error}", int(event_id)),
            )
        else:
            delay = min(900, 30 * (2 ** max(0, int(attempts) - 1)))
            conn.execute(
                "UPDATE ai_memory_event_processing SET lease_until=?,last_error=? WHERE event_id=?",
                ((now + timedelta(seconds=delay)).isoformat(), error, int(event_id)),
            )
        conn.commit()


def calendar_event_change(user_id: int, google_event_id: str, fingerprint: str) -> str:
    event_id = _clean_text(google_event_id, 300)
    fingerprint = _clean_text(fingerprint, 128)
    if not event_id or not fingerprint:
        raise ValueError("Calendar event id and fingerprint are required")
    now = _now().isoformat()
    with db_lock:
        row = conn.execute(
            "SELECT fingerprint FROM ai_calendar_sync WHERE user_id=? AND google_event_id=?",
            (int(user_id), event_id),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO ai_calendar_sync (user_id,google_event_id,fingerprint,last_seen_at) VALUES (?,?,?,?)",
                (int(user_id), event_id, fingerprint, now),
            )
            conn.commit()
            return "created"
        if row[0] == fingerprint:
            conn.execute(
                "UPDATE ai_calendar_sync SET last_seen_at=? WHERE user_id=? AND google_event_id=?",
                (now, int(user_id), event_id),
            )
            conn.commit()
            return "unchanged"
        conn.execute(
            "UPDATE ai_calendar_sync SET fingerprint=?,last_seen_at=? WHERE user_id=? AND google_event_id=?",
            (fingerprint, now, int(user_id), event_id),
        )
        conn.commit()
        return "updated"


def memory_status(user_id: int) -> dict:
    with db_lock:
        memories = conn.execute(
            "SELECT COUNT(*) FROM user_memories WHERE user_id=? AND status='active'",
            (int(user_id),),
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM ai_memory_events e "
            "LEFT JOIN ai_memory_event_processing p ON p.event_id=e.event_id "
            "WHERE e.user_id=? AND p.processed_at IS NULL AND COALESCE(p.attempts,0)<?",
            (int(user_id), MAX_PROCESSING_ATTEMPTS),
        ).fetchone()[0]
    return {"memories": int(memories), "pending_events": int(pending)}


init_memory_store()
