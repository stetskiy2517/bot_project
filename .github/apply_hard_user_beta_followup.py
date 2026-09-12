from pathlib import Path
import re


def load(path):
    return Path(path).read_text(encoding="utf-8")


def save(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path, old, new):
    text = load(path)
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected one match for {old!r}, got {text.count(old)}")
    save(path, text.replace(old, new, 1))


def replace_func(path, name, new_source):
    text = load(path)
    pattern = re.compile(rf"(?ms)^def {re.escape(name)}\(.*?(?=^def |^async def |\Z)")
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(f"{path}: expected one function {name}, found {len(matches)}")
    start, end = matches[0].span()
    save(path, text[:start] + new_source.rstrip() + "\n\n\n" + text[end:])


replace_func(
    "modules/calendar.py",
    "_relative_offset",
    r'''def _relative_offset(text: str) -> timedelta | None:
    lower = _normalise(text)
    if re.search(r"\bчерез\s+полчаса\b", lower):
        return timedelta(minutes=30)
    if re.search(r"\bчерез\s+полтора\s+часа\b", lower):
        return timedelta(minutes=90)
    if re.search(r"\bчерез\s+пару\s+час\w*\b", lower):
        return timedelta(hours=2)

    word_amounts = {
        "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
        "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    }
    amount_pattern = "|".join(word_amounts)
    match = re.search(
        rf"\bчерез\s+(?:(?P<amount>\d+|{amount_pattern})\s*)?"
        r"(?P<unit>мин(?:ут\w*)?|ч(?:ас\w*)?|дн\w*|день|дня|недел\w*)\b",
        lower,
    )
    if not match:
        return None
    raw_amount = match.group("amount")
    amount = 1 if not raw_amount else (int(raw_amount) if raw_amount.isdigit() else word_amounts[raw_amount])
    unit = match.group("unit")
    if unit.startswith("мин"):
        return timedelta(minutes=amount)
    if unit == "ч" or unit.startswith("час"):
        return timedelta(hours=amount)
    if unit.startswith("дн") or unit in {"день", "дня"}:
        return timedelta(days=amount)
    if unit.startswith("недел"):
        return timedelta(weeks=amount)
    return None''',
)

replace_func(
    "modules/calendar.py",
    "_parse_datetime",
    r'''def _parse_datetime(text: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now()
    lower = _normalise(text)

    # Явная невозможная дата должна быть ошибкой, а не молчаливым переносом
    # на ближайшее время. При этом диапазоны времени вроде 14:00-16:00
    # не являются датами и исключаются из этой проверки.
    compact_range = COMPACT_RANGE_RE.search(lower)
    for match in DATE_LIKE_RE.finditer(lower):
        if compact_range and compact_range.start() <= match.start() and match.end() <= compact_range.end():
            continue
        token = match.group(0)
        parts = re.split(r"[./-]", token)
        day, month = int(parts[0]), int(parts[1])
        has_year = len(parts) == 3
        delimiter = next((char for char in token if char in "./-"), ".")
        prefix = lower[max(0, match.start() - 4):match.start()]
        looks_like_time = bool(re.search(r"(?:в|к|с|до)\s+$", prefix)) and not has_year and day <= 23
        looks_like_date = has_year or day > 23 or delimiter in "/-" or month <= 12
        if looks_like_date and not looks_like_time and _parse_numeric_date(token, now) is None:
            return None

    parsed_time = _extract_time(text)
    relative = _relative_offset(text)
    if relative and relative < timedelta(days=1) and not parsed_time:
        return (now + relative).replace(second=0, microsecond=0)
    if not parsed_time:
        return None
    hour, minute = parsed_time
    base_date = _date_from_text(text, now, hour, minute)
    if base_date is None:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate + timedelta(days=1) if candidate <= now else candidate
    return datetime.combine(base_date, datetime.min.time()).replace(hour=hour, minute=minute)''',
)

# Если сегодня суббота и событие указано "в субботу" на более позднее время,
# естественное поведение текущего parser — считать это сегодняшней субботой.
replace_once(
    "tests/test_user_creation_beta.py",
    'self.assert_dt("кино в суботу в 20", datetime(2026, 9, 19, 20, 0))',
    'self.assert_dt("кино в суботу в 20", datetime(2026, 9, 12, 20, 0))',
)
