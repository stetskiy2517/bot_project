import os
import json
import re
from datetime import datetime
from typing import Dict, List

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

DEFAULT_CATEGORIES = {
    "еда": ["еда", "кафе", "ресторан", "кофе", "обед", "ужин"],
    "транспорт": ["такси", "метро", "бензин", "бенз", "ТО", "заправка", "транспорт"],
    "жилье": ["аренда", "квартира", "коммуналка"],
    "здоровье": ["аптека", "врач", "лекарства"],
    "развлечения": ["кино", "театр", "игры"],
    "прочее": [],
}


# ---------- STORAGE ----------

def _file_path(user_id: int) -> str:
    return os.path.join(DATA_DIR, f"finance_{user_id}.json")


def load_data(user_id: int) -> Dict:
    path = _file_path(user_id)
    if not os.path.exists(path):
        return {"balance": 0, "transactions": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(user_id: int, data: Dict):
    with open(_file_path(user_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- CATEGORY ----------

def detect_category(text: str) -> str:
    text = text.lower()
    for category, keywords in DEFAULT_CATEGORIES.items():
        if any(k in text for k in keywords):
            return category
    return "прочее"


# ---------- PARSER ----------

import re

FINANCE_KEYWORDS = [
    # еда
    "еда", "продукты", "кафе", "ресторан", "обед", "ужин", "сигареты",
    # транспорт
    "бензин", "дизель", "такси", "метро", "автобус", "проезд", "Бенз", "сто", "то", "страховка",
    # быт
    "аренда", "квартира", "жкх", "свет", "вода", "интернет",
    # покупки
    "покупка", "купил", "заказ", "маркет", "магазин",
    # явные финансы
    "₽", "руб", "рублей", "р.", "р ",
    "потратил", "потратила", "заплатил", "заплатила",
    "доход", "зарплата", "аванс", "получил", "получила"
]

INCOME_KEYWORDS = [
    "доход", "зарплата", "аванс", "получил", "получила",
    "премия", "вознаграждение"
]


def parse_finance(text: str):
    text = text.lower().strip()

    # ---------- команды ----------
    if text in ("баланс", "💰 баланс"):
        return "balance", None, None, None

    if text in ("расходы по категориям", "📊 расходы по категориям"):
        return "summary", None, None, None

    # ---------- сумма ----------
    amount_match = re.search(r"\b(\d{2,7})\b", text)
    if not amount_match:
        return None

    amount = int(amount_match.group(1))

    # ---------- финансовый контекст ----------
    if not any(k in text for k in FINANCE_KEYWORDS):
        return None  # ⬅ теперь это умный фильтр

    category = detect_category(text)
    comment = text

    # ---------- доход или расход ----------
    if any(k in text for k in INCOME_KEYWORDS):
        return "income", amount, category, comment

    return "expense", amount, category, comment



# ---------- CORE LOGIC ----------

def add_transaction(user_id: int, t_type: str, amount: int, category: str, comment: str):
    data = load_data(user_id)

    if t_type == "income":
        data["balance"] += amount
    else:
        data["balance"] -= amount

    data["transactions"].append({
        "type": t_type,
        "amount": amount,
        "category": category,
        "comment": comment,
        "date": datetime.now().isoformat()
    })

    save_data(user_id, data)


def get_balance(user_id: int) -> int:
    return load_data(user_id)["balance"]


def get_summary_by_category(user_id: int) -> Dict[str, int]:
    data = load_data(user_id)
    summary: Dict[str, int] = {}

    for t in data["transactions"]:
        if t["type"] != "expense":
            continue
        summary[t["category"]] = summary.get(t["category"], 0) + t["amount"]

    return summary
