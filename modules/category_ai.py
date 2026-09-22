"""AI fallback for calendar category classification.

Deterministic rules remain the primary classifier. This module is called only when
those rules cannot choose a useful category. The model may select only from the
user's existing category keys; invalid or unavailable AI results are ignored.
"""

from __future__ import annotations

import json
import logging
import re

from core.category_store import get_user_categories
from core.feature_access import has_ai_access
from integrations import ai

logger = logging.getLogger(__name__)

_NONE_VALUES = {"", "none", "null", "unknown", "uncategorized", "не знаю", "нет"}


def _parse_choice(raw: object, categories: list[dict]) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None

    if value.startswith("\`\`\`") and value.endswith("\`\`\`"):
        value = re.sub(r"^\`\`\`(?:json|text)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*\`\`\`$", "", value).strip()

    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None

    if isinstance(parsed, dict):
        value = str(parsed.get("category") or parsed.get("key") or "").strip()
    elif isinstance(parsed, str):
        value = parsed.strip()

    value = value.strip().strip("\`'\".,;: ").strip()
    lowered = value.casefold()
    if lowered in _NONE_VALUES:
        return None

    by_key = {str(item["key"]).casefold(): str(item["key"]) for item in categories}
    by_label = {str(item["label"]).casefold(): str(item["key"]) for item in categories}

    if lowered in by_key:
        return by_key[lowered]
    if lowered in by_label:
        return by_label[lowered]

    match = re.fullmatch(
        r"(?:category|категория|key|ключ)\s*[:=\-]\s*([a-z][a-z0-9_-]{0,63})",
        value,
        flags=re.IGNORECASE,
    )
    if match:
        return by_key.get(match.group(1).casefold())
    return None


def classify_event_category(user_id: int, text: str) -> tuple[str, str | None] | None:
    """Return an existing user category chosen by AI, or None on any fallback."""

    clean_text = " ".join(str(text or "").split()).strip()
    if not clean_text or not has_ai_access(user_id) or not ai.is_ai_available():
        return None

    categories = get_user_categories(int(user_id))
    if not categories:
        return None

    for item in categories:
        if str(item["key"]) != "other":
            continue
        label = str(item["label"] or "").strip()
        if label and re.search(rf"\b{re.escape(label)}\b", clean_text, flags=re.IGNORECASE):
            return str(item["key"]), item.get("color_id")

    choices = "\n".join(
        f"- {item['key']} — {item['label']}"
        for item in categories
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Ты классификатор событий календаря. Выбери наиболее подходящую категорию "
                "только из списка пользователя. Названия категорий пользователя важнее "
                "каких-либо стандартных представлений о категориях. "
                "Верни только точный ключ категории без пояснений. "
                "Если ни одна категория разумно не подходит, верни NONE."
            ),
        },
        {
            "role": "user",
            "content": f"Событие: {clean_text}\nКатегории:\n{choices}",
        },
    ]

    try:
        raw = ai.complete(messages, max_tokens=24, temperature=0.0)
    except ai.AIError as exc:
        logger.warning("AI category fallback unavailable for user %s: %s", user_id, exc)
        return None
    except Exception:
        logger.exception("Unexpected AI category fallback failure for user %s", user_id)
        return None

    key = _parse_choice(raw, categories)
    if key is None:
        logger.info("AI category fallback returned no valid category for user %s", user_id)
        return None

    item = next((item for item in categories if str(item["key"]) == key), None)
    if item is None:
        return None
    return key, item.get("color_id")
