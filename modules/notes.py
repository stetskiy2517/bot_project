"""Personal notes commands, separate from calendar events and reminders."""

from __future__ import annotations

from datetime import datetime
import re

from telegram import Update
from telegram.ext import ContextTypes

from core.note_store import create_note, delete_note, list_notes, search_notes
from core.db import get_user_timezone
from modules.calendar_user import _user_zone

NOTE_CREATE = "note_create"
NOTE_LIST = "note_list"
NOTE_SEARCH = "note_search"
NOTE_DELETE = "note_delete"

NOTE_CREATE_RE = re.compile(
    r"^\s*(?:(?:создай|добавь|сохрани|запиши)\s+(?:мне\s+)?(?:заметку|запись)\b|"
    r"(?:заметка|запись)\b\s*[:\-])",
    re.IGNORECASE,
)
NOTE_LIST_RE = re.compile(
    r"^\s*(?:покажи|открой|выведи)\s+(?:мои\s+)?заметки\b|^\s*(?:мои\s+)?заметки\s*$",
    re.IGNORECASE,
)
NOTE_SEARCH_RE = re.compile(
    r"^\s*(?:найди|поищи|покажи)\s+(?:мне\s+)?(?:заметку|заметки|запись|записи)\s+(?:про|о|об)\b",
    re.IGNORECASE,
)
NOTE_DELETE_RE = re.compile(
    r"^\s*(?:удали|удалить|убери|убрать)\s+(?:мою\s+)?(?:заметку|запись)\b",
    re.IGNORECASE,
)
NOTE_CREATE_PREFIX_RE = re.compile(
    r"^\s*(?:(?:создай|добавь|сохрани|запиши)\s+(?:мне\s+)?(?:заметку|запись)|"
    r"(?:заметка|запись))\s*[:\-]?\s*",
    re.IGNORECASE,
)
NOTE_SEARCH_PREFIX_RE = re.compile(
    r"^\s*(?:найди|поищи|покажи)\s+(?:мне\s+)?(?:заметку|заметки|запись|записи)\s+(?:про|о|об)\s+",
    re.IGNORECASE,
)
NOTE_DELETE_PREFIX_RE = re.compile(
    r"^\s*(?:удали|удалить|убери|убрать)\s+(?:мою\s+)?(?:заметку|запись)\s*(?:про|о|об)?\s*",
    re.IGNORECASE,
)
CHOICE_WORD_RE = re.compile(r"\b(перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*)\b", re.IGNORECASE)
CANCEL_WORDS = {"нет", "не надо", "отмена", "отменить", "стоп"}


def _normalise(text: str) -> str:
    return text.lower().replace("ё", "е").strip(" \t\r\n.,!?;:…\"'«»")


def detect_note_intent(text: str) -> str | None:
    if NOTE_DELETE_RE.search(text):
        return NOTE_DELETE
    if NOTE_SEARCH_RE.search(text):
        return NOTE_SEARCH
    if NOTE_LIST_RE.search(text):
        return NOTE_LIST
    if NOTE_CREATE_RE.search(text):
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


def _format_created_at(note: dict, timezone: str) -> str:
    try:
        created = datetime.fromisoformat(str(note["created_at"]).replace("Z", "+00:00"))
        return created.astimezone(_user_zone(timezone)).strftime("%d.%m %H:%M")
    except (KeyError, TypeError, ValueError):
        return ""


def _format_line(note: dict, timezone: str, index: int | None = None) -> str:
    prefix = f"{index}." if index is not None else "•"
    when = _format_created_at(note, timezone)
    suffix = f" · {when}" if when else ""
    return f"{prefix} {note['text']}{suffix}"


def _store_pending(context: ContextTypes.DEFAULT_TYPE, payload: dict) -> None:
    context.user_data["smart_planner_pending"] = payload


async def create_note_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    body = _note_body(text)
    if not body:
        _store_pending(context, {"type": "note_text"})
        await update.message.reply_text("Что записать в заметку?")
        return True
    try:
        note = create_note(update.effective_user.id, body)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return True
    await update.message.reply_text(f"Заметка сохранена · «{note['text']}»")
    return True


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
    await update.message.reply_text(f"Заметка «{matches[0]['text']}» удалена.")
    return True


async def handle_note_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, intent: str) -> bool:
    if intent == NOTE_CREATE:
        return await create_note_from_text(update, context, text)
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
        body = text.strip()
        if not body:
            await update.message.reply_text("Что записать в заметку?")
            return True
        context.user_data.pop("smart_planner_pending", None)
        try:
            note = create_note(update.effective_user.id, body)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return True
        await update.message.reply_text(f"Заметка сохранена · «{note['text']}»")
        return True

    if pending_type == "note_select_delete":
        index = _choice_index(text)
        if index is None:
            await update.message.reply_text("Напиши номер заметки или скажи, например, «вторую».")
            return True
        notes = pending.get("notes") or []
        if index < 0 or index >= len(notes):
            await update.message.reply_text("Такого номера нет. Выбери номер из списка.")
            return True
        context.user_data.pop("smart_planner_pending", None)
        note = notes[index]
        if delete_note(update.effective_user.id, note["note_id"]):
            await update.message.reply_text(f"Заметка «{note['text']}» удалена.")
        else:
            await update.message.reply_text("Эта заметка уже удалена.")
        return True

    return False
