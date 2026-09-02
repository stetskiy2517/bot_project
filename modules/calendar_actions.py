"""Изменяющие действия AI Smart Planner для Google Calendar."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_user_timezone
from modules.calendar import (
    _build_event,
    _create_event,
    _date_from_text,
    _extract_duration,
    _extract_time,
    _parse_event_timing,
)
from modules.calendar_user import (
    _event_start,
    _extract_search_query,
    _format_event_line,
    _get_calendar_service,
    _list_events,
    _parse_search_period,
    _user_zone,
)

logger = logging.getLogger(__name__)

YES_WORDS = {"да", "ага", "подтверждаю", "подтвердить", "создавай", "удаляй", "меняй", "ок", "окей"}
NO_WORDS = {"нет", "не надо", "отмена", "отменить", "стоп"}

DELETE_PREFIX_RE = re.compile(r"^(?:удали|удалить|отмени|отменить|убери|убрать)\s+", re.IGNORECASE)
UPDATE_PREFIX_RE = re.compile(
    r"^(?:перенеси|перенести|сдвинь|сдвинуть|измени|изменить|поменяй|поменять|сделай|переименуй)\s+",
    re.IGNORECASE,
)
DATE_TAIL_RE = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|в\s+понедельник\w*|во?\s+вторник\w*|в\s+сред\w*|"
    r"в\s+четверг\w*|в\s+пятниц\w*|в\s+суббот\w*|в\s+воскресень\w*)\b",
    re.IGNORECASE,
)
DURATION_RE = re.compile(
    r"\bна\s+(?:(полчаса)|(полтора\s+часа)|(\d+)\s*(минут\w*|час\w*))\b",
    re.IGNORECASE,
)
RENAME_RE = re.compile(r"^переименуй\s+(.+?)\s+в\s+(.+)$", re.IGNORECASE)


def _normalise(text: str) -> str:
    return text.lower().replace("ё", "е").strip()


def _event_end(event: dict, timezone: str) -> datetime | None:
    end = event.get("end") or {}
    zone = _user_zone(timezone)
    if end.get("dateTime"):
        value = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
        return value.astimezone(zone)
    if end.get("date"):
        return datetime.fromisoformat(end["date"]).replace(tzinfo=zone)
    return None


def _find_conflicts(
    user_id: int,
    start: datetime,
    end: datetime,
    *,
    exclude_event_id: str | None = None,
) -> list[dict]:
    """Вернуть события, которые реально пересекаются с новым интервалом."""
    events = _list_events(user_id, start, end)
    conflicts = []
    timezone = str(start.tzinfo) if start.tzinfo else "Europe/Moscow"
    for event in events:
        if exclude_event_id and event.get("id") == exclude_event_id:
            continue
        event_start, _ = _event_start(event, timezone)
        event_end = _event_end(event, timezone)
        if event_start and event_end and event_start < end and event_end > start:
            conflicts.append(event)
    return conflicts


def _candidate_search(
    user_id: int,
    timezone: str,
    text: str,
    query: str,
) -> list[dict]:
    start, end = _parse_search_period(text, timezone)
    return _list_events(user_id, start, end, query=query)


def _extract_delete_query(text: str) -> str:
    query = DELETE_PREFIX_RE.sub("", text.strip().rstrip("?.!,"))
    query = DATE_TAIL_RE.sub(" ", query)
    query = re.sub(r"\s+", " ", query).strip(" ,.-")
    return query


def _extract_update_target(text: str) -> str:
    rename = RENAME_RE.match(text.strip())
    if rename:
        return rename.group(1).strip()

    body = UPDATE_PREFIX_RE.sub("", text.strip().rstrip("?.!,"))
    duration = DURATION_RE.search(body)
    if duration:
        body = body[: duration.start()]
    else:
        # Для переноса всё после «на ...» — новая дата/время.
        move = re.search(r"\s+на\s+(?=(?:сегодня|завтра|послезавтра|понедельник|вторник|сред|четверг|пятниц|суббот|воскрес|\d))", body, re.IGNORECASE)
        if move:
            body = body[: move.start()]
        else:
            # «перенеси встречу в 16:00»
            move_time = re.search(r"\s+в\s+(?=\d{1,2}(?::|\.|\s)\d{2}\b)", body, re.IGNORECASE)
            if move_time:
                body = body[: move_time.start()]
    return re.sub(r"\s+", " ", body).strip(" ,.-")


def _duration_from_update(text: str) -> timedelta | None:
    match = DURATION_RE.search(_normalise(text))
    if not match:
        return None
    if match.group(1):
        return timedelta(minutes=30)
    if match.group(2):
        return timedelta(minutes=90)
    amount = int(match.group(3))
    unit = match.group(4)
    return timedelta(minutes=amount) if unit.startswith("минут") else timedelta(hours=amount)


def _new_title_from_update(text: str) -> str | None:
    match = RENAME_RE.match(text.strip())
    if not match:
        return None
    title = match.group(2).strip(" ,.-")
    return title[:1].upper() + title[1:] if title else None


def _build_update_patch(event: dict, text: str, timezone: str) -> dict:
    """Построить минимальный patch для даты, времени, длительности или названия."""
    old_start, all_day = _event_start(event, timezone)
    old_end = _event_end(event, timezone)
    if not old_start or not old_end:
        return {}

    patch: dict = {}
    new_title = _new_title_from_update(text)
    if new_title:
        patch["summary"] = new_title
        return patch

    duration = _duration_from_update(text)
    if duration:
        if all_day:
            return {}
        patch["end"] = {"dateTime": (old_start + duration).isoformat(), "timeZone": timezone}
        return patch

    if all_day:
        return {}

    parsed_time = _extract_time(text)
    new_date = _date_from_text(text, old_start, old_start.hour, old_start.minute)
    new_start = old_start
    if new_date:
        new_start = new_start.replace(year=new_date.year, month=new_date.month, day=new_date.day)
    if parsed_time:
        new_start = new_start.replace(hour=parsed_time[0], minute=parsed_time[1], second=0, microsecond=0)

    if new_start == old_start:
        return {}

    old_duration = old_end - old_start
    patch["start"] = {"dateTime": new_start.isoformat(), "timeZone": timezone}
    patch["end"] = {"dateTime": (new_start + old_duration).isoformat(), "timeZone": timezone}
    return patch


def _patch_interval(event: dict, patch: dict, timezone: str) -> tuple[datetime, datetime] | None:
    if "start" in patch:
        start = datetime.fromisoformat(patch["start"]["dateTime"])
    else:
        start, all_day = _event_start(event, timezone)
        if not start or all_day:
            return None
    if "end" in patch:
        end = datetime.fromisoformat(patch["end"]["dateTime"])
    else:
        end = _event_end(event, timezone)
        if not end:
            return None
    return start, end


def _store_pending(context: ContextTypes.DEFAULT_TYPE, payload: dict) -> None:
    context.user_data["smart_planner_pending"] = payload


def _format_candidates(events: list[dict], timezone: str) -> str:
    lines = []
    for index, event in enumerate(events[:5], start=1):
        lines.append(f"{index}. {_format_event_line(event, timezone, include_date=True).lstrip('• ')}")
    return "\n".join(lines)


async def create_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    timing = _parse_event_timing(text)
    if not timing:
        return False
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default=None)
    if not timezone:
        await update.message.reply_text("Сначала выбери часовой пояс для календаря: /timezone")
        return True

    start_naive, end_naive = timing
    zone = _user_zone(timezone)
    start = start_naive.replace(tzinfo=zone) if start_naive.tzinfo is None else start_naive.astimezone(zone)
    end = end_naive.replace(tzinfo=zone) if end_naive.tzinfo is None else end_naive.astimezone(zone)

    try:
        event = _build_event(text, start, end)
        event["start"]["timeZone"] = timezone
        event["end"]["timeZone"] = timezone
        conflicts = _find_conflicts(user_id, start, end)
        if conflicts:
            _store_pending(context, {"type": "confirm_create_conflict", "event": event})
            await update.message.reply_text(
                "В это время уже есть событие:\n"
                f"{_format_event_line(conflicts[0], timezone, include_date=True)}\n"
                "Создать новое событие всё равно? Ответь «да» или «нет»."
            )
            return True
        _create_event(user_id, event)
    except PermissionError:
        await update.message.reply_text("Сначала подключите Google Calendar: /start")
        return True
    except Exception:
        logger.exception("Calendar event creation failed for user %s", user_id)
        await update.message.reply_text("Не удалось добавить событие в Google Calendar. Попробуй ещё раз.")
        return True

    await update.message.reply_text(
        f"Событие «{event['summary']}» добавлено: {start.strftime('%d.%m.%Y %H:%M')}–{end.strftime('%H:%M')} ({timezone})"
    )
    return True


async def delete_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default=None)
    if not timezone:
        await update.message.reply_text("Сначала выбери часовой пояс для календаря: /timezone")
        return True
    query = _extract_delete_query(text)
    if not query:
        await update.message.reply_text("Какое событие удалить?")
        return True

    try:
        events = _candidate_search(user_id, timezone, text, query)
    except PermissionError:
        await update.message.reply_text("Сначала подключите Google Calendar: /start")
        return True
    except Exception:
        logger.exception("Calendar delete search failed for user %s", user_id)
        await update.message.reply_text("Не удалось найти событие для удаления.")
        return True

    if not events:
        await update.message.reply_text(f"Не нашёл событие «{query}».")
        return True
    if len(events) > 1:
        visible = events[:5]
        _store_pending(context, {"type": "select_delete", "events": visible, "timezone": timezone})
        await update.message.reply_text(
            "Нашёл несколько событий. Напиши номер нужного:\n" + _format_candidates(visible, timezone)
        )
        return True

    event = events[0]
    _store_pending(context, {"type": "confirm_delete", "event": event, "timezone": timezone})
    await update.message.reply_text(
        "Удалить это событие?\n"
        f"{_format_event_line(event, timezone, include_date=True)}\n"
        "Ответь «да» или «нет»."
    )
    return True


async def update_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default=None)
    if not timezone:
        await update.message.reply_text("Сначала выбери часовой пояс для календаря: /timezone")
        return True
    query = _extract_update_target(text)
    if not query:
        await update.message.reply_text("Какое событие изменить?")
        return True

    try:
        events = _candidate_search(user_id, timezone, text, query)
    except PermissionError:
        await update.message.reply_text("Сначала подключите Google Calendar: /start")
        return True
    except Exception:
        logger.exception("Calendar update search failed for user %s", user_id)
        await update.message.reply_text("Не удалось найти событие для изменения.")
        return True

    if not events:
        await update.message.reply_text(f"Не нашёл событие «{query}».")
        return True
    if len(events) > 1:
        visible = events[:5]
        _store_pending(context, {"type": "select_update", "events": visible, "timezone": timezone, "text": text})
        await update.message.reply_text(
            "Нашёл несколько событий. Напиши номер нужного:\n" + _format_candidates(visible, timezone)
        )
        return True

    return await _prepare_update_confirmation(update, context, events[0], text, timezone)


async def _prepare_update_confirmation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    event: dict,
    text: str,
    timezone: str,
) -> bool:
    patch = _build_update_patch(event, text, timezone)
    if not patch:
        await update.message.reply_text("Не понял, что именно изменить в событии.")
        return True

    conflict_text = ""
    interval = _patch_interval(event, patch, timezone)
    if interval and ("start" in patch or "end" in patch):
        conflicts = _find_conflicts(update.effective_user.id, interval[0], interval[1], exclude_event_id=event.get("id"))
        if conflicts:
            conflict_text = f"\nВ новом времени есть конфликт: {_format_event_line(conflicts[0], timezone, include_date=True)}"

    _store_pending(context, {"type": "confirm_update", "event": event, "patch": patch, "timezone": timezone})
    await update.message.reply_text(
        "Подтвердить изменение события?\n"
        f"{_format_event_line(event, timezone, include_date=True)}"
        f"{conflict_text}\nОтветь «да» или «нет»."
    )
    return True


async def resume_pending_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    pending: dict,
) -> bool:
    """Продолжить выбор/подтверждение календарного действия."""
    normal = _normalise(text)
    pending_type = pending.get("type")

    if normal in NO_WORDS:
        context.user_data.pop("smart_planner_pending", None)
        await update.message.reply_text("Хорошо, отменил действие.")
        return True

    if pending_type in {"select_delete", "select_update"}:
        if not text.strip().isdigit():
            await update.message.reply_text("Напиши номер события из списка или «отмена».")
            return True
        index = int(text.strip()) - 1
        events = pending.get("events") or []
        if index < 0 or index >= len(events):
            await update.message.reply_text("Такого номера нет. Выбери номер из списка.")
            return True
        event = events[index]
        timezone = pending["timezone"]
        if pending_type == "select_delete":
            _store_pending(context, {"type": "confirm_delete", "event": event, "timezone": timezone})
            await update.message.reply_text(
                "Удалить это событие?\n"
                f"{_format_event_line(event, timezone, include_date=True)}\nОтветь «да» или «нет»."
            )
            return True
        return await _prepare_update_confirmation(update, context, event, pending["text"], timezone)

    if normal not in YES_WORDS:
        await update.message.reply_text("Ответь «да» или «нет».")
        return True

    context.user_data.pop("smart_planner_pending", None)
    user_id = update.effective_user.id
    try:
        if pending_type == "confirm_create_conflict":
            _create_event(user_id, pending["event"])
            await update.message.reply_text(f"Событие «{pending['event'].get('summary', 'Без названия')}» добавлено несмотря на конфликт.")
            return True
        if pending_type == "confirm_delete":
            service = _get_calendar_service(user_id)
            service.events().delete(calendarId="primary", eventId=pending["event"]["id"]).execute()
            await update.message.reply_text(f"Событие «{pending['event'].get('summary', 'Без названия')}» удалено.")
            return True
        if pending_type == "confirm_update":
            service = _get_calendar_service(user_id)
            updated = service.events().patch(
                calendarId="primary",
                eventId=pending["event"]["id"],
                body=pending["patch"],
            ).execute()
            await update.message.reply_text(f"Событие «{updated.get('summary', pending['event'].get('summary', 'Без названия'))}» изменено.")
            return True
    except Exception:
        logger.exception("Calendar pending action failed for user %s", user_id)
        await update.message.reply_text("Не удалось выполнить действие в Google Calendar.")
        return True

    return False
