"""Deterministic storage for Sales Memory companies, contacts, interactions and commitments."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.db import conn, db_lock

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


def init_sales_memory_store() -> None:
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
            "VALUES (?,?,?,?,?,'open',?,?,?,?,?)",
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


init_sales_memory_store()
