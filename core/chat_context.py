"""Short-lived per-user chat context for conversational AI replies."""

from __future__ import annotations

import threading
import time

CHAT_CONTEXT_TTL_SECONDS = 30 * 60
CHAT_CONTEXT_MAX_MESSAGES = 8
CHAT_CONTEXT_MAX_CONTENT = 4000

_lock = threading.RLock()
_history: dict[int, list[dict]] = {}


def _clean_content(value: str) -> str:
    return " ".join(str(value or "").split()).strip()[:CHAT_CONTEXT_MAX_CONTENT]


def _active_items(user_id: int, now: float) -> list[dict]:
    items = _history.get(int(user_id), [])
    cutoff = now - CHAT_CONTEXT_TTL_SECONDS
    active = [item for item in items if float(item.get("ts", 0)) >= cutoff]
    if active:
        _history[int(user_id)] = active[-CHAT_CONTEXT_MAX_MESSAGES:]
    else:
        _history.pop(int(user_id), None)
    return active[-CHAT_CONTEXT_MAX_MESSAGES:]


def recent_chat_messages(user_id: int) -> list[dict]:
    """Return a bounded copy of recent user/assistant turns."""
    with _lock:
        items = _active_items(int(user_id), time.time())
        return [
            {"role": str(item["role"]), "content": str(item["content"])}
            for item in items
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]


def append_chat_exchange(user_id: int, user_text: str, assistant_text: str) -> None:
    """Store one completed AI exchange after the answer has been produced."""
    user = _clean_content(user_text)
    assistant = _clean_content(assistant_text)
    if not user or not assistant:
        return
    now = time.time()
    with _lock:
        items = _active_items(int(user_id), now)
        items.extend(
            (
                {"role": "user", "content": user, "ts": now},
                {"role": "assistant", "content": assistant, "ts": now},
            )
        )
        _history[int(user_id)] = items[-CHAT_CONTEXT_MAX_MESSAGES:]


def clear_chat_context(user_id: int | None = None) -> None:
    """Clear one user's short-term context, or all context for tests/process reset."""
    with _lock:
        if user_id is None:
            _history.clear()
        else:
            _history.pop(int(user_id), None)
