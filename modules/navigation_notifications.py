"""Navigation alerts recorded in the shared attention queue.

The reminder dispatcher owns Web Push delivery, so navigation follows the same
opt-in, quiet hours, deduplication and retry policy as the rest of the assistant.
"""

from __future__ import annotations

from core.attention_store import upsert_attention_item


def _event_key(tag: str) -> str:
    value = str(tag or "navigation").strip()
    return value[len("navigation-"):] if value.startswith("navigation-") else value


def send_navigation_push_for_user(user_id: int, *, title: str, body: str, tag: str) -> bool:
    """Queue one navigation signal; centralized attention delivery sends push if enabled."""
    event_key = _event_key(tag) or "navigation"
    normalized = str(title or "").strip().casefold()
    if normalized == "пора выезжать":
        source_type = "navigation_leave"
        priority = "high"
        action_type = "none"
        action = None
    elif normalized == "нужно оптимизировать расписание":
        source_type = "navigation_optimization"
        priority = "high"
        action_type = "navigation"
        action = {"kind": "optimization", "event_id": event_key}
    else:
        source_type = "navigation_route_change"
        priority = "normal"
        action_type = "none"
        action = None

    item = upsert_attention_item(
        user_id,
        source_type=source_type,
        source_key=event_key,
        category="navigation",
        priority=priority,
        title=str(title or "Маршрут")[:300],
        body=str(body or "")[:1600],
        action_type=action_type,
        action=action,
    )
    return bool(item.get("attention_id"))
