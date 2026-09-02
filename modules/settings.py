from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.db import get_user_timezone, save_user_timezone

TIMEZONE_OPTIONS = [
    ("Москва", "Europe/Moscow"),
    ("Калининград", "Europe/Kaliningrad"),
    ("Самара", "Europe/Samara"),
    ("Екатеринбург", "Asia/Yekaterinburg"),
    ("Омск", "Asia/Omsk"),
    ("Красноярск", "Asia/Krasnoyarsk"),
    ("Иркутск", "Asia/Irkutsk"),
    ("Якутск", "Asia/Yakutsk"),
    ("Владивосток", "Asia/Vladivostok"),
    ("Магадан", "Asia/Magadan"),
    ("Камчатка", "Asia/Kamchatka"),
    ("Таллин", "Europe/Tallinn"),
]


def _keyboard() -> InlineKeyboardMarkup:
    rows = []
    for index in range(0, len(TIMEZONE_OPTIONS), 2):
        row = []
        for label, timezone in TIMEZONE_OPTIONS[index:index + 2]:
            row.append(InlineKeyboardButton(label, callback_data=f"tz:{timezone}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    current = get_user_timezone(update.effective_user.id, default=None)
    suffix = f"\nСейчас: {current}" if current else ""
    await update.message.reply_text(
        "Выбери часовой пояс для календаря." + suffix,
        reply_markup=_keyboard(),
    )


async def timezone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    timezone = query.data.removeprefix("tz:")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        await query.edit_message_text("Не удалось сохранить часовой пояс. Выбери другой.")
        return
    save_user_timezone(update.effective_user.id, timezone)
    await query.edit_message_text(f"Часовой пояс сохранён: {timezone}")
