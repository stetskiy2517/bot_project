"""Aggregate admin metrics that never expose user content."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.db import conn, db_lock
from integrations.ai import get_ai_status
from integrations.calendar_provider import supported_calendar_providers
from integrations.speech import speech_status
from modules.navigation import navigation_configured, navigation_provider


def _tables() -> set[str]:
    with db_lock:
        return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _count(table: str, where: str = "", params: tuple = ()) -> int:
    if table not in _tables():
        return 0
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += " WHERE " + where
    with db_lock:
        row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0)


def _recent_entity_count(table: str, column: str, since: datetime) -> int:
    return _count(table, f"{column}>=?", (since.isoformat(),))


def product_metrics() -> dict:
    now = datetime.now(timezone.utc)
    week = now - timedelta(days=7)
    month = now - timedelta(days=30)
    tables = _tables()
    users_total = _count("users")
    identities = {}
    if "identity_accounts" in tables:
        with db_lock:
            rows = conn.execute("SELECT provider,COUNT(*) FROM identity_accounts GROUP BY provider").fetchall()
        identities = {str(provider): int(count) for provider, count in rows}
    integrations = {
        "google_calendar_tokens": _count("users", "google_token IS NOT NULL"),
        "email_accounts": _count("email_accounts", "enabled=1") if "email_accounts" in tables else 0,
        "push_subscriptions": _count("push_subscriptions") if "push_subscriptions" in tables else 0,
        "navigation_enabled": _count("navigation_preferences", "enabled=1") if "navigation_preferences" in tables else 0,
        "ai_entitlements_enabled": _count("feature_entitlements", "feature='ai' AND enabled=1") if "feature_entitlements" in tables else 0,
    }
    activity = {
        "notes_7d": _recent_entity_count("notes", "created_at", week),
        "reminders_7d": _recent_entity_count("reminders", "created_at", week),
        "tasks_7d": _recent_entity_count("tasks", "created_at", week),
        "memory_events_7d": _recent_entity_count("ai_memory_events", "created_at", week),
        "notes_30d": _recent_entity_count("notes", "created_at", month),
        "reminders_30d": _recent_entity_count("reminders", "created_at", month),
        "tasks_30d": _recent_entity_count("tasks", "created_at", month),
    }
    total_objects = {
        "notes": _count("notes", "deleted_at IS NULL") if "notes" in tables else 0,
        "reminders": _count("reminders", "deleted_at IS NULL") if "reminders" in tables else 0,
        "tasks": _count("tasks"),
        "learned_memories": _count("user_memories", "status='active'") if "user_memories" in tables else 0,
        "proactive_actions": _count("proactive_actions") if "proactive_actions" in tables else 0,
    }
    return {
        "generated_at": now.isoformat(),
        "users_total": users_total,
        "identities": identities,
        "integrations": integrations,
        "activity": activity,
        "objects": total_objects,
    }


def system_health() -> dict:
    return {
        "ai": get_ai_status(),
        "speech": speech_status(),
        "navigation": {"provider": navigation_provider(), "configured": navigation_configured()},
        "calendar_providers": supported_calendar_providers(),
        "database": {"ok": True},
    }
