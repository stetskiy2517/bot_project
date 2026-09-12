from pathlib import Path
import re


def load(path):
    return Path(path).read_text(encoding="utf-8")


def save(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path, old, new):
    text = load(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:100]!r}")
    save(path, text.replace(old, new, 1))


def replace_func(path, name, new_source, *, async_def=False):
    text = load(path)
    prefix = "async def" if async_def else "def"
    pattern = re.compile(
        rf"(?ms)^{prefix} {re.escape(name)}\(.*?(?=^def |^async def |\Z)"
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(f"{path}: expected one function {name}, found {len(matches)}")
    start, end = matches[0].span()
    save(path, text[:start] + new_source.rstrip() + "\n\n\n" + text[end:])


# --- modules/calendar.py -------------------------------------------------
replace_once(
    "modules/calendar.py",
    'CLOCK_TIME_RE = re.compile(\n    r"(?<!\\d)(?:(?:в|к)\\s*)?(?P<hour>[01]?\\d|2[0-3])"\n    r"(?:\\s*(?::|\\.)\\s*(?P<minute>[0-5]\\d)|\\s+(?P<space_minute>[0-5]\\d))"\n    r"(?:\\s*(?:ч|час(?:а|ов)?))?(?!\\d)",\n    re.IGNORECASE,\n)\n',
    'CLOCK_TIME_RE = re.compile(\n    r"(?<!\\d)(?:(?:в|к)\\s*)?(?P<hour>[01]?\\d|2[0-3])"\n    r"(?:\\s*(?::|\\.|-)\\s*(?P<minute>[0-5]\\d)|\\s+(?P<space_minute>[0-5]\\d))"\n    r"(?:\\s*(?:ч|час(?:а|ов)?))?(?!\\d)",\n    re.IGNORECASE,\n)\n',
)
replace_once(
    "modules/calendar.py",
    'RANGE_RE = re.compile(\n    r"\\bс\\s+(\\d{1,2})(?:(?::|\\.|\\s)(\\d{2}))?\\s+до\\s+"\n    r"(\\d{1,2})(?:(?::|\\.|\\s)(\\d{2}))?\\b",\n    re.IGNORECASE,\n)\n',
    'RANGE_RE = re.compile(\n    r"\\bс\\s+(\\d{1,2})(?:(?::|\\.|-|\\s)(\\d{2}))?\\s+до\\s+"\n    r"(\\d{1,2})(?:(?::|\\.|-|\\s)(\\d{2}))?\\b",\n    re.IGNORECASE,\n)\nCOMPACT_RANGE_RE = re.compile(\n    r"(?<!\\d)(\\d{1,2})(?::|\\.|-)([0-5]\\d)\\s*[-–—]\\s*"\n    r"(\\d{1,2})(?::|\\.|-)([0-5]\\d)(?!\\d)",\n    re.IGNORECASE,\n)\nDATE_LIKE_RE = re.compile(r"\\b\\d{1,2}[./-]\\d{1,2}(?:[./-]\\d{2,4})?\\b")\nSPOKEN_CLOCK_RE = re.compile(\n    r"\\b(?:в|к)\\s+(?P<hour>[01]?\\d|2[0-3])\\s*(?:ч|час(?:а|ов)?)\\s+"\n    r"(?P<minute>[0-5]?\\d)\\s*(?:мин|минут\\w*)\\b",\n    re.IGNORECASE,\n)\n',
)
replace_once(
    "modules/calendar.py",
    'HOUR_WORDS = {\n    "один": 1, "час": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5,\n    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,\n    "одиннадцать": 11, "двенадцать": 12,\n}\n',
    'HOUR_WORDS = {\n    "один": 1, "час": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5,\n    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,\n    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,\n    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,\n    "девятнадцать": 19, "двадцать": 20,\n}\n',
)
replace_once(
    "modules/calendar.py",
    'HOUR_WORDS_GENITIVE = {\n    "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4,\n    "пятого": 5, "шестого": 6, "седьмого": 7, "восьмого": 8,\n    "девятого": 9, "десятого": 10, "одиннадцатого": 11, "двенадцатого": 12,\n}\n',
    'HOUR_WORDS_GENITIVE = {\n    "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4,\n    "пятого": 5, "шестого": 6, "седьмого": 7, "восьмого": 8,\n    "девятого": 9, "десятого": 10, "одиннадцатого": 11, "двенадцатого": 12,\n}\nHOUR_WORD_PATTERN = "|".join(sorted((re.escape(word) for word in HOUR_WORDS), key=len, reverse=True))\nWORD_HOUR_RE = re.compile(\n    rf"\\b(?:в|к)\\s+(?P<word>{HOUR_WORD_PATTERN})(?:\\s+(?P<part>утра|дня|вечера|ночи))?\\b",\n    re.IGNORECASE,\n)\nPREFIX_DAYPART_RE = re.compile(\n    rf"\\b(?P<part>утром|днем|вечером|ночью)\\s+(?:в\\s+)?(?P<hour>\\d{{1,2}}|{HOUR_WORD_PATTERN})\\b",\n    re.IGNORECASE,\n)\nCOMMON_TEXT_REPLACEMENTS = {\n    "сегодя": "сегодня",\n    "севодня": "сегодня",\n    "завтро": "завтра",\n    "послезавтро": "послезавтра",\n    "понеделник": "понедельник",\n    "вторниик": "вторник",\n    "четврг": "четверг",\n    "пятнца": "пятница",\n    "пятнцу": "пятницу",\n    "пятнитца": "пятница",\n    "субота": "суббота",\n    "суботу": "субботу",\n    "воскрсенье": "воскресенье",\n}\n',
)
replace_once(
    "modules/calendar.py",
    '    "rest": {"color_id": "10", "keywords": ("отдых", "выходн", "кино", "театр", "ресторан", "кафе", "прогул", "сауна", "баня", "спорт", "трениров", "зал", "друз")},\n',
    '    "rest": {"color_id": "10", "keywords": ("отдых", "отпуск", "выходн", "кино", "театр", "ресторан", "кафе", "прогул", "сауна", "баня", "спорт", "трениров", "зал", "друз")},\n',
)
replace_func(
    "modules/calendar.py",
    "_normalise",
    '''def _normalise(text: str) -> str:\n    normal = text.lower().replace("ё", "е")\n    for wrong, right in COMMON_TEXT_REPLACEMENTS.items():\n        normal = re.sub(rf"\\b{re.escape(wrong)}\\b", right, normal)\n    return normal''',
)
replace_func(
    "modules/calendar.py",
    "_extract_time",
    '''def _extract_time(text: str) -> tuple[int, int] | None:\n    lower = _normalise(_strip_explicit_dates(text))\n\n    range_match = RANGE_RE.search(lower)\n    if range_match:\n        hour, minute = int(range_match.group(1)), int(range_match.group(2) or 0)\n        if 0 <= hour <= 23 and 0 <= minute <= 59:\n            return hour, minute\n\n    compact_range = COMPACT_RANGE_RE.search(lower)\n    if compact_range:\n        hour, minute = int(compact_range.group(1)), int(compact_range.group(2))\n        if 0 <= hour <= 23:\n            return hour, minute\n\n    if re.search(r"\\bполдень\\b", lower):\n        return 12, 0\n    if re.search(r"\\bполночь\\b", lower):\n        return 0, 0\n\n    spoken = SPOKEN_CLOCK_RE.search(lower)\n    if spoken:\n        return int(spoken.group("hour")), int(spoken.group("minute"))\n\n    def apply_part(hour: int, part: str | None) -> int | None:\n        if not part:\n            return hour if 0 <= hour <= 23 else None\n        aliases = {"утром": "утра", "днем": "дня", "вечером": "вечера", "ночью": "ночи"}\n        normalized_part = aliases.get(part, part)\n        if hour > 12:\n            return hour if hour <= 23 else None\n        return _apply_daypart(hour, normalized_part)\n\n    prefix_part = PREFIX_DAYPART_RE.search(lower)\n    if prefix_part:\n        raw = prefix_part.group("hour")\n        hour = int(raw) if raw.isdigit() else HOUR_WORDS.get(raw)\n        if hour is not None:\n            resolved = apply_part(hour, prefix_part.group("part"))\n            if resolved is not None:\n                return resolved, 0\n\n    daypart = DAYPART_HOUR_RE.search(lower)\n    if daypart:\n        hour = _apply_daypart(int(daypart.group("hour")), daypart.group("part"))\n        if hour is not None:\n            return hour, int(daypart.group("minute") or 0)\n\n    half = re.search(\n        r"\\b(?:в\\s+)?(?:половин[аеуы]?|пол)\\s+(\\w+)(?:\\s+(утра|дня|вечера|ночи))?\\b",\n        lower,\n    )\n    if half and half.group(1) in HOUR_WORDS_GENITIVE:\n        base = (HOUR_WORDS_GENITIVE[half.group(1)] - 1) % 12\n        if half.group(2):\n            base = 12 if base == 0 else base\n            base = _apply_daypart(base, half.group(2))\n        if base is not None:\n            return base, 30\n\n    quarter_to = re.search(\n        r"\\bбез\\s+четверти\\s+(\\w+)(?:\\s+(утра|дня|вечера|ночи))?\\b",\n        lower,\n    )\n    if quarter_to:\n        target = HOUR_WORDS.get(quarter_to.group(1)) or HOUR_WORDS_GENITIVE.get(quarter_to.group(1))\n        if target:\n            base = (target - 1) % 12\n            if quarter_to.group(2):\n                base = 12 if base == 0 else base\n                base = _apply_daypart(base, quarter_to.group(2))\n            if base is not None:\n                return base, 45\n\n    quarter_past = re.search(\n        r"\\bчетверть\\s+(\\w+)(?:\\s+(утра|дня|вечера|ночи))?\\b",\n        lower,\n    )\n    if quarter_past and quarter_past.group(1) in HOUR_WORDS_GENITIVE:\n        base = (HOUR_WORDS_GENITIVE[quarter_past.group(1)] - 1) % 12\n        if quarter_past.group(2):\n            base = 12 if base == 0 else base\n            base = _apply_daypart(base, quarter_past.group(2))\n        if base is not None:\n            return base, 15\n\n    word_hour = WORD_HOUR_RE.search(lower)\n    if word_hour:\n        hour = HOUR_WORDS[word_hour.group("word")]\n        resolved = apply_part(hour, word_hour.group("part"))\n        if resolved is not None:\n            return resolved, 0\n\n    matches = list(CLOCK_TIME_RE.finditer(lower))\n    if matches:\n        match = matches[-1]\n        return int(match.group("hour")), int(match.group("minute") or match.group("space_minute") or 0)\n    match = SIMPLE_HOUR_RE.search(lower)\n    return (int(match.group("hour")), 0) if match else None''',
)
replace_func(
    "modules/calendar.py",
    "_relative_offset",
    '''def _relative_offset(text: str) -> timedelta | None:\n    lower = _normalise(text)\n    if re.search(r"\\bчерез\\s+полчаса\\b", lower):\n        return timedelta(minutes=30)\n    if re.search(r"\\bчерез\\s+полтора\\s+часа\\b", lower):\n        return timedelta(minutes=90)\n    if re.search(r"\\bчерез\\s+пару\\s+час\\w*\\b", lower):\n        return timedelta(hours=2)\n\n    word_amounts = {\n        "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,\n        "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,\n    }\n    amount_pattern = "|".join(word_amounts)\n    match = re.search(\n        rf"\\bчерез\\s+(?P<amount>\\d+|{amount_pattern})\\s*"\n        r"(?P<unit>мин(?:ут\\w*)?|ч(?:ас\\w*)?|дн\\w*|день|дня|недел\\w*)\\b",\n        lower,\n    )\n    if not match:\n        return None\n    raw_amount = match.group("amount")\n    amount = int(raw_amount) if raw_amount.isdigit() else word_amounts[raw_amount]\n    unit = match.group("unit")\n    if unit.startswith("мин"):\n        return timedelta(minutes=amount)\n    if unit == "ч" or unit.startswith("час"):\n        return timedelta(hours=amount)\n    if unit.startswith("дн") or unit in {"день", "дня"}:\n        return timedelta(days=amount)\n    if unit.startswith("недел"):\n        return timedelta(weeks=amount)\n    return None''',
)
replace_func(
    "modules/calendar.py",
    "_parse_datetime",
    '''def _parse_datetime(text: str, now: datetime | None = None) -> datetime | None:\n    now = now or datetime.now()\n    lower = _normalise(text)\n\n    # Если пользователь явно написал дату, но такой даты нет, не превращаем ее\n    # молча в ближайшее время. Это опасно для календаря.\n    for match in DATE_LIKE_RE.finditer(lower):\n        token = match.group(0)\n        parts = re.split(r"[./-]", token)\n        day, month = int(parts[0]), int(parts[1])\n        has_year = len(parts) == 3\n        delimiter = next((char for char in token if char in "./-"), ".")\n        prefix = lower[max(0, match.start() - 4):match.start()]\n        looks_like_time = bool(re.search(r"(?:в|к|с|до)\\s+$", prefix)) and not has_year and day <= 23\n        looks_like_date = has_year or day > 23 or delimiter in "/-" or month <= 12\n        if looks_like_date and not looks_like_time and _parse_numeric_date(token, now) is None:\n            return None\n\n    parsed_time = _extract_time(text)\n    relative = _relative_offset(text)\n    if relative and relative < timedelta(days=1) and not parsed_time:\n        return (now + relative).replace(second=0, microsecond=0)\n    if not parsed_time:\n        return None\n    hour, minute = parsed_time\n    base_date = _date_from_text(text, now, hour, minute)\n    if base_date is None:\n        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)\n        return candidate + timedelta(days=1) if candidate <= now else candidate\n    return datetime.combine(base_date, datetime.min.time()).replace(hour=hour, minute=minute)''',
)
replace_func(
    "modules/calendar.py",
    "_extract_duration",
    '''def _extract_duration(text: str) -> timedelta:\n    lower = _normalise(text)\n    if re.search(r"\\bна\\s+полчаса\\b", lower):\n        return timedelta(minutes=30)\n    if re.search(r"\\bна\\s+полтора\\s+часа\\b", lower):\n        return timedelta(minutes=90)\n    if re.search(r"\\bна\\s+час\\b", lower):\n        return timedelta(hours=1)\n    match = re.search(\n        r"\\bна\\s+(\\d+(?:[.,]\\d+)?)\\s*(мин(?:ут\\w*)?|ч(?:ас\\w*)?)\\b",\n        lower,\n    )\n    if match:\n        amount = float(match.group(1).replace(",", "."))\n        unit = match.group(2)\n        if unit.startswith("мин"):\n            return timedelta(minutes=amount)\n        return timedelta(hours=amount)\n    return DEFAULT_EVENT_DURATION''',
)
replace_func(
    "modules/calendar.py",
    "_extract_range_end",
    '''def _extract_range_end(text: str, start: datetime) -> datetime | None:\n    lower = _normalise(_strip_explicit_dates(text))\n    match = RANGE_RE.search(lower)\n    if match:\n        end_hour, end_minute = int(match.group(3)), int(match.group(4) or 0)\n    else:\n        compact = COMPACT_RANGE_RE.search(lower)\n        if not compact:\n            return None\n        end_hour, end_minute = int(compact.group(3)), int(compact.group(4))\n    if not (0 <= end_hour <= 23 and 0 <= end_minute <= 59):\n        return None\n    end = start.replace(hour=end_hour, minute=end_minute)\n    return end + timedelta(days=1) if end <= start else end''',
)
replace_func(
    "modules/calendar.py",
    "_extract_title",
    '''def _extract_title(text: str) -> str:\n    title = NAMED_DATE_RE.sub(" ", NUMERIC_DATE_RE.sub(" ", text.strip()))\n    title = RANGE_RE.sub(" ", title)\n    title = COMPACT_RANGE_RE.sub(" ", title)\n    title = SPOKEN_CLOCK_RE.sub(" ", title)\n    title = PREFIX_DAYPART_RE.sub(" ", title)\n    title = DAYPART_HOUR_RE.sub(" ", title)\n    title = WORD_HOUR_RE.sub(" ", title)\n    title = CLOCK_TIME_RE.sub(" ", title)\n    title = SIMPLE_HOUR_RE.sub(" ", title)\n    title = re.sub(r"\\b(?:в\\s+)?(?:полдень|полночь)\\b", " ", title, flags=re.IGNORECASE)\n    title = re.sub(\n        r"\\b(?:в\\s+)?(?:половин[аеуы]?|пол)\\s+\\w+(?:\\s+(?:утра|дня|вечера|ночи))?\\b",\n        " ", title, flags=re.IGNORECASE,\n    )\n    title = re.sub(\n        r"\\bбез\\s+четверти\\s+\\w+(?:\\s+(?:утра|дня|вечера|ночи))?\\b|"\n        r"\\bчетверть\\s+\\w+(?:\\s+(?:утра|дня|вечера|ночи))?\\b",\n        " ", title, flags=re.IGNORECASE,\n    )\n    title = re.sub(r"\\b(?:сегодня|завтра|завтро|послезавтра|после\\s*завтра|вчера)\\b", " ", title, flags=re.IGNORECASE)\n    title = re.sub(\n        r"\\bчерез\\s+(?:полчаса|полтора\\s+часа|пару\\s+час\\w*|"\n        r"(?:\\d+|один|одну|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять)\\s*"\n        r"(?:мин(?:ут\\w*)?|ч(?:ас\\w*)?|дн\\w*|день|дня|недел\\w*))\\b",\n        " ", title, flags=re.IGNORECASE,\n    )\n    title = re.sub(\n        r"\\b(?:на\\s+)?следующ\\w*\\s+(?:понедельник\\w*|вторник\\w*|сред\\w*|четверг\\w*|пятниц\\w*|суббот\\w*|воскресень\\w*)\\b",\n        " ", title, flags=re.IGNORECASE,\n    )\n    title = re.sub(\n        r"\\b(?:в|во)?\\s*(?:понедельник(?:а)?|вторник(?:а)?|среда|среду|среды|четверг(?:а)?|"\n        r"пятница|пятницу|пятницы|суббота|субботу|субботы|воскресенье|воскресенья)\\b",\n        " ", title, flags=re.IGNORECASE,\n    )\n    title = re.sub(\n        r"\\bна\\s+(?:полчаса|полтора\\s+часа|час|\\d+(?:[.,]\\d+)?\\s*(?:мин(?:ут\\w*)?|ч(?:ас\\w*)?))\\b",\n        " ", title, flags=re.IGNORECASE,\n    )\n    title = re.sub(r"\\s+", " ", title).strip(" ,.-")\n    title = re.sub(r"^(?:(?:пожалуйста|плиз)\\s*,?\\s*)+", "", title, flags=re.IGNORECASE)\n    title = re.sub(r"^(?:можешь|можно|давай)\\s+(?:мне\\s+)?", "", title, flags=re.IGNORECASE)\n    title = re.sub(\n        r"^(?:добавь|добавить|создай|создать|поставь|поставить|запиши|записать|"\n        r"запланируй|запланировать|назначь|назначить|внеси|напомни|напомнить)\\s+",\n        "", title, flags=re.IGNORECASE,\n    )\n    title = re.sub(r"^(?:пожалуйста|плиз)\\s+", "", title, flags=re.IGNORECASE)\n    title = re.sub(r"^(?:в|на)\\s+", "", title, flags=re.IGNORECASE).strip()\n    return title[0].upper() + title[1:] if title else "Встреча"''',
)

# --- modules/router.py ---------------------------------------------------
replace_once(
    "modules/router.py",
    'from modules.calendar_actions import create_from_text, delete_from_text, resume_pending_action, update_from_text\n',
    'from modules.calendar import _extract_time, _relative_offset\nfrom modules.calendar_actions import create_from_text, delete_from_text, resume_pending_action, update_from_text\n',
)
replace_once(
    "modules/router.py",
    '    "записать", "запланируй", "запланировать", "назначь", "назначить", "внеси",\n)\n',
    '    "записать", "запланируй", "запланировать", "назначь", "назначить", "внеси",\n    "напомни", "напомнить",\n)\n',
)
replace_once(
    "modules/router.py",
    'EVENT_WORDS = (\n    "встреч", "созвон", "звонок", "врач", "невролог", "стоматолог", "мрт", "узи",\n    "трениров", "зал", "кино", "ресторан", "рейс", "полет", "полёт", "поезд", "такси", "совещ",\n    "планерк", "клиент", "переговор", "день рождения", "обед", "ужин",\n)\n',
    'EVENT_WORDS = (\n    "встреч", "созвон", "звонок", "врач", "невролог", "стоматолог", "мрт", "узи",\n    "трениров", "зал", "кино", "ресторан", "рейс", "полет", "полёт", "поезд", "такси", "совещ",\n    "планерк", "клиент", "переговор", "день рождения", "обед", "ужин",\n)\nACTION_WORDS = (\n    "забрат", "отвез", "купит", "куплю", "оплат", "заех", "позвон", "сход", "поех",\n    "получ", "отправ", "подготов", "сдат", "заказ", "заброниров", "встрет", "записат",\n)\nNEGATED_CREATE_RE = re.compile(\n    r"\\bне\\s+(?:создавай|создай|добавляй|добавь|ставь|поставь|записывай|запиши|"\n    r"планируй|запланируй|назначай|назначь|вноси|внеси)\\b",\n    re.IGNORECASE,\n)\n',
)
replace_func(
    "modules/router.py",
    "_normalise",
    '''def _normalise(text: str) -> str:\n    normal = text.lower().replace("ё", "е").strip()\n    replacements = {\n        "сегодя": "сегодня", "севодня": "сегодня", "завтро": "завтра",\n        "послезавтро": "послезавтра", "понеделник": "понедельник",\n        "вторниик": "вторник", "четврг": "четверг", "пятнца": "пятница",\n        "пятнцу": "пятницу", "пятнитца": "пятница", "субота": "суббота",\n        "суботу": "субботу", "воскрсенье": "воскресенье",\n    }\n    for wrong, right in replacements.items():\n        normal = re.sub(rf"\\b{re.escape(wrong)}\\b", right, normal)\n    return normal''',
)
replace_func(
    "modules/router.py",
    "detect_intent",
    '''def detect_intent(text: str) -> IntentResult:\n    lower = _normalise(text)\n    if NEGATED_CREATE_RE.search(lower):\n        return IntentResult(INTENT_UNKNOWN, 0.0)\n    if any(word in lower for word in DELETE_WORDS):\n        return IntentResult(INTENT_DELETE, 0.98)\n    if any(word in lower for word in UPDATE_WORDS):\n        return IntentResult(INTENT_UPDATE, 0.98)\n    if any(word in lower for word in FREE_WORDS):\n        return IntentResult(INTENT_FREE, 0.96)\n    if any(word in lower for word in SEARCH_WORDS):\n        return IntentResult(INTENT_SEARCH, 0.97)\n    if WHEN_SEARCH_RE.search(lower):\n        return IntentResult(INTENT_SEARCH, 0.93)\n    if any(word in lower for word in VIEW_WORDS):\n        return IntentResult(INTENT_VIEW, 0.96)\n    if any(word in lower for word in CREATE_WORDS):\n        return IntentResult(INTENT_CREATE, 0.99)\n\n    has_event = any(word in lower for word in EVENT_WORDS)\n    has_action = any(word in lower for word in ACTION_WORDS)\n    has_date = bool(DATE_HINT_RE.search(lower))\n    has_time = _extract_time(lower) is not None or _relative_offset(lower) is not None\n    is_question = bool(QUESTION_PREFIX_RE.search(lower)) or text.rstrip().endswith("?")\n    is_current_state = bool(CURRENT_STATE_RE.search(lower))\n\n    if has_date and has_time and not is_question and not is_current_state:\n        return IntentResult(INTENT_CREATE, 0.92)\n    if has_event and (has_date or has_time) and not is_question and not is_current_state:\n        return IntentResult(INTENT_CREATE, 0.86)\n    if has_action and (has_date or has_time) and not is_question and not is_current_state:\n        return IntentResult(INTENT_CREATE, 0.84)\n    return IntentResult(INTENT_UNKNOWN, 0.0)''',
)
replace_func(
    "modules/router.py",
    "_needs_time",
    '''def _needs_time(text: str) -> bool:\n    if is_all_day(text):\n        return False\n    lower = _normalise(text)\n    return bool(DATE_HINT_RE.search(lower)) and _extract_time(lower) is None''',
)
replace_func(
    "modules/router.py",
    "_resume_pending",
    '''async def _resume_pending(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:\n    pending = _pending(context)\n    if not pending:\n        return False\n    if pending.get("type") != "create_time":\n        return await resume_pending_action(update, context, text, pending)\n    if _normalise(text) in {"отмена", "отменить", "не надо", "нет"}:\n        _clear_pending(context)\n        await update.message.reply_text("Хорошо, не создаю событие.")\n        return True\n\n    reply = text.strip()\n    if _extract_time(reply) is None and _extract_time(f"в {reply}") is not None:\n        reply = f"в {reply}"\n    combined = f"{pending['text']} {reply}"\n    _clear_pending(context)\n    handled = await create_from_text(update, context, combined)\n    if not handled:\n        context.user_data["smart_planner_pending"] = pending\n        await update.message.reply_text("Не понял время. Напиши, например: 19:00, 19 или в 7 вечера.")\n    return True''',
    async_def=True,
)

# --- modules/calendar_event_features.py ----------------------------------
replace_once(
    "modules/calendar_event_features.py",
    'REMINDER_RE = re.compile(\n    r"\\bза\\s+(?:(\\d+)\\s*(минут\\w*|час\\w*|дн\\w*)|(полчаса)|(час)|(день))\\b",\n    re.IGNORECASE,\n)\n',
    'REMINDER_RE = re.compile(\n    r"\\bза\\s+(?:(?P<amount>\\d+)\\s*(?P<unit>мин(?:ут\\w*)?|ч(?:ас\\w*)?|дн\\w*)|"\n    r"(?P<half>полчаса)|(?P<hour>час)|(?P<day>день)|(?P<day_alias>сутки|суток))\\b",\n    re.IGNORECASE,\n)\n',
)
replace_func(
    "modules/calendar_event_features.py",
    "_reminder_minutes",
    '''def _reminder_minutes(text: str) -> list[int]:\n    values: list[int] = []\n    for match in REMINDER_RE.finditer(text):\n        if match.group("half"):\n            minutes = 30\n        elif match.group("hour"):\n            minutes = 60\n        elif match.group("day") or match.group("day_alias"):\n            minutes = 1440\n        else:\n            amount = int(match.group("amount"))\n            unit = match.group("unit").lower()\n            if unit.startswith("мин"):\n                minutes = amount\n            elif unit == "ч" or unit.startswith("час"):\n                minutes = amount * 60\n            else:\n                minutes = amount * 1440\n        if 0 <= minutes <= 40320 and minutes not in values:\n            values.append(minutes)\n    return sorted(values, reverse=True)[:5]''',
)
replace_func(
    "modules/calendar_event_features.py",
    "_recurrence_rule",
    '''def _recurrence_rule(text: str) -> str | None:\n    lower = text.lower().replace("ё", "е")\n    if re.search(r"\\bкажд(?:ый|ую|ое)\\s+день\\b|\\bежедневно\\b", lower):\n        return "RRULE:FREQ=DAILY"\n\n    interval = re.search(r"\\bкажд\\w*\\s+(\\d+)\\s+недел\\w*\\b", lower)\n    if interval:\n        rule = f"RRULE:FREQ=WEEKLY;INTERVAL={int(interval.group(1))}"\n        for word, code in WEEKDAY_BY_RE.items():\n            if re.search(rf"\\b(?:в\\s+)?{word}\\b", lower):\n                return rule + f";BYDAY={code}"\n        return rule\n\n    for word, code in WEEKDAY_BY_RE.items():\n        if re.search(rf"\\bкажд\\w*\\s+{word}\\b", lower):\n            return f"RRULE:FREQ=WEEKLY;BYDAY={code}"\n        if re.search(rf"\\bпо\\s+{word}\\b", lower):\n            return f"RRULE:FREQ=WEEKLY;BYDAY={code}"\n\n    if re.search(r"\\bкажд(?:ую|ой)\\s+недел\\w*\\b|\\bеженедельно\\b|\\bраз\\s+в\\s+недел\\w*\\b", lower):\n        return "RRULE:FREQ=WEEKLY"\n    if re.search(r"\\bкажд(?:ый|ого)\\s+месяц\\w*\\b|\\bежемесячно\\b", lower):\n        return "RRULE:FREQ=MONTHLY"\n    return None''',
)
replace_once(
    "modules/calendar_event_features.py",
    '    cleaned = re.sub(r"\\bкажд\\w*\\s+(?:день|недел\\w*|месяц\\w*|понедельник\\w*|вторник\\w*|сред\\w*|четверг\\w*|пятниц\\w*|суббот\\w*|воскресень\\w*)\\b", " ", cleaned, flags=re.IGNORECASE)\n    cleaned = re.sub(r"\\b(?:ежедневно|еженедельно|ежемесячно)\\b", " ", cleaned, flags=re.IGNORECASE)\n',
    '    cleaned = re.sub(r"\\bкажд\\w*\\s+\\d+\\s+недел\\w*\\b", " ", cleaned, flags=re.IGNORECASE)\n    cleaned = re.sub(r"\\bкажд\\w*\\s+(?:день|недел\\w*|месяц\\w*|понедельник\\w*|вторник\\w*|сред\\w*|четверг\\w*|пятниц\\w*|суббот\\w*|воскресень\\w*)\\b", " ", cleaned, flags=re.IGNORECASE)\n    cleaned = re.sub(r"\\bпо\\s+(?:понедельник\\w*|вторник\\w*|сред\\w*|четверг\\w*|пятниц\\w*|суббот\\w*|воскресень\\w*)\\b", " ", cleaned, flags=re.IGNORECASE)\n    cleaned = re.sub(r"\\bраз\\s+в\\s+недел\\w*\\b", " ", cleaned, flags=re.IGNORECASE)\n    cleaned = re.sub(r"\\b(?:ежедневно|еженедельно|ежемесячно)\\b", " ", cleaned, flags=re.IGNORECASE)\n',
)

# --- modules/calendar_actions.py -----------------------------------------
replace_once(
    "modules/calendar_actions.py",
    '        timing = _parse_event_timing(text)\n        if not timing:\n            return False\n        start_naive, end_naive = timing\n        zone = _user_zone(timezone)\n',
    '        zone = _user_zone(timezone)\n        local_now = datetime.now(zone).replace(tzinfo=None)\n        timing = _parse_event_timing(text, local_now)\n        if not timing:\n            return False\n        start_naive, end_naive = timing\n',
)
