"""Administrative account views, roles and immutable action audit."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from core.db import conn, db_lock, get_google_account
from core.feature_access import AI_FEATURE, ai_access_mode, has_ai_access

USER_ROLES = {"user", "admin"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bootstrap_admin_emails() -> set[str]:
    raw = str(os.getenv("ADMIN_EMAILS", "") or "")
    return {item.strip().casefold() for item in raw.split(",") if item.strip()}


def init_admin_store() -> None:
    with db_lock:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "role" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS admin_audit_log (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target_user_id INTEGER,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_admin_audit_created "
            "ON admin_audit_log(created_at DESC, audit_id DESC)"
        )
        conn.commit()


def get_user_role(user_id: int) -> str:
    account = get_google_account(int(user_id))
    if account and str(account.get("email") or "").casefold() in _bootstrap_admin_emails():
        return "admin"
    with db_lock:
        row = conn.execute("SELECT role FROM users WHERE user_id=?", (int(user_id),)).fetchone()
    role = str(row[0] or "user").strip().lower() if row else "user"
    return role if role in USER_ROLES else "user"


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and get_user_role(int(user_id)) == "admin"


def set_user_role(user_id: int, role: str, *, commit: bool = True) -> str:
    clean_role = str(role or "").strip().lower()
    if clean_role not in USER_ROLES:
        raise ValueError("Unknown user role")
    with db_lock:
        cursor = conn.execute("UPDATE users SET role=? WHERE user_id=?", (clean_role, int(user_id)))
        if cursor.rowcount == 0:
            raise ValueError("User not found")
        if commit:
            conn.commit()
    return clean_role


def _row_to_user(row) -> dict:
    user_id = int(row[0])
    explicit = None
    if row[5] is not None:
        explicit = {
            "enabled": bool(int(row[5])),
            "source": row[6],
            "updated_at": row[7],
            "expires_at": row[8],
        }
    return {
        "id": user_id,
        "name": row[1] or "",
        "email": row[2] or "",
        "created_at": row[3],
        "role": get_user_role(user_id),
        "features": {
            AI_FEATURE: {
                "enabled": has_ai_access(user_id),
                "mode": ai_access_mode(),
                "explicit": explicit,
            }
        },
    }


def list_users_for_admin(query: str = "", *, limit: int = 200) -> list[dict]:
    safe_limit = max(1, min(int(limit), 500))
    clean_query = " ".join(str(query or "").split()).strip().casefold()[:200]
    params: list[object] = [AI_FEATURE]
    where = ""
    if clean_query:
        pattern = f"%{clean_query}%"
        where = " WHERE lower(COALESCE(ga.email,'')) LIKE ? OR lower(COALESCE(ga.name,'')) LIKE ?"
        params.extend([pattern, pattern])
    params.append(safe_limit)
    sql = (
        "SELECT ga.user_id,COALESCE(ga.name,u.name,''),ga.email,ga.created_at,u.role,"
        "e.enabled,e.source,e.updated_at,e.expires_at "
        "FROM google_accounts ga JOIN users u ON u.user_id=ga.user_id "
        "LEFT JOIN feature_entitlements e ON e.user_id=ga.user_id AND e.feature=?"
        + where
        + " ORDER BY ga.created_at DESC,ga.user_id DESC LIMIT ?"
    )
    with db_lock:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_user(row) for row in rows]


def get_user_for_admin(user_id: int) -> dict | None:
    with db_lock:
        row = conn.execute(
            "SELECT ga.user_id,COALESCE(ga.name,u.name,''),ga.email,ga.created_at,u.role,"
            "e.enabled,e.source,e.updated_at,e.expires_at "
            "FROM google_accounts ga JOIN users u ON u.user_id=ga.user_id "
            "LEFT JOIN feature_entitlements e ON e.user_id=ga.user_id AND e.feature=? "
            "WHERE ga.user_id=?",
            (AI_FEATURE, int(user_id)),
        ).fetchone()
    return _row_to_user(row) if row else None


def admin_overview() -> dict:
    with db_lock:
        total_users = int(conn.execute("SELECT COUNT(*) FROM google_accounts").fetchone()[0])
        managed_ai = int(
            conn.execute(
                "SELECT COUNT(*) FROM feature_entitlements WHERE feature=?",
                (AI_FEATURE,),
            ).fetchone()[0]
        )
        audit_events = int(conn.execute("SELECT COUNT(*) FROM admin_audit_log").fetchone()[0])
    return {
        "users": total_users,
        "managed_ai": managed_ai,
        "audit_events": audit_events,
        "ai_mode": ai_access_mode(),
    }


def record_admin_audit(
    admin_user_id: int,
    action: str,
    *,
    target_user_id: int | None = None,
    details: dict | None = None,
    commit: bool = True,
) -> int:
    clean_action = " ".join(str(action or "").split()).strip()[:100]
    if not clean_action:
        raise ValueError("Audit action is required")
    payload = json.dumps(details or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) > 8192:
        raise ValueError("Audit details are too large")
    with db_lock:
        cursor = conn.execute(
            "INSERT INTO admin_audit_log "
            "(admin_user_id,action,target_user_id,details_json,created_at) VALUES (?,?,?,?,?)",
            (
                int(admin_user_id),
                clean_action,
                int(target_user_id) if target_user_id is not None else None,
                payload,
                _now(),
            ),
        )
        if commit:
            conn.commit()
        return int(cursor.lastrowid)


def list_admin_audit(*, limit: int = 100) -> list[dict]:
    safe_limit = max(1, min(int(limit), 500))
    with db_lock:
        rows = conn.execute(
            "SELECT a.audit_id,a.admin_user_id,aa.email,a.action,a.target_user_id,ta.email,"
            "a.details_json,a.created_at FROM admin_audit_log a "
            "LEFT JOIN google_accounts aa ON aa.user_id=a.admin_user_id "
            "LEFT JOIN google_accounts ta ON ta.user_id=a.target_user_id "
            "ORDER BY a.audit_id DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
    result = []
    for row in rows:
        try:
            details = json.loads(row[6]) if row[6] else {}
        except (TypeError, json.JSONDecodeError):
            details = {}
        result.append(
            {
                "id": int(row[0]),
                "admin_user_id": int(row[1]),
                "admin_email": row[2],
                "action": row[3],
                "target_user_id": int(row[4]) if row[4] is not None else None,
                "target_email": row[5],
                "details": details,
                "created_at": row[7],
            }
        )
    return result


init_admin_store()
