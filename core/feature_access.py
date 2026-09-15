"""Feature access boundary between the free deterministic core and paid AI layer."""

from __future__ import annotations

from datetime import datetime, timezone
import os

from core.db import conn, db_lock

AI_FEATURE = "ai"
AI_ACCESS_MODES = {"beta", "entitled", "off"}
FEATURES = {
    AI_FEATURE: {
        "key": AI_FEATURE,
        "label": "AI assistant",
        "paid": True,
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_expiry(value: str | datetime | None) -> str | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Feature entitlement expiry must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError("Feature entitlement expiry must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _not_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return True
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None:
        return False
    return parsed.astimezone(timezone.utc) > datetime.now(timezone.utc)


def ai_access_mode() -> str:
    """Return the runtime AI access policy.

    beta: authenticated users get AI by default, but an explicit manual deny wins.
    entitled: only users with an active explicit AI entitlement may use AI.
    off: AI is disabled for every user.
    """
    mode = str(os.getenv("AI_ACCESS_MODE", "beta") or "beta").strip().lower()
    return mode if mode in AI_ACCESS_MODES else "off"


def feature_catalog() -> list[dict]:
    return [dict(item) for item in FEATURES.values()]


def init_feature_access_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS feature_entitlements (
                user_id INTEGER NOT NULL,
                feature TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                PRIMARY KEY(user_id, feature)
            )"""
        )
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(feature_entitlements)").fetchall()
        }
        if "expires_at" not in columns:
            conn.execute("ALTER TABLE feature_entitlements ADD COLUMN expires_at TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_feature_entitlements_feature "
            "ON feature_entitlements(feature, enabled, user_id)"
        )
        conn.commit()


def get_feature_entitlement(user_id: int, feature: str) -> dict | None:
    if feature not in FEATURES:
        raise ValueError("Unknown feature")
    with db_lock:
        row = conn.execute(
            "SELECT enabled,source,updated_at,expires_at FROM feature_entitlements "
            "WHERE user_id=? AND feature=?",
            (int(user_id), feature),
        ).fetchone()
    if not row:
        return None
    enabled = bool(int(row[0]))
    expires_at = row[3]
    return {
        "feature": feature,
        "enabled": enabled,
        "active": enabled and _not_expired(expires_at),
        "source": row[1],
        "updated_at": row[2],
        "expires_at": expires_at,
    }


def list_feature_entitlements(user_id: int) -> list[dict]:
    with db_lock:
        rows = conn.execute(
            "SELECT feature,enabled,source,updated_at,expires_at FROM feature_entitlements "
            "WHERE user_id=? ORDER BY feature",
            (int(user_id),),
        ).fetchall()
    result = []
    for row in rows:
        if row[0] not in FEATURES:
            continue
        enabled = bool(int(row[1]))
        result.append(
            {
                "feature": row[0],
                "enabled": enabled,
                "active": enabled and _not_expired(row[4]),
                "source": row[2],
                "updated_at": row[3],
                "expires_at": row[4],
            }
        )
    return result


def set_feature_entitlement(
    user_id: int,
    feature: str,
    enabled: bool,
    *,
    source: str = "manual",
    expires_at: str | datetime | None = None,
    commit: bool = True,
) -> bool:
    if feature not in FEATURES:
        raise ValueError("Unknown feature")
    if not isinstance(enabled, bool):
        raise ValueError("Feature entitlement must be boolean")
    clean_source = " ".join(str(source or "manual").split()).strip()[:80] or "manual"
    expiry = _normalize_expiry(expires_at)
    with db_lock:
        conn.execute(
            "INSERT INTO feature_entitlements "
            "(user_id,feature,enabled,source,updated_at,expires_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(user_id,feature) DO UPDATE SET "
            "enabled=excluded.enabled,source=excluded.source,updated_at=excluded.updated_at,"
            "expires_at=excluded.expires_at",
            (int(user_id), feature, 1 if enabled else 0, clean_source, _now(), expiry),
        )
        if commit:
            conn.commit()
    return enabled


def has_ai_access(user_id: int | None) -> bool:
    mode = ai_access_mode()
    if mode == "off":
        return False
    if user_id is None:
        return mode == "beta"

    entitlement = get_feature_entitlement(int(user_id), AI_FEATURE)
    if entitlement is not None and not entitlement["enabled"]:
        return False
    if mode == "beta":
        return int(user_id) > 0
    return bool(entitlement and entitlement["active"])


def set_ai_entitlement(
    user_id: int,
    enabled: bool,
    *,
    source: str = "manual",
    expires_at: str | datetime | None = None,
    commit: bool = True,
) -> bool:
    return set_feature_entitlement(
        user_id,
        AI_FEATURE,
        enabled,
        source=source,
        expires_at=expires_at,
        commit=commit,
    )


def ai_access_status(user_id: int | None) -> dict:
    mode = ai_access_mode()
    entitlement = get_feature_entitlement(int(user_id), AI_FEATURE) if user_id is not None else None
    return {
        "mode": mode,
        "enabled": has_ai_access(user_id),
        "requires_entitlement": mode == "entitled",
        "entitlement": entitlement,
    }


def ai_enabled_user_ids(*, limit: int = 1000) -> list[int]:
    """Return users whose data may be sent to the AI provider."""
    safe_limit = max(1, min(int(limit), 10000))
    mode = ai_access_mode()
    if mode == "off":
        return []
    with db_lock:
        if mode == "beta":
            rows = conn.execute(
                "SELECT u.user_id FROM users u "
                "LEFT JOIN feature_entitlements e ON e.user_id=u.user_id AND e.feature=? "
                "WHERE e.user_id IS NULL OR e.enabled=1 ORDER BY u.user_id LIMIT ?",
                (AI_FEATURE, safe_limit),
            ).fetchall()
        else:
            now = _now()
            rows = conn.execute(
                "SELECT u.user_id FROM users u "
                "JOIN feature_entitlements e ON e.user_id=u.user_id "
                "WHERE e.feature=? AND e.enabled=1 "
                "AND (e.expires_at IS NULL OR e.expires_at>?) "
                "ORDER BY u.user_id LIMIT ?",
                (AI_FEATURE, now, safe_limit),
            ).fetchall()
    return [int(row[0]) for row in rows]


init_feature_access_store()
