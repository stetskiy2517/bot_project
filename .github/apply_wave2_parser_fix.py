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
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:120]!r}")
    save(path, text.replace(old, new, 1))


def replace_func(path, name, new_source, *, async_def=False):
    text = load(path)
    prefix = "async def" if async_def else "def"
    pattern = re.compile(rf"(?ms)^{prefix} {re.escape(name)}\(.*?(?=^def |^async def |\Z)")
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(f"{path}: expected one function {name}, found {len(matches)}")
    start, end = matches[0].span()
    save(path, text[:start] + new_source.rstrip() + "\n\n\n" + text[end:])


# modules/calendar.py
replace_once(
    "modules/calendar.py",
    'SPOKEN_CLOCK_RE = re.compile(\n    r"\\b(?:в|к)\\s+(?P<hour>[01]?\\d|2[0-3])\\s*(?:ч|час(?:а|ов)?)\\s+"\n    r"(?P<minute>[0-5]?\\d)\\s*(?:мин|минут\\w*)\\b",\n    re.IGNORECASE,\n)\n',
    'SPOKEN_CLOCK_RE = re.compile(\n    r"\\b(?:в|к)\\s+(?P<hour>[01]?\\d|2[0-3])\\s*(?:ч|час(?:а|ов)?)\\s+"\n    r"(?P<minute>[0-5]?\\d)\\s*(?:мин|минут\\w*)\\b",\n    re.IGNORECASE,\n)\nEXPLICIT_CLOCK_TOKEN_RE = re.compile(\n    r"\\b(?:в|к)\\s*(?P<hour>\\d{1,2})(?::|\\.|-)(?P<minute>\\d{2})\\b",\n    re.IGNORECASE,\n)\n',
)
replace_once(
    "modules/calendar.py",
    '    "девятнадцать": 19, "двадцать": 20,\n}\n',
    '    "девятнадцать": 19, "двадцать": 20,\n    "двадцать один": 21, "двадцать два": 22, "двадцать три": 23,\n}\n',
)
replace_once(
    "modules/calendar.py",
    'HOUR_WORD_PATTERN = "|".join(sorted((re.escape(word) for word in HOUR_WORDS), key=len, reverse=True))\nWORD_HOUR_RE = re.compile(\n',
    'HOUR_WORD_PATTERN = "|".join(sorted((re.escape(word) for word in HOUR_WORDS), key=len, reverse=True))\nMINUTE_WORDS = {"пятнадцать": 15, "тридцать": 30, "сорок пять": 45}\nMINUTE_WORD_PATTERN = "|".join(sorted((re.escape(word) for word in MINUTE_WORDS), key=len, reverse=True))\nWORD_CLOCK_RE = re.compile(\n    rf"\\b(?:в|к)\\s+(?P<hour>{HOUR_WORD_PATTERN})\\s+(?P<minute>{MINUTE_WORD_PATTERN})\\b",\n    re.IGNORECASE,\n)\nWORD_HOUR_RE = re.compile(\n',
)
replace_once(
    "modules/calendar.py",
    '    "воскрсенье": "воскресенье",\n}\n',
    '    "воскрсенье": "воскресенье",\n    "сентебря": "сентября",\n    "встеча": "встреча",\n    "втреча": "встреча",\n    "созовон": "созвон",\n}\n',
)
replace_func(
    "modules/calendar.py",
    "_extract_time",
    r'''def _extract_time(text: str) -> tuple[int, int] | None:
    lower = _normalise(_strip_explicit_dates(text))

    explicit_clock = EXPLICIT_CLOCK_TOKEN_RE.search(lower)
    if explicit_clock:
        hour = int(explicit_clock.group("hour"))
        minute = int(explicit_clock.group("minute"))
        if hour > 23 or minute > 59:
            return None

    range_match = RANGE_RE.search(lower)
    if range_match:
        hour, minute = int(range_match.group(1)), int(range_match.group(2) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute

    compact_range = COMPACT_RANGE_RE.search(lower)
    if compact_range:
        hour, minute = int(compact_range.group(1)), int(compact_range.group(2))
        if 0 <= hour <= 23:
            return hour, minute

    if re.search(r"\bполдень\b", lower):
        return 12, 0
    if re.search(r"\bполночь\b", lower):
        return 0, 0

    spoken = SPOKEN_CLOCK_RE.search(lower)
    if spoken:
        return int(spoken.group("hour")), int(spoken.group("minute"))

    def apply_part(hour: int, part: str | None) -> int | None:
        if not part:
            return hour if 0 <= hour <= 23 else None
        aliases = {"утром": "утра", "днем": "дня", "вечером": "вечера", "ночью": "ночи"}
        normalized_part = aliases.get(part, part)
        if hour > 12:
            return hour if hour <= 23 else None
        return _apply_daypart(hour, normalized_part)

    prefix_part = PREFIX_DAYPART_RE.search(lower)
    if prefix_part:
        raw = prefix_part.group("hour")
        hour = int(raw) if raw.isdigit() else HOUR_WORDS.get(raw)
        if hour is not None:
            resolved = apply_part(hour, prefix_part.group("part"))
            if resolved is not None:
                return resolved, 0

    daypart = DAYPART_HOUR_RE.search(lower)
    if daypart:
        hour = _apply_daypart(int(daypart.group("hour")), daypart.group("part"))
        if hour is not None:
            return hour, int(daypart.group("minute") or 0)

    half = re.search(
        r"\b(?:в\s+)?(?:половин[аеуы]?|пол)\s+(\w+)(?:\s+(утра|дня|вечера|ночи))?\b",
        lower,
    )
    if half and half.group(1) in HOUR_WORDS_GENITIVE:
        base = (HOUR_WORDS_GENITIVE[half.group(1)] - 1) % 12
        if half.group(2):
            base = 12 if base == 0 else base
            base = _apply_daypart(base, half.group(2))
        if base is not None:
            return base, 30

    quarter_to = re.search(
        r"\bбез\s+четверти\s+(\w+)(?:\s+(утра|дня|вечера|ночи))?\b",
        lower,
    )
    if quarter_to:
        target = HOUR_WORDS.get(quarter_to.group(1)) or HOUR_WORDS_GENITIVE.get(quarter_to.group(1))
        if target:
            base = (target - 1) % 12
            if quarter_to.group(2):
                base = 12 if base == 0 else base
                base = _apply_daypart(base, quarter_to.group(2))
            if base is not None:
                return base, 45

    quarter_past = re.search(
        r"\bчетверть\s+(\w+)(?:\s+(утра|дня|вечера|ночи))?\b",
        lower,
    )
    if quarter_past and quarter_past.group(1) in HOUR_WORDS_GENITIVE:
        base = (HOUR_WORDS_GENITIVE[quarter_past.group(1)] - 1) % 12
        if quarter_past.group(2):
            base = 12 if base == 0 else base
            base = _apply_daypart(base, quarter_past.group(2))
        if base is not None:
            return base, 15

    word_clock = WORD_CLOCK_RE.search(lower)
    if word_clock:
        return HOUR_WORDS[word_clock.group("hour")], MINUTE_WORDS[word_clock.group("minute")]

    word_hour = WORD_HOUR_RE.search(lower)
    if word_hour:
        hour = HOUR_WORDS[word_hour.group("word")]
        resolved = apply_part(hour, word_hour.group("part"))
        if resolved is not None:
            return resolved, 0

    matches = list(CLOCK_TIME_RE.finditer(lower))
    if matches:
        match = matches[-1]
        return int(match.group("hour")), int(match.group("minute") or match.group("space_minute") or 0)
    match = SIMPLE_HOUR_RE.search(lower)
    return (int(match.group("hour")), 0) if match else None''',
)
replace_func(
    "modules/calendar.py",
    "_parse_datetime",
    r'''def _parse_datetime(text: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now()
    lower = _normalise(text)

    explicit_clock = EXPLICIT_CLOCK_TOKEN_RE.search(lower)
    if explicit_clock:
        hour_value = int(explicit_clock.group("hour"))
        minute_value = int(explicit_clock.group("minute"))
        if hour_value > 23 or minute_value > 59:
            return None

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
    if relative and not parsed_time:
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
replace_func(
    "modules/calendar.py",
    "_extract_range_end",
    r'''def _extract_range_end(text: str, start: datetime) -> datetime | None:
    lower = _normalise(_strip_explicit_dates(text))
    match = RANGE_RE.search(lower)
    if match:
        end_hour, end_minute = int(match.group(3)), int(match.group(4) or 0)
    else:
        compact = COMPACT_RANGE_RE.search(lower)
        if not compact:
            return None
        end_hour, end_minute = int(compact.group(3)), int(compact.group(4))
    if not (0 <= end_hour <= 23 and 0 <= end_minute <= 59):
        return None
    end = start.replace(hour=end_hour, minute=end_minute)
    if end > start:
        return end
    if start.hour >= 20 and end_hour <= 6:
        return end + timedelta(days=1)
    return None''',
)
replace_func(
    "modules/calendar.py",
    "_parse_event_timing",
    r'''def _parse_event_timing(text: str, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    start = _parse_datetime(text, now)
    if not start:
        return None
    lower = _normalise(_strip_explicit_dates(text))
    has_range = bool(RANGE_RE.search(lower) or COMPACT_RANGE_RE.search(lower))
    range_end = _extract_range_end(text, start)
    if has_range and not range_end:
        return None
    return start, (range_end or start + _extract_duration(text))''',
)
replace_func(
    "modules/calendar.py",
    "_extract_title",
    r'''def _extract_title(text: str) -> str:
    title = NAMED_DATE_RE.sub(" ", NUMERIC_DATE_RE.sub(" ", text.strip()))
    title = RANGE_RE.sub(" ", title)
    title = COMPACT_RANGE_RE.sub(" ", title)
    title = SPOKEN_CLOCK_RE.sub(" ", title)
    title = PREFIX_DAYPART_RE.sub(" ", title)
    title = DAYPART_HOUR_RE.sub(" ", title)
    title = WORD_CLOCK_RE.sub(" ", title)
    title = WORD_HOUR_RE.sub(" ", title)
    title = CLOCK_TIME_RE.sub(" ", title)
    title = SIMPLE_HOUR_RE.sub(" ", title)
    title = re.sub(r"\b(?:в\s+)?(?:полдень|полночь)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(
        r"\b(?:в\s+)?(?:половин[аеуы]?|пол)\s+\w+(?:\s+(?:утра|дня|вечера|ночи))?\b",
        " ", title, flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\bбез\s+четверти\s+\w+(?:\s+(?:утра|дня|вечера|ночи))?\b|"
        r"\bчетверть\s+\w+(?:\s+(?:утра|дня|вечера|ночи))?\b",
        " ", title, flags=re.IGNORECASE,
    )
    title = re.sub(r"\b(?:сегодня|завтра|завтро|послезавтра|после\s*завтра|вчера)\b", " ", title, flags=re.IGNORECASE)
    title = re.sub(
        r"\bчерез\s+(?:полчаса|полтора\s+часа|пару\s+час\w*|"
        r"(?:\d+|один|одну|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять)?\s*"
        r"(?:мин(?:ут\w*)?|ч(?:ас\w*)?|дн\w*|день|дня|недел\w*))\b",
        " ", title, flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\b(?:на\s+)?следующ\w*\s+(?:понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)\b",
        " ", title, flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\b(?:в|во)?\s*(?:понедельник(?:а)?|вторник(?:а)?|среда|среду|среды|четверг(?:а)?|"
        r"пятница|пятницу|пятницы|суббота|субботу|субботы|воскресенье|воскресенья)\b",
        " ", title, flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\bна\s+(?:полчаса|полтора\s+часа|час|\d+(?:[.,]\d+)?\s*(?:мин(?:ут\w*)?|ч(?:ас\w*)?))\b",
        " ", title, flags=re.IGNORECASE,
    )
    title = re.sub(r"\s+", " ", title).strip(" ,.-")
    title = re.sub(r"^(?:(?:пожалуйста|плиз)\s*,?\s*)+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:не\s+забудь(?:те)?\s+)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:можешь|можно|давай)\s+(?:мне\s+)?", "", title, flags=re.IGNORECASE)
    title = re.sub(
        r"^(?:добавь|добавить|создай|создать|поставь|поставить|запиши|записать|"
        r"запланируй|запланировать|назначь|назначить|внеси|напомни|напомнить)\s+",
        "", title, flags=re.IGNORECASE,
    )
    title = re.sub(r"^(?:пожалуйста|плиз)\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:в|на)\s+", "", title, flags=re.IGNORECASE).strip()
    return title[0].upper() + title[1:] if title else "Встреча"''',
)

# modules/router.py
replace_once(
    "modules/router.py",
    '    "получ", "отправ", "подготов", "сдат", "заказ", "заброниров", "встрет", "записат",\n)\n',
    '    "получ", "отправ", "подготов", "сдат", "заказ", "заброниров", "встрет", "записат",\n    "сдела", "провер", "законч",\n)\n',
)
replace_once(
    "modules/router.py",
    'NEGATED_CREATE_RE = re.compile(\n',
    'INFO_CREATE_QUESTION_RE = re.compile(\n    r"^\\s*(?:можно\\s+ли|как\\b|умеешь\\s+ли(?:\\s+ты)?|можешь\\s+ли(?:\\s+ты)?)",\n    re.IGNORECASE,\n)\nNEGATED_CREATE_RE = re.compile(\n',
)
replace_once(
    "modules/router.py",
    '    "планерк", "клиент", "переговор", "день рождения", "обед", "ужин",\n)\n',
    '    "планерк", "клиент", "переговор", "день рождения", "обед", "ужин",\n    "отпуск", "командиров",\n)\n',
)
replace_once(
    "modules/router.py",
    '    r"\\d{1,2}\\s+(?:январ\\w*|феврал\\w*|март\\w*|апрел\\w*|ма[йя]|июн\\w*|июл\\w*|"\n',
    '    r"следующ\\w*\\s+недел\\w*|\\d{1,2}\\s+(?:январ\\w*|феврал\\w*|март\\w*|апрел\\w*|ма[йя]|июн\\w*|июл\\w*|"\n',
)
replace_once(
    "modules/router.py",
    '        "суботу": "субботу", "воскрсенье": "воскресенье",\n',
    '        "суботу": "субботу", "воскрсенье": "воскресенье", "сентебря": "сентября",\n        "встеча": "встреча", "втреча": "встреча", "созовон": "созвон",\n',
)
replace_func(
    "modules/router.py",
    "detect_intent",
    r'''def detect_intent(text: str) -> IntentResult:
    lower = _normalise(text)
    if NEGATED_CREATE_RE.search(lower):
        return IntentResult(INTENT_UNKNOWN, 0.0)
    if any(word in lower for word in DELETE_WORDS):
        return IntentResult(INTENT_DELETE, 0.98)
    if any(word in lower for word in UPDATE_WORDS):
        return IntentResult(INTENT_UPDATE, 0.98)
    if any(word in lower for word in FREE_WORDS):
        return IntentResult(INTENT_FREE, 0.96)
    if any(word in lower for word in SEARCH_WORDS):
        return IntentResult(INTENT_SEARCH, 0.97)
    if WHEN_SEARCH_RE.search(lower):
        return IntentResult(INTENT_SEARCH, 0.93)
    if any(word in lower for word in VIEW_WORDS):
        return IntentResult(INTENT_VIEW, 0.96)
    if INFO_CREATE_QUESTION_RE.search(lower) and any(word in lower for word in CREATE_WORDS):
        return IntentResult(INTENT_UNKNOWN, 0.0)
    if any(word in lower for word in CREATE_WORDS):
        return IntentResult(INTENT_CREATE, 0.99)

    has_event = any(word in lower for word in EVENT_WORDS)
    has_action = any(word in lower for word in ACTION_WORDS)
    has_date = bool(DATE_HINT_RE.search(lower))
    has_time = _extract_time(lower) is not None or _relative_offset(lower) is not None
    is_question = bool(QUESTION_PREFIX_RE.search(lower)) or text.rstrip().endswith("?")
    is_current_state = bool(CURRENT_STATE_RE.search(lower))

    if has_date and has_time and not is_question and not is_current_state:
        return IntentResult(INTENT_CREATE, 0.92)
    if has_event and (has_date or has_time) and not is_question and not is_current_state:
        return IntentResult(INTENT_CREATE, 0.86)
    if has_action and (has_date or has_time) and not is_question and not is_current_state:
        return IntentResult(INTENT_CREATE, 0.84)
    return IntentResult(INTENT_UNKNOWN, 0.0)''',
)

# modules/calendar_event_features.py
replace_once(
    "modules/calendar_event_features.py",
    'from datetime import datetime, timedelta\nimport re\n\nfrom modules.calendar import _date_from_text, _detect_category, _extract_title\n',
    'from datetime import datetime, timedelta\nimport re\n\nimport dateparser\n\nfrom modules.calendar import MONTHS_PATTERN, _date_from_text, _detect_category, _extract_time, _extract_title\n',
)
replace_once(
    "modules/calendar_event_features.py",
    'BIRTHDAY_RE = re.compile(r"\\bдень\\s+рождени[яе]\\b", re.IGNORECASE)\n',
    'BIRTHDAY_RE = re.compile(r"\\bдень\\s+рождени[яе]\\b", re.IGNORECASE)\nSPAN_EVENT_RE = re.compile(r"\\b(?:отпуск|командировк\\w*)\\b", re.IGNORECASE)\nNEXT_WEEK_RE = re.compile(r"\\b(?:на\\s+)?следующ\\w*\\s+недел\\w*\\b", re.IGNORECASE)\nCROSS_MONTH_RANGE_RE = re.compile(\n    rf"\\bс\\s+(?P<start_day>\\d{{1,2}})\\s+(?P<start_month>(?:{MONTHS_PATTERN}))\\s+"\n    rf"по\\s+(?P<end_day>\\d{{1,2}})\\s+(?P<end_month>(?:{MONTHS_PATTERN}))\\b",\n    re.IGNORECASE,\n)\nSAME_MONTH_RANGE_RE = re.compile(\n    rf"\\bс\\s+(?P<start_day>\\d{{1,2}})\\s+по\\s+(?P<end_day>\\d{{1,2}})\\s+"\n    rf"(?P<month>(?:{MONTHS_PATTERN}))\\b",\n    re.IGNORECASE,\n)\n',
)
replace_func(
    "modules/calendar_event_features.py",
    "is_all_day",
    r'''def is_all_day(text: str) -> bool:
    """Явный all-day, день рождения без времени или многодневный тип без часов."""
    if ALL_DAY_RE.search(text):
        return True
    if BIRTHDAY_RE.search(text):
        return _extract_time(text) is None
    if SPAN_EVENT_RE.search(text):
        return _extract_time(text) is None
    return False''',
)
replace_func(
    "modules/calendar_event_features.py",
    "build_all_day_event",
    r'''def build_all_day_event(text: str, timezone: str, now: datetime | None = None, category_colors: dict[str, str | None] | None = None) -> dict | None:
    zone = _user_zone(timezone)
    local_now = now.astimezone(zone) if now and now.tzinfo else (now.replace(tzinfo=zone) if now else datetime.now(zone))
    naive_now = local_now.replace(tzinfo=None)

    def parse_fragment(value: str):
        parsed = dateparser.parse(
            value,
            languages=["ru"],
            settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": naive_now, "DATE_ORDER": "DMY"},
        )
        return parsed.date() if parsed else None

    start_date = None
    end_date = None
    if NEXT_WEEK_RE.search(text):
        days_to_next_monday = 7 - local_now.weekday()
        start_date = local_now.date() + timedelta(days=days_to_next_monday)
        end_date = start_date + timedelta(days=7)
    else:
        cross = CROSS_MONTH_RANGE_RE.search(text)
        same = SAME_MONTH_RANGE_RE.search(text)
        if cross:
            start_date = parse_fragment(f"{cross.group('start_day')} {cross.group('start_month')}")
            end_inclusive = parse_fragment(f"{cross.group('end_day')} {cross.group('end_month')}")
            if start_date and end_inclusive and end_inclusive < start_date:
                try:
                    end_inclusive = end_inclusive.replace(year=end_inclusive.year + 1)
                except ValueError:
                    return None
            end_date = end_inclusive + timedelta(days=1) if end_inclusive else None
        elif same:
            start_date = parse_fragment(f"{same.group('start_day')} {same.group('month')}")
            end_inclusive = parse_fragment(f"{same.group('end_day')} {same.group('month')}")
            if start_date and end_inclusive and end_inclusive < start_date:
                return None
            end_date = end_inclusive + timedelta(days=1) if end_inclusive else None
        else:
            start_date = _date_from_text(text, naive_now, 12, 0)
            end_date = start_date + timedelta(days=1) if start_date else None

    if not start_date or not end_date:
        return None
    category, color_id = _detect_category(text, category_colors)
    event = {
        "summary": _clean_title(text),
        "description": f"AI Smart Planner category: {category}",
        "start": {"date": start_date.isoformat()},
        "end": {"date": end_date.isoformat()},
    }
    if color_id:
        event["colorId"] = color_id
    return apply_event_features(event, text)''',
)
replace_func(
    "modules/calendar_event_features.py",
    "_recurrence_rule",
    r'''def _recurrence_rule(text: str) -> str | None:
    lower = text.lower().replace("ё", "е")
    if re.search(r"\b(?:по\s+будням|кажд\w*\s+будн\w*\s+день)\b", lower):
        return "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    if re.search(r"\b(?:кажд\w*\s+выходн\w*|по\s+выходным)\b", lower):
        return "RRULE:FREQ=WEEKLY;BYDAY=SA,SU"
    if re.search(r"\b(?:ежегодно|кажд\w*\s+год)\b", lower):
        return "RRULE:FREQ=YEARLY"
    if re.search(r"\bкажд(?:ый|ую|ое)\s+день\b|\bежедневно\b", lower):
        return "RRULE:FREQ=DAILY"

    interval = re.search(r"\bкажд\w*\s+(\d+)\s+недел\w*\b", lower)
    if interval:
        rule = f"RRULE:FREQ=WEEKLY;INTERVAL={int(interval.group(1))}"
        for word, code in WEEKDAY_BY_RE.items():
            if re.search(rf"\b(?:в\s+)?{word}\b", lower):
                return rule + f";BYDAY={code}"
        return rule

    for word, code in WEEKDAY_BY_RE.items():
        if re.search(rf"\bкажд\w*\s+{word}\b", lower):
            return f"RRULE:FREQ=WEEKLY;BYDAY={code}"
        if re.search(rf"\bпо\s+{word}\b", lower):
            return f"RRULE:FREQ=WEEKLY;BYDAY={code}"

    if re.search(r"\bкажд(?:ую|ой)\s+недел\w*\b|\bеженедельно\b|\bраз\s+в\s+недел\w*\b", lower):
        return "RRULE:FREQ=WEEKLY"
    if re.search(r"\bкажд(?:ый|ого)\s+месяц\w*\b|\bежемесячно\b", lower):
        return "RRULE:FREQ=MONTHLY"
    return None''',
)
replace_once(
    "modules/calendar_event_features.py",
    '    cleaned = LOCATION_RE.sub(" ", cleaned)\n',
    '    cleaned = LOCATION_RE.sub(" ", cleaned)\n    cleaned = CROSS_MONTH_RANGE_RE.sub(" ", cleaned)\n    cleaned = SAME_MONTH_RANGE_RE.sub(" ", cleaned)\n    cleaned = NEXT_WEEK_RE.sub(" ", cleaned)\n',
)
replace_once(
    "modules/calendar_event_features.py",
    '    cleaned = re.sub(r"\\b(?:ежедневно|еженедельно|ежемесячно)\\b", " ", cleaned, flags=re.IGNORECASE)\n',
    '    cleaned = re.sub(r"\\b(?:по\\s+будням|по\\s+выходным|кажд\\w*\\s+выходн\\w*|ежегодно|кажд\\w*\\s+год)\\b", " ", cleaned, flags=re.IGNORECASE)\n    cleaned = re.sub(r"\\b(?:ежедневно|еженедельно|ежемесячно)\\b", " ", cleaned, flags=re.IGNORECASE)\n',
)
