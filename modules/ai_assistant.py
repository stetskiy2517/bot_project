"""LLM fallback for messages not handled by deterministic product modules."""

from __future__ import annotations

import logging

from core.ai_prompts import chat_system_prompt
from core.assistant_preferences import get_assistant_preferences
from core.memory_store import memory_prompt_context
from integrations.ai import AIError, complete, get_ai_status, is_ai_available
from integrations.navigation_ors import configured as navigation_configured

logger = logging.getLogger(__name__)

UNHANDLED_WEB_MESSAGE = "Не понял команду. Сформулируй её иначе или уточни, что нужно сделать."


def ai_status() -> dict:
    return get_ai_status()


def _runtime_capabilities(user_id: int | None) -> list[str]:
    capabilities = [
        "календарь",
        "напоминания",
        "заметки",
        "колесо жизни",
        "обычный диалог с помощником",
    ]
    try:
        if navigation_configured():
            capabilities.append("навигация и расчёт дороги")
    except Exception:
        logger.exception("Failed to resolve navigation capability")
    if user_id is not None:
        try:
            prefs = get_assistant_preferences(user_id)
            if prefs.get("proactive_reminders_enabled", False):
                capabilities.append("проактивные напоминания")
            if prefs.get("proactive_calendar_events_enabled", False):
                capabilities.append("проактивные события календаря")
        except Exception:
            logger.exception("Failed to resolve proactive capability for user %s", user_id)
    return capabilities


def _system_prompt_for_user(user_id: int | None) -> str:
    memory = ""
    if user_id is not None:
        try:
            memory = memory_prompt_context(user_id)
        except Exception:
            logger.exception("Failed to load AI memory context for user %s", user_id)
    return chat_system_prompt(_runtime_capabilities(user_id), memory)


def answer_unhandled(text: str, *, user_id: int | None = None) -> str | None:
    candidate = " ".join(str(text or "").split()).strip()
    if not candidate or not is_ai_available():
        return None
    try:
        messages = [
            {"role": "system", "content": _system_prompt_for_user(user_id)},
            {"role": "user", "content": candidate[:10000]},
        ]
        return complete(messages, temperature=0.2)
    except AIError as exc:
        logger.warning("AI fallback failed: %s", exc)
        return None
    except Exception:
        logger.exception("Unexpected AI fallback failure")
        return None


def replace_unhandled_reply(replies: list, answer: str) -> list:
    replaced = False
    result = []
    for item in replies:
        if item == UNHANDLED_WEB_MESSAGE and not replaced:
            result.append(answer)
            replaced = True
        else:
            result.append(item)
    return result
