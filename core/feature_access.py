"""Feature access boundary between the free deterministic core and paid AI layer."""

from __future__ import annotations

from datetime import datetime, timezone
import os

from core.db import conn, db_lock

AI_FEATURE = "ai"
AI_ACCESS_MODES = {"beta", "entitled", "off"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ai_access_mode() -> str:
    """Return the runtime AI access policy.

    beta: all authenticated users may use AI (current development/beta mode).
    entitled: only users with an explicit AI entitlement may use AI.
    off: AI is disabled for every user.
    """
    mode = str(os.getenv("AI_ACCESS_MODE", "beta") or "beta").strip().lower()
    return mode if mode in AI_ACCESS_MODES else "off"


def init_feature_access_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS feature_entitlements (
                user_id INTEGER NOT NULL,
                feature TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id, feature)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_feature_entitlements_feature "
            "ON feature_entitlements(feature, enabled, user_id)"
        )
        conn.commit()


def has_ai_access(user_id: int | None) -> bool:
    mode = ai_access_mode()
    if mode == "beta":
        return user_id is None or int(user_id) > 0
    if mode == "off" or user_id is None:
        return False
    with db_lock:
        row = conn.execute(
            "SELECT enabled FROM feature_entitlements WHERE user_id=? AND feature=?",
            (int(user_id), AI_FEATURE),
        ).fetchone()
    return bool(row and int(row[0]) == 1)


def set_ai_entitlement(user_id: int, enabled: bool, *, source: str = "manual") -> bool:
    if not isinstance(enabled, bool):
        raise ValueError("AI entitlement must be boolean")
    clean_source = " ".join(str(source or "manual").split()).strip()[:80] or "manual"
    with db_lock:
        conn.execute(
            "INSERT INTO feature_entitlements (user_id,feature,enabled,source,updated_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(user_id,feature) DO UPDATE SET "
            "enabled=excluded.enabled,source=excluded.source,updated_at=excluded.updated_at",
            (int(user_id), AI_FEATURE, 1 if enabled else 0, clean_source, _now()),
        )
        conn.commit()
    return enabled


def ai_access_status(user_id: int | None) -> dict:
    mode = ai_access_mode()
    return {
        "mode": mode,
        "enabled": has_ai_access(user_id),
        "requires_entitlement": mode == "entitled",
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
                "SELECT user_id FROM users ORDER BY user_id LIMIT ?",
                (safe_limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT u.user_id FROM users u "
                "JOIN feature_entitlements e ON e.user_id=u.user_id "
                "WHERE e.feature=? AND e.enabled=1 ORDER BY u.user_id LIMIT ?",
                (AI_FEATURE, safe_limit),
            ).fetchall()
    return [int(row[0]) for row in rows]


init_feature_access_store()
