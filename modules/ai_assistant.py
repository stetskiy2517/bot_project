"""LLM fallback for messages not handled by deterministic product modules."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.ai_prompts import chat_system_prompt
from core.assistant_preferences import get_assistant_preferences
from core.db import get_user_timezone
from core.feature_access import has_ai_access
from core.memory_store import memory_prompt_context, work_memory_prompt_context
from modules.language_support import canonicalize_english, detect_input_language
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
            "remind_at_local": _local_reminder_iso(
                reminder.get("scheduled_at") or reminder.get("remind_at"),
                timezone_name,
            ),
            "timezone": timezone_name,
        }
        label = repeat_label(reminder.get("repeat_rule"))
        if label:
            item["repeat"] = label
        if item["text"]:
            items.append(item)
    return json.dumps(items, ensure_ascii=False, separators=(",", ":")) if items else ""


def _system_prompt_for_user(user_id: int | None, query: str = "") -> str:
    memory = ""
    if user_id is not None:
        try:
            memory = memory_prompt_context(user_id)
        except Exception:
            logger.exception("Failed to load AI memory context for user %s", user_id)
    prompt = chat_system_prompt(_runtime_capabilities(user_id), memory)
    if user_id is not None:
        try:
            work_memory = work_memory_prompt_context(user_id, query, limit=30)
        except Exception:
            logger.exception("Failed to load work memory context for user %s", user_id)
            work_memory = ""
        if work_memory:
            prompt += (
                "\n\nРабочая память пользователя — фактический контекст о компаниях, людях, истории "
                "взаимодействий и открытых договорённостях. Используй только эти данные и не додумывай "
                "отсутствующие должности, связи, результаты или сроки. Если пользователь спрашивает, "
                "что было обещано или обсуждалось, опирайся на этот блок.\n"
                f"Рабочая память: {work_memory}"
            )

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


ACTION_GROUNDING_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
ACTION_GROUNDING_IGNORE = {
    "добавь", "добавить", "создай", "создать", "поставь", "поставить",
    "запиши", "записать", "измени", "изменить", "удали", "удалить",
    "покажи", "напомни", "напомнить", "задача", "задачу", "задачи",
    "заметка", "заметку", "заметки", "напоминание", "напоминания",
    "событие", "события", "встреча", "встречу", "встречи", "календарь",
    "дело", "дела", "мне", "мой", "моя", "мою", "пожалуйста",
    "create", "add", "make", "save", "write", "schedule", "plan", "book",
    "show", "list", "delete", "remove", "cancel", "change", "update", "edit",
    "reschedule", "remind", "task", "tasks", "note", "notes", "reminder",
    "reminders", "event", "events", "meeting", "meetings", "calendar",
    "please", "could", "would", "my", "the", "me",
}
NUMERIC_DATE_RE = re.compile(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b")


def _grounding_roots(value: str) -> set[str]:
    roots = set()
    for token in ACTION_GROUNDING_TOKEN_RE.findall(
        str(value or "").casefold().replace("ё", "е")
    ):
        if token in ACTION_GROUNDING_IGNORE or token.isdigit() or len(token) < 4:
            continue
        roots.add(token[:4])
    return roots


def _rewrite_is_grounded(original: str, rewritten: str) -> bool:
    """Reject an AI rewrite that introduces facts absent from the user message."""
    original_roots = _grounding_roots(original)
    rewritten_roots = _grounding_roots(rewritten)
    if rewritten_roots - original_roots:
        return False

    original_dates = set(NUMERIC_DATE_RE.findall(original))
    rewritten_dates = set(NUMERIC_DATE_RE.findall(rewritten))
    if rewritten_dates - original_dates:
        return False

    # Calendar parsers already understand colloquial time. Compare their parsed
    # semantics so normalisation like "в 18" -> "18:00" is allowed, while an
    # invented clock time or relative delay is rejected.
    try:
        from modules.calendar import _extract_time, _relative_offset
        original_for_time = canonicalize_english(original) if detect_input_language(original) == "en" else original
        rewritten_for_time = canonicalize_english(rewritten) if detect_input_language(rewritten) == "en" else rewritten
        original_time = _extract_time(original_for_time)
        rewritten_time = _extract_time(rewritten_for_time)
        if rewritten_time is not None and rewritten_time != original_time:
            return False
        original_offset = _relative_offset(original_for_time)
        rewritten_offset = _relative_offset(rewritten_for_time)
        if rewritten_offset is not None and rewritten_offset != original_offset:
            return False
    except Exception:
        logger.exception("Could not validate AI rewrite time grounding")
        return False
    return True


ACTION_REWRITE_PREFIXES = (
    "добавь задачу ", "создай задачу ", "задача: ",
    "создай заметку ", "заметка: ",
    "напомни ", "создай напоминание ",
    "добавь событие ", "создай событие ", "добавь встречу ", "создай встречу ",
    "покажи задачи", "покажи заметки", "покажи календарь",
    "удали задачу ", "удали заметку ", "удали напоминание ", "удали событие ", "удали встречу ",
    "измени задачу ", "измени напоминание ", "измени событие ", "измени встречу ",
    "create task ", "create note ", "note: ", "remind me ", "create reminder ",
    "schedule ", "show my tasks", "show my notes", "show my calendar",
    "delete task ", "delete note ", "delete reminder ", "delete event ", "delete meeting ",
    "update task ", "update reminder ", "update event ", "update meeting ",
)


def interpret_unhandled_action(text: str, *, user_id: int | None = None) -> str | None:
    """Translate an unhandled action request into one canonical planner command.

    The AI never executes an action here. It may only rewrite the user's request;
    the deterministic router validates and executes the rewritten command.
    """
    candidate = " ".join(str(text or "").split()).strip()
    if not candidate or not has_ai_access(user_id) or not is_ai_available():
        return None

    language = detect_input_language(candidate)
    if language == "en":
        system = (
            "You classify commands for a personal planner. Only rewrite a clear user action into one canonical "
            "English command for the existing deterministic router. Never execute anything and never claim an action "
            "was completed. Supported entities: task, calendar event/meeting, reminder, note. Preserve every explicit "
            "date, time and user-supplied subject. Never invent missing facts. If this is a question, normal chat, advice, "
            "or not an app action, reply exactly NONE. Return only one command, no markdown or explanation. "
            "Use these canonical forms: 'create task ...', 'create note ...', 'remind me ...', 'schedule ...', "
            "'show my tasks', 'show my notes', 'show my calendar', 'delete task ...', 'delete note ...', "
            "'delete reminder ...', 'delete event ...', 'update task ...', 'update reminder ...', 'update event ...'. "
            "Examples: 'put laundry on my todo list today' -> 'create task laundry today'; "
            "'tomorrow at 6 call with Ivan' -> 'schedule call with Ivan tomorrow at 6 PM'; "
            "'jot down buy filters' -> 'create note buy filters'; "
            "'ping me in an hour to call mom' -> 'remind me in an hour to call mom'."
        )
    else:
        system = (
            "Ты классификатор команд персонального планировщика. "
            "Твоя задача — только переписать понятную команду пользователя в одну каноническую команду "
            "для существующего детерминированного роутера. Ничего не выполняй и не утверждай, что действие выполнено. "
            "Поддерживаемые сущности: задача, событие/встреча календаря, напоминание, заметка. "
            "Сохраняй все явные даты, время, название и смысл. Не выдумывай отсутствующие параметры. "
            "Если это вопрос, обычный разговор, просьба о совете или команда не относится к этим сущностям — ответь ровно NONE. "
            "Если это действие, ответь только одной русской канонической командой без кавычек, markdown и пояснений. "
            "Примеры: "
            "«закинь в дела постирать белье сегодня» -> «добавь задачу постирать белье сегодня»; "
            "«завтра в 18 созвон с Иваном» -> «создай встречу созвон с Иваном завтра в 18:00»; "
            "«черкани заметку купить фильтр» -> «создай заметку купить фильтр»; "
            "«маякни через час позвонить маме» -> «напомни через час позвонить маме»."
        )
    try:
        raw = complete(
            [{"role": "system", "content": system}, {"role": "user", "content": candidate[:10000]}],
            max_tokens=160,
            temperature=0.001,
        )
    except AIError as exc:
        logger.warning("AI action rewrite failed: %s", exc)
        return None
    except Exception:
        logger.exception("Unexpected AI action rewrite failure")
        return None

    rewritten = " ".join(str(raw or "").split()).strip().strip("«»\"'")
    if not rewritten or rewritten.upper() == "NONE":
        return None
    lower = rewritten.casefold().replace("ё", "е")
    if not any(lower.startswith(prefix) for prefix in ACTION_REWRITE_PREFIXES):
        logger.warning("AI action rewrite rejected unsafe/noncanonical output: %r", rewritten)
        return None
    if not _rewrite_is_grounded(candidate, rewritten):
        logger.warning("AI action rewrite rejected ungrounded output")
        return None
    return rewritten


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
        messages = [{"role": "system", "content": _system_prompt_for_user(user_id, candidate)}]
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
