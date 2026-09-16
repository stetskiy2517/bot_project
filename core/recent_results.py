"""Short-lived cross-module context for the latest list shown to a user.

Only one list is active at a time. This makes follow-ups like «удали второе»
deterministic without letting old results from another module leak into a new
conversation.
"""

from __future__ import annotations

import re
import time
from typing import Any

RECENT_RESULTS_KEY = "smart_planner_recent_results"
RECENT_RESULTS_TTL_SECONDS = 10 * 60
MAX_RECENT_RESULTS = 20

ORDINAL_RE = re.compile(
    r"\b(?P<ordinal>перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*|шест\w*|"
    r"седьм\w*|восьм\w*|девят\w*|десят\w*|\d{1,2})\b",
    re.IGNORECASE,
)


def _normalise(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split()).strip(" .,!?:;«»\"'")


def ordinal_index(text: str) -> int | None:
    match = ORDINAL_RE.search(_normalise(text))
    if not match:
        return None
    token = match.group("ordinal")
    if token.isdigit():
        index = int(token) - 1
        return index if index >= 0 else None
    stems = (
        ("перв", 0), ("втор", 1), ("трет", 2), ("четверт", 3), ("пят", 4),
        ("шест", 5), ("седьм", 6), ("восьм", 7), ("девят", 8), ("десят", 9),
    )
    for stem, index in stems:
        if token.startswith(stem):
            return index
    return None


def remember_recent_results(
    context: Any,
    kind: str,
    items: list[dict],
    *,
    id_field: str,
    label_field: str,
    now: float | None = None,
) -> None:
    compact = []
    for item in items[:MAX_RECENT_RESULTS]:
        raw_id = item.get(id_field)
        if raw_id in {None, ""}:
            continue
        compact.append({"id": raw_id, "label": str(item.get(label_field) or "").strip()[:300]})
    if not compact:
        context.user_data.pop(RECENT_RESULTS_KEY, None)
        return
    context.user_data[RECENT_RESULTS_KEY] = {
        "kind": str(kind),
        "items": compact,
        "touched_at": time.time() if now is None else float(now),
    }


def clear_recent_results(context: Any, *, kind: str | None = None) -> None:
    value = context.user_data.get(RECENT_RESULTS_KEY)
    if kind is None or not isinstance(value, dict) or value.get("kind") == kind:
        context.user_data.pop(RECENT_RESULTS_KEY, None)


def recent_results(context: Any, kind: str, *, now: float | None = None) -> list[dict]:
    value = context.user_data.get(RECENT_RESULTS_KEY)
    if not isinstance(value, dict) or value.get("kind") != kind:
        return []
    try:
        touched_at = float(value.get("touched_at"))
    except (TypeError, ValueError):
        clear_recent_results(context)
        return []
    current = time.time() if now is None else float(now)
    if current < touched_at or current - touched_at > RECENT_RESULTS_TTL_SECONDS:
        clear_recent_results(context)
        return []
    items = value.get("items")
    return list(items) if isinstance(items, list) else []


def selected_recent_result(context: Any, kind: str, text: str, *, now: float | None = None) -> dict | None:
    index = ordinal_index(text)
    if index is None:
        return None
    items = recent_results(context, kind, now=now)
    if not 0 <= index < len(items):
        return None
    item = items[index]
    return dict(item) if isinstance(item, dict) else None


def recent_kind(context: Any, *, now: float | None = None) -> str | None:
    value = context.user_data.get(RECENT_RESULTS_KEY)
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "")
    return kind if kind and recent_results(context, kind, now=now) else None
