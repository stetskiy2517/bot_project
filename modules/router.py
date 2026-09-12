"""Central message routing for Smart Planner calendar and tasks."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from telegram import Update
from telegram.ext import ContextTypes

from modules.calendar import _extract_time, _relative_offset
from modules.calendar_actions import create_from_text, delete_from_text, resume_pending_action, update_from_text
from modules.calendar_availability import free_slots_from_text
from modules.calendar_event_features import is_all_day
from modules.calendar_user import search_from_text, view_from_text
from modules.tasks import detect_task_intent, handle_task_text, resume_pending_task

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
    "напомни", "напомнить",
)
SEARCH_WORDS = (
    "когда у меня", "найди встреч", "найди событ", "найди созвон", "найди звонок",
    "найди запись", "найти встреч", "найти событ", "покажи когда", "покажи где",
)
VIEW_WORDS = (
    "что у меня", "что мне", "покажи", "покажи календар", "какие встречи", "какие события",
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
    "отпуск", "командиров",
)
ACTION_WORDS = (
    "забрат", "отвез", "купит", "куплю", "оплат", "заех", "позвон", "сход", "поех",
    "получ", "отправ", "подготов", "сдат", "заказ", "заброниров", "встрет", "записат",
    "сдела", "провер", "законч", "выпит", "принят", "лекарств", "таблет",
)
NON_EVENT_STATEMENT_RE = re.compile(
    r"\b(?:погод\w*|прогноз\s+погоды|температур\w*|дожд\w*|снег\w*|градус\w*|"
    r"курс\s+(?:доллар\w*|евро|юан\w*)|новост\w*)\b",
    re.IGNORECASE,
)
INFO_CREATE_QUESTION_RE = re.compile(
    r"^\s*(?:можно\s+ли|как\b|умеешь\s+ли(?:\s+ты)?|можешь\s+ли(?:\s+ты)?)",
    re.IGNORECASE,
)
NEGATED_CREATE_RE = re.compile(
    r"\b(?:не\s+(?:создавай|создай|добавляй|добавь|ставь|поставь|записывай|запиши|"
    r"планируй|запланируй|назначай|назначь|вноси|внеси)|"
    r"не\s+надо\s+(?:создавать|добавлять|ставить|записывать|планировать|назначать|вносить))\b",
    re.IGNORECASE,
)
DATE_HINT_RE = re.compile(
    r"\b(?:сегодня|завтра|завтро|послезавтра|понедельник\w*|вторник\w*|сред\w*|"
    r"четверг\w*|пятниц\w*|суббот\w*|воскресень\w*|пн|вт|ср|чт|пт|сб|вс|\d{1,2}[./-]\d{1,2}|"
    r"следующ\w*\s+недел\w*|\d{1,2}(?:-?го)?\s+(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|"
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
REMIND_ME_AS_COMMAND_RE = re.compile(r"^\s*напомню\b", re.IGNORECASE)
PROPERTY_UPDATE_RE = re.compile(
    r"^\s*(?:добавь|добавить|убери|убрать|удали|удалить|поставь|поставить)\s+"
    r"(?=[^.!?]{0,100}\b(?:напоминани\w*|место|адрес|участник\w*|повтор\w*|приоритет\w*|категори\w*)\b)"
    r"[^.!?]{0,120}\b(?:у|для|к)\s+(?:встреч\w*|событ\w*|созвон\w*|звонк\w*)\b",
    re.IGNORECASE,
)
PROPERTY_DELETE_RE = re.compile(
    r"^\s*(?:убери|убрать|удали|удалить|отмени|отменить)\s+"
    r"(?:напоминани\w*|место|адрес|участник\w*|повтор\w*|приоритет\w*|категори\w*)\s+"
    r"(?:у\s+)?(?:встреч\w*|событ\w*|созвон\w*|звонк\w*)\b",
    re.IGNORECASE,
)
PENDING_CONTROL_REPLIES = {
    "да", "ага", "подтверждаю", "подтвердить", "создавай", "удаляй", "меняй", "ок", "окей",
    "нет", "не надо", "отмена", "отменить", "стоп",
}


@dataclass(frozen=True)
class IntentResult:
    name: str
    confidence: float


def _normalise(text: str) -> str:
    normal = text.lower().replace("ё", "е").strip()
    replacements = {
        "сегодя": "сегодня", "севодня": "сегодня", "завтро": "завтра",
        "послезавтро": "послезавтра", "понеделник": "понедельник",
        "вторниик": "вторник", "четврг": "четверг", "пятнца": "пятница",
        "пятнцу": "пятницу", "пятнитца": "пятница", "субота": "суббота",
        "суботу": "субботу", "воскрсенье": "воскресенье", "сентебря": "сентября",
        "встеча": "встреча", "втреча": "встреча", "созовон": "созвон",
    }
    for wrong, right in replacements.items():
        normal = re.sub(rf"\b{re.escape(wrong)}\b", right, normal)
    return normal


def _creation_text(text: str) -> str:
    """Исправить безопасные разговорные/ASR-варианты только для создания события."""
    if REMIND_ME_AS_COMMAND_RE.search(text):
        return REMIND_ME_AS_COMMAND_RE.sub("напомни", text, count=1)
    return text


def detect_intent(text: str) -> IntentResult:
    lower = _normalise(text)
    if NEGATED_CREATE_RE.search(lower):
        return IntentResult(INTENT_UNKNOWN, 0.0)
    if PROPERTY_UPDATE_RE.search(lower) or PROPERTY_DELETE_RE.search(lower):
        return IntentResult(INTENT_UPDATE, 0.98)
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
    if INFO_CREATE_QUESTION_RE.search(lower) and any(word in lower for word in CREATE_WORDS):
        return IntentResult(INTENT_UNKNOWN, 0.0)
    if REMIND_ME_AS_COMMAND_RE.search(lower):
        return IntentResult(INTENT_CREATE, 0.97)
    if any(word in lower for word in CREATE_WORDS):
        return IntentResult(INTENT_CREATE, 0.99)
    if NON_EVENT_STATEMENT_RE.search(lower):
        return IntentResult(INTENT_UNKNOWN, 0.0)

    has_event = any(word in lower for word in EVENT_WORDS)
    has_action = any(word in lower for word in ACTION_WORDS)
    has_date = bool(DATE_HINT_RE.search(lower))
    has_time = _extract_time(lower) is not None or _relative_offset(lower) is not None
    is_question = bool(QUESTION_PREFIX_RE.search(lower)) or text.rstrip().endswith("?")
    is_current_state = bool(CURRENT_STATE_RE.search(lower))

    if has_date and has_time and not is_question and not is_current_state:
        return IntentResult(INTENT_CREATE, 0.92)
    if has_event and (has_date or has_time) and not is_question and not is_current_state:
        return IntentResult(INTENT_CREATE, 0.86)
    if has_action and (has_date or has_time) and not is_question and not is_current_state:
        return IntentResult(INTENT_CREATE, 0.84)
    return IntentResult(INTENT_UNKNOWN, 0.0)


def _needs_time(text: str) -> bool:
    if is_all_day(text):
        return False
    lower = _normalise(text)
    return bool(DATE_HINT_RE.search(lower)) and _extract_time(lower) is None


def _pending(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    value = context.user_data.get("smart_planner_pending")
    return value if isinstance(value, dict) else None


def _clear_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("smart_planner_pending", None)


def _normalise_pending_reply(text: str) -> str:
    original = text.strip()
    candidate = original.strip(" \t\r\n.,!?;:…\"'«»")
    normal = _normalise(candidate)
    if normal in PENDING_CONTROL_REPLIES or normal.isdigit():
        return candidate
    return original


async def _resume_pending(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    pending = _pending(context)
    if not pending:
        return False
    pending_type = str(pending.get("type") or "")
    reply_text = _normalise_pending_reply(text)
    if pending_type.startswith("task_"):
        return await resume_pending_task(update, context, reply_text, pending)
    if pending_type != "create_time":
        return await resume_pending_action(update, context, reply_text, pending)
    if _normalise(reply_text) in {"отмена", "отменить", "не надо", "нет"}:
        _clear_pending(context)
        await update.message.reply_text("Хорошо, не создаю событие.")
        return True

    reply = reply_text.strip()
    if not re.match(r"^(?:в|к)\b", _normalise(reply)) and _extract_time(f"в {reply}") is not None:
        reply = f"в {reply}"
    combined = f"{pending['text']} {reply}"
    _clear_pending(context)
    handled = await create_from_text(update, context, combined)
    if not handled:
        context.user_data["smart_planner_pending"] = pending
        await update.message.reply_text("Не понял время. Напиши, например: 19:00, 19 или в 7 вечера.")
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

    task_intent = detect_task_intent(text)
    if task_intent:
        logger.info("Router task_intent=%s", task_intent)
        return await handle_task_text(update, context, text, task_intent)

    intent = detect_intent(text)
    logger.info("Router intent=%s confidence=%.2f", intent.name, intent.confidence)
    if intent.name == INTENT_CREATE:
        create_text = _creation_text(text)
        if _needs_time(create_text):
            context.user_data["smart_planner_pending"] = {"type": "create_time", "text": create_text}
            await update.message.reply_text("Во сколько поставить событие?")
            return True
        return await create_from_text(update, context, create_text)
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
            await update.message.reply_text("Не понял команду. Например: «врач завтра в 19:00» или «добавь задачу купить билеты».")
    except Exception:
        logger.exception("Unhandled error in text router")
        if update.message:
            await update.message.reply_text("Не удалось обработать сообщение. Попробуйте ещё раз.")