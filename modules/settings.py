from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.db import (
    get_calendar_preferences,
    get_user_timezone,
    save_calendar_preferences,
    save_user_timezone,
)

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

DAY_NAMES = {0: "пн", 1: "вт", 2: "ср", 3: "чт", 4: "пт", 5: "сб", 6: "вс"}
DAY_ALIASES = {
    "пн": 0, "понедельник": 0,
    "вт": 1, "вторник": 1,
    "ср": 2, "среда": 2,
    "чт": 3, "четверг": 3,
    "пт": 4, "пятница": 4,
    "сб": 5, "суббота": 5,
    "вс": 6, "воскресенье": 6,
}


def timezone_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for index in range(0, len(TIMEZONE_OPTIONS), 2):
        row = []
        for label, timezone in TIMEZONE_OPTIONS[index:index + 2]:
            row.append(InlineKeyboardButton(label, callback_data=f"tz:{timezone}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _workhours_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("09:00–18:00", callback_data="wh:09:00:18:00"),
            InlineKeyboardButton("10:00–19:00", callback_data="wh:10:00:19:00"),
        ],
        [
            InlineKeyboardButton("08:00–17:00", callback_data="wh:08:00:17:00"),
            InlineKeyboardButton("09:00–20:00", callback_data="wh:09:00:20:00"),
        ],
    ])


def _workdays_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Пн–Пт", callback_data="wd:0,1,2,3,4"),
            InlineKeyboardButton("Пн–Сб", callback_data="wd:0,1,2,3,4,5"),
        ],
        [InlineKeyboardButton("Каждый день", callback_data="wd:0,1,2,3,4,5,6")],
    ])


def _buffer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Без буфера", callback_data="buf:0"),
            InlineKeyboardButton("15 минут", callback_data="buf:15"),
        ],
        [
            InlineKeyboardButton("30 минут", callback_data="buf:30"),
            InlineKeyboardButton("60 минут", callback_data="buf:60"),
        ],
    ])


def _parse_hhmm(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%H:%M")
    except ValueError:
        return None


def _valid_hhmm(value: str) -> bool:
    return _parse_hhmm(value) is not None


def _parse_work_days(values: list[str]) -> list[int] | None:
    if len(values) == 1 and "-" in values[0]:
        left, right = values[0].split("-", 1)
        try:
            start = int(left) - 1
            end = int(right) - 1
        except ValueError:
            return None
        if 0 <= start <= end <= 6:
            return list(range(start, end + 1))
        return None

    days = []
    for value in values:
        key = value.lower().strip(" ,")
        if key.isdigit():
            day = int(key) - 1
        else:
            day = DAY_ALIASES.get(key)
        if day is None or not 0 <= day <= 6:
            return None
        days.append(day)
    return sorted(set(days)) if days else None


def _settings_text(user_id: int) -> str:
    prefs = get_calendar_preferences(user_id)
    timezone = get_user_timezone(user_id, default=None) or "не выбран"
    days = ", ".join(DAY_NAMES[day] for day in prefs["work_days"])
    return (
        "Настройки календаря:\n"
        f"• часовой пояс: {timezone}\n"
        f"• рабочие часы: {prefs['work_start']}–{prefs['work_end']}\n"
        f"• рабочие дни: {days}\n"
        f"• буфер между встречами: {prefs['buffer_minutes']} мин"
    )


async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    current = get_user_timezone(update.effective_user.id, default=None)
    suffix = f"\nСейчас: {current}" if current else ""
    await update.message.reply_text("Выбери часовой пояс для календаря." + suffix, reply_markup=timezone_keyboard())


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
    await query.edit_message_text(
        f"Часовой пояс сохранён: {timezone}\n\nТеперь выбери обычные рабочие часы.",
        reply_markup=_workhours_keyboard(),
    )


async def workhours_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    parts = query.data.split(":")
    if len(parts) != 5:
        await query.edit_message_text("Не удалось сохранить рабочие часы. Используй /workhours.")
        return
    start = f"{parts[1]}:{parts[2]}"
    end = f"{parts[3]}:{parts[4]}"
    if not _valid_hhmm(start) or not _valid_hhmm(end):
        await query.edit_message_text("Не удалось сохранить рабочие часы. Используй /workhours.")
        return
    save_calendar_preferences(update.effective_user.id, work_start=start, work_end=end)
    await query.edit_message_text(
        f"Рабочие часы: {start}–{end}\n\nТеперь выбери рабочие дни.",
        reply_markup=_workdays_keyboard(),
    )


async def workdays_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    raw = query.data.removeprefix("wd:")
    try:
        days = sorted({int(value) for value in raw.split(",")})
    except ValueError:
        days = []
    if not days or any(day < 0 or day > 6 for day in days):
        await query.edit_message_text("Не удалось сохранить рабочие дни. Используй /workdays.")
        return
    save_calendar_preferences(update.effective_user.id, work_days=days)
    labels = ", ".join(DAY_NAMES[day] for day in days)
    await query.edit_message_text(
        f"Рабочие дни: {labels}\n\nКакой буфер оставлять между встречами?",
        reply_markup=_buffer_keyboard(),
    )


async def buffer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    raw = query.data.removeprefix("buf:")
    if not raw.isdigit():
        await query.edit_message_text("Не удалось сохранить буфер. Используй /buffer.")
        return
    minutes = int(raw)
    if not 0 <= minutes <= 180:
        await query.edit_message_text("Буфер должен быть от 0 до 180 минут.")
        return
    save_calendar_preferences(update.effective_user.id, buffer_minutes=minutes)
    await query.edit_message_text(
        "Настройка завершена.\n\n" + _settings_text(update.effective_user.id) +
        "\n\nТеперь можно писать или диктовать команды календарю."
    )


async def calendar_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        _settings_text(update.effective_user.id) +
        "\n\nИзменить:\n/timezone\n/workhours 09:00 18:00\n"
        "/workdays 1-5  или  /workdays пн вт ср чт пт\n/buffer 15"
    )


async def workhours_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if len(context.args) != 2:
        await update.message.reply_text("Формат: /workhours 09:00 18:00")
        return
    parsed_start = _parse_hhmm(context.args[0])
    parsed_end = _parse_hhmm(context.args[1])
    if not parsed_start or not parsed_end:
        await update.message.reply_text("Формат: /workhours 09:00 18:00")
        return
    if parsed_start >= parsed_end:
        await update.message.reply_text("Начало рабочего дня должно быть раньше конца.")
        return
    start = parsed_start.strftime("%H:%M")
    end = parsed_end.strftime("%H:%M")
    save_calendar_preferences(update.effective_user.id, work_start=start, work_end=end)
    await update.message.reply_text(f"Рабочие часы сохранены: {start}–{end}")


async def workdays_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    days = _parse_work_days(context.args)
    if not days:
        await update.message.reply_text("Формат: /workdays 1-5 или /workdays пн вт ср чт пт")
        return
    save_calendar_preferences(update.effective_user.id, work_days=days)
    labels = ", ".join(DAY_NAMES[day] for day in days)
    await update.message.reply_text(f"Рабочие дни сохранены: {labels}")


async def buffer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Формат: /buffer 15")
        return
    minutes = int(context.args[0])
    if not 0 <= minutes <= 180:
        await update.message.reply_text("Буфер должен быть от 0 до 180 минут.")
        return
    save_calendar_preferences(update.effective_user.id, buffer_minutes=minutes)
    await update.message.reply_text(f"Буфер между встречами сохранён: {minutes} мин")
