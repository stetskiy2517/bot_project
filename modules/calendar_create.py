"""Calendar event creation with per-user category colors."""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from core.db import get_user_timezone
from modules.calendar import _build_event, _create_event, _parse_event_timing
from modules.calendar_actions import _find_conflicts, _format_event_line, _store_pending
from modules.calendar_availability import format_alternatives, suggest_alternatives
from modules.calendar_categories import apply_user_category
from modules.calendar_event_features import apply_event_features, build_all_day_event, is_all_day
from modules.calendar_user import _user_zone

logger = logging.getLogger(__name__)


async def create_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user_id = update.effective_user.id
    timezone = get_user_timezone(user_id, default=None)
    if not timezone:
        await update.message.reply_text("Сначала выбери часовой пояс для календаря: /timezone")
        return True

    try:
        if is_all_day(text):
            event = build_all_day_event(text, timezone)
            if not event:
                return False
            apply_user_category(event, text, user_id)
            _create_event(user_id, event)
            await update.message.reply_text(
                f"Событие «{event['summary']}» добавлено на весь день: {event['start']['date']}"
            )
            return True

        timing = _parse_event_timing(text)
        if not timing:
            return False
        start_naive, end_naive = timing
        zone = _user_zone(timezone)
        start = start_naive.replace(tzinfo=zone) if start_naive.tzinfo is None else start_naive.astimezone(zone)
        end = end_naive.replace(tzinfo=zone) if end_naive.tzinfo is None else end_naive.astimezone(zone)

        event = apply_event_features(_build_event(text, start, end), text)
        apply_user_category(event, text, user_id)
        event["start"]["timeZone"] = timezone
        event["end"]["timeZone"] = timezone
        conflicts = _find_conflicts(user_id, start, end)
        if conflicts:
            alternatives = suggest_alternatives(user_id, timezone, start, end - start, limit=3)
            _store_pending(context, {"type": "confirm_create_conflict", "event": event})
            await update.message.reply_text(
                "В это время уже есть событие:\n"
                f"{_format_event_line(conflicts[0], timezone, include_date=True)}\n"
                f"{format_alternatives(alternatives)}\n"
                "Если всё равно создать в исходное время — ответь «да». Для отмены — «нет»."
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

    extras = []
    if event.get("recurrence"):
        extras.append("повторяется")
    if event.get("reminders"):
        extras.append("с напоминанием")
    if event.get("location"):
        extras.append(f"место: {event['location']}")
    if event.get("attendees"):
        extras.append(f"участников: {len(event['attendees'])}")
    suffix = " · " + ", ".join(extras) if extras else ""
    await update.message.reply_text(
        f"Событие «{event['summary']}» добавлено: {start.strftime('%d.%m.%Y %H:%M')}–{end.strftime('%H:%M')} ({timezone}){suffix}"
    )
    return True
