"""Conversation helpers for natural follow-up operations on notes.

This layer keeps note-specific conversational state out of the central router:
- resolves both "add to <note> X" and "add X to <note>" word orders;
- remembers one recently used note for short follow-ups such as "add water";
- remembers the latest displayed note list for "first/second note" follow-ups;
- keeps direct note appends concise instead of echoing the whole note body.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any

from core.note_store import append_note, get_note, list_notes, search_notes
from modules.notes import (
    NOTE_CREATE,
    NOTE_DELETE,
    NOTE_LIST,
    NOTE_SEARCH,
    NOTE_SEARCH_PREFIX_RE,
    _note_query,
)

ACTIVE_NOTE_KEY = "smart_planner_active_note"
NOTE_LIST_CONTEXT_KEY = "smart_planner_note_list_context"
ACTIVE_NOTE_TTL_SECONDS = 10 * 60
NOTE_LIST_TTL_SECONDS = 10 * 60
ACTIVE_APPEND_WORD_LIMIT = 8
NOTE_LIST_LIMIT = 20

_APPEND_VERBS = r"(?:добавь|добавить|внеси|внести|допиши|дописать|дополни|дополнить)"
TARGET_FIRST_RE = re.compile(
    rf"^\s*{_APPEND_VERBS}\s+(?:мне\s+)?(?:в|к)\s+(?P<payload>.+?)\s*$",
    re.IGNORECASE,
)
REVERSE_APPEND_RE = re.compile(
    rf"^\s*{_APPEND_VERBS}\s+(?:мне\s+)?(?P<payload>.+?)\s*$",
    re.IGNORECASE,
)
ACTIVE_APPEND_RE = re.compile(
    rf"^\s*{_APPEND_VERBS}\s+(?:мне\s+)?(?P<addition>.+?)\s*$",
    re.IGNORECASE,
)
ACTIVE_CONTINUATION_RE = re.compile(
    r"^\s*(?:и|ещ[её])\s+(?P<addition>.+?)\s*$",
    re.IGNORECASE,
)
DELETE_NAMED_RE = re.compile(
    r"^\s*(?:удали|удалить|убери|убрать)\s+(?P<query>.+?)\s*[.!]*$",
    re.IGNORECASE,
)
PREPOSITION_RE = re.compile(r"\s+(?:в|к)\s+", re.IGNORECASE)
APPEND_SEPARATOR_RE = re.compile(
    r"\s*[,;:]\s*(?:что\s+)?|\s+[—–-]\s+(?:что\s+)?|\s+что\s+",
    re.IGNORECASE,
)
TARGET_FILLER_RE = re.compile(
    r"^\s*(?:(?:мою|мой|мое|моё|мои)\s+)?"
    r"(?:(?:заметку|заметка|запись)\s+)?"
    r"(?:(?:про|о|об)\s+)?",
    re.IGNORECASE,
)
DIRECT_MODULE_TARGET_RE = re.compile(
    r"^(?:календар\w*|расписани\w*|напоминани\w*|задач\w*|"
    r"встреч\w*|событ\w*|созвон\w*|звонок\w*)\b",
    re.IGNORECASE,
)
CALENDAR_ACTIVE_GUARD_RE = re.compile(
    r"\b(?:встреч\w*|событ\w*|созвон\w*|звонок\w*|календар\w*|расписани\w*|"
    r"напоминани\w*|задач\w*|врач\w*|невролог\w*|стоматолог\w*|рейс\w*|"
    r"полет\w*|полёт\w*|поезд\w*|трениров\w*)\b",
    re.IGNORECASE,
)

_ORDINAL_TOKEN = (
    r"(?:перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*|шест\w*|"
    r"седьм\w*|восьм\w*|девят\w*|десят\w*|\d{1,2})"
)
NOTE_SELECTION_FIRST_RE = re.compile(
    rf"^(?:(?:покажи|открой|выведи|дай)\s+)?(?P<ordinal>{_ORDINAL_TOKEN})\s+"
    rf"(?:заметк\w*|запис\w*)(?:\s+(?:в|из)\s+списк\w*)?$",
    re.IGNORECASE,
)
NOTE_SELECTION_AFTER_RE = re.compile(
    rf"^(?:(?:покажи|открой|выведи|дай)\s+)?(?:заметк\w*|запис\w*)\s+"
    rf"(?:номер\s+)?(?P<ordinal>{_ORDINAL_TOKEN})(?:\s+(?:в|из)\s+списк\w*)?$",
    re.IGNORECASE,
)
SELECTION_CORRECTION_RE = re.compile(r"^(?:нет|не\s+это|нет\s+нет)\s*[,;:.!?-]*\s+", re.IGNORECASE)
SELECTION_NEGATION_RE = re.compile(r"\bне\s+(?:показывай|открывай|выводи|давай)\b", re.IGNORECASE)


@dataclass(frozen=True)
class NoteAppendResolution:
    query: str
    addition: str
    matches: list[dict]


def _normalise(value: str) -> str:
    return " ".join(str(value).lower().replace("ё", "е").split()).strip(" \t\r\n.,!?;:…\"'«»")


def _note_title(note: dict) -> str:
    return str(note.get("title") or note.get("text") or "").strip()


def _clean_target(value: str) -> str:
    target = TARGET_FILLER_RE.sub("", str(value).strip(), count=1)
    return target.strip(" \t\r\n.,!?;:-—–«»\"")


def _clean_addition(value: str) -> str:
    addition = str(value).strip(" \t\r\n,;:-—–")
    addition = re.sub(r"^(?:(?:ещ[её]|туда|сюда)\s+)+", "", addition, flags=re.IGNORECASE)
    return addition.rstrip(" .!?")


def _ordered_matches(user_id: int, query: str) -> list[dict]:
    matches = search_notes(user_id, query, limit=50)
    if not matches:
        return []
    normal_query = _normalise(query)
    exact = [item for item in matches if _normalise(_note_title(item)) == normal_query]
    return exact or matches


def _split_target_first(user_id: int, payload: str) -> NoteAppendResolution | None:
    raw_payload = payload.strip()
    if DIRECT_MODULE_TARGET_RE.match(raw_payload):
        return None
    payload = _clean_target(raw_payload)
    if not payload:
        return None

    separator = APPEND_SEPARATOR_RE.search(payload)
    if separator:
        query = _clean_target(payload[: separator.start()])
        addition = _clean_addition(payload[separator.end() :])
        matches = _ordered_matches(user_id, query) if query else []
        if query and matches:
            return NoteAppendResolution(query, addition, matches)

    words = payload.split()
    if len(words) < 2:
        matches = _ordered_matches(user_id, payload)
        return NoteAppendResolution(payload, "", matches) if matches else None

    fallback: NoteAppendResolution | None = None
    for cut in range(len(words) - 1, 0, -1):
        query = _clean_target(" ".join(words[:cut]))
        addition = _clean_addition(" ".join(words[cut:]))
        matches = _ordered_matches(user_id, query)
        if not matches:
            continue
        resolution = NoteAppendResolution(query, addition, matches)
        if any(_normalise(_note_title(item)) == _normalise(query) for item in matches):
            return resolution
        if len(matches) == 1 and fallback is None:
            fallback = resolution

    full_matches = _ordered_matches(user_id, payload)
    if full_matches:
        return NoteAppendResolution(payload, "", full_matches)
    return fallback


def _split_reverse(user_id: int, payload: str) -> NoteAppendResolution | None:
    separators = list(PREPOSITION_RE.finditer(payload))
    if not separators:
        return None

    for separator in reversed(separators):
        addition = _clean_addition(payload[: separator.start()])
        query = _clean_target(payload[separator.end() :])
        if not addition or not query or DIRECT_MODULE_TARGET_RE.match(query):
            continue
        matches = _ordered_matches(user_id, query)
        if matches:
            return NoteAppendResolution(query, addition, matches)
    return None


def resolve_named_note_append(user_id: int, text: str) -> NoteAppendResolution | None:
    """Resolve an append command only when it points to an existing note."""
    target_first = TARGET_FIRST_RE.match(text)
    if target_first:
        return _split_target_first(user_id, target_first.group("payload"))

    reverse = REVERSE_APPEND_RE.match(text)
    if not reverse:
        return None
    payload = reverse.group("payload").strip()
    if re.match(r"^(?:в|к)\b", payload, flags=re.IGNORECASE):
        return None
    return _split_reverse(user_id, payload)


def remember_active_note(context: Any, note: dict) -> None:
    note_id = note.get("note_id")
    if note_id is None:
        return
    context.user_data[ACTIVE_NOTE_KEY] = {
        "note_id": int(note_id),
        "title": _note_title(note),
        "touched_at": time.time(),
    }


def remember_latest_note(user_id: int, context: Any) -> None:
    notes = list_notes(user_id, limit=1)
    if notes:
        remember_active_note(context, notes[0])
    else:
        clear_active_note(context)


def clear_active_note(context: Any) -> None:
    context.user_data.pop(ACTIVE_NOTE_KEY, None)


def get_active_note(context: Any, user_id: int, *, now: float | None = None) -> dict | None:
    value = context.user_data.get(ACTIVE_NOTE_KEY)
    if not isinstance(value, dict):
        return None
    try:
        touched_at = float(value.get("touched_at"))
        note_id = int(value.get("note_id"))
    except (TypeError, ValueError):
        clear_active_note(context)
        return None
    current = time.time() if now is None else float(now)
    if current - touched_at > ACTIVE_NOTE_TTL_SECONDS:
        clear_active_note(context)
        return None
    note = get_note(user_id, note_id)
    if not note:
        clear_active_note(context)
        return None
    return note


def _remember_note_list(context: Any, notes: list[dict]) -> None:
    note_ids = [int(item["note_id"]) for item in notes[:NOTE_LIST_LIMIT] if item.get("note_id") is not None]
    if not note_ids:
        context.user_data.pop(NOTE_LIST_CONTEXT_KEY, None)
        return
    context.user_data[NOTE_LIST_CONTEXT_KEY] = {
        "note_ids": note_ids,
        "touched_at": time.time(),
    }


def _clear_note_list(context: Any) -> None:
    context.user_data.pop(NOTE_LIST_CONTEXT_KEY, None)


def _remembered_note_ids(context: Any, *, now: float | None = None) -> list[int]:
    value = context.user_data.get(NOTE_LIST_CONTEXT_KEY)
    if not isinstance(value, dict):
        return []
    try:
        touched_at = float(value.get("touched_at"))
        raw_ids = list(value.get("note_ids") or [])
        note_ids = [int(item) for item in raw_ids]
    except (TypeError, ValueError):
        _clear_note_list(context)
        return []
    current = time.time() if now is None else float(now)
    if current - touched_at > NOTE_LIST_TTL_SECONDS:
        _clear_note_list(context)
        return []
    return note_ids


def _ordinal_index(token: str) -> int | None:
    value = _normalise(token)
    if value.isdigit():
        index = int(value) - 1
        return index if index >= 0 else None
    stems = (
        ("перв", 0), ("втор", 1), ("трет", 2), ("четверт", 3), ("пят", 4),
        ("шест", 5), ("седьм", 6), ("восьм", 7), ("девят", 8), ("десят", 9),
    )
    for stem, index in stems:
        if value.startswith(stem):
            return index
    return None


def note_selection_index(text: str) -> int | None:
    """Parse explicit requests such as ``первая заметка`` or ``покажи заметку 2``."""
    candidate = _normalise(text)
    if not candidate or SELECTION_NEGATION_RE.search(candidate):
        return None
    candidate = SELECTION_CORRECTION_RE.sub("", candidate, count=1).strip()
    match = NOTE_SELECTION_FIRST_RE.fullmatch(candidate) or NOTE_SELECTION_AFTER_RE.fullmatch(candidate)
    if not match:
        return None
    return _ordinal_index(match.group("ordinal"))


def _note_detail(note: dict) -> str:
    title = _note_title(note)
    text = str(note.get("text") or "").strip()
    if not text or _normalise(text) == _normalise(title):
        return f"«{title}»"
    body_limit = 3500
    if len(text) > body_limit:
        text = text[: body_limit - 3].rstrip() + "..."
    return f"«{title}»\n{text}"


async def open_note_selection(update: Any, context: Any, text: str) -> bool:
    """Open an ordinal note from the last shown list, or from the current list as fallback."""
    index = note_selection_index(text)
    if index is None:
        return False

    user_id = update.effective_user.id
    note_ids = _remembered_note_ids(context)
    if not note_ids:
        notes = list_notes(user_id, limit=NOTE_LIST_LIMIT)
        if not notes:
            await update.message.reply_text("Заметок пока нет.")
            return True
        _remember_note_list(context, notes)
        note_ids = [int(item["note_id"]) for item in notes]

    if index >= len(note_ids):
        await update.message.reply_text(
            f"В списке только {len(note_ids)} заметок. Выбери номер от 1 до {len(note_ids)}."
        )
        return True

    note = get_note(user_id, note_ids[index])
    if not note:
        _clear_note_list(context)
        await update.message.reply_text("Эта заметка уже удалена. Покажи список заметок ещё раз.")
        return True

    remember_active_note(context, note)
    await update.message.reply_text(_note_detail(note))
    return True


def active_note_addition(text: str) -> str | None:
    """Extract a safe short follow-up such as ``добавь воду`` or ``и молоко``."""
    match = ACTIVE_APPEND_RE.match(text) or ACTIVE_CONTINUATION_RE.match(text)
    if not match:
        return None
    addition = _clean_addition(match.group("addition"))
    if not addition:
        return None
    if len(addition.split()) > ACTIVE_APPEND_WORD_LIMIT:
        return None
    if PREPOSITION_RE.search(addition):
        return None
    if CALENDAR_ACTIVE_GUARD_RE.search(addition):
        return None
    return addition


async def append_to_note(update: Any, context: Any, note: dict, addition: str) -> bool:
    """Append to a known note and keep the response compact."""
    addition = _clean_addition(addition)
    if not addition:
        return False
    try:
        updated = append_note(update.effective_user.id, note["note_id"], addition)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return True
    if not updated:
        clear_active_note(context)
        await update.message.reply_text("Эта заметка уже удалена.")
        return True
    remember_active_note(context, updated)
    preview = addition if len(addition) <= 160 else addition[:157].rstrip() + "..."
    await update.message.reply_text(f"Добавил в «{_note_title(updated)}»: {preview}.")
    return True


def remember_after_note_action(user_id: int, context: Any, intent: str, text: str) -> None:
    """Remember note/list context after note actions for safe conversational follow-ups."""
    if intent == NOTE_DELETE:
        clear_active_note(context)
        _clear_note_list(context)
        return
    if intent == NOTE_CREATE:
        remember_latest_note(user_id, context)
        _clear_note_list(context)
        return
    if intent == NOTE_LIST:
        notes = list_notes(user_id, limit=NOTE_LIST_LIMIT)
        _remember_note_list(context, notes)
        clear_active_note(context)
        return
    if intent != NOTE_SEARCH:
        return

    query = _note_query(text, NOTE_SEARCH_PREFIX_RE)
    if not query:
        clear_active_note(context)
        _clear_note_list(context)
        return
    matches = _ordered_matches(user_id, query)[:NOTE_LIST_LIMIT]
    _remember_note_list(context, matches)
    if len(matches) == 1:
        remember_active_note(context, matches[0])
    else:
        clear_active_note(context)


def resolve_named_note_delete(user_id: int, text: str) -> str | None:
    """Resolve ``удали <title>`` only for an exact existing note title.

    Exact-title-only behavior is deliberate: a phrase such as
    ``удали шоколад из списка покупок`` must never delete the whole note merely
    because the note body contains the word ``шоколад``.
    """
    match = DELETE_NAMED_RE.match(text)
    if not match:
        return None
    query = _clean_target(match.group("query"))
    if not query:
        return None
    matches = search_notes(user_id, query, limit=50)
    if not matches:
        return None
    normal_query = _normalise(query)
    exact = [item for item in matches if _normalise(_note_title(item)) == normal_query]
    return query if exact else None
