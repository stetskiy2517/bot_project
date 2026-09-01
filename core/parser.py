import re
from dateparser import parse

EVENT_KEYWORDS = ["встреча", "создай", "поездка", "звонок", "встречаемся"]
INCOME_KEYWORDS = ["зарплата", "доход"]
EXPENSE_PATTERN = r"(\D+)\s(\d+)"

def parse_message(text):
    text = text.lower()
    if any(word in text for word in EVENT_KEYWORDS):
        date = parse(text, languages=["ru"])
        category = "работа" if "работа" in text else "личное"
        return {"type": "event", "title": text, "start": date, "category": category}

    for word in INCOME_KEYWORDS:
        if word in text:
            amount = int(re.search(r"\d+", text).group())
            return {"type": "income", "category": word, "amount": amount}

    m = re.search(EXPENSE_PATTERN, text)
    if m:
        category, amount = m.groups()
        return {"type": "expense", "category": category.strip(), "amount": int(amount)}

    return {"type": "unknown"}
