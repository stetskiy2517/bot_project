from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from core.db import conn, db_lock

RETENTION_DAYS = 90
RUN_RETENTION_DAYS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_email_auto_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS email_auto_preferences (
                user_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS email_auto_accounts (
                user_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                initialized_at TEXT NOT NULL,
                last_scan_at TEXT,
                last_error TEXT,
                PRIMARY KEY(user_id,account_id)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS email_auto_messages (
                user_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                provider_message_id TEXT,
                state TEXT NOT NULL,
                reason TEXT,
                processed_at TEXT NOT NULL,
                PRIMARY KEY(user_id,account_id,fingerprint)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS email_auto_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                new_messages INTEGER NOT NULL DEFAULT 0,
                candidates INTEGER NOT NULL DEFAULT 0,
                ai_text_calls INTEGER NOT NULL DEFAULT 0,
                attachments_analyzed INTEGER NOT NULL DEFAULT 0,
                auto_created INTEGER NOT NULL DEFAULT 0,
                already_present INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                plan_json TEXT
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_email_auto_messages_user ON email_auto_messages(user_id,processed_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_email_auto_runs_user ON email_auto_runs(user_id,created_at)")
        conn.commit()


def email_auto_enabled(user_id: int) -> bool:
    with db_lock:
        row = conn.execute("SELECT enabled FROM email_auto_preferences WHERE user_id=?", (int(user_id),)).fetchone()
    return bool(row and row[0])


def set_email_auto_enabled(user_id: int, enabled: bool) -> bool:
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be boolean")
    stamp = _now()
    with db_lock:
        row = conn.execute("SELECT enabled FROM email_auto_preferences WHERE user_id=?", (int(user_id),)).fetchone()
        was_enabled = bool(row and row[0])
        if enabled and not was_enabled:
            # Re-enabling must never backfill mail accumulated while the feature was off.
            # The next worker pass will baseline the current inbox without calling AI.
            conn.execute("DELETE FROM email_auto_accounts WHERE user_id=?", (int(user_id),))
            conn.execute("DELETE FROM email_auto_messages WHERE user_id=?", (int(user_id),))
        conn.execute(
            """INSERT INTO email_auto_preferences(user_id,enabled,updated_at) VALUES (?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at""",
            (int(user_id), 1 if enabled else 0, stamp),
        )
        conn.commit()
    return enabled


def reset_email_auto_baseline(user_id: int) -> None:
    """Forget scan cursors/fingerprints so the next enabled cycle creates a fresh no-AI baseline."""
    with db_lock:
        conn.execute("DELETE FROM email_auto_accounts WHERE user_id=?", (int(user_id),))
        conn.execute("DELETE FROM email_auto_messages WHERE user_id=?", (int(user_id),))
        conn.commit()


def message_fingerprint(account: dict, message: dict) -> str:
    provider = str(account.get("provider") or "").strip().lower()
    provider_id = str(message.get("provider_message_id") or "").strip() if provider == "gmail" else ""
    body = re.sub(r"\s+", " ", str(message.get("body") or message.get("preview") or "")).strip()[:2500]
    attachments = []
    for item in message.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        try:
            size = max(0, int(item.get("size") or 0))
        except (TypeError, ValueError):
            size = 0
        attachments.append(
            (
                str(item.get("filename") or "").strip().casefold()[:255],
                str(item.get("mime_type") or "").strip().casefold()[:200],
                size,
            )
        )
    payload = {
        "provider": provider,
        "provider_id": provider_id,
        "from": re.sub(r"\s+", " ", str(message.get("from") or "")).strip().casefold()[:500],
        "subject": re.sub(r"\s+", " ", str(message.get("subject") or "")).strip().casefold()[:500],
        "date": str(message.get("date") or "").strip()[:200],
        "body": body,
        "attachments": sorted(attachments),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def account_is_initialized(user_id: int, account_id: int) -> bool:
    with db_lock:
        row = conn.execute(
            "SELECT 1 FROM email_auto_accounts WHERE user_id=? AND account_id=?",
            (int(user_id), int(account_id)),
        ).fetchone()
    return bool(row)


def initialize_account(user_id: int, account: dict, messages: list[dict]) -> None:
    account_id = int(account["account_id"])
    stamp = _now()
    with db_lock:
        conn.execute(
            "INSERT OR IGNORE INTO email_auto_accounts(user_id,account_id,initialized_at,last_scan_at,last_error) VALUES (?,?,?,?,NULL)",
            (int(user_id), account_id, stamp, stamp),
        )
        for message in messages:
            fingerprint = message_fingerprint(account, message)
            conn.execute(
                """INSERT OR IGNORE INTO email_auto_messages
                   (user_id,account_id,fingerprint,provider_message_id,state,reason,processed_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    int(user_id),
                    account_id,
                    fingerprint,
                    str(message.get("provider_message_id") or "")[:500],
                    "baseline",
                    "Существовало до включения автоматического разбора.",
                    stamp,
                ),
            )
        conn.commit()


def message_was_processed(user_id: int, account_id: int, fingerprint: str) -> bool:
    with db_lock:
        row = conn.execute(
            "SELECT 1 FROM email_auto_messages WHERE user_id=? AND account_id=? AND fingerprint=?",
            (int(user_id), int(account_id), str(fingerprint)),
        ).fetchone()
    return bool(row)


def mark_message_processed(
    user_id: int,
    account_id: int,
    fingerprint: str,
    provider_message_id: object,
    state: str,
    reason: str = "",
) -> None:
    if state not in {"baseline", "ignored", "analyzed", "failed"}:
        raise ValueError("Unknown automatic email state")
    with db_lock:
        conn.execute(
            """INSERT INTO email_auto_messages
               (user_id,account_id,fingerprint,provider_message_id,state,reason,processed_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(user_id,account_id,fingerprint) DO UPDATE SET
                 provider_message_id=excluded.provider_message_id,
                 state=excluded.state,
                 reason=excluded.reason,
                 processed_at=excluded.processed_at""",
            (
                int(user_id),
                int(account_id),
                str(fingerprint),
                str(provider_message_id or "")[:500],
                state,
                str(reason or "")[:500],
                _now(),
            ),
        )
        conn.commit()


def touch_account_scan(user_id: int, account_id: int, *, error: str | None = None) -> None:
    stamp = _now()
    with db_lock:
        conn.execute(
            """INSERT INTO email_auto_accounts(user_id,account_id,initialized_at,last_scan_at,last_error)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id,account_id) DO UPDATE SET
                 last_scan_at=excluded.last_scan_at,
                 last_error=excluded.last_error""",
            (int(user_id), int(account_id), stamp, stamp, str(error or "")[:500] or None),
        )
        conn.commit()


def record_auto_run(user_id: int, metrics: dict, plan: dict | None = None) -> int:
    plan_json = None
    if isinstance(plan, dict):
        safe_plan = {
            "summary": str(plan.get("summary") or "")[:1400],
            "actions": list(plan.get("actions") or [])[:12],
            "auto_calendar": plan.get("auto_calendar") or {},
        }
        plan_json = json.dumps(safe_plan, ensure_ascii=False, separators=(",", ":"))
    with db_lock:
        cur = conn.execute(
            """INSERT INTO email_auto_runs
               (user_id,created_at,new_messages,candidates,ai_text_calls,attachments_analyzed,auto_created,already_present,failed,plan_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                int(user_id),
                _now(),
                int(metrics.get("new_messages") or 0),
                int(metrics.get("candidates") or 0),
                int(metrics.get("ai_text_calls") or 0),
                int(metrics.get("attachments_analyzed") or 0),
                int(metrics.get("auto_created") or 0),
                int(metrics.get("already_present") or 0),
                int(metrics.get("failed") or 0),
                plan_json,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def recent_auto_plans(user_id: int, *, hours: int = 48, limit: int = 10) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, min(int(hours), 24 * 30)))).isoformat()
    with db_lock:
        rows = conn.execute(
            "SELECT created_at,plan_json FROM email_auto_runs WHERE user_id=? AND created_at>=? AND plan_json IS NOT NULL ORDER BY run_id DESC LIMIT ?",
            (int(user_id), cutoff, max(1, min(int(limit), 50))),
        ).fetchall()
    result = []
    for created_at, raw in rows:
        try:
            plan = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(plan, dict):
            plan["created_at"] = created_at
            result.append(plan)
    return result


def email_auto_status(user_id: int) -> dict:
    with db_lock:
        account = conn.execute(
            "SELECT MAX(last_scan_at),SUM(CASE WHEN last_error IS NOT NULL THEN 1 ELSE 0 END) FROM email_auto_accounts WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
        recent = conn.execute(
            """SELECT COUNT(*),
                      SUM(CASE WHEN state='analyzed' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN state='ignored' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END)
               FROM email_auto_messages
               WHERE user_id=? AND processed_at>=?""",
            (int(user_id), (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()),
        ).fetchone()
        last_run = conn.execute(
            """SELECT created_at,new_messages,candidates,ai_text_calls,attachments_analyzed,auto_created,already_present,failed
               FROM email_auto_runs WHERE user_id=? ORDER BY run_id DESC LIMIT 1""",
            (int(user_id),),
        ).fetchone()
    return {
        "enabled": email_auto_enabled(user_id),
        "last_scan_at": account[0] if account else None,
        "accounts_with_errors": int((account[1] if account else 0) or 0),
        "messages_24h": int((recent[0] if recent else 0) or 0),
        "analyzed_24h": int((recent[1] if recent else 0) or 0),
        "ignored_24h": int((recent[2] if recent else 0) or 0),
        "failed_24h": int((recent[3] if recent else 0) or 0),
        "last_run": (
            {
                "created_at": last_run[0],
                "new_messages": int(last_run[1] or 0),
                "candidates": int(last_run[2] or 0),
                "ai_text_calls": int(last_run[3] or 0),
                "attachments_analyzed": int(last_run[4] or 0),
                "auto_created": int(last_run[5] or 0),
                "already_present": int(last_run[6] or 0),
                "failed": int(last_run[7] or 0),
            }
            if last_run
            else None
        ),
    }


def cleanup_email_auto_store() -> None:
    messages_before = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    runs_before = (datetime.now(timezone.utc) - timedelta(days=RUN_RETENTION_DAYS)).isoformat()
    with db_lock:
        conn.execute("DELETE FROM email_auto_messages WHERE processed_at<?", (messages_before,))
        conn.execute("DELETE FROM email_auto_runs WHERE created_at<?", (runs_before,))
        conn.commit()


init_email_auto_store()
