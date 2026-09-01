# handlers/calendar_conflicts.py
import logging
from datetime import timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from handlers.calendar_service import get_calendar_service

logger = logging.getLogger(__name__)

def merge_event(service, conflict: dict):
    try:
        service.events().insert(calendarId="primary", body=conflict["new_event"]).execute()
        return "🔗 События объединены"
    except Exception as e:
        logger.exception("Ошибка при объединении события")
        return f"❌ Ошибка объединения: {e}"

def replace_event(service, conflict: dict):
    try:
        service.events().delete(calendarId="primary", eventId=conflict["existing_event_id"]).execute()
        service.events().insert(calendarId="primary", body=conflict["new_event"]).execute()
        return "♻️ Событие заменено"
    except Exception as e:
        logger.exception("Ошибка при замене события")
        return f"❌ Ошибка замены: {e}"

def suggest_new_slot(service, conflict: dict, find_next_free_slot_func):
    new_start = find_next_free_slot_func(service, conflict["start"])
    conflict["new_event"]["start"]["dateTime"] = new_start.isoformat()
    conflict["new_event"]["end"]["dateTime"] = (
        new_start + timedelta(hours=1)
    ).isoformat()

    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Подтвердить новое время",
            callback_data="conflict_confirm_new_time"
        )
    ]]), new_start


async def handle_conflict_choice(update, context):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    from handlers.calendar_service import get_calendar_service
    service = get_calendar_service(user_id)

    conflict = context.user_data.get("conflict")
    if not conflict or not service:
        await query.edit_message_text("❌ Не удалось обработать конфликт")
        return

    if query.data == "conflict_merge":
        text = merge_event(service, conflict)
        await query.edit_message_text(text)

    elif query.data == "conflict_replace":
        text = replace_event(service, conflict)
        await query.edit_message_text(text)

    elif query.data == "conflict_new_time":
        from handlers.utils import find_next_free_slot
        keyboard, new_start = suggest_new_slot(service, conflict, find_next_free_slot)
        if keyboard:
            await query.edit_message_text(
                f"Предлагается новый слот: {new_start.strftime('%d.%m %H:%M')}",
                reply_markup=keyboard
            )
        else:
            await query.edit_message_text("❌ Не удалось найти свободный слот")

    elif query.data == "conflict_confirm_new_time":
        try:
            service.events().insert(calendarId="primary", body=conflict["new_event"]).execute()
            await query.edit_message_text("✅ Событие создано в новом слоте")
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка создания события: {e}")
