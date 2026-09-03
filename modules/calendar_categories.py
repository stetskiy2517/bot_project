"""Calendar event categorization with per-user Google Calendar colors."""
from __future__ import annotations

import re

from core.db import DEFAULT_CATEGORY_COLORS, get_category_colors

CATEGORY_LABELS = {
    "work": "Работа",
    "health": "Здоровье",
    "rest": "Отдых",
    "travel": "Поездки",
    "personal": "Личное",
    "other": "Другое",
}

CATEGORY_KEYWORDS = {
    "work": ("работ", "встреч", "созвон", "совещ", "клиент", "офис", "проект", "презентац", "отчет", "отчёт", "коммерчес", "переговор", "планерк", "защит"),
    "health": ("врач", "доктор", "невролог", "стоматолог", "клиник", "больниц", "анализ", "мрт", "узи", "массаж", "физиотерап", "здоров", "лекар"),
    "rest": ("отдых", "выходн", "кино", "театр", "ресторан", "кафе", "прогул", "сауна", "баня", "спорт", "трениров", "зал", "друз"),
    "travel": ("самолет", "самолёт", "рейс", "полет", "полёт", "поезд", "вокзал", "аэропорт", "дорог", "такси", "перелет", "перелёт", "командиров", "отъезд", "прилет", "прилёт", "саратов"),
    "personal": ("личн", "дом", "покуп", "магазин", "семья", "родител", "ребен", "ребён", "день рождения", "забрать", "отвезти"),
}


def detect_category(text: str) -> str:
    lower = text.lower().replace("ё", "е")
    if re.search(r"\bсемь(?:я|и|е|ю|ей|ям|ями|ях)\b", lower):
        return "personal"
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword.replace("ё", "е") in lower for keyword in keywords):
            return category
    return "other"


def apply_user_category(event: dict, text: str, user_id: int) -> dict:
    """Set category metadata and the user's chosen Google Calendar color."""
    category = detect_category(text)
    colors = get_category_colors(user_id)
    event["colorId"] = colors.get(category, DEFAULT_CATEGORY_COLORS["other"])
    description = str(event.get("description") or "")
    marker = f"AI Smart Planner category: {category}"
    if "AI Smart Planner category:" in description:
        description = re.sub(r"AI Smart Planner category:\s*\w+", marker, description)
    else:
        description = (description + "\n" + marker).strip()
    event["description"] = description
    return event
