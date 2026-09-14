"""Explicit calendar templates and user-requested daily overviews."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re
from zoneinfo import ZoneInfo

from core.assistant_store import (
    assistant_preferences, get_template, list_templates, normalize_template_name,
)
from core.db import conn, db_lock, get_calendar_preferences, get_category_colors, get_user_timezone
from modules.calendar import _build_event, _create_event, _parse_datetime, _relative_offset
from modules.calendar_user import _event_start, _list_events

logger = logging.getLogger(__name__)


def user_zone(user_id: int) -> ZoneInfo:
    return ZoneInfo(get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow")


def quiet_now(user_id: int, now: datetime | None = None) -> bool:
    prefs = assistant_preferences(user_id)
    if not prefs["quiet_enabled"]:
        return False
    local = (now or datetime.now(timezone.utc)).astimezone(user_zone(user_id))
    clock = local.strftime("%H:%M")
    start, end = prefs["quiet_start"], prefs["quiet_end"]
    return start <= clock < end if start < end else clock >= start or clock < end


def daily_overview(user_id: int, *, evening: bool = False, now: datetime | None = None) -> dict:
    from modules.calendar_availability import find_free_slots, _parse_hhmm

    zone = user_zone(user_id)
    local = (now or datetime.now(timezone.utc)).astimezone(zone)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    warnings, events, windows = [], [], []
    calendar_available = True
    try:
        raw_events = _list_events(user_id, start, end)
        for event in raw_events:
            if event.get("status") == "cancelled":
                continue
            when, all_day = _event_start(event, str(zone))
            events.append({
                "title": str(event.get("summary") or "Без названия"),
                "start": when.isoformat() if when else None,
                "all_day": all_day,
            })
        prefs = get_calendar_preferences(user_id)
        slots = find_free_slots(
            raw_events, str(zone), start, end, timedelta(minutes=30),
            work_start=_parse_hhmm(prefs["work_start"]), work_end=_parse_hhmm(prefs["work_end"]),
            work_days=prefs["work_days"], buffer=timedelta(minutes=prefs["buffer_minutes"]),
            now=local, limit=3,
        )
        windows = [{"start": left.isoformat(), "end": right.isoformat()} for left, right in slots]
    except Exception:
        calendar_available = False
        warnings.append("Календарь сейчас недоступен. Встречи и свободные окна не проверены.")
        logger.warning("Daily overview calendar unavailable for user %s", user_id)

    with db_lock:
        rows = conn.execute(
            "SELECT reminder_id,text,remind_at,status FROM reminders "
            "WHERE user_id=? AND deleted_at IS NULL AND status<>? AND remind_at<? "
            "ORDER BY remind_at,reminder_id LIMIT 50",
            (int(user_id), "completed", end.astimezone(timezone.utc).isoformat()),
        ).fetchall()
    reminders = [dict(zip(("id", "text", "remind_at", "status"), row)) for row in rows]
    with db_lock:
        delivery_rows = conn.execute(
            "SELECT delivery_key,status,attempted_at FROM notification_attempts "
            "WHERE user_id=? AND attempted_at>=? ORDER BY attempted_at DESC LIMIT 10",
            (int(user_id), start.timestamp()),
        ).fetchall()
    deliveries = [dict(zip(("key", "status", "attempted_at"), row)) for row in delivery_rows]
    lines = [("Вечерний разбор" if evening else "Обзор дня") + " · " + local.strftime("%d.%m.%Y")]
    if calendar_available:
        lines.append(f"Встреч: {len(events)}.")
        for event in events[:5]:
            label = "Весь день" if event["all_day"] else (
                datetime.fromisoformat(event["start"]).strftime("%H:%M") if event["start"] else "Время не указано"
            )
            lines.append(f"{label} · {event['title']}")
        if windows and not evening:
            lines.append("Свободно на 30 минут: " + ", ".join(
                datetime.fromisoformat(item["start"]).strftime("%H:%M") for item in windows
            ))
    lines.extend(warnings)
    lines.append(f"Незавершённых напоминаний на сегодня и раньше: {len(reminders)}.")
    for item in reminders[:5]:
        lines.append("• " + item["text"])
    if evening and reminders:
        lines.append("Отметь выполненные, перенеси или оставь как есть. Встречи автоматически не переношу.")
    return {
        "date": local.date().isoformat(), "timezone": str(zone), "evening": evening,
        "calendar_available": calendar_available, "events": events, "windows": windows,
        "reminders": reminders, "warnings": warnings, "deliveries": deliveries, "text": "\n".join(lines),
    }


def match_template(user_id: int, text: str):
    normal = normalize_template_name(text)
    explicit = normal.startswith("шаблон ")
    if explicit:
        normal = normal[7:].strip()
    for item in sorted(list_templates(user_id), key=lambda value: len(value["name"]), reverse=True):
        name = normalize_template_name(item["name"])
        # Bare aliases match only the complete saved name; using dates requires the explicit prefix.
        if normal == name:
            return item, ""
        if explicit and normal.startswith(name + " "):
            return item, normal[len(name):].strip()
    return None


async def prepare_template(update, context, template: dict, date_text: str) -> bool:
    from modules.router import DATE_HINT_RE

    user_id = update.effective_user.id
    zone_name = get_user_timezone(user_id, default=None)
    if not zone_name:
        await update.message.reply_text("Сначала выбери часовой пояс в настройках календаря.")
        return True
    context.user_data["smart_planner_pending"] = {
        "type": "template_date", "template_id": template["id"], "version": template["updated_at"],
    }
    has_date = DATE_HINT_RE.search(date_text) or _relative_offset(date_text)
    if not has_date:
        await update.message.reply_text("Укажи дату и время для шаблона, например: завтра в 15:00.")
        return True
    start = _parse_datetime(date_text, datetime.now(ZoneInfo(zone_name)))
    if start is None or start <= datetime.now(ZoneInfo(zone_name)):
        await update.message.reply_text("Не понял будущую дату и время. Например: завтра в 15:00.")
        return True
    context.user_data["smart_planner_pending"] = {
        "type": "template_confirm", "template_id": template["id"], "version": template["updated_at"],
        "start": start, "timezone": zone_name,
    }
    await update.message.reply_text(
        f"Создать «{template['title']}» {start.strftime('%d.%m.%Y %H:%M')}, "
        f"на {template['duration_minutes']} минут? Да или нет."
    )
    return True


async def handle_assistant_command(update, context, text: str) -> bool:
    user_id = getattr(update.effective_user, "id", None)
    if user_id is None:
        return False
    pending = context.user_data.get("smart_planner_pending") or {}
    normal = normalize_template_name(text).strip(" .,!?")
    if pending.get("type") in {"template_date", "template_confirm"}:
        if normal in {"нет", "отмена", "стоп", "не надо"}:
            context.user_data.pop("smart_planner_pending", None)
            await update.message.reply_text("Отменил создание по шаблону.")
            return True
        template = get_template(user_id, pending["template_id"])
        if not template or template["updated_at"] != pending["version"]:
            context.user_data.pop("smart_planner_pending", None)
            await update.message.reply_text("Шаблон изменён или удалён. Выбери его заново.")
            return True
        if pending["type"] == "template_date":
            return await prepare_template(update, context, template, text)
        if normal not in {"да", "ок", "создавай", "подтверждаю"}:
            await update.message.reply_text("Ответь «да» или «нет».")
            return True
        from modules.calendar_actions import _find_conflicts

        context.user_data.pop("smart_planner_pending", None)
        start = pending["start"]
        end = start + timedelta(minutes=template["duration_minutes"])
        if start <= datetime.now(start.tzinfo) or _find_conflicts(user_id, start, end):
            await update.message.reply_text("Это время прошло или уже занято. Выбери другое время.")
            return True
        event = _build_event(template["title"], start, end, get_category_colors(user_id))
        event["summary"] = template["title"]
        event["description"] = f"Smart Planner category: {template['category']}"
        color = get_category_colors(user_id).get(template["category"])
        event.pop("colorId", None)
        if color:
            event["colorId"] = color
        event["start"]["timeZone"] = pending["timezone"]
        event["end"]["timeZone"] = pending["timezone"]
        _create_event(user_id, event)
        await update.message.reply_text(f"Создано «{template['title']}» на {start.strftime('%d.%m %H:%M')}.")
        return True
    if pending:
        return False
    if normal in {"обзор дня", "мой день", "вечерний разбор"}:
        result = daily_overview(user_id, evening=normal == "вечерний разбор")
        await update.message.reply_text(result["text"])
        return True
    if normal in {"отмени последнее действие", "верни последнее действие"}:
        from core.undo_store import last_undo, undo_action

        action = last_undo(user_id)
        if not action:
            await update.message.reply_text("Нет локального действия, которое можно отменить.")
        else:
            try:
                undo_action(user_id, action["id"])
                await update.message.reply_text("Локальное действие отменено.")
            except (ValueError, LookupError) as exc:
                await update.message.reply_text(str(exc))
        return True
    found = match_template(user_id, text)
    if found:
        return await prepare_template(update, context, *found)
    return False
