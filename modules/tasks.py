"""Отдельные задачи Smart Planner, не смешанные с календарными событиями."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import re

from telegram import Update
from telegram.ext import ContextTypes

from core.db import (
    create_task,
    delete_task,
    get_user_timezone,
    list_tasks,
    set_task_completed,
)
from modules.calendar import _date_from_text, _extract_title, _parse_datetime
from modules.calendar_event_features import _priority_value
from modules.calendar_user import _parse_view_period, _user_zone

TASK_CREATE = "task_create"
TASK_LIST = "task_list"
TASK_COMPLETE = "task_complete"
TASK_DELETE = "task_delete"

TASK_WORD_RE = re.compile(r"\bзадач\w*\b", re.IGNORECASE)
TASK_CREATE_RE = re.compile(
    r"^\s*(?:(?:добавь|добавить|создай|создать|запиши|записать|поставь|поставить)\s+)?"
    r"(?:нов\w*\s+)?(?:задач\w*|дело)\s*[:\-]?\s+.+",
    re.IGNORECASE,
)
TASK_LIST_RE = re.compile(
    r"^\s*(?:какие\s+(?:у\s+меня\s+)?задач\w*|покажи\s+(?:мои\s+)?задач\w*|"
    r"список\s+задач\w*|мои\s+задач\w*|задач\w*\s+(?:на\s+)?(?:сегодня|завтра|послезавтра|"
    r"понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)|"
    r"что\s+(?:мне\s+)?(?:надо|нужно)\s+сделать)\b",
    re.IGNORECASE,
)
TASK_DONE_LIST_RE = re.compile(r"\b(?:выполненн\w*|завершенн\w*|готов\w*)\s+задач\w*\b", re.IGNORECASE)
TASK_COMPLETE_RE = re.compile(
    r"^\s*(?:отметь|пометь|закрой|заверши|выполнил|выполнила|сделал|сделала)\b[^\n]{0,60}\bзадач\w*\b|"
    r"^\s*задач\w*\b.+\b(?:выполнен\w*|готов\w*|сделан\w*)\s*[.!?]*$",
    re.IGNORECASE,
)
TASK_DELETE_RE = re.compile(r"^\s*(?:удали|удалить|убери|убрать)\s+задач\w*\b", re.IGNORECASE)
TASK_DATE_HINT_RE = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|понедельник\w*|вторник\w*|сред\w*|четверг\w*|"
    r"пятниц\w*|суббот\w*|воскресень\w*|\d{1,2}[./-]\d{1,2}|\d{1,2}(?:-?го)?\s+"
    r"(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|август\w*|"
    r"сентябр\w*|октябр\w*|ноябр\w*|декабр\w*))\b",
    re.IGNORECASE,
)
TASK_PREFIX_RE = re.compile(
    r"^\s*(?:(?:добавь|добавить|создай|создать|запиши|записать|поставь|поставить)\s+)?"
    r"(?:нов\w*\s+)?(?:задач\w*|дело)\s*[:\-]?\s*",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PRIORITY_CLEAN_RE = re.compile(
    r"\b(?:высок\w*\s+приоритет\w*|приоритет\w*\s+высок\w*|низк\w*\s+приоритет\w*|"
    r"приоритет\w*\s+низк\w*|(?:обычн|средн|нормальн)\w*\s+приоритет\w*|"
    r"приоритет\w*\s+(?:обычн|средн|нормальн)\w*|срочн\w*|не\s+срочн\w*)\b",
    re.IGNORECASE,
)


def detect_task_intent(text: str) -> str | None:
    """Распознавать только явные команды задач, чтобы не перехватывать календарь."""
    if TASK_DELETE_RE.search(text):
        return TASK_DELETE
    if TASK_COMPLETE_RE.search(text):
        return TASK_COMPLETE
    if TASK_LIST_RE.search(text) or TASK_DONE_LIST_RE.search(text):
        return TASK_LIST
    if TASK_CREATE_RE.search(text):
        return TASK_CREATE
    return None


def _local_now(timezone: str, now: datetime | None = None) -> datetime:
    zone = _user_zone(timezone)
    if now is None:
        return datetime.now(zone)
    return now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)


def _task_due_at(text: str, timezone: str, now: datetime | None = None) -> datetime | None:
    local_now = _local_now(timezone, now)
    naive_now = local_now.replace(tzinfo=None)
    parsed = _parse_datetime(text, naive_now)
    if parsed:
        return parsed.replace(tzinfo=local_now.tzinfo) if parsed.tzinfo is None else parsed.astimezone(local_now.tzinfo)
    due_date = _date_from_text(text, naive_now, 23, 59)
    if due_date:
        return datetime.combine(due_date, time(23, 59), tzinfo=local_now.tzinfo)
    return None


def _task_title(text: str) -> str:
    body = TASK_PREFIX_RE.sub("", text.strip(), count=1)
    body = PRIORITY_CLEAN_RE.sub(" ", body)
    title = _extract_title(body)
    title = re.sub(r"\b(?:до|к|на)\s*$", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" ,.-")
    return title[:1].upper() + title[1:] if title else ""


def _task_priority(text: str) -> str:
    return _priority_value(text) or "normal"


def _task_query(text: str) -> str:
    value = text.strip().rstrip("?.!,")
    value = re.sub(
        r"^\s*(?:отметь|пометь|закрой|заверши|выполнил|выполнила|сделал|сделала|удали|удалить|убери|убрать)\s+",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"^\s*задач\w*\s*[:\-]?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:выполнен\w*|выполненн\w*|готов\w*|сделан\w*)\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\bзадач\w*\b", " ", value, flags=re.IGNORECASE)
    value = PRIORITY_CLEAN_RE.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip(" ,.-")
    return value


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-zа-я0-9]+", value.lower().replace("ё", "е")) if len(token) >= 3]


def _matches(task: dict, query: str) -> bool:
    if not query:
        return False
    hay = _tokens(task["title"])
    for token in _tokens(query):
        prefix = token[:4] if len(token) >= 4 else token
        if not any(word.startswith(prefix) or prefix in word for word in hay):
            return False
    return True


def _find_matching_tasks(user_id: int, query: str, *, status: str = "open") -> list[dict]:
    return [task for task in list_tasks(user_id, status=status, limit=200) if _matches(task, query)]


def _task_period(text: str, timezone: str, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    if not TASK_DATE_HINT_RE.search(text):
        return None
    start, end, _ = _parse_view_period(text, timezone, _local_now(timezone, now))
    return start, end


def _due_datetime(task: dict, timezone: str) -> datetime | None:
    value = task.get("due_at")
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    zone = _user_zone(timezone)
    return parsed.astimezone(zone) if parsed.tzinfo else parsed.replace(tzinfo=zone)


def _format_due(task: dict, timezone: str, now: datetime | None = None) -> str:
    due = _due_datetime(task, timezone)
    if not due:
        return "без срока"
    local_now = _local_now(timezone, now)
    delta = (due.date() - local_now.date()).days
    if delta == 0:
        day = "сегодня"
    elif delta == 1:
        day = "завтра"
    elif delta == 2:
        day = "послезавтра"
    else:
        day = due.strftime("%d.%m")
    if due.hour == 23 and due.minute == 59:
        return day
    return f"{day}, {due.strftime('%H:%M')}"


def _format_task_line(task: dict, timezone: str, index: int | None = None) -> str:
    prefix = f"{index}." if index is not None else "•"
    priority = task.get("priority") or "normal"
    suffix = " · высокий" if priority == "high" else (" · низкий" if priority == "low" else "")
    return f"{prefix} {_format_due(task, timezone)} — {task['title']}{suffix}"


def _format_task_list(tasks: list[dict], timezone: str, *, done: bool = False) -> str:
    if not tasks:
        return "Выполненных задач нет." if done else "Открытых задач нет."
    title = "Выполненные задачи:" if done else "Задачи:"
    return title + "\n" + "\n".join(_format_task_line(task, timezone) for task in tasks[:20])


def _store_pending(context: ContextTypes.DEFAULT_TYPE, payload: dict) -> None:
    context.user_data["smart_planner_pending"] = payload


async def create_task_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    timezone = get_user_timezone(update.effective_user.id, default="Europe/Moscow") or "Europe/Moscow"
    title = _task_title(text)
    if not title:
        await update.message.reply_text("Что записать в задачу?")
        return True
    due_at = _task_due_at(text, timezone)
    task = create_task(
        update.effective_user.id,
        title,
        due_at=due_at,
        priority=_task_priority(text),
    )
    suffix = f" · {_format_due(task, timezone)}" if due_at else ""
    priority = task["priority"]
    if priority != "normal":
        suffix += " · " + ("высокий приоритет" if priority == "high" else "низкий приоритет")
    await update.message.reply_text(f"Задача добавлена · «{task['title']}»{suffix}")
    return True


async def list_tasks_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    timezone = get_user_timezone(update.effective_user.id, default="Europe/Moscow") or "Europe/Moscow"
    done = bool(TASK_DONE_LIST_RE.search(text))
    status = "done" if done else "open"
    period = _task_period(text, timezone)
    if period:
        tasks = list_tasks(
            update.effective_user.id,
            status=status,
            due_start=period[0],
            due_end=period[1],
            limit=100,
        )
    else:
        tasks = list_tasks(update.effective_user.id, status=status, limit=100)
    await update.message.reply_text(_format_task_list(tasks, timezone, done=done))
    return True


async def complete_task_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    timezone = get_user_timezone(update.effective_user.id, default="Europe/Moscow") or "Europe/Moscow"
    query = _task_query(text)
    if not query:
        await update.message.reply_text("Какую задачу отметить выполненной?")
        return True
    matches = _find_matching_tasks(update.effective_user.id, query)
    if not matches:
        await update.message.reply_text(f"Не нашёл открытую задачу «{query}».")
        return True
    if len(matches) > 1:
        visible = matches[:5]
        _store_pending(context, {"type": "task_select_complete", "tasks": visible, "timezone": timezone})
        await update.message.reply_text(
            "Нашёл несколько задач. Напиши номер:\n" +
            "\n".join(_format_task_line(task, timezone, index=index) for index, task in enumerate(visible, start=1))
        )
        return True
    task = set_task_completed(update.effective_user.id, matches[0]["task_id"], True)
    await update.message.reply_text(f"Готово · «{task['title']}»")
    return True


async def delete_task_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    timezone = get_user_timezone(update.effective_user.id, default="Europe/Moscow") or "Europe/Moscow"
    query = _task_query(text)
    if not query:
        await update.message.reply_text("Какую задачу удалить?")
        return True
    matches = _find_matching_tasks(update.effective_user.id, query)
    if not matches:
        await update.message.reply_text(f"Не нашёл открытую задачу «{query}».")
        return True
    if len(matches) > 1:
        visible = matches[:5]
        _store_pending(context, {"type": "task_select_delete", "tasks": visible, "timezone": timezone})
        await update.message.reply_text(
            "Нашёл несколько задач. Напиши номер:\n" +
            "\n".join(_format_task_line(task, timezone, index=index) for index, task in enumerate(visible, start=1))
        )
        return True
    _store_pending(context, {"type": "task_confirm_delete", "task": matches[0], "timezone": timezone})
    await update.message.reply_text(f"Удалить задачу «{matches[0]['title']}»? Ответь «да» или «нет».")
    return True


async def resume_pending_task(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, pending: dict) -> bool:
    pending_type = pending.get("type")
    normal = text.lower().replace("ё", "е").strip()
    if normal in {"нет", "не надо", "отмена", "отменить", "стоп"}:
        context.user_data.pop("smart_planner_pending", None)
        await update.message.reply_text("Хорошо, отменил.")
        return True

    if pending_type in {"task_select_complete", "task_select_delete"}:
        if not text.strip().isdigit():
            await update.message.reply_text("Напиши номер задачи или «отмена».")
            return True
        index = int(text.strip()) - 1
        tasks = pending.get("tasks") or []
        if index < 0 or index >= len(tasks):
            await update.message.reply_text("Такого номера нет. Выбери номер из списка.")
            return True
        task = tasks[index]
        if pending_type == "task_select_complete":
            context.user_data.pop("smart_planner_pending", None)
            completed = set_task_completed(update.effective_user.id, task["task_id"], True)
            await update.message.reply_text(f"Готово · «{completed['title']}»")
            return True
        _store_pending(context, {"type": "task_confirm_delete", "task": task, "timezone": pending.get("timezone")})
        await update.message.reply_text(f"Удалить задачу «{task['title']}»? Ответь «да» или «нет».")
        return True

    if pending_type == "task_confirm_delete":
        if normal not in {"да", "ага", "удаляй", "подтверждаю", "ок", "окей"}:
            await update.message.reply_text("Ответь «да» или «нет».")
            return True
        context.user_data.pop("smart_planner_pending", None)
        task = pending["task"]
        if delete_task(update.effective_user.id, task["task_id"]):
            await update.message.reply_text(f"Задача «{task['title']}» удалена.")
        else:
            await update.message.reply_text("Задача уже удалена или не найдена.")
        return True

    return False


async def handle_task_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    intent: str | None = None,
) -> bool:
    intent = intent or detect_task_intent(text)
    if intent == TASK_CREATE:
        return await create_task_from_text(update, context, text)
    if intent == TASK_LIST:
        return await list_tasks_from_text(update, context, text)
    if intent == TASK_COMPLETE:
        return await complete_task_from_text(update, context, text)
    if intent == TASK_DELETE:
        return await delete_task_from_text(update, context, text)
    return False
