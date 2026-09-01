from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def conflict_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔁 Заменить", callback_data="conflict_replace"),
            InlineKeyboardButton("🧩 Объединить", callback_data="conflict_merge"),
        ],
        [
            InlineKeyboardButton("⏰ Предложить другой слот", callback_data="conflict_reschedule")
        ]
    ])
