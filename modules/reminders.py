"""Standalone Smart Planner reminders, separate from Google Calendar events."""

from __future__ import annotations

from datetime import datetime
import re

from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_user_timezone
from core.reminder_store import claim_due_reminders, create_reminder, delete_reminder, list_reminders
from modules.calendar import _extract_title, _parse_datetime
from modules.calendar_user import _user_zone

REMINDER_CREATE = "reminder_create"
REMINDER_LIST = "reminder_list"
REMINDER_DELETE = "reminder_delete"

REMINDER_CREATE_RE = re.compile(
    r"^\s*(?:(?:напомни|напомнить|напомню)(?:\s+мне)?|не\s+забудь(?:те)?|"
    r"(?:добавь|добавить|создай|создать|поставь|поставить)\s+напоминани\w*)\b",
    re.IGNORECASE,
)
REMINDER_COMMAND_ANY_RE = re.compile(
    r"\b(?:напомни|напомнить|напомню)(?:\s+мне)?\b",
    re.IGNORECASE,
)
TIME_FIRST_REMINDER_RE = re.compile(
    r"^\s*(?:"
    r"(?:(?:сегодня|завтра|послезавтра)\b[^,.!?]{0,32})|"
    r"(?:(?:в|к)\s+[^,.!?]{1,24})|"
    r"(?:через\s+[^,.!?]{1,32})"
    r")\s+(?:напомни|напомнить|напомню)(?:\s+мне)?\b",
    re.IGNORECASE,
)
REMINDER_LIST_RE = re.compile(
    r"^\s*(?:какие\s+(?:у\s+меня\s+)?напоминани\w*|покажи\s+(?:мои\s+)?напоминани\w*|"
    r"список\s+напоминани\w*|мои\s+напоминани\w*)\b",
    re.IGNORECASE,
)
REMINDER_DELETE_RE = re.compile(
    r"^\s*(?:удали|удалить|убери|убрать|отмени|отменить)\s+напоминани\w*\b",
    re.IGNORECASE,
)
CALENDAR_REMINDER_PROPERTY_RE = re.compile(
    r"\b(?:у|для|к)\s+(?:встреч\w*|событ\w*|созвон\w*|звонк\w*)\b",
    re.IGNORECASE,
)
CALENDAR_INLINE_REMINDER_RE = re.compile(
    r"\b(?:встреч\w*|событ\w*|созвон\w*|звонк\w*)\b[^.!?]{0,120}"
    r"\b(?:напомни|напоминани\w*)\s+за\b",
    re.IGNORECASE,
)
REMINDER_PREFIX_RE = re.compile(
    r"^\s*(?:(?:напомни|напомнить|напомню)(?:\s+мне)?|не\s+забудь(?:те)?|"
    r"(?:добавь|добавить|создай|создать|поставь|поставить)\s+напоминани\w*)\s*[,.:;\-]?\s*",
    re.IGNORECASE,
)
REMINDER_DELETE_PREFIX_RE = re.compile(
    r"^\s*(?:удали|удалить|убери|убрать|отмени|отменить)\s+напоминани\w*\s*[,.:;\-]?\s*",
    re.IGNORECASE,
)
CANCEL_WORDS = {"нет", "не надо", "отмена", "отменить", "стоп"}


def _normalise(text: str) -> str:
    return text.lower().replace("ё", "е").strip(" \t\r\n.,!?;:…\"'«»")


def detect_reminder_intent(text: str) -> str | None:
    if CALENDAR_REMINDER_PROPERTY_RE.search(text) or CALENDAR_INLINE_REMINDER_RE.search(text):
        return None
    if REMINDER_DELETE_RE.search(text):
        return REMINDER_DELETE
    if REMINDER_LIST_RE.search(text):
        return REMINDER_LIST
    if REMINDER_CREATE_RE.search(text) or TIME_FIRST_REMINDER_RE.search(text):
        return REMINDER_CREATE
    return None


def _local_now(timezone: str, now: datetime | None = None) -> datetime:
    zone = _user_zone(timezone)
    if now is None:
        return datetime.now(zone)
    return now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)


def _reminder_due_at(text: str, timezone: str, now: datetime | None = None) -> datetime | None:
    local_now = _local_now(timezone, now)
    parsed = _parse_datetime(text, local_now.replace(tzinfo=None))
    if not parsed:
        return None
    return parsed.replace(tzinfo=local_now.tzinfo) if parsed.tzinfo is None else parsed.astimezone(local_now.tzinfo)


def _reminder_title(text: str) -> str:
    raw = text.strip()
    if TIME_FIRST_REMINDER_RE.search(raw):
        command = REMINDER_COMMAND_ANY_RE.search(raw)
        body = raw[command.end():].lstrip(" ,.:;-") if command else raw
    else:
        body = REMINDER_PREFIX_RE.sub("", raw, count=1)
    title = _extract_title(body)
    if title == "Встреча" and not re.search(r"\bвстреч\w*\b", body, re.IGNORECASE):
        return ""
    return title.strip(" ,.-")


def _reminder_datetime(reminder: dict, timezone: str) -> datetime:
    value = datetime.fromisoformat(str(reminder["remind_at"]).replace("Z", "+00:00"))
    return value.astimezone(_user_zone(timezone))


def _format_when(reminder: dict, timezone: str, now: datetime | None = None) -> str:
    due = _reminder_datetime(reminder, timezone)
    local_now = _local_now(timezone, now)
    delta = (due.date() - local_now.date()).days
    if delta == 0:
        day = "Сегодня"
    elif delta == 1:
        day = "Завтра"
    elif delta == 2:
        day = "Послезавтра"
    else:
        day = due.strftime("%d.%m")
    return f"{day}, {due.strftime('%H:%M')}"


def _format_line(reminder: dict, timezone: str, index: int | None = None) -> str:
    prefix = f"{index}." if index is not None else "•"
    return f"{prefix} {_format_when(reminder, timezone)} — {reminder['text']}"


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-zа-я0-9]+", value.lower().replace("ё", "е")) if len(token) >= 3]


def _matches(reminder: dict, query: str) -> bool:
    query_tokens = _tokens(query)
    if not query_tokens:
        return False
    hay = _tokens(str(reminder.get("text") or ""))
    for token in query_tokens:
        prefix = token[:4] if len(token) >= 4 else token
        if not any(word.startswith(prefix) or prefix in word for word in hay):
            return False
    return True


def _store_pending(context: ContextTypes.DEFAULT_TYPE, payload: dict) -> None:
    context.user_data["smart_planner_pending"] = payload


async def create_reminder_from_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    title = _reminder_title(text)
    if not title:
        await update.message.reply_text("О чём напомнить?")
        return True
    due_at = _reminder_due_at(text, timezone)
    if not due_at:
        _store_pending(context, {"type": "reminder_time", "text": text, "title": title, "timezone": timezone})
        await update.message.reply_text("Когда напомнить?")
        return True
    reminder = create_reminder(user_id, title, due_at)
    await update.message.reply_text(
        f"Напоминание · «{reminder['text']}»\n{_format_when(reminder, timezone)}"
    )
    return True


async def list_reminders_from_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    reminders = list_reminders(user_id, limit=100)
    if not reminders:
        await update.message.reply_text("Активных напоминаний нет.")
        return True
    await update.message.reply_text(
        "Напоминания:\n" + "\n".join(_format_line(item, timezone) for item in reminders[:20])
    )
    return True


async def delete_reminder_from_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    query = REMINDER_DELETE_PREFIX_RE.sub("", text.strip().rstrip("?.!,"), count=1).strip(" ,.-")
    if not query:
        await update.message.reply_text("Какое напоминание удалить?")
        return True
    matches = [item for item in list_reminders(user_id, limit=200) if _matches(item, query)]
    if not matches:
        await update.message.reply_text(f"Не нашёл напоминание «{query}».")
        return True
    if len(matches) > 1:
        visible = matches[:5]
        _store_pending(context, {"type": "reminder_select_delete", "reminders": visible, "timezone": timezone})
        await update.message.reply_text(
            "Нашёл несколько напоминаний. Напиши номер:\n" +
            "\n".join(_format_line(item, timezone, index=index) for index, item in enumerate(visible, start=1))
        )
        return True
    delete_reminder(user_id, matches[0]["reminder_id"])
    await update.message.reply_text(f"Напоминание «{matches[0]['text']}» удалено.")
    return True


async def handle_reminder_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    intent: str,
) -> bool:
    if intent == REMINDER_CREATE:
        return await create_reminder_from_text(update, context, text)
    if intent == REMINDER_LIST:
        return await list_reminders_from_text(update, context, text)
    if intent == REMINDER_DELETE:
        return await delete_reminder_from_text(update, context, text)
    return False


async def resume_pending_reminder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    pending: dict,
) -> bool:
    normal = _normalise(text)
    if normal in CANCEL_WORDS:
        context.user_data.pop("smart_planner_pending", None)
        await update.message.reply_text("Хорошо, отменил.")
        return True

    pending_type = str(pending.get("type") or "")
    if pending_type == "reminder_time":
        timezone = pending.get("timezone") or get_user_timezone(update.effective_user.id, default="Europe/Moscow") or "Europe/Moscow"
        combined = f"{pending.get('text', '')} {text}".strip()
        due_at = _reminder_due_at(combined, timezone)
        if not due_at:
            await update.message.reply_text("Не понял время. Например: «в 22:00», «завтра в 9» или «через 30 минут».")
            return True
        context.user_data.pop("smart_planner_pending", None)
        reminder = create_reminder(update.effective_user.id, pending["title"], due_at)
        await update.message.reply_text(
            f"Напоминание · «{reminder['text']}»\n{_format_when(reminder, timezone)}"
        )
        return True

    if pending_type == "reminder_select_delete":
        if not text.strip().isdigit():
            await update.message.reply_text("Напиши номер напоминания или «отмена».")
            return True
        index = int(text.strip()) - 1
        reminders = pending.get("reminders") or []
        if index < 0 or index >= len(reminders):
            await update.message.reply_text("Такого номера нет. Выбери номер из списка.")
            return True
        context.user_data.pop("smart_planner_pending", None)
        reminder = reminders[index]
        if delete_reminder(update.effective_user.id, reminder["reminder_id"]):
            await update.message.reply_text(f"Напоминание «{reminder['text']}» удалено.")
        else:
            await update.message.reply_text("Это напоминание уже выполнено или удалено.")
        return True

    return False


def claim_due_for_user(user_id: int, timezone: str | None = None) -> list[dict]:
    timezone = timezone or get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    result = []
    for reminder in claim_due_reminders(user_id):
        result.append(
            {
                "id": reminder["reminder_id"],
                "text": reminder["text"],
                "remind_at": _reminder_datetime(reminder, timezone).isoformat(),
                "message": f"Напоминание · {reminder['text']}",
            }
        )
    return result
