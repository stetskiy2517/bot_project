"""Central message routing and calendar intent handling."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from telegram import Update
from telegram.ext import ContextTypes

from modules.calendar_user import create_from_text

logger = logging.getLogger(__name__)

INTENT_CREATE = "calendar_create"
INTENT_VIEW = "calendar_view"
INTENT_UPDATE = "calendar_update"
INTENT_DELETE = "calendar_delete"
INTENT_FREE = "calendar_free_slots"
INTENT_UNKNOWN = "unknown"

CREATE_WORDS = (
    "добавь", "добавить", "создай", "создать", "поставь", "поставить", "запиши",
    "записать", "запланируй", "запланировать", "назначь", "назначить", "внеси",
)
VIEW_WORDS = (
    "что у меня", "покажи", "покажи календар", "какие встречи", "какие события",
    "что запланировано", "что запланирован", "расписание", "когда у меня",
)
UPDATE_WORDS = (
    "перенеси", "перенести", "сдвинь", "сдвинуть", "измени", "изменить", "поменяй", "поменять",
)
DELETE_WORDS = ("удали", "удалить", "отмени", "отменить", "убери", "убрать")
FREE_WORDS = (
    "когда свобод", "свободное окно", "свободные окна", "найди время", "найди окно", "куда поставить",
)
EVENT_WORDS = (
    "встреч", "созвон", "звонок", "врач", "невролог", "стоматолог", "мрт", "узи",
    "трениров", "зал", "кино", "ресторан", "рейс", "поезд", "такси", "совещ",
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
    if any(word in lower for word in VIEW_WORDS):
        return IntentResult(INTENT_VIEW, 0.96)
    if any(word in lower for word in CREATE_WORDS):
        return IntentResult(INTENT_CREATE, 0.99)

    has_event = any(word in lower for word in EVENT_WORDS)
    has_date_or_time = bool(DATE_HINT_RE.search(lower) or TIME_HINT_RE.search(lower))
    if has_event and has_date_or_time:
        return IntentResult(INTENT_CREATE, 0.86)
    return IntentResult(INTENT_UNKNOWN, 0.0)


def _needs_time(text: str) -> bool:
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

    if _normalise(text) in {"отмена", "отменить", "не надо", "нет"}:
        _clear_pending(context)
        await update.message.reply_text("Хорошо, не создаю событие.")
        return True

    if pending.get("type") == "create_time":
        combined = f"{pending['text']} {text}"
        _clear_pending(context)
        handled = await create_from_text(update, context, combined)
        if not handled:
            context.user_data["smart_planner_pending"] = pending
            await update.message.reply_text("Не понял время. Напиши, например: 19:00 или в 7 вечера.")
        return True

    _clear_pending(context)
    return False


async def route_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str | None = None,
) -> bool:
    """Route one text command. Used by both text and voice input."""
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

    if intent.name == INTENT_VIEW:
        await update.message.reply_text("Просмотр календаря подключаю следующим шагом.")
        return True
    if intent.name == INTENT_UPDATE:
        await update.message.reply_text("Перенос и изменение событий подключаю следующим шагом.")
        return True
    if intent.name == INTENT_DELETE:
        await update.message.reply_text("Удаление событий подключаю следующим шагом с подтверждением.")
        return True
    if intent.name == INTENT_FREE:
        await update.message.reply_text("Поиск свободных окон подключаю следующим шагом.")
        return True

    return False


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Telegram text entry point."""
    try:
        handled = await route_text(update, context)
        if handled:
            return
        if update.message:
            await update.message.reply_text(
                "Не понял команду календаря. Например: «поставь врача завтра в 19:00»."
            )
    except Exception:
        logger.exception("Unhandled error in text router")
        if update.message:
            await update.message.reply_text("Не удалось обработать сообщение. Попробуйте ещё раз.")
