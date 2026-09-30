"""Short-lived reference to the user's most recently created calendar event."""

from __future__ import annotations

import threading
import time

_TTL_SECONDS = 60 * 60
_lock = threading.RLock()
_recent: dict[int, tuple[str, float]] = {}


def remember_calendar_event(user_id: int, event: dict | None) -> None:
    event_id = str((event or {}).get("id") or "").strip()
    if not event_id:
        return
    with _lock:
        _recent[int(user_id)] = (event_id, time.time())


def recent_calendar_event_id(user_id: int) -> str | None:
    now = time.time()
    with _lock:
        item = _recent.get(int(user_id))
        if not item:
            return None
        event_id, created_at = item
        if now - created_at > _TTL_SECONDS:
            _recent.pop(int(user_id), None)
            return None
        return event_id


def clear_calendar_reference(user_id: int | None = None) -> None:
    with _lock:
        if user_id is None:
            _recent.clear()
        else:
            _recent.pop(int(user_id), None)
