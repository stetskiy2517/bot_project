"""Central message routing and calendar intent handling."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from telegram import Update
from telegram.ext import ContextTypes

from modules.calendar_actions import delete_from_text, resume_pending_action, update_from_text
from modules.calendar_availability import free_slots_from_text
from modules.calendar_create import create_from_text
from modules.calendar_event_features import is_all_day
from modules.calendar_user import search_from_text, view_from_text

logger = logging.getLogger(__name__)

INTENT_CREATE = "calendar_create"
INTENT_VIEW = "calendar_view"
INTENT_SEARCH = "calendar_search"
INTENT_UPDATE = "calendar_update"
INTENT_DELETE = "calendar_delete"
INTENT_FREE = "calendar_free_slots"
INTENT_UNKNOWN = "unknown"

CREATE_WORDS = (
    "добавь", "добавить", "создай", "создать", "поставь", "поставить", "запиши",
    "записать", "запланируй", "запланировать", "назначь", "назначить", "внеси",
)
SEARCH_WORDS = (
    "когда у меня", "найди встреч", "найди событ", "найди созвон", "найди звонок",
    "найди запись", "найти встреч", "найти событ", "покажи когда", "покажи где",
)
VIEW_WORDS = (
    "что у меня", "покажи", "покажи календар", "какие встречи", "какие события",
    "что запланировано", "что запланирован", "расписание", "что на неделе",
    "что на неделю", "планы на неделю", "планы на завтра",
)
UPDATE_WORDS = (
    "перенеси", "перенести", "сдвинь", "сдвинуть", "измени", "изменить", "поменяй", "поменять",
    "переименуй", "сделай встреч", "сделай созвон", "сделай событ",
)
DELETE_WORDS = ("удали", "удалить", "отмени", "отменить", "убери", "убрать")
FREE_WORDS = (
    "когда свобод", "когда я свобод", "свободное окно", "свободные окна", "найди время",
    "найди окно", "куда поставить", "есть ли окно", "есть окно",
)
EVENT_WORDS = (
    "встреч", "созвон", "звонок", "врач", "невролог", "стоматолог", "мрт", "узи",
    "трениров", "зал", "кино", "ресторан", "рейс", "полет", "полёт", "поезд", "такси", "совещ",
    "планерк", "клиент", "переговор", "день рождения", "обед", "ужин",
)
DATE_HINT_RE = re.compile(
    r"\b(?:сегодня|завтра|завтро|послезавтра|понедельник\w*|вторник\w*|сред\w*|"
    r"четверг\w*|пятниц\w*|суббот\w*|воскресень\w*|\d{1,2}[./-]\d{1,2}|"
    r"\d{1,2}\s+(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|"
    r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*))\b",
    re.IGNORECASE,
)
TIME_HINT_RE = re.compile(
    r"\b(?:в|к|с)\s*(?:[01]?\d|2[0-3])(?:(?::|\.|\s)[0-5]\d)?\b|"
    r"\b(?:полдень|полночь|половин\w+|без\s+четверти|через\s+\d+\s+(?:минут\w*|час\w*))\b",
    re.IGNORECASE,
)
QUESTION_PREFIX_RE = re.compile(r"^\s*(?:когда|что|где|почему|зачем|как|сколько|есть ли|можно ли)\b", re.IGNORECASE)
WHEN_SEARCH_RE = re.compile(r"^\s*когда\s+(?!свобод\w*\b|я\s+свобод\w*\b|у\s+меня\b)(.+)", re.IGNORECASE)
CURRENT_STATE_RE = re.compile(r"\b(?:сейчас|уже|прямо сейчас)\b", re.IGNORECASE)


@dataclass(frozen=True)
class IntentResult:
    name: str
    confidence: float


def _normalise(text: str) -> str:
    return text.lower().replace("ё", "е").strip()


def detect_intent(text: str) -> IntentResult:
    lower = _normalise(text)
    if any(word in lower for word in DELETE_WORDS):
        return IntentResult(INTENT_DELETE, 0.98)
    if any(word in lower for word in UPDATE_WORDS):
        return IntentResult(INTENT_UPDATE, 0.98)
    if any(word in lower for word in FREE_WORDS):
        return IntentResult(INTENT_FREE, 0.96)
    if any(word in lower for word in SEARCH_WORDS):
        return IntentResult(INTENT_SEARCH, 0.97)
    if WHEN_SEARCH_RE.search(lower):
        return IntentResult(INTENT_SEARCH, 0.93)
    if any(word in lower for word in VIEW_WORDS):
        return IntentResult(INTENT_VIEW, 0.96)
    if any(word in lower for word in CREATE_WORDS):
        return IntentResult(INTENT_CREATE, 0.99)

    has_event = any(word in lower for word in EVENT_WORDS)
    has_date = bool(DATE_HINT_RE.search(lower))
    has_time = bool(TIME_HINT_RE.search(lower))
    is_question = bool(QUESTION_PREFIX_RE.search(lower))
    is_current_state = bool(CURRENT_STATE_RE.search(lower))

    if has_date and has_time and not is_question and not is_current_state:
        return IntentResult(INTENT_CREATE, 0.92)
    if has_event and (has_date or has_time) and not is_question and not is_current_state:
        return IntentResult(INTENT_CREATE, 0.86)
    return IntentResult(INTENT_UNKNOWN, 0.0)


def _needs_time(text: str) -> bool:
    if is_all_day(text):
        return False
    return bool(DATE_HINT_RE.search(text)) and not bool(TIME_HINT_RE.search(text))


def _pending(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    value = context.user_data.get("smart_planner_pending")
    return value if isinstance(value, dict) else None


def _clear_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("smart_planner_pending", None)


async def _resume_pending(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    pending = _pending(context)
    if not pending:
        return False
    if pending.get("type") != "create_time":
        return await resume_pending_action(update, context, text, pending)
    if _normalise(text) in {"отмена", "отменить", "не надо", "нет"}:
        _clear_pending(context)
        await update.message.reply_text("Хорошо, не создаю событие.")
        return True
    combined = f"{pending['text']} {text}"
    _clear_pending(context)
    handled = await create_from_text(update, context, combined)
    if not handled:
        context.user_data["smart_planner_pending"] = pending
        await update.message.reply_text("Не понял время. Напиши, например: 19:00 или в 7 вечера.")
    return True


def _normalise_search_text(text: str) -> str:
    match = WHEN_SEARCH_RE.search(text)
    if match:
        return f"когда у меня {match.group(1)}"
    return text


async def route_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str | None = None) -> bool:
    if not update.message:
        return False
    text = (text if text is not None else update.message.text or "").strip()
    if not text:
        return False
    if await _resume_pending(update, context, text):
        return True
    intent = detect_intent(text)
    logger.info("Router intent=%s confidence=%.2f", intent.name, intent.confidence)
    if intent.name == INTENT_CREATE:
        if _needs_time(text):
            context.user_data["smart_planner_pending"] = {"type": "create_time", "text": text}
            await update.message.reply_text("Во сколько поставить событие?")
            return True
        return await create_from_text(update, context, text)
    if intent.name == INTENT_SEARCH:
        return await search_from_text(update, context, _normalise_search_text(text))
    if intent.name == INTENT_VIEW:
        return await view_from_text(update, context, text)
    if intent.name == INTENT_UPDATE:
        return await update_from_text(update, context, text)
    if intent.name == INTENT_DELETE:
        return await delete_from_text(update, context, text)
    if intent.name == INTENT_FREE:
        return await free_slots_from_text(update, context, text)
    return False


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        handled = await route_text(update, context)
        if handled:
            return
        if update.message:
            await update.message.reply_text("Не понял команду календаря. Например: «врач завтра в 19:00».")
    except Exception:
        logger.exception("Unhandled error in text router")
        if update.message:
            await update.message.reply_text("Не удалось обработать сообщение. Попробуйте ещё раз.")
