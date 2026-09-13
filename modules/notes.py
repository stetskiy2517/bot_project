"""Personal notes commands, separate from calendar events and reminders."""

from __future__ import annotations

from datetime import datetime
import re

from telegram import Update
from telegram.ext import ContextTypes

from core.note_store import append_note, create_note, delete_note, derive_note_title, list_notes, search_notes
from core.db import get_user_timezone
from modules.calendar_user import _user_zone

NOTE_CREATE = "note_create"
NOTE_APPEND = "note_append"
NOTE_LIST = "note_list"
NOTE_SEARCH = "note_search"
NOTE_DELETE = "note_delete"

NOTE_APPEND_RE = re.compile(
    r"^\s*(?:(?:добавь|добавить|внеси|внести)\s+(?:в|к)\s+(?:мою\s+)?(?:заметку|запись)\b|"
    r"(?:допиши|дописать|дополни|дополнить)\s+(?:(?:в|к)\s+)?(?:мою\s+)?(?:заметку|запись)\b)",
    re.IGNORECASE,
)
NOTE_CONTEXTUAL_APPEND_RE = re.compile(
    r"^\s*(?:добавь|добавить|внеси|внести|допиши|дописать|дополни|дополнить)\s+"
    r"(?:мне\s+)?(?:в|к)\s+"
    r"(?!(?:календар\w*|расписани\w*|встреч\w*|событ\w*|созвон\w*|звонок\w*|напоминани\w*)\b)"
    r".+",
    re.IGNORECASE,
)
NOTE_CREATE_RE = re.compile(
    r"^\s*(?:(?:создай|добавь|сохрани|запиши)\s+(?:мне\s+)?(?:заметку|запись)\b|"
    r"(?:создай|создать|сохрани|сохранить|запиши|записать|составь|составить)\s+(?:мне\s+)?список\b|"
    r"(?:заметка|запись)\b\s*[:\-])",
    re.IGNORECASE,
)
NOTE_LIST_RE = re.compile(
    r"^\s*(?:покажи|открой|выведи)\s+(?:мои\s+)?заметки\b|^\s*(?:мои\s+)?заметки\s*$",
    re.IGNORECASE,
)
NOTE_SEARCH_RE = re.compile(
    r"^\s*(?:"
    r"(?:найди|поищи|покажи)\s+(?:мне\s+)?(?:заметку|заметки|запись|записи)\s+(?:про|о|об)\b|"
    r"(?:покажи|открой|выведи)\s+(?:мне\s+)?(?:мой\s+)?список\b|"
    r"(?:что|чего)\s+(?:у\s+меня\s+)?(?:записано\s+)?(?:есть\s+)?в\s+(?:мо(?:ем|ём)\s+)?списке\b|"
    r"что\s+(?:есть\s+)?у\s+меня\s+в\s+(?:мо(?:ем|ём)\s+)?списке\b"
    r")",
    re.IGNORECASE,
)
NOTE_DELETE_RE = re.compile(
    r"^\s*(?:удали|удалить|убери|убрать)\s+(?:мою\s+)?(?:заметку|запись)\b",
    re.IGNORECASE,
)
NOTE_CREATE_PREFIX_RE = re.compile(
    r"^\s*(?:(?:создай|добавь|сохрани|запиши)\s+(?:мне\s+)?(?:заметку|запись)|"
    r"(?:(?:создай|создать|сохрани|сохранить|запиши|записать|составь|составить)\s+(?:мне\s+)?(?=список\b))|"
    r"(?:заметка|запись))\s*[:\-]?\s*",
    re.IGNORECASE,
)
NOTE_APPEND_PREFIX_RE = re.compile(
    r"^\s*(?:(?:добавь|добавить|внеси|внести)\s+(?:в|к)\s+(?:мою\s+)?(?:заметку|запись)|"
    r"(?:допиши|дописать|дополни|дополнить)\s+(?:(?:в|к)\s+)?(?:мою\s+)?(?:заметку|запись))\s*",
    re.IGNORECASE,
)
NOTE_CONTEXTUAL_APPEND_PREFIX_RE = re.compile(
    r"^\s*(?:добавь|добавить|внеси|внести|допиши|дописать|дополни|дополнить)\s+"
    r"(?:мне\s+)?(?:в|к)\s+",
    re.IGNORECASE,
)
NOTE_SEARCH_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"(?:найди|поищи|покажи)\s+(?:мне\s+)?(?:заметку|заметки|запись|записи)\s+(?:про|о|об)\s+|"
    r"(?:покажи|открой|выведи)\s+(?:мне\s+)?(?:мой\s+)?(?=список\b)|"
    r"(?:что|чего)\s+(?:у\s+меня\s+)?(?:записано\s+)?(?:есть\s+)?в\s+(?:мо(?:ем|ём)\s+)?(?=списке\b)|"
    r"что\s+(?:есть\s+)?у\s+меня\s+в\s+(?:мо(?:ем|ём)\s+)?(?=списке\b)"
    r")",
    re.IGNORECASE,
)
NOTE_DELETE_PREFIX_RE = re.compile(
    r"^\s*(?:удали|удалить|убери|убрать)\s+(?:мою\s+)?(?:заметку|запись)\s*(?:про|о|об)?\s*",
    re.IGNORECASE,
)
NOTE_TOPIC_PREFIX_RE = re.compile(r"^\s*(?:про|о|об)\s+", re.IGNORECASE)
NOTE_APPEND_PUNCT_RE = re.compile(r"\s*[,;:]\s*(?:что\s+)?|\s+[—–-]\s+(?:что\s+)?", re.IGNORECASE)
NOTE_APPEND_WHAT_RE = re.compile(r"\s+что\s+", re.IGNORECASE)
NOTE_TITLE_SEPARATOR_RE = re.compile(
    r"^\s*(?P<title>.{2,120}?)(?:(?<!\d):(?!\d)|(?<!\d)\.(?!\d)|\s+[—–]\s+)\s*(?P<body>.+?)\s*$"
)
BARE_NOTE_CONTROL_RE = re.compile(
    r"^\s*(?:что|чего|когда|где|как|почему|зачем|кто|сколько|можно|есть\s+ли|"
    r"покажи|открой|найди|поищи|удали|убери|перенеси|измени|создай|добавь|добавить|"
    r"внеси|внести|запиши|сохрани|поставь|напомни|запланируй|назначь)\b",
    re.IGNORECASE,
)
BARE_NOTE_CALENDAR_RE = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|понедельник\w*|вторник\w*|сред\w*|четверг\w*|"
    r"пятниц\w*|суббот\w*|воскресень\w*|следующ\w*\s+недел\w*|"
    r"встреч\w*|событ\w*|созвон\w*|звонок\w*|календар\w*|расписани\w*|"
    r"свободн\w*\s+окн\w*|врач\w*|невролог\w*|стоматолог\w*|рейс\w*|полет\w*|полёт\w*)\b|"
    r"\b(?:в|к|с)\s*(?:[01]?\d|2[0-3])(?:(?::|\.)[0-5]\d)?\b|"
    r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b",
    re.IGNORECASE,
)
BARE_NOTE_BODY_QUESTION_RE = re.compile(
    r"^\s*(?:что|чего|когда|где|как|почему|зачем|кто|сколько|можно\s+ли|есть\s+ли)\b",
    re.IGNORECASE,
)
CHOICE_WORD_RE = re.compile(r"\b(перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*)\b", re.IGNORECASE)
CANCEL_WORDS = {"нет", "не надо", "отмена", "отменить", "стоп"}


def _normalise(text: str) -> str:
    return text.lower().replace("ё", "е").strip(" \t\r\n.,!?;:…\"'«»")


def _split_note_payload(payload: str) -> tuple[str | None, str]:
    """Extract an optional explicit title from ``Название. Текст`` or similar syntax."""
    cleaned = payload.strip(" \t\r\n.,;")
    match = NOTE_TITLE_SEPARATOR_RE.match(cleaned)
    if not match:
        return None, cleaned
    title = " ".join(match.group("title").split()).strip()
    body = " ".join(match.group("body").split()).strip()
    if not title or not body:
        return None, cleaned
    return title, body


def detect_bare_note(text: str) -> tuple[str, str] | None:
    """Recognize a safe title/body note dictated without an explicit command verb."""
    candidate = text.strip()
    if not candidate or "?" in candidate:
        return None
    if BARE_NOTE_CONTROL_RE.search(candidate) or BARE_NOTE_CALENDAR_RE.search(candidate):
        return None
    title, body = _split_note_payload(candidate)
    if not title or not body:
        return None
    if len(title.split()) > 8 or BARE_NOTE_BODY_QUESTION_RE.search(body):
        return None
    return title, body


def detect_note_intent(text: str) -> str | None:
    if NOTE_APPEND_RE.search(text) or NOTE_CONTEXTUAL_APPEND_RE.search(text):
        return NOTE_APPEND
    if NOTE_DELETE_RE.search(text):
        return NOTE_DELETE
    if NOTE_SEARCH_RE.search(text):
        return NOTE_SEARCH
    if NOTE_LIST_RE.search(text):
        return NOTE_LIST
    if NOTE_CREATE_RE.search(text) or detect_bare_note(text):
        return NOTE_CREATE
    return None


def _choice_index(text: str) -> int | None:
    stripped = text.strip()
    if stripped.isdigit():
        index = int(stripped) - 1
        return index if index >= 0 else None
    match = CHOICE_WORD_RE.search(_normalise(text))
    if not match:
        return None
    token = match.group(1)
    if token.startswith("втор"):
        return 1
    if token.startswith("трет"):
        return 2
    if token.startswith("четверт"):
        return 3
    if token.startswith("пят"):
        return 4
    return 0


def _note_body(text: str) -> str:
    return NOTE_CREATE_PREFIX_RE.sub("", text.strip(), count=1).strip(" \t\r\n.,;:-")


def _note_query(text: str, prefix_re: re.Pattern) -> str:
    return prefix_re.sub("", text.strip().rstrip("?.!,"), count=1).strip(" \t\r\n.,;:-")


def _split_append_payload(text: str) -> tuple[str, str, bool]:
    original = text.strip()
    payload = NOTE_APPEND_PREFIX_RE.sub("", original, count=1)
    if payload == original:
        payload = NOTE_CONTEXTUAL_APPEND_PREFIX_RE.sub("", original, count=1)
    payload = payload.strip(" \t\r\n.,;:-—–")
    payload = NOTE_TOPIC_PREFIX_RE.sub("", payload, count=1).strip()
    if not payload:
        return "", "", False

    match = NOTE_APPEND_PUNCT_RE.search(payload)
    if not match:
        match = NOTE_APPEND_WHAT_RE.search(payload)
    if not match:
        return payload.strip(" \t\r\n.,;:-—–"), "", False

    query = payload[:match.start()].strip(" \t\r\n.,;:-—–")
    addition = payload[match.end():].strip(" \t\r\n.,;:-—–")
    return query, addition, True


def _note_title(note: dict) -> str:
    title = str(note.get("title") or "").strip()
    if title:
        return title
    return derive_note_title(str(note.get("text") or ""))


def _exact_title_matches(matches: list[dict], query: str) -> list[dict]:
    normal_query = _normalise(query)
    return [item for item in matches if _normalise(_note_title(item)) == normal_query]


def _resolve_append_request(user_id: int, text: str) -> tuple[str, str, list[dict]]:
    query, addition, explicit_separator = _split_append_payload(text)
    if not query:
        return "", addition, []
    if explicit_separator:
        matches = search_notes(user_id, query, limit=50)
        exact = _exact_title_matches(matches, query)
        return query, addition, exact or matches

    contextual = bool(NOTE_CONTEXTUAL_APPEND_RE.search(text) and not NOTE_APPEND_RE.search(text))
    words = query.split()

    if contextual and len(words) >= 2:
        for cut in range(len(words) - 1, 0, -1):
            target = " ".join(words[:cut])
            remainder = " ".join(words[cut:]).strip()
            matches = search_notes(user_id, target, limit=50)
            exact = _exact_title_matches(matches, target)
            if exact:
                return target, remainder, exact

        for cut in range(len(words) - 1, 0, -1):
            target = " ".join(words[:cut])
            remainder = " ".join(words[cut:]).strip()
            matches = search_notes(user_id, target, limit=50)
            if len(matches) == 1:
                return target, remainder, matches

    full_matches = search_notes(user_id, query, limit=50)
    exact_full = _exact_title_matches(full_matches, query)
    if exact_full:
        return query, "", exact_full
    if full_matches and not contextual:
        return query, "", full_matches

    fallback: tuple[str, str, list[dict]] | None = None
    for cut in range(1, min(len(words), 7)):
        target = " ".join(words[:cut])
        remainder = " ".join(words[cut:]).strip()
        if not remainder:
            break
        matches = search_notes(user_id, target, limit=50)
        if len(matches) == 1:
            return target, remainder, matches
        if matches:
            fallback = (target, remainder, matches)
    return fallback or (query, "", [])


def _format_created_at(note: dict, timezone: str) -> str:
    try:
        value = note.get("updated_at") or note["created_at"]
        created = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return created.astimezone(_user_zone(timezone)).strftime("%d.%m %H:%M")
    except (KeyError, TypeError, ValueError):
        return ""


def _format_line(note: dict, timezone: str, index: int | None = None) -> str:
    prefix = f"{index}." if index is not None else "•"
    title = _note_title(note)
    text = str(note.get("text") or "").strip()
    when = _format_created_at(note, timezone)
    suffix = f" · {when}" if when else ""
    if not text or _normalise(title) == _normalise(text):
        return f"{prefix} {title}{suffix}"
    preview = text if len(text) <= 220 else text[:217].rstrip() + "..."
    return f"{prefix} {title}{suffix}\n  {preview}"


def _store_pending(context: ContextTypes.DEFAULT_TYPE, payload: dict) -> None:
    context.user_data["smart_planner_pending"] = payload


def _created_reply(note: dict) -> str:
    title = _note_title(note)
    text = str(note.get("text") or "").strip()
    if not text or _normalise(title) == _normalise(text):
        return f"Заметка «{title}» сохранена."
    return f"Заметка «{title}» сохранена.\n{text}"


async def _append_to_note(update: Update, note: dict, addition: str) -> bool:
    try:
        updated = append_note(update.effective_user.id, note["note_id"], addition)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return True
    if not updated:
        await update.message.reply_text("Эта заметка уже удалена.")
        return True
    await update.message.reply_text(
        f"Дополнил заметку «{_note_title(updated)}».\n{updated['text']}"
    )
    return True


async def create_note_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    payload = _note_body(text)
    if not payload:
        _store_pending(context, {"type": "note_text"})
        await update.message.reply_text("Что записать в заметку?")
        return True
    title, body = _split_note_payload(payload)
    try:
        note = create_note(update.effective_user.id, body, title=title)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return True
    await update.message.reply_text(_created_reply(note))
    return True


async def append_note_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    query, addition, matches = _resolve_append_request(user_id, text)

    if not query:
        _store_pending(context, {"type": "note_append_target", "addition": addition})
        await update.message.reply_text("В какую заметку добавить? Назови её название или тему.")
        return True
    if not matches:
        await update.message.reply_text(f"Не нашёл заметку «{query}».")
        return True
    if len(matches) > 1:
        visible = matches[:5]
        _store_pending(
            context,
            {"type": "note_select_append", "notes": visible, "addition": addition, "timezone": timezone},
        )
        await update.message.reply_text(
            "Нашёл несколько заметок. В какую добавить? Напиши номер:\n" +
            "\n".join(_format_line(item, timezone, index=index) for index, item in enumerate(visible, start=1))
        )
        return True

    note = matches[0]
    if not addition:
        _store_pending(context, {"type": "note_append_text", "note": note})
        await update.message.reply_text(f"Что добавить в заметку «{_note_title(note)}»?")
        return True
    return await _append_to_note(update, note, addition)


async def list_notes_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    notes = list_notes(user_id, limit=50)
    if not notes:
        await update.message.reply_text("Заметок пока нет.")
        return True
    await update.message.reply_text(
        "Последние заметки:\n" + "\n".join(_format_line(item, timezone) for item in notes[:20])
    )
    return True


async def search_notes_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    query = _note_query(text, NOTE_SEARCH_PREFIX_RE)
    if not query:
        await update.message.reply_text("Что искать в заметках?")
        return True
    matches = search_notes(user_id, query, limit=50)
    if not matches:
        await update.message.reply_text(f"Не нашёл заметок про «{query}».")
        return True
    await update.message.reply_text(
        f"Нашёл заметки про «{query}»:\n" +
        "\n".join(_format_line(item, timezone) for item in matches[:20])
    )
    return True


async def delete_note_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    query = _note_query(text, NOTE_DELETE_PREFIX_RE)
    if not query:
        await update.message.reply_text("Какую заметку удалить?")
        return True
    matches = search_notes(user_id, query, limit=50)
    if not matches:
        await update.message.reply_text(f"Не нашёл заметку «{query}».")
        return True
    if len(matches) > 1:
        visible = matches[:5]
        _store_pending(context, {"type": "note_select_delete", "notes": visible, "timezone": timezone})
        await update.message.reply_text(
            "Нашёл несколько заметок. Напиши номер:\n" +
            "\n".join(_format_line(item, timezone, index=index) for index, item in enumerate(visible, start=1))
        )
        return True
    delete_note(user_id, matches[0]["note_id"])
    await update.message.reply_text(f"Заметка «{_note_title(matches[0])}» удалена.")
    return True


async def handle_note_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, intent: str) -> bool:
    if intent == NOTE_CREATE:
        return await create_note_from_text(update, context, text)
    if intent == NOTE_APPEND:
        return await append_note_from_text(update, context, text)
    if intent == NOTE_LIST:
        return await list_notes_from_text(update, context, text)
    if intent == NOTE_SEARCH:
        return await search_notes_from_text(update, context, text)
    if intent == NOTE_DELETE:
        return await delete_note_from_text(update, context, text)
    return False


async def resume_pending_note(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, pending: dict) -> bool:
    normal = _normalise(text)
    if normal in CANCEL_WORDS:
        context.user_data.pop("smart_planner_pending", None)
        await update.message.reply_text("Хорошо, отменил.")
        return True

    pending_type = str(pending.get("type") or "")
    if pending_type == "note_text":
        payload = text.strip()
        if not payload:
            await update.message.reply_text("Что записать в заметку?")
            return True
        title, body = _split_note_payload(payload)
        context.user_data.pop("smart_planner_pending", None)
        try:
            note = create_note(update.effective_user.id, body, title=title)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return True
        await update.message.reply_text(_created_reply(note))
        return True

    if pending_type == "note_append_target":
        query = NOTE_TOPIC_PREFIX_RE.sub("", text.strip(), count=1).strip(" \t\r\n.,;:-")
        matches = search_notes(update.effective_user.id, query, limit=50) if query else []
        if not matches:
            await update.message.reply_text(f"Не нашёл заметку «{query}». Назови её иначе.")
            return True
        addition = str(pending.get("addition") or "").strip()
        timezone = get_user_timezone(update.effective_user.id, default="Europe/Moscow") or "Europe/Moscow"
        if len(matches) > 1:
            visible = matches[:5]
            _store_pending(
                context,
                {"type": "note_select_append", "notes": visible, "addition": addition, "timezone": timezone},
            )
            await update.message.reply_text(
                "Нашёл несколько заметок. В какую добавить? Напиши номер:\n" +
                "\n".join(_format_line(item, timezone, index=index) for index, item in enumerate(visible, start=1))
            )
            return True
        note = matches[0]
        if not addition:
            _store_pending(context, {"type": "note_append_text", "note": note})
            await update.message.reply_text(f"Что добавить в заметку «{_note_title(note)}»?")
            return True
        context.user_data.pop("smart_planner_pending", None)
        return await _append_to_note(update, note, addition)

    if pending_type == "note_append_text":
        addition = text.strip()
        if not addition:
            await update.message.reply_text("Что добавить в заметку?")
            return True
        note = pending.get("note") or {}
        context.user_data.pop("smart_planner_pending", None)
        return await _append_to_note(update, note, addition)

    if pending_type in {"note_select_delete", "note_select_append"}:
        index = _choice_index(text)
        if index is None:
            await update.message.reply_text("Напиши номер заметки или скажи, например, «вторую».")
            return True
        notes = pending.get("notes") or []
        if index < 0 or index >= len(notes):
            await update.message.reply_text("Такого номера нет. Выбери номер из списка.")
            return True
        note = notes[index]

        if pending_type == "note_select_append":
            addition = str(pending.get("addition") or "").strip()
            if not addition:
                _store_pending(context, {"type": "note_append_text", "note": note})
                await update.message.reply_text(f"Что добавить в заметку «{_note_title(note)}»?")
                return True
            context.user_data.pop("smart_planner_pending", None)
            return await _append_to_note(update, note, addition)

        context.user_data.pop("smart_planner_pending", None)
        if delete_note(update.effective_user.id, note["note_id"]):
            await update.message.reply_text(f"Заметка «{_note_title(note)}» удалена.")
        else:
            await update.message.reply_text("Эта заметка уже удалена.")
        return True

    return False
