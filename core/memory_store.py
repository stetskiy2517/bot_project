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




# Relationship and work-context memory
# Structured tables keep companies/contacts/interactions queryable while remaining
# part of the same user memory subsystem.

COMPANY_STATUSES = {"active", "archived"}
CONTACT_STATUSES = {"active", "archived"}
INTERACTION_TYPES = {"meeting", "call", "email", "message", "note", "other"}
COMMITMENT_STATUSES = {"open", "done", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any, limit: int, *, required: bool = False) -> str | None:
    text = " ".join(str(value or "").split()).strip()
    if required and not text:
        raise ValueError("Required value is empty")
    return text[:limit] if text else None


def _init_relationship_memory_tables() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sales_companies (
                company_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                industry TEXT,
                website TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sales_companies_user_name "
            "ON sales_companies(user_id,status,name)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sales_contacts (
                contact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                company_id INTEGER,
                full_name TEXT NOT NULL,
                position TEXT,
                phone TEXT,
                email TEXT,
                telegram TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sales_contacts_user_name "
            "ON sales_contacts(user_id,status,full_name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sales_contacts_company "
            "ON sales_contacts(user_id,company_id,status)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sales_interactions (
                interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                company_id INTEGER,
                contact_id INTEGER,
                interaction_type TEXT NOT NULL,
                happened_at TEXT NOT NULL,
                summary TEXT NOT NULL,
                outcome TEXT,
                next_step TEXT,
                source_type TEXT,
                source_id TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sales_interactions_user_time "
            "ON sales_interactions(user_id,happened_at DESC,interaction_id DESC)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sales_commitments (
                commitment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                company_id INTEGER,
                contact_id INTEGER,
                title TEXT NOT NULL,
                due_at TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                source_type TEXT,
                source_id TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sales_commitments_user_status_due "
            "ON sales_commitments(user_id,status,due_at)"
        )
        conn.commit()


def create_company(user_id: int, name: str, *, industry: str | None = None, website: str | None = None, notes: str | None = None) -> dict:
    name = _clean(name, 300, required=True)
    now = _now()
    with db_lock:
        duplicate = conn.execute(
            "SELECT company_id FROM sales_companies WHERE user_id=? AND deleted_at IS NULL AND lower(name)=lower(?) LIMIT 1",
            (int(user_id), name),
        ).fetchone()
        if duplicate:
            raise ValueError("Company already exists")
        cur = conn.execute(
            "INSERT INTO sales_companies(user_id,name,industry,website,notes,status,created_at,updated_at) VALUES (?,?,?,?,?,'active',?,?)",
            (int(user_id), name, _clean(industry, 200), _clean(website, 500), _clean(notes, 4000), now, now),
        )
        conn.commit()
        company_id = int(cur.lastrowid)
    return get_company(user_id, company_id)


def get_company(user_id: int, company_id: int) -> dict | None:
    with db_lock:
        row = conn.execute(
            "SELECT company_id,user_id,name,industry,website,notes,status,created_at,updated_at "
            "FROM sales_companies WHERE user_id=? AND company_id=? AND deleted_at IS NULL",
            (int(user_id), int(company_id)),
        ).fetchone()
    if not row:
        return None
    keys = ("company_id","user_id","name","industry","website","notes","status","created_at","updated_at")
    return dict(zip(keys, row))


def list_companies(user_id: int, *, status: str = "active", limit: int = 100) -> list[dict]:
    if status not in COMPANY_STATUSES:
        raise ValueError("Unknown company status")
    with db_lock:
        rows = conn.execute(
            "SELECT company_id,user_id,name,industry,website,notes,status,created_at,updated_at "
            "FROM sales_companies WHERE user_id=? AND status=? AND deleted_at IS NULL ORDER BY name COLLATE NOCASE LIMIT ?",
            (int(user_id), status, max(1, min(int(limit), 500))),
        ).fetchall()
    keys = ("company_id","user_id","name","industry","website","notes","status","created_at","updated_at")
    return [dict(zip(keys, row)) for row in rows]


def create_contact(user_id: int, full_name: str, *, company_id: int | None = None, position: str | None = None, phone: str | None = None, email: str | None = None, telegram: str | None = None, notes: str | None = None) -> dict:
    full_name = _clean(full_name, 300, required=True)
    if company_id is not None and get_company(user_id, company_id) is None:
        raise ValueError("Unknown company")
    now = _now()
    with db_lock:
        cur = conn.execute(
            "INSERT INTO sales_contacts(user_id,company_id,full_name,position,phone,email,telegram,notes,status,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,'active',?,?)",
            (int(user_id), company_id, full_name, _clean(position, 300), _clean(phone, 100), _clean(email, 320), _clean(telegram, 200), _clean(notes, 4000), now, now),
        )
        conn.commit()
        contact_id = int(cur.lastrowid)
    return get_contact(user_id, contact_id)


def get_contact(user_id: int, contact_id: int) -> dict | None:
    with db_lock:
        row = conn.execute(
            "SELECT contact_id,user_id,company_id,full_name,position,phone,email,telegram,notes,status,created_at,updated_at "
            "FROM sales_contacts WHERE user_id=? AND contact_id=? AND deleted_at IS NULL",
            (int(user_id), int(contact_id)),
        ).fetchone()
    if not row:
        return None
    keys = ("contact_id","user_id","company_id","full_name","position","phone","email","telegram","notes","status","created_at","updated_at")
    return dict(zip(keys, row))


def list_contacts(user_id: int, *, company_id: int | None = None, limit: int = 100) -> list[dict]:
    clauses = ["user_id=?", "status='active'", "deleted_at IS NULL"]
    values: list[Any] = [int(user_id)]
    if company_id is not None:
        clauses.append("company_id=?")
        values.append(int(company_id))
    values.append(max(1, min(int(limit), 500)))
    with db_lock:
        rows = conn.execute(
            "SELECT contact_id,user_id,company_id,full_name,position,phone,email,telegram,notes,status,created_at,updated_at "
            f"FROM sales_contacts WHERE {' AND '.join(clauses)} ORDER BY full_name COLLATE NOCASE LIMIT ?",
            values,
        ).fetchall()
    keys = ("contact_id","user_id","company_id","full_name","position","phone","email","telegram","notes","status","created_at","updated_at")
    return [dict(zip(keys, row)) for row in rows]


def record_interaction(user_id: int, interaction_type: str, summary: str, *, happened_at: str | None = None, company_id: int | None = None, contact_id: int | None = None, outcome: str | None = None, next_step: str | None = None, source_type: str | None = None, source_id: str | int | None = None) -> dict:
    interaction_type = str(interaction_type or "").strip().lower()
    if interaction_type not in INTERACTION_TYPES:
        raise ValueError("Unknown interaction type")
    summary = _clean(summary, 4000, required=True)
    if company_id is not None and get_company(user_id, company_id) is None:
        raise ValueError("Unknown company")
    if contact_id is not None and get_contact(user_id, contact_id) is None:
        raise ValueError("Unknown contact")
    timestamp = _clean(happened_at, 80) or _now()
    now = _now()
    with db_lock:
        cur = conn.execute(
            "INSERT INTO sales_interactions(user_id,company_id,contact_id,interaction_type,happened_at,summary,outcome,next_step,source_type,source_id,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (int(user_id), company_id, contact_id, interaction_type, timestamp, summary, _clean(outcome, 4000), _clean(next_step, 2000), _clean(source_type, 64), _clean(source_id, 300), now),
        )
        conn.commit()
        interaction_id = int(cur.lastrowid)
        row = conn.execute(
            "SELECT interaction_id,user_id,company_id,contact_id,interaction_type,happened_at,summary,outcome,next_step,source_type,source_id,created_at "
            "FROM sales_interactions WHERE interaction_id=? AND user_id=?",
            (interaction_id, int(user_id)),
        ).fetchone()
    keys = ("interaction_id","user_id","company_id","contact_id","interaction_type","happened_at","summary","outcome","next_step","source_type","source_id","created_at")
    return dict(zip(keys, row))



def list_interactions(
    user_id: int,
    *,
    company_id: int | None = None,
    contact_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    clauses = ["user_id=?"]
    values: list[Any] = [int(user_id)]
    if company_id is not None:
        clauses.append("company_id=?")
        values.append(int(company_id))
    if contact_id is not None:
        clauses.append("contact_id=?")
        values.append(int(contact_id))
    values.append(max(1, min(int(limit), 500)))
    with db_lock:
        rows = conn.execute(
            "SELECT interaction_id,user_id,company_id,contact_id,interaction_type,happened_at,summary,outcome,next_step,source_type,source_id,created_at "
            f"FROM sales_interactions WHERE {' AND '.join(clauses)} "
            "ORDER BY happened_at DESC,interaction_id DESC LIMIT ?",
            values,
        ).fetchall()
    keys = ("interaction_id","user_id","company_id","contact_id","interaction_type","happened_at","summary","outcome","next_step","source_type","source_id","created_at")
    return [dict(zip(keys, row)) for row in rows]


def create_commitment(user_id: int, title: str, *, due_at: str | None = None, company_id: int | None = None, contact_id: int | None = None, source_type: str | None = None, source_id: str | int | None = None) -> dict:
    title = _clean(title, 500, required=True)
    if company_id is not None and get_company(user_id, company_id) is None:
        raise ValueError("Unknown company")
    if contact_id is not None and get_contact(user_id, contact_id) is None:
        raise ValueError("Unknown contact")
    now = _now()
    with db_lock:
        cur = conn.execute(
            "INSERT INTO sales_commitments(user_id,company_id,contact_id,title,due_at,status,source_type,source_id,created_at,updated_at) "
            "VALUES (?,?,?,?,?,'open',?,?,?,?)",
            (int(user_id), company_id, contact_id, title, _clean(due_at, 80), _clean(source_type, 64), _clean(source_id, 300), now, now),
        )
        conn.commit()
        commitment_id = int(cur.lastrowid)
    return get_commitment(user_id, commitment_id)


def get_commitment(user_id: int, commitment_id: int) -> dict | None:
    with db_lock:
        row = conn.execute(
            "SELECT commitment_id,user_id,company_id,contact_id,title,due_at,status,source_type,source_id,created_at,completed_at,updated_at "
            "FROM sales_commitments WHERE user_id=? AND commitment_id=?",
            (int(user_id), int(commitment_id)),
        ).fetchone()
    if not row:
        return None
    keys = ("commitment_id","user_id","company_id","contact_id","title","due_at","status","source_type","source_id","created_at","completed_at","updated_at")
    return dict(zip(keys, row))


def set_commitment_status(user_id: int, commitment_id: int, status: str) -> dict | None:
    status = str(status or "").strip().lower()
    if status not in COMMITMENT_STATUSES:
        raise ValueError("Unknown commitment status")
    now = _now()
    completed_at = now if status == "done" else None
    with db_lock:
        cur = conn.execute(
            "UPDATE sales_commitments SET status=?,completed_at=?,updated_at=? WHERE user_id=? AND commitment_id=?",
            (status, completed_at, now, int(user_id), int(commitment_id)),
        )
        conn.commit()
    return get_commitment(user_id, commitment_id) if cur.rowcount else None


def list_commitments(user_id: int, *, status: str = "open", limit: int = 100) -> list[dict]:
    if status not in COMMITMENT_STATUSES:
        raise ValueError("Unknown commitment status")
    with db_lock:
        rows = conn.execute(
            "SELECT commitment_id,user_id,company_id,contact_id,title,due_at,status,source_type,source_id,created_at,completed_at,updated_at "
            "FROM sales_commitments WHERE user_id=? AND status=? "
            "ORDER BY CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,due_at,commitment_id LIMIT ?",
            (int(user_id), status, max(1, min(int(limit), 500))),
        ).fetchall()
    keys = ("commitment_id","user_id","company_id","contact_id","title","due_at","status","source_type","source_id","created_at","completed_at","updated_at")
    return [dict(zip(keys, row)) for row in rows]



def search_work_memory(user_id: int, query: str, *, limit: int = 30) -> dict:
    """Search structured work memory without exposing another user's rows."""
    needle = " ".join(str(query or "").split()).strip().casefold()
    if not needle:
        return {"companies": [], "contacts": [], "interactions": [], "commitments": []}
    tokens = [part for part in re.findall(r"[a-zа-яё0-9@.+_-]+", needle, flags=re.IGNORECASE) if len(part) >= 2]
    if not tokens:
        return {"companies": [], "contacts": [], "interactions": [], "commitments": []}

    def matches(*values: Any) -> bool:
        haystack = " ".join(str(value or "") for value in values).casefold().replace("ё", "е")
        return all(token.replace("ё", "е") in haystack for token in tokens)

    companies = [
        item for item in list_companies(user_id, limit=500)
        if matches(item.get("name"), item.get("industry"), item.get("website"), item.get("notes"))
    ][:limit]
    company_ids = {int(item["company_id"]) for item in companies}
    contacts = [
        item for item in list_contacts(user_id, limit=500)
        if matches(item.get("full_name"), item.get("position"), item.get("phone"), item.get("email"), item.get("telegram"), item.get("notes"))
        or (item.get("company_id") is not None and int(item["company_id"]) in company_ids)
    ][:limit]
    contact_ids = {int(item["contact_id"]) for item in contacts}
    interactions = [
        item for item in list_interactions(user_id, limit=500)
        if matches(item.get("summary"), item.get("outcome"), item.get("next_step"))
        or (item.get("company_id") is not None and int(item["company_id"]) in company_ids)
        or (item.get("contact_id") is not None and int(item["contact_id"]) in contact_ids)
    ][:limit]
    commitments = [
        item for item in list_commitments(user_id, status="open", limit=500)
        if matches(item.get("title"))
        or (item.get("company_id") is not None and int(item["company_id"]) in company_ids)
        or (item.get("contact_id") is not None and int(item["contact_id"]) in contact_ids)
    ][:limit]
    return {"companies": companies, "contacts": contacts, "interactions": interactions, "commitments": commitments}


def work_memory_prompt_context(user_id: int, query: str = "", *, limit: int = 20) -> str:
    """Compact structured relationship memory for assistant grounding."""
    if str(query or "").strip():
        data = search_work_memory(user_id, query, limit=limit)
    else:
        data = {
            "companies": list_companies(user_id, limit=limit),
            "contacts": list_contacts(user_id, limit=limit),
            "interactions": list_interactions(user_id, limit=limit),
            "commitments": list_commitments(user_id, status="open", limit=limit),
        }
    company_names = {int(item["company_id"]): item["name"] for item in data["companies"]}
    contact_names = {int(item["contact_id"]): item["full_name"] for item in data["contacts"]}
    payload = {
        "companies": [
            {"name": item["name"], "industry": item.get("industry"), "website": item.get("website"), "notes": item.get("notes")}
            for item in data["companies"]
        ],
        "contacts": [
            {
                "name": item["full_name"], "position": item.get("position"),
                "company": company_names.get(int(item["company_id"])) if item.get("company_id") is not None else None,
                "phone": item.get("phone"), "email": item.get("email"), "telegram": item.get("telegram"), "notes": item.get("notes"),
            }
            for item in data["contacts"]
        ],
        "interactions": [
            {
                "type": item["interaction_type"], "when": item["happened_at"], "summary": item["summary"],
                "outcome": item.get("outcome"), "next_step": item.get("next_step"),
                "company": company_names.get(int(item["company_id"])) if item.get("company_id") is not None else None,
                "contact": contact_names.get(int(item["contact_id"])) if item.get("contact_id") is not None else None,
            }
            for item in data["interactions"][:limit]
        ],
        "open_commitments": [
            {
                "title": item["title"], "due_at": item.get("due_at"),
                "company": company_names.get(int(item["company_id"])) if item.get("company_id") is not None else None,
                "contact": contact_names.get(int(item["contact_id"])) if item.get("contact_id") is not None else None,
            }
            for item in data["commitments"][:limit]
        ],
    }
    if not any(payload.values()):
        return ""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)[:10000]


def update_company(user_id: int, company_id: int, **fields: Any) -> dict | None:
    allowed = {"name": 300, "industry": 200, "website": 500, "notes": 4000}
    updates, values = [], []
    for key, limit in allowed.items():
        if key in fields:
            value = _clean(fields[key], limit, required=(key == "name"))
            updates.append(f"{key}=?")
            values.append(value)
    if not updates:
        raise ValueError("No company fields to update")
    values.extend([_now(), int(user_id), int(company_id)])
    with db_lock:
        cur = conn.execute(
            f"UPDATE sales_companies SET {','.join(updates)},updated_at=? WHERE user_id=? AND company_id=? AND deleted_at IS NULL",
            values,
        )
        conn.commit()
    return get_company(user_id, company_id) if cur.rowcount else None


def update_contact(user_id: int, contact_id: int, **fields: Any) -> dict | None:
    allowed = {"full_name": 300, "position": 300, "phone": 100, "email": 320, "telegram": 200, "notes": 4000}
    updates, values = [], []
    for key, limit in allowed.items():
        if key in fields:
            value = _clean(fields[key], limit, required=(key == "full_name"))
            updates.append(f"{key}=?")
            values.append(value)
    if "company_id" in fields:
        company_id = fields["company_id"]
        if company_id is not None and get_company(user_id, company_id) is None:
            raise ValueError("Unknown company")
        updates.append("company_id=?")
        values.append(company_id)
    if not updates:
        raise ValueError("No contact fields to update")
    values.extend([_now(), int(user_id), int(contact_id)])
    with db_lock:
        cur = conn.execute(
            f"UPDATE sales_contacts SET {','.join(updates)},updated_at=? WHERE user_id=? AND contact_id=? AND deleted_at IS NULL",
            values,
        )
        conn.commit()
    return get_contact(user_id, contact_id) if cur.rowcount else None


init_memory_store()
_init_relationship_memory_tables()
