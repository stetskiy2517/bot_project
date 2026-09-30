"""Unified short-term conversation context for planner modules.

All conversational modules share this state instead of keeping their own
"last_*" or "active_*" references. The context contains the current entity,
recent entities, and one pending interaction awaiting clarification.
"""

from __future__ import annotations

from copy import deepcopy
import threading
import time
from types import SimpleNamespace
from typing import Any

CONTEXT_KEY = "smart_planner_context"
SCHEMA_VERSION = 1
ENTITY_TTL_SECONDS = 60 * 60
RECENT_ENTITY_LIMIT = 12

_web_states: dict[int, dict[str, Any]] = {}
_web_states_lock = threading.RLock()


def user_state(user_id: int) -> dict[str, Any]:
    """Return the shared mutable web/PWA conversation state for one user."""
    with _web_states_lock:
        return _web_states.setdefault(int(user_id), {})


def replace_user_state(user_id: int, state: dict[str, Any] | None) -> dict[str, Any]:
    """Load durable state into the one shared in-process mapping without replacing references."""
    with _web_states_lock:
        target = _web_states.setdefault(int(user_id), {})
        target.clear()
        if isinstance(state, dict):
            target.update(state)
        return target


def clear_user_state(user_id: int | None = None) -> None:
    with _web_states_lock:
        if user_id is None:
            _web_states.clear()
        else:
            _web_states.pop(int(user_id), None)


def _user_data(context: Any) -> dict[str, Any]:
    data = getattr(context, "user_data", None)
    if not isinstance(data, dict):
        raise TypeError("Conversation context must expose a mutable user_data dict")
    return data


def _legacy_entity(data: dict[str, Any]) -> dict[str, Any] | None:
    """Migrate pre-unified short-term references once, without keeping parallel state."""
    note = data.pop("smart_planner_active_note", None)
    if isinstance(note, dict) and note.get("note_id") is not None:
        try:
            touched_at = float(note.get("touched_at") or time.time())
        except (TypeError, ValueError):
            touched_at = time.time()
        return _clean_entity(
            "note",
            note.get("note_id"),
            note.get("title") or "",
            touched_at=touched_at,
        )

    reminder = data.pop("smart_planner_active_reminder", None)
    if not isinstance(reminder, dict):
        reminder = data.pop("smart_planner_last_reminder", None)
    else:
        data.pop("smart_planner_last_reminder", None)
    if isinstance(reminder, dict) and reminder.get("reminder_id") is not None:
        return _clean_entity(
            "reminder",
            reminder.get("reminder_id"),
            reminder.get("text") or "",
        )
    return None


def _root(context: Any, *, create: bool = True) -> dict[str, Any]:
    data = _user_data(context)
    value = data.get(CONTEXT_KEY)
    if isinstance(value, dict):
        if value.get("version") != SCHEMA_VERSION:
            value["version"] = SCHEMA_VERSION
        legacy_pending = data.pop("smart_planner_pending", None)
        if value.get("pending") is None and isinstance(legacy_pending, dict):
            value["pending"] = legacy_pending
        data.pop("smart_planner_active_note", None)
        data.pop("smart_planner_active_reminder", None)
        data.pop("smart_planner_last_reminder", None)
        return value

    legacy_pending = data.pop("smart_planner_pending", None)
    legacy_entity = _legacy_entity(data)
    if not create and not isinstance(legacy_pending, dict) and legacy_entity is None:
        return {}

    value = {
        "version": SCHEMA_VERSION,
        "current_entity": legacy_entity,
        "recent_entities": [legacy_entity] if legacy_entity else [],
        "pending": legacy_pending if isinstance(legacy_pending, dict) else None,
    }
    data[CONTEXT_KEY] = value
    return value


def _clean_entity(
    entity_type: str,
    entity_id: object,
    title: object = "",
    *,
    metadata: dict[str, Any] | None = None,
    touched_at: float | None = None,
) -> dict[str, Any]:
    kind = str(entity_type or "").strip().lower()
    identifier = str(entity_id or "").strip()
    if not kind or not identifier:
        raise ValueError("entity_type and entity_id are required")
    entity = {
        "type": kind,
        "id": identifier,
        "title": " ".join(str(title or "").split()).strip()[:300],
        "touched_at": float(time.time() if touched_at is None else touched_at),
    }
    if metadata:
        entity["metadata"] = deepcopy(metadata)
    return entity


def remember_entity(
    context: Any,
    entity_type: str,
    entity_id: object,
    title: object = "",
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make an entity the shared conversational focus and add it to recents."""
    entity = _clean_entity(entity_type, entity_id, title, metadata=metadata)
    root = _root(context)
    root["current_entity"] = entity
    recents = [
        item for item in (root.get("recent_entities") or [])
        if isinstance(item, dict)
        and not (item.get("type") == entity["type"] and str(item.get("id")) == entity["id"])
    ]
    root["recent_entities"] = [entity, *recents][:RECENT_ENTITY_LIMIT]
    return deepcopy(entity)


def _persist_user_state(user_id: int) -> None:
    """Persist web/PWA context so UI focus survives workers and process restarts."""
    from core.command_store import save_conversation

    save_conversation(int(user_id), user_state(user_id))


def remember_entity_for_user(
    user_id: int,
    entity_type: str,
    entity_id: object,
    title: object = "",
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entity = remember_entity(
        _context_for_user(user_id),
        entity_type,
        entity_id,
        title,
        metadata=metadata,
    )
    _persist_user_state(user_id)
    return entity


def _context_for_user(user_id: int) -> Any:
    return SimpleNamespace(user_data=user_state(user_id))


def current_entity_for_user(
    user_id: int,
    entity_type: str | None = None,
    *,
    now: float | None = None,
    ttl_seconds: int = ENTITY_TTL_SECONDS,
) -> dict[str, Any] | None:
    return current_entity(
        _context_for_user(user_id),
        entity_type,
        now=now,
        ttl_seconds=ttl_seconds,
    )


def current_entity(
    context: Any,
    entity_type: str | None = None,
    *,
    now: float | None = None,
    ttl_seconds: int = ENTITY_TTL_SECONDS,
) -> dict[str, Any] | None:
    root = _root(context, create=False)
    entity = root.get("current_entity")
    if not isinstance(entity, dict):
        return None
    if entity_type and entity.get("type") != str(entity_type).strip().lower():
        return None
    try:
        touched_at = float(entity.get("touched_at"))
    except (TypeError, ValueError):
        clear_current_entity(context)
        return None
    current = time.time() if now is None else float(now)
    if current - touched_at > ttl_seconds:
        clear_current_entity(context)
        return None
    return deepcopy(entity)


def recent_entities(
    context: Any,
    entity_type: str | None = None,
    *,
    now: float | None = None,
    ttl_seconds: int = ENTITY_TTL_SECONDS,
) -> list[dict[str, Any]]:
    root = _root(context, create=False)
    current = time.time() if now is None else float(now)
    result = []
    for item in root.get("recent_entities") or []:
        if not isinstance(item, dict):
            continue
        if entity_type and item.get("type") != str(entity_type).strip().lower():
            continue
        try:
            touched_at = float(item.get("touched_at"))
        except (TypeError, ValueError):
            continue
        if current - touched_at <= ttl_seconds:
            result.append(deepcopy(item))
    return result


def clear_current_entity(context: Any, entity_type: str | None = None, entity_id: object | None = None) -> None:
    root = _root(context, create=False)
    entity = root.get("current_entity")
    if not isinstance(entity, dict):
        return
    if entity_type and entity.get("type") != str(entity_type).strip().lower():
        return
    if entity_id is not None and str(entity.get("id")) != str(entity_id):
        return
    root["current_entity"] = None


def clear_current_entity_for_user(
    user_id: int,
    entity_type: str | None = None,
    entity_id: object | None = None,
) -> None:
    clear_current_entity(_context_for_user(user_id), entity_type, entity_id)
    _persist_user_state(user_id)


def get_pending(context: Any) -> dict[str, Any] | None:
    pending = _root(context, create=False).get("pending")
    return pending if isinstance(pending, dict) else None


def set_pending(context: Any, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or not payload.get("type"):
        raise ValueError("Pending interaction requires a type")
    _root(context)["pending"] = payload


def clear_pending(context: Any) -> None:
    root = _root(context, create=False)
    if root:
        root["pending"] = None


def context_snapshot(context: Any) -> dict[str, Any]:
    """Return a safe copy for tests/debugging without exposing mutable state."""
    return deepcopy(_root(context, create=False))
