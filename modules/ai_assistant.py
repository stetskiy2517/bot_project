"""LLM fallback for messages not handled by deterministic product modules."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.ai_prompts import chat_system_prompt
from core.assistant_preferences import get_assistant_preferences
from core.db import get_user_timezone
from core.feature_access import has_ai_access
from core.memory_store import memory_prompt_context
from modules.language_support import detect_input_language
from core.reminder_recurrence import repeat_label
from core.reminder_store import list_active_reminders
from integrations.ai import AIRateLimitError, AIError, complete, get_ai_status, is_ai_available
from integrations.navigation_ors import configured as navigation_configured

logger = logging.getLogger(__name__)

UNHANDLED_WEB_MESSAGE = "Не понял команду. Сформулируй её иначе или уточни, что нужно сделать."
UNHANDLED_WEB_MESSAGE_EN = "I didn't understand the command. Try rephrasing it or add a date/time."
UNHANDLED_MESSAGES = frozenset({UNHANDLED_WEB_MESSAGE, UNHANDLED_WEB_MESSAGE_EN})


def unhandled_reply_for(text: str) -> str:
    return UNHANDLED_WEB_MESSAGE_EN if detect_input_language(text) == "en" else UNHANDLED_WEB_MESSAGE


def is_unhandled_reply(value: object) -> bool:
    return isinstance(value, str) and value in UNHANDLED_MESSAGES


def ai_status() -> dict:
    return get_ai_status()


def _runtime_capabilities(user_id: int | None) -> list[str]:
    capabilities = [
        "календарь",
        "напоминания",
        "заметки",
        "задачи",
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


def _local_reminder_iso(value: object, timezone_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        due = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            zone = timezone.utc
        return due.astimezone(zone).isoformat()
    except (TypeError, ValueError):
        return raw


def _active_reminders_context(user_id: int) -> str:
    """Expose live reminder state as authoritative context instead of learned guesses."""
    try:
        user_timezone = get_user_timezone(user_id, default="UTC") or "UTC"
        reminders = list_active_reminders(user_id, limit=20)
    except Exception:
        logger.exception("Failed to load active reminder context for user %s", user_id)
        return ""

    items = []
    for reminder in reminders:
        timezone_name = str(reminder.get("repeat_timezone") or user_timezone or "UTC")
        item = {
            "text": str(reminder.get("text") or "").strip(),
            "remind_at_local": _local_reminder_iso(reminder.get("remind_at"), timezone_name),
            "timezone": timezone_name,
        }
        label = repeat_label(reminder.get("repeat_rule"))
        if label:
            item["repeat"] = label
        if item["text"]:
            items.append(item)
    return json.dumps(items, ensure_ascii=False, separators=(",", ":")) if items else ""


def _system_prompt_for_user(user_id: int | None) -> str:
    memory = ""
    if user_id is not None:
        try:
            memory = memory_prompt_context(user_id)
        except Exception:
            logger.exception("Failed to load AI memory context for user %s", user_id)
    prompt = chat_system_prompt(_runtime_capabilities(user_id), memory)
    if user_id is not None:
        reminders = _active_reminders_context(user_id)
        if reminders:
            prompt += (
                "\n\nТекущие активные напоминания приложения — фактический и более свежий источник, "
                "чем долговременная память. Если время, повтор или текст здесь конфликтуют с памятью, "
                "доверяй этому блоку. Не выдавай разовое напоминание за привычку без поля repeat.\n"
                f"Активные напоминания: {reminders}"
            )
    return prompt


def _history_messages(history: list[dict] | None) -> list[dict]:
    result = []
    for item in (history or [])[-8:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = " ".join(str(item.get("content") or "").split()).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        result.append({"role": role, "content": content[:4000]})
    return result


def _rate_limit_reply(error: AIRateLimitError) -> str:
    wait = error.retry_after
    if wait is None:
        wait_text = "через несколько секунд"
    else:
        seconds = max(1, int(round(wait)))
        wait_text = f"примерно через {seconds} сек."
    return (
        f"ИИ сейчас временно занят. Попробуй ещё раз {wait_text} "
        "Календарь, напоминания, заметки и другие обычные функции продолжают работать."
    )


def answer_unhandled(
    text: str,
    *,
    user_id: int | None = None,
    history: list[dict] | None = None,
) -> str | None:
    candidate = " ".join(str(text or "").split()).strip()
    if not candidate or not has_ai_access(user_id) or not is_ai_available():
        return None
    try:
        messages = [{"role": "system", "content": _system_prompt_for_user(user_id)}]
        messages.extend(_history_messages(history))
        messages.append({"role": "user", "content": candidate[:10000]})
        return complete(messages, temperature=0.2)
    except AIRateLimitError as exc:
        logger.warning("AI fallback rate-limited: %s", exc)
        return _rate_limit_reply(exc)
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
        if is_unhandled_reply(item) and not replaced:
            result.append(answer)
            replaced = True
        else:
            result.append(item)
    return result
