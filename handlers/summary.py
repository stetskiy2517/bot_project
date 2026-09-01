import re

EVENT_TYPES = {
    "встреча": ["встреч", "переговор", "обсужд"],
    "созвон": ["звон", "созвон", "колл", "call"],
    "поездка": ["поезд", "ехать", "дорог", "выех"],
    "оплата": ["оплат", "платеж", "счет", "перевод"],
    "тренировка": ["тренир", "спорт", "зал", "бег"],
    "врач": ["врач", "клиник", "прием", "анализ"],
    "обучение": ["курс", "обуч", "урок", "лекци"],
    "встреча_друзья": ["друг", "друзья"],
    "семья": ["дет", "семь", "сын", "дочь", "жена"],
}

STOP_WORDS = [
    "мне", "надо", "нужно", "пожалуйста", "давай",
    "напомни", "завтра", "сегодня", "вечером",
    "утром", "днем", "в", "на", "по"
]


def detect_event_type(text: str) -> str:
    text = text.lower()
    for event_type, keywords in EVENT_TYPES.items():
        for kw in keywords:
            if kw in text:
                return event_type.replace("_", " ").title()
    return "Дело"


def extract_object(text: str) -> str:
    text = text.lower()

    # Убираем мусор
    for word in STOP_WORDS:
        text = re.sub(rf"\b{word}\b", "", text)

    # Убираем время и даты
    text = re.sub(r"\b\d{1,2}[:.]\d{2}\b", "", text)
    text = re.sub(r"\b\d{1,2}\b", "", text)

    words = [w.strip() for w in text.split() if len(w) > 2]

    if not words:
        return "Не уточнено"

    return " ".join(words[:3]).title()


def build_summary(original_text: str) -> str:
    event_type = detect_event_type(original_text)
    obj = extract_object(original_text)

    return f"{event_type} · {obj}"
