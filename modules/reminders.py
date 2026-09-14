"""Standalone Smart Planner reminders, separate from Google Calendar events."""

from __future__ import annotations

from datetime import datetime, timedelta
import re

from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_user_timezone
from core.reminder_recurrence import repeat_label
from core.reminder_store import (
    claim_due_reminders,
    create_reminder,
    delete_reminder,
    list_active_reminders,
    list_reminders,
)
from modules.calendar import _extract_time, _extract_title, _parse_datetime
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
BARE_DATE_HOUR_RE = re.compile(
    r"\b(?P<date>сегодня|завтра|завтро|послезавтра|послезавтро|"
    r"понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)"
    r"\s+(?P<hour>[01]?\d|2[0-3])\b",
    re.IGNORECASE,
)
RECURRING_DAYPART_CLOCK_RE = re.compile(
    r"\bкажд\w*\s+(?P<part>утр\w*|вечер\w*|дн\w*|ноч\w*)\s+"
    r"(?:в\s+)?(?P<hour>\d{1,2})(?:\s*(?::|\.)\s*(?P<minute>[0-5]\d))?\b",
    re.IGNORECASE,
)
REPEAT_CLEAN_RE = re.compile(
    r"\b(?:"
    r"ежедневно|еженедельно|"
    r"по\s+(?:будням|выходным|понедельникам|вторникам|средам|четвергам|пятницам|субботам|воскресеньям)|"
    r"кажд\w*\s+(?:будн\w*\s+день|день|утр\w*|вечер\w*|ноч\w*|выходн\w*|"
    r"понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*|недел\w*)"
    r")\b",
    re.IGNORECASE,
)
CHOICE_WORD_RE = re.compile(r"\b(перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*)\b", re.IGNORECASE)
QUERY_STOP_WORDS = {"про", "напоминание", "напоминания", "напоминанию"}
CANCEL_WORDS = {"нет", "не надо", "отмена", "отменить", "стоп"}
REMINDER_REFERENCE_REPLIES = {"это", "его", "это напоминание", "последнее", "последнее напоминание"}
LAST_REMINDER_KEY = "smart_planner_last_reminder"
WEEKDAY_REPEAT_PATTERNS = (
    (0, r"(?:кажд\w*\s+понедельник\w*|по\s+понедельникам)"),
    (1, r"(?:кажд\w*\s+вторник\w*|по\s+вторникам)"),
    (2, r"(?:кажд\w*\s+сред\w*|по\s+средам)"),
    (3, r"(?:кажд\w*\s+четверг\w*|по\s+четвергам)"),
    (4, r"(?:кажд\w*\s+пятниц\w*|по\s+пятницам)"),
    (5, r"(?:кажд\w*\s+суббот\w*|по\s+субботам)"),
    (6, r"(?:кажд\w*\s+воскресень\w*|по\s+воскресеньям)"),
)


def _normalise(text: str) -> str:
    return text.lower().replace("ё", "е").strip(" \t\r\n.,!?;:…\"'«»")


def _repeat_rule(text: str) -> str | None:
    """Return the deterministic recurrence rule for standalone reminders."""
    lower = _normalise(text)
    if re.search(r"\b(?:по\s+будням|кажд\w*\s+будн\w*\s+день)\b", lower):
        return "weekdays"
    if re.search(r"\b(?:по\s+выходным|кажд\w*\s+выходн\w*)\b", lower):
        return "weekends"
    for weekday, pattern in WEEKDAY_REPEAT_PATTERNS:
        if re.search(rf"\b{pattern}\b", lower):
            return f"weekly:{weekday}"
    if re.search(
        r"\b(?:ежедневно|кажд\w*\s+день|кажд\w*\s+(?:утр\w*|вечер\w*|ноч\w*))\b",
        lower,
    ):
        return "daily"
    if re.search(r"\b(?:еженедельно|кажд\w*\s+недел\w*|раз\s+в\s+недел\w*)\b", lower):
        return "weekly"
    return None


def _repair_reminder_text(text: str) -> str:
    """Repair safe ASR shorthand like «завтра 9 напомни…» without guessing arbitrary numbers."""
    if _extract_time(text) is not None:
        return text

    def repl(match: re.Match) -> str:
        return f"{match.group('date')} в {match.group('hour')}"

    return BARE_DATE_HOUR_RE.sub(repl, text, count=1)


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


def detect_reminder_intent(text: str) -> str | None:
    if CALENDAR_REMINDER_PROPERTY_RE.search(text) or CALENDAR_INLINE_REMINDER_RE.search(text):
        return None
    if REMINDER_DELETE_RE.search(text):
        return REMINDER_DELETE
    if REMINDER_LIST_RE.search(text):
        return REMINDER_LIST
    if REMINDER_CREATE_RE.search(text) or TIME_FIRST_REMINDER_RE.search(text):
        return REMINDER_CREATE
    # Recurring requests often put the schedule before the action:
    # «каждый вечер в 11 напомни выпить таблетку».
    if _repeat_rule(text) and REMINDER_COMMAND_ANY_RE.search(text):
        return REMINDER_CREATE
    return None


def _local_now(timezone: str, now: datetime | None = None) -> datetime:
    zone = _user_zone(timezone)
    if now is None:
        return datetime.now(zone)
    return now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)


def _daypart_hour(hour: int, part: str) -> int | None:
    if not 0 <= hour <= 23:
        return None
    if hour > 12:
        return hour
    lower = part.lower().replace("ё", "е")
    if lower.startswith("вечер") or lower.startswith("дн"):
        if 1 <= hour < 12:
            return hour + 12
        return hour if hour == 12 else None
    if lower.startswith("ноч") or lower.startswith("утр"):
        if hour == 12:
            return 0
        return hour if 0 <= hour <= 11 else None
    return hour


def _reminder_clock(text: str) -> tuple[int, int] | None:
    recurring_part = RECURRING_DAYPART_CLOCK_RE.search(_normalise(text))
    if recurring_part:
        hour = _daypart_hour(int(recurring_part.group("hour")), recurring_part.group("part"))
        if hour is None:
            return None
        return hour, int(recurring_part.group("minute") or 0)
    return _extract_time(text)


def _first_repeat_due(
    text: str,
    timezone: str,
    rule: str,
    now: datetime | None = None,
) -> datetime | None:
    clock = _reminder_clock(text)
    if clock is None:
        return None
    hour, minute = clock
    local_now = _local_now(timezone, now)
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if rule == "daily":
        if candidate <= local_now:
            candidate += timedelta(days=1)
        return candidate

    if rule == "weekdays":
        while candidate <= local_now or candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    if rule == "weekends":
        while candidate <= local_now or candidate.weekday() < 5:
            candidate += timedelta(days=1)
        return candidate

    if rule.startswith("weekly:"):
        weekday = int(rule.split(":", 1)[1])
        days = (weekday - candidate.weekday()) % 7
        candidate += timedelta(days=days)
        if candidate <= local_now:
            candidate += timedelta(days=7)
        return candidate

    if rule == "weekly":
        if candidate <= local_now:
            candidate += timedelta(days=7)
        return candidate
    return None


def _reminder_due_at(text: str, timezone: str, now: datetime | None = None) -> datetime | None:
    repeat_rule = _repeat_rule(text)
    if repeat_rule:
        return _first_repeat_due(text, timezone, repeat_rule, now)
    local_now = _local_now(timezone, now)
    parsed = _parse_datetime(_repair_reminder_text(text), local_now.replace(tzinfo=None))
    if not parsed:
        return None
    return parsed.replace(tzinfo=local_now.tzinfo) if parsed.tzinfo is None else parsed.astimezone(local_now.tzinfo)


def _reminder_title(text: str) -> str:
    raw = _repair_reminder_text(text).strip()
    command = REMINDER_COMMAND_ANY_RE.search(raw)
    if command:
        body = raw[command.end():].lstrip(" ,.:;-")
    else:
        body = REMINDER_PREFIX_RE.sub("", raw, count=1)
    body = REPEAT_CLEAN_RE.sub(" ", body)
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


def _repeat_suffix(reminder: dict) -> str:
    label = repeat_label(reminder.get("repeat_rule"))
    return f" · повтор: {label}" if label else " · повтор: нет"


def _format_line(reminder: dict, timezone: str, index: int | None = None) -> str:
    prefix = f"{index}." if index is not None else "•"
    return f"{prefix} {_format_when(reminder, timezone)} — {reminder['text']}{_repeat_suffix(reminder)}"


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-zа-я0-9]+", value.lower().replace("ё", "е")) if len(token) >= 3]


def _matches(reminder: dict, query: str) -> bool:
    query_tokens = [token for token in _tokens(query) if token not in QUERY_STOP_WORDS]
    if not query_tokens:
        return False
    hay = _tokens(str(reminder.get("text") or ""))
    for token in query_tokens:
        is_cyrillic = bool(re.fullmatch(r"[а-я]+", token))
        prefix_len = 3 if is_cyrillic and len(token) >= 4 else min(4, len(token))
        prefix = token[:prefix_len]
        if not any(word.startswith(prefix) or prefix in word for word in hay):
            return False
    return True


def _store_pending(context: ContextTypes.DEFAULT_TYPE, payload: dict) -> None:
    context.user_data["smart_planner_pending"] = payload


def _remember_reminder_reference(context: ContextTypes.DEFAULT_TYPE, reminder: dict) -> None:
    context.user_data[LAST_REMINDER_KEY] = {
        "reminder_id": reminder.get("reminder_id"),
        "text": reminder.get("text"),
    }


def _active_reference(context: ContextTypes.DEFAULT_TYPE, reminders: list[dict]) -> dict | None:
    reference = context.user_data.get(LAST_REMINDER_KEY)
    if not isinstance(reference, dict):
        return None
    reminder_id = reference.get("reminder_id")
    return next((item for item in reminders if item.get("reminder_id") == reminder_id), None)


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
    repeat_rule = _repeat_rule(text)
    due_at = _reminder_due_at(text, timezone)
    if not due_at:
        _store_pending(
            context,
            {
                "type": "reminder_time",
                "text": text,
                "title": title,
                "timezone": timezone,
                "repeat_rule": repeat_rule,
            },
        )
        await update.message.reply_text("Когда напомнить?")
        return True
    reminder = create_reminder(
        user_id,
        title,
        due_at,
        repeat_rule=repeat_rule,
        repeat_timezone=timezone if repeat_rule else None,
    )
    _remember_reminder_reference(context, reminder)
    await update.message.reply_text(
        f"Напоминание · «{reminder['text']}»\n{_format_when(reminder, timezone)}{_repeat_suffix(reminder)}"
    )
    return True


async def list_reminders_from_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
    reminders = list_active_reminders(user_id, limit=100)
    if not reminders:
        context.user_data.pop(LAST_REMINDER_KEY, None)
        await update.message.reply_text("Активных напоминаний нет.")
        return True
    if len(reminders) == 1:
        _remember_reminder_reference(context, reminders[0])
    else:
        context.user_data.pop(LAST_REMINDER_KEY, None)
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
        reminders = list_active_reminders(user_id, limit=200)
        if not reminders:
            context.user_data.pop(LAST_REMINDER_KEY, None)
            await update.message.reply_text("Активных напоминаний нет.")
            return True
        _store_pending(
            context,
            {
                "type": "reminder_delete_query",
                "timezone": timezone,
                "reference": _active_reference(context, reminders),
            },
        )
        await update.message.reply_text("Какое напоминание удалить?")
        return True
    matches = [item for item in list_active_reminders(user_id, limit=200) if _matches(item, query)]
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
    context.user_data.pop(LAST_REMINDER_KEY, None)
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
        repeat_rule = pending.get("repeat_rule") or _repeat_rule(combined)
        context.user_data.pop("smart_planner_pending", None)
        reminder = create_reminder(
            update.effective_user.id,
            pending["title"],
            due_at,
            repeat_rule=repeat_rule,
            repeat_timezone=timezone if repeat_rule else None,
        )
        _remember_reminder_reference(context, reminder)
        await update.message.reply_text(
            f"Напоминание · «{reminder['text']}»\n{_format_when(reminder, timezone)}{_repeat_suffix(reminder)}"
        )
        return True

    if pending_type == "reminder_delete_query":
        user_id = update.effective_user.id
        timezone = pending.get("timezone") or get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow"
        if normal in REMINDER_REFERENCE_REPLIES:
            reminders = list_active_reminders(user_id, limit=200)
            reminder = pending.get("reference") if isinstance(pending.get("reference"), dict) else None
            if reminder is not None:
                reminder = next(
                    (item for item in reminders if item.get("reminder_id") == reminder.get("reminder_id")),
                    None,
                )
            if reminder is None and len(reminders) == 1:
                reminder = reminders[0]
            if reminder is None:
                if not reminders:
                    context.user_data.pop("smart_planner_pending", None)
                    context.user_data.pop(LAST_REMINDER_KEY, None)
                    await update.message.reply_text("Активных напоминаний нет.")
                    return True
                visible = reminders[:5]
                _store_pending(
                    context,
                    {"type": "reminder_select_delete", "reminders": visible, "timezone": timezone},
                )
                await update.message.reply_text(
                    "Не понял, какое именно. Напиши номер:\n" +
                    "\n".join(
                        _format_line(item, timezone, index=index)
                        for index, item in enumerate(visible, start=1)
                    )
                )
                return True
            context.user_data.pop("smart_planner_pending", None)
            context.user_data.pop(LAST_REMINDER_KEY, None)
            if delete_reminder(user_id, reminder["reminder_id"]):
                await update.message.reply_text(f"Напоминание «{reminder['text']}» удалено.")
            else:
                await update.message.reply_text("Это напоминание уже выполнено или удалено.")
            return True

        context.user_data.pop("smart_planner_pending", None)
        return await delete_reminder_from_text(update, context, f"удали напоминание {text}")

    if pending_type == "reminder_select_delete":
        index = _choice_index(text)
        if index is None:
            await update.message.reply_text("Напиши номер напоминания или скажи, например, «второе».")
            return True
        reminders = pending.get("reminders") or []
        if index < 0 or index >= len(reminders):
            await update.message.reply_text("Такого номера нет. Выбери номер из списка.")
            return True
        context.user_data.pop("smart_planner_pending", None)
        context.user_data.pop(LAST_REMINDER_KEY, None)
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
