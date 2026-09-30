"""Central message routing for Smart Planner calendar, reminders, notes and tasks."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from telegram import Update
from telegram.ext import ContextTypes

from core.conversation_context import clear_pending, current_entity, get_pending, set_pending
from core.conversation_policy import is_declarative_statement, should_resume_pending
from modules.calendar import _extract_time, _relative_offset
from modules.command_templates import handle_template
from modules.calendar_actions import create_from_text, delete_from_text, resume_pending_action, update_from_text
from modules.calendar_availability import free_slots_from_text
from modules.calendar_event_features import is_all_day
from modules.calendar_user import search_from_text, view_from_text
from modules.note_conversation import (
    active_note_addition,
    append_to_note,
    clear_active_note,
    get_active_note,
    open_note_selection,
    remember_after_note_action,
    resolve_named_note_append,
    resolve_named_note_delete,
)
from modules.note_reference import resolve_note_reference
from modules.notes import NOTE_APPEND, NOTE_DELETE, NOTE_SEARCH, detect_note_intent, handle_note_text, resume_pending_note
from modules.reminders import (
    REMINDER_DELETE,
    REMINDER_UPDATE,
    detect_reminder_intent,
    handle_reminder_text,
    resume_pending_reminder,
)
from modules.tasks import (
    TASK_COMPLETE,
    TASK_DELETE,
    TASK_UPDATE,
    detect_task_intent,
    handle_task_text,
    resume_pending_task,
    update_task_from_text,
)

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
    "найди встреч", "найди событ", "найди созвон", "найди звонок",
    "найди запись", "найти встреч", "найти событ", "покажи когда", "покажи где",
    "найди мне встреч", "найди мне событ", "найди мне созвон", "найди мне звонок",
)
VIEW_WORDS = (
    "что у меня", "что мне", "покажи", "покажи календар", "какие встречи", "какие события",
    "что запланировано", "что запланирован", "расписание", "что на неделе",
    "что на неделю", "планы на неделю", "планы на завтра", "какие планы",
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
FREE_QUERY_RE = re.compile(
    r"^\s*(?:когда\b[^.!?]{0,80}\bсвобод\w*\b|есть\s+ли\s+свободн\w*\s+окн\w*)",
    re.IGNORECASE,
)
EVENT_WORDS = (
    "встреч", "созвон", "звонок", "врач", "невролог", "стоматолог", "мрт", "узи",
    "трениров", "зал", "кино", "ресторан", "рейс", "полет", "полёт", "поезд", "такси", "совещ",
    "планерк", "клиент", "переговор", "день рождения", "обед", "ужин", "завтрак",
    "отпуск", "командиров", "прогул", "прагул", "погуля", "прогуля", "пробеж", "театр", "концерт",
    "выстав", "музей", "бассейн", "футбол", "матч", "массаж", "парикмах", "барбер",
    "маникюр", "стриж", "занят", "урок", "лекци", "экзамен", "прием", "приём", "йог", "пилатес",
)
BARE_CREATE_EVENT_WORDS = (
    "встреч", "созвон", "звонок", "врач", "невролог", "стоматолог", "мрт", "узи",
    "трениров", "кино", "совещ", "планерк", "переговор", "день рождения", "обед", "ужин", "завтрак",
    "отпуск", "командиров", "прогул", "прагул", "погуля", "прогуля", "пробеж", "поездк", "театр", "концерт",
    "выстав", "музей", "бассейн", "футбол", "матч", "массаж", "парикмах", "барбер",
    "маникюр", "стриж", "занят", "урок", "лекци", "экзамен", "прием", "приём", "йог", "пилатес",
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
BARE_EVENT_STATEMENT_RE = re.compile(
    r"\b(?:люблю|нравит\w*|полезн\w*|обычн\w*|часто|редко|был\w*|была|были|прошл\w*|"
    r"прошел|прошёл|прошла|закончил\w*|состоял\w*|тяжел\w*|тяжёл\w*)\b",
    re.IGNORECASE,
)
RECURRENCE_DECLARATION_RE = re.compile(
    r"\b(?:ежедневно|еженедельно|раз\s+в\s+недел\w*|"
    r"по\s+(?:будням|выходным|понедельникам|вторникам|средам|четвергам|пятницам|субботам|воскресеньям)|"
    r"кажд\w*\s+(?:будн\w*\s+день|день|утр\w*|вечер\w*|ноч\w*|недел\w*|"
    r"понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*))\b",
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
WHEN_MY_SEARCH_RE = re.compile(r"^\s*когда\s+у\s+меня\b", re.IGNORECASE)
WHEN_SEARCH_RE = re.compile(r"^\s*когда\s+(?!свобод\w*\b|я\s+свобод\w*\b|у\s+меня\b)(.+)", re.IGNORECASE)
TIME_SEARCH_RE = re.compile(r"^\s*во\s+сколько\b", re.IGNORECASE)
SHORT_VIEW_PREFIX_RE = re.compile(
    r"^\s*(?:что\b|есть\s+что(?:-|\s)?нибудь\b|какие\s+планы\b)",
    re.IGNORECASE,
)
TRAILING_BARE_HOUR_RE = re.compile(
    r"(?<!\d)(?P<hour>[01]?\d|2[0-3])(?P<punct>\s*[.!?]*)$",
    re.IGNORECASE,
)
LEADING_BARE_HOUR_RE = re.compile(
    r"^\s*(?P<hour>[01]?\d|2[0-3])\s+(?P<body>\D.+)$",
    re.IGNORECASE,
)
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
PENDING_REPLY_ALIASES = {
    "да конечно": "да",
    "конечно": "да",
    "конечно да": "да",
    "нет спасибо": "нет",
    "не надо спасибо": "не надо",
    "первый": "1", "первая": "1", "первую": "1", "первое": "1",
    "второй": "2", "вторая": "2", "вторую": "2", "второе": "2",
    "третий": "3", "третья": "3", "третью": "3", "третье": "3",
    "четвертый": "4", "четвертая": "4", "четвертую": "4", "четвертое": "4",
    "пятый": "5", "пятая": "5", "пятую": "5", "пятое": "5",
}
FORCE_CONFLICT_REPLIES = {
    "все равно",
    "создай все равно",
    "создавай все равно",
    "оставь время",
    "оставь исходное время",
    "оставь как есть",
    "ставь как есть",
    "все равно ставь",
    "ставь все равно",
    "несмотря на конфликт",
    "создай несмотря на конфликт",
    "создавай несмотря на конфликт",
}
EXPLICIT_ENTITY_TARGET_RE = re.compile(
    r"\b(?:событ\w*|встреч\w*|календар\w*|расписани\w*|напоминани\w*|"
    r"задач\w*|заметк\w*|запис\w*)\b",
    re.IGNORECASE,
)
GENERIC_UPDATE_RE = re.compile(
    r"^\s*(?:измени|изменить|поменяй|поменять|перенеси|перенести|сдвинь|сдвинуть|"
    r"переименуй|переименовать|сделай|добавь|добавить|убери|убрать|поставь|поставить)\b",
    re.IGNORECASE,
)
GENERIC_DELETE_RE = re.compile(
    r"^\s*(?:удали|удалить|убери|убрать|отмени|отменить)\b",
    re.IGNORECASE,
)
GENERIC_COMPLETE_RE = re.compile(
    r"^\s*(?:готово|выполнено|сделано|выполнил|выполнила|закрой|закрыть|заверши|завершить|"
    r"отметь\s+(?:как\s+)?выполненн\w*)\b",
    re.IGNORECASE,
)
LEADING_CONTEXT_REFERENCE_RE = re.compile(
    r"^\s*(?:его|ее|её|это|эту|этот|последн\w*)\b\s*",
    re.IGNORECASE,
)
EVENT_RELATIVE_REMINDER_RE = re.compile(
    r"^\s*(?:и\s+)?(?:(?:напомни|напомнить)(?:\s+мне)?|"
    r"(?:добавь|добавить|поставь|поставить)\s+напоминани\w*)\s+за\s+",
    re.IGNORECASE,
)
CONTEXT_PROPERTY_UPDATE_RE = re.compile(
    r"^\s*(?:и\s+)?(?:"
    r"(?:измени|изменить|поменяй|поменять|перенеси|перенести|сделай|добавь|добавить|"
    r"поставь|поставить|убери|убрать|переименуй|переименовать)\b"
    r"[^.!?]{0,80}\b(?:адрес|место|локаци\w*|длительност\w*|срок\w*|"
    r"приоритет\w*|категори\w*|напоминани\w*|повтор\w*|участник\w*|назван\w*|текст)\b"
    r"|(?:адрес|место|локаци\w*|длительност\w*|срок\w*|приоритет\w*|категори\w*|"
    r"напоминани\w*|повтор\w*|участник\w*|назван\w*|текст)\b"
    r"[^.!?]{0,40}\b(?:измени|изменить|поменяй|поменять|перенеси|перенести|сделай|"
    r"добавь|добавить|поставь|поставить|убери|убрать|переименуй|переименовать)\b"
    r")",
    re.IGNORECASE,
)


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
        "удоли": "удали", "удолить": "удалить", "перинеси": "перенеси", "измини": "измени",
    }
    for wrong, right in replacements.items():
        normal = re.sub(rf"\b{re.escape(wrong)}\b", right, normal)
    return normal


def _contains_event_marker(text: str) -> bool:
    return any(word in text for word in EVENT_WORDS)


def _looks_like_calendar_search(text: str) -> bool:
    return bool(
        DATE_HINT_RE.search(text)
        or _contains_event_marker(text)
        or re.search(r"\b(?:календар|расписан)\w*\b", text, re.IGNORECASE)
    )


def _looks_like_bare_event(text: str) -> bool:
    normal = _normalise(text)
    if BARE_EVENT_STATEMENT_RE.search(normal):
        return False
    tokens = re.findall(r"[a-zа-я0-9]+", normal)
    if not tokens or len(tokens) > 10:
        return False
    if any(normal.startswith(word) for word in BARE_CREATE_EVENT_WORDS):
        return True
    if re.match(r"^(?:после|перед)\b", normal) and any(word in normal for word in BARE_CREATE_EVENT_WORDS):
        return True
    return False


def _creation_text(text: str) -> str:
    result = text
    if REMIND_ME_AS_COMMAND_RE.search(result):
        result = REMIND_ME_AS_COMMAND_RE.sub("напомни", result, count=1)

    lower = _normalise(result)
    command_like = (
        bool(DATE_HINT_RE.search(lower))
        or any(word in lower for word in CREATE_WORDS)
        or _contains_event_marker(lower)
        or _looks_like_bare_event(lower)
    )
    if command_like and _extract_time(lower) is None:
        match = TRAILING_BARE_HOUR_RE.search(result)
        if match:
            result = f"{result[:match.start('hour')]}в {match.group('hour')}{match.group('punct') or ''}"
            lower = _normalise(result)
    if command_like and _extract_time(lower) is None:
        leading = LEADING_BARE_HOUR_RE.match(result)
        if leading:
            result = f"в {leading.group('hour')} {leading.group('body').strip()}"
    return result


def _action_text(text: str) -> str:
    """Repair safe command typos/ASR shorthand before update/delete parsers see the text."""
    result = text
    replacements = {
        r"^\s*удоли\b": "удали",
        r"^\s*удолить\b": "удалить",
        r"^\s*перинеси\b": "перенеси",
        r"^\s*измини\b": "измени",
    }
    for pattern, replacement in replacements.items():
        if re.search(pattern, result, flags=re.IGNORECASE):
            result = re.sub(pattern, replacement, result, count=1, flags=re.IGNORECASE)
            break
    return _creation_text(result)


def detect_intent(text: str) -> IntentResult:
    candidate = _creation_text(text)
    lower = _normalise(candidate)
    if NEGATED_CREATE_RE.search(lower):
        return IntentResult(INTENT_UNKNOWN, 0.0)
    if PROPERTY_UPDATE_RE.search(lower) or PROPERTY_DELETE_RE.search(lower):
        return IntentResult(INTENT_UPDATE, 0.98)
    if any(word in lower for word in DELETE_WORDS):
        return IntentResult(INTENT_DELETE, 0.98)
    if any(word in lower for word in UPDATE_WORDS):
        return IntentResult(INTENT_UPDATE, 0.98)
    if (
        any(word in lower for word in FREE_WORDS)
        or FREE_QUERY_RE.search(lower)
        or re.search(r"\bкогда\b.*\bесть\s+\d+\s*(?:минут|час)", lower)
    ):
        return IntentResult(INTENT_FREE, 0.96)
    if any(word in lower for word in SEARCH_WORDS):
        return IntentResult(INTENT_SEARCH, 0.97)
    if (
        WHEN_MY_SEARCH_RE.search(lower)
        or WHEN_SEARCH_RE.search(lower)
        or TIME_SEARCH_RE.search(lower)
    ) and _looks_like_calendar_search(lower):
        return IntentResult(INTENT_SEARCH, 0.93)

    has_action = any(word in lower for word in ACTION_WORDS)
    if DATE_HINT_RE.search(lower) and SHORT_VIEW_PREFIX_RE.search(lower) and not has_action:
        return IntentResult(INTENT_VIEW, 0.95)
    if any(word in lower for word in VIEW_WORDS):
        return IntentResult(INTENT_VIEW, 0.96)
    if INFO_CREATE_QUESTION_RE.search(lower) and any(word in lower for word in CREATE_WORDS):
        return IntentResult(INTENT_UNKNOWN, 0.0)
    if REMIND_ME_AS_COMMAND_RE.search(lower):
        return IntentResult(INTENT_CREATE, 0.97)
    if any(word in lower for word in CREATE_WORDS):
        return IntentResult(INTENT_CREATE, 0.99)
    if RECURRENCE_DECLARATION_RE.search(lower) and (
        lower.startswith("я ") or not _contains_event_marker(lower)
    ):
        return IntentResult(INTENT_UNKNOWN, 0.0)
    if NON_EVENT_STATEMENT_RE.search(lower):
        return IntentResult(INTENT_UNKNOWN, 0.0)
    if BARE_EVENT_STATEMENT_RE.search(lower):
        return IntentResult(INTENT_UNKNOWN, 0.0)
    if is_declarative_statement(lower):
        return IntentResult(INTENT_UNKNOWN, 0.0)

    has_event = _contains_event_marker(lower)
    has_date = bool(DATE_HINT_RE.search(lower))
    has_time = _extract_time(lower) is not None or _relative_offset(lower) is not None
    is_question = bool(QUESTION_PREFIX_RE.search(lower)) or text.rstrip().endswith("?")
    is_current_state = bool(CURRENT_STATE_RE.search(lower))

    if lower.startswith("я ") and has_time and not has_date and not is_question and not is_current_state:
        return IntentResult(INTENT_UNKNOWN, 0.0)
    if has_date and has_time and not is_question and not is_current_state:
        return IntentResult(INTENT_CREATE, 0.92)
    if has_event and (has_date or has_time) and not is_question and not is_current_state:
        return IntentResult(INTENT_CREATE, 0.86)
    if has_action and (has_date or has_time) and not is_question and not is_current_state:
        return IntentResult(INTENT_CREATE, 0.84)
    if _looks_like_bare_event(lower) and not is_question and not is_current_state:
        return IntentResult(INTENT_CREATE, 0.76)
    return IntentResult(INTENT_UNKNOWN, 0.0)


def _needs_time(text: str) -> bool:
    if is_all_day(text):
        return False
    lower = _normalise(text)
    if _extract_time(lower) is not None or _relative_offset(lower) is not None:
        return False
    return bool(
        DATE_HINT_RE.search(lower)
        or any(word in lower for word in CREATE_WORDS)
        or _looks_like_bare_event(lower)
    )


def _pending(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    return get_pending(context)


def _clear_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_pending(context)


def _normalise_pending_reply(text: str) -> str:
    original = text.strip()
    candidate = original.strip(" \t\r\n.,!?;:…\"'«»")
    normal = _normalise(candidate)
    if normal in PENDING_CONTROL_REPLIES or normal.isdigit():
        return candidate

    spoken = re.sub(r"[,.!?;:…]+", " ", candidate)
    spoken = re.sub(r"\s+", " ", _normalise(spoken)).strip()
    alias = PENDING_REPLY_ALIASES.get(spoken)
    if alias:
        return alias
    return original


def _force_conflict_reply(text: str) -> bool:
    candidate = text.strip(" \t\r\n.,!?;:…\"'«»")
    return _normalise(candidate) in FORCE_CONFLICT_REPLIES


async def _resume_pending(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    pending = _pending(context)
    if not pending:
        return False
    pending_type = str(pending.get("type") or "")
    reply_text = _normalise_pending_reply(text)
    if pending_type == "confirm_create_conflict" and _force_conflict_reply(text):
        reply_text = "да"
    if pending_type.startswith("reminder_"):
        return await resume_pending_reminder(update, context, reply_text, pending)
    if pending_type.startswith("note_"):
        handled = await resume_pending_note(update, context, reply_text, pending)
        if handled and not _pending(context) and pending_type == "note_text":
            user_id = getattr(update.effective_user, "id", None)
            if user_id is not None:
                remember_after_note_action(user_id, context, "note_create", reply_text)
        return handled
    if pending_type.startswith("task_"):
        return await resume_pending_task(update, context, reply_text, pending)
    if pending_type != "create_time":
        return await resume_pending_action(update, context, reply_text, pending)
    if _normalise(reply_text) in {"отмена", "отменить", "не надо", "нет", "стоп"}:
        _clear_pending(context)
        await update.message.reply_text("Хорошо, не создаю событие.")
        return True

    reply = reply_text.strip()
    if not re.match(r"^(?:в|к)\b", _normalise(reply)) and _extract_time(f"в {reply}") is not None:
        reply = f"в {reply}"
    combined = _creation_text(f"{pending['text']} {reply}")
    _clear_pending(context)
    handled = await create_from_text(update, context, combined)
    if not handled:
        set_pending(context, pending)
        await update.message.reply_text("Не понял время. Напиши, например: 19:00, 19 или «завтра в 19».")
    return True


def _normalise_search_text(text: str) -> str:
    match = WHEN_SEARCH_RE.search(text)
    if match:
        return f"когда у меня {match.group(1)}"
    if TIME_SEARCH_RE.search(text):
        return TIME_SEARCH_RE.sub("когда у меня", text, count=1)
    return text


def _has_explicit_calendar_reference(text: str) -> bool:
    """Protect clear calendar reads from contextual note matching."""
    lower = _normalise(text)
    if DATE_HINT_RE.search(lower) or TIME_HINT_RE.search(lower):
        return True
    if _extract_time(lower) is not None or _relative_offset(lower) is not None:
        return True
    calendar_markers = (
        "календар", "расписан", "встреч", "событ", "созвон", "звонок", "свобод", "окно",
    )
    return any(marker in lower for marker in calendar_markers) or _looks_like_bare_event(lower)


def _blocks_active_note_append(text: str) -> bool:
    if _has_explicit_calendar_reference(text):
        return True
    lower = _normalise(text)
    return any(word in lower for word in EVENT_WORDS)


def _context_action_tail(text: str) -> str:
    tail = re.sub(
        r"^\s*(?:измени|изменить|поменяй|поменять|перенеси|перенести|сдвинь|сдвинуть|"
        r"переименуй|переименовать|сделай|добавь|добавить|убери|убрать|поставь|поставить)\b",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    ).strip(" ,.-")
    return LEADING_CONTEXT_REFERENCE_RE.sub("", tail, count=1).strip(" ,.-")


async def _route_current_entity_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> bool:
    if EXPLICIT_ENTITY_TARGET_RE.search(text):
        return False
    reference = current_entity(context)
    if not reference:
        return False

    kind = str(reference.get("type") or "")
    title = str(reference.get("title") or "").strip()
    is_delete = bool(GENERIC_DELETE_RE.match(text))
    is_update = bool(GENERIC_UPDATE_RE.match(text) or CONTEXT_PROPERTY_UPDATE_RE.match(text))
    is_complete = bool(GENERIC_COMPLETE_RE.match(text))
    if not (is_delete or is_update or is_complete):
        return False

    if kind == "calendar_event":
        event_id = str(reference.get("id") or "").strip()
        if is_delete:
            return await delete_from_text(update, context, text)
        if is_update or EVENT_RELATIVE_REMINDER_RE.match(text):
            return await update_from_text(
                update,
                context,
                _action_text(text),
                event_id=event_id or None,
            )
        return False

    if kind == "reminder":
        if is_delete:
            canonical = f"удали напоминание {title}".strip()
            return await handle_reminder_text(update, context, canonical, REMINDER_DELETE)
        if is_update:
            tail = _context_action_tail(text)
            canonical = f"измени напоминание {title}".strip()
            if tail:
                canonical += f" {tail}"
            return await handle_reminder_text(update, context, canonical, REMINDER_UPDATE)
        return False

    if kind == "task":
        if is_delete:
            canonical = f"удали задачу {title}".strip()
            return await handle_task_text(update, context, canonical, TASK_DELETE)
        if is_complete:
            canonical = f"закрой задачу {title}".strip()
            return await handle_task_text(update, context, canonical, TASK_COMPLETE)
        if is_update:
            try:
                task_id = int(reference.get("id"))
            except (TypeError, ValueError):
                return False
            return await update_task_from_text(update, context, text, task_id=task_id)
        return False

    if kind == "note" and is_delete:
        canonical = f"удали заметку {title}".strip()
        handled = await handle_note_text(update, context, canonical, NOTE_DELETE)
        if handled:
            clear_active_note(context)
        return handled

    return False


async def route_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str | None = None) -> bool:
    if not update.message:
        return False
    text = (text if text is not None else update.message.text or "").strip()
    if not text:
        return False
    if await handle_template(update, context, text):
        return True

    pending = _pending(context)
    if pending:
        if should_resume_pending(pending, text):
            if await _resume_pending(update, context, text):
                return True
        else:
            logger.info("Router interrupted pending type=%s with a new command", pending.get("type"))
            _clear_pending(context)

    user_id = getattr(update.effective_user, "id", None)

    # Property edits belong to the focused entity before note-append heuristics
    # can reinterpret generic verbs such as "добавь".
    if CONTEXT_PROPERTY_UPDATE_RE.match(text) and await _route_current_entity_action(update, context, text):
        logger.info("Router updated property of focused entity")
        return True

    # Relative reminder follow-ups ("напомни за час") belong to the focused
    # calendar event, while absolute reminders ("напомни завтра в 9") remain
    # standalone reminders.
    if EVENT_RELATIVE_REMINDER_RE.match(text) and await _route_current_entity_action(update, context, text):
        logger.info("Router attached reminder to focused calendar event")
        return True

    reminder_intent = detect_reminder_intent(text)
    if reminder_intent:
        logger.info("Router reminder_intent=%s", reminder_intent)
        return await handle_reminder_text(update, context, text, reminder_intent)

    note_intent = detect_note_intent(text)
    if note_intent:
        if user_id is not None and note_intent == NOTE_APPEND:
            resolution = resolve_named_note_append(user_id, text)
            if resolution and resolution.addition and len(resolution.matches) == 1:
                logger.info("Router direct note append query=%s", resolution.query)
                return await append_to_note(update, context, resolution.matches[0], resolution.addition)
        logger.info("Router note_intent=%s", note_intent)
        handled = await handle_note_text(update, context, text, note_intent)
        if handled and user_id is not None and not _pending(context):
            remember_after_note_action(user_id, context, note_intent, text)
        return handled

    if user_id is not None and await open_note_selection(update, context, text):
        logger.info("Router ordinal note selection")
        return True

    task_intent = detect_task_intent(text)
    if task_intent:
        logger.info("Router task_intent=%s", task_intent)
        return await handle_task_text(update, context, text, task_intent)

    if user_id is not None:
        resolution = resolve_named_note_append(user_id, text)
        if resolution:
            logger.info("Router named note append query=%s", resolution.query)
            if resolution.addition and len(resolution.matches) == 1:
                return await append_to_note(update, context, resolution.matches[0], resolution.addition)
            canonical = f"добавь в заметку {resolution.query}"
            if resolution.addition:
                canonical += f": {resolution.addition}"
            return await handle_note_text(update, context, canonical, NOTE_APPEND)

        if not _blocks_active_note_append(text):
            addition = active_note_addition(text)
            if addition:
                active_note = get_active_note(context, user_id)
                if active_note:
                    logger.info("Router active note append note_id=%s", active_note.get("note_id"))
                    return await append_to_note(update, context, active_note, addition)
                await update.message.reply_text(
                    "Куда добавить? Назови заметку, например: «добавь воду в список покупок»."
                )
                return True

        if not _blocks_active_note_append(text):
            delete_query = resolve_named_note_delete(user_id, text)
            if delete_query:
                logger.info("Router named note delete query=%s", delete_query)
                handled = await handle_note_text(update, context, f"удали заметку {delete_query}", NOTE_DELETE)
                if handled:
                    clear_active_note(context)
                return handled

        note_query = resolve_note_reference(
            user_id,
            text,
            allow_generic=not _has_explicit_calendar_reference(text),
        )
        if note_query:
            logger.info("Router contextual note_query=%s", note_query)
            note_search_text = f"найди заметки про {note_query}"
            handled = await handle_note_text(update, context, note_search_text, NOTE_SEARCH)
            if handled and not _pending(context):
                remember_after_note_action(user_id, context, NOTE_SEARCH, note_search_text)
            return handled

    if await _route_current_entity_action(update, context, text):
        logger.info("Router resolved action through unified current entity")
        return True

    intent = detect_intent(text)
    logger.info("Router intent=%s confidence=%.2f", intent.name, intent.confidence)
    if intent.name == INTENT_CREATE:
        create_text = _creation_text(text)
        if _needs_time(create_text):
            set_pending(context, {"type": "create_time", "text": create_text})
            if DATE_HINT_RE.search(_normalise(create_text)):
                prompt = "Во сколько поставить событие?"
            else:
                prompt = "Когда поставить событие? Напиши, например: «22:00» или «завтра в 19»."
            await update.message.reply_text(prompt)
            return True
        return await create_from_text(update, context, create_text)
    if intent.name == INTENT_SEARCH:
        return await search_from_text(update, context, _normalise_search_text(text))
    if intent.name == INTENT_VIEW:
        return await view_from_text(update, context, text)
    if intent.name == INTENT_UPDATE:
        return await update_from_text(update, context, _action_text(text))
    if intent.name == INTENT_DELETE:
        return await delete_from_text(update, context, _action_text(text))
    if intent.name == INTENT_FREE:
        return await free_slots_from_text(update, context, text)
    return False


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        handled = await route_text(update, context)
        if handled:
            return
        if update.message:
            await update.message.reply_text("Не понял команду. Например: «врач завтра в 19:00» или «напомни через 30 минут позвонить».")
    except Exception:
        logger.exception("Unhandled error in text router")
        if update.message:
            await update.message.reply_text("Не удалось обработать сообщение. Попробуйте ещё раз.")