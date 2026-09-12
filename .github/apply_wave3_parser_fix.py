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


def replace_func(path, name, new_source):
    text = load(path)
    pattern = re.compile(rf"(?ms)^def {re.escape(name)}\(.*?(?=^def |^async def |\Z)")
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(f"{path}: expected one function {name}, found {len(matches)}")
    start, end = matches[0].span()
    save(path, text[:start] + new_source.rstrip() + "\n\n\n" + text[end:])


# modules/calendar.py
replace_once(
    "modules/calendar.py",
    'EXPLICIT_CLOCK_TOKEN_RE = re.compile(\n    r"\\b(?:в|к)\\s*(?P<hour>\\d{1,2})(?::|\\.|-)(?P<minute>\\d{2})\\b",\n    re.IGNORECASE,\n)\n',
    'EXPLICIT_CLOCK_TOKEN_RE = re.compile(\n    r"\\b(?:в|к)\\s*(?P<hour>\\d{1,2})(?::|\\.|-)(?P<minute>\\d{2})\\b",\n    re.IGNORECASE,\n)\nCOMPACT_HHMM_RE = re.compile(\n    r"\\b(?:в|к)\\s*(?P<digits>\\d{3,4})(?!\\s*(?:г(?:\\.|оду)?))\\b",\n    re.IGNORECASE,\n)\n',
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

    compact_hhmm = COMPACT_HHMM_RE.search(lower)
    if compact_hhmm:
        digits = compact_hhmm.group("digits")
        hour = int(digits[:-2])
        minute = int(digits[-2:])
        if hour <= 23 and minute <= 59:
            return hour, minute
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
    "_relative_offset",
    r'''def _relative_offset(text: str) -> timedelta | None:
    lower = _normalise(text)
    composite = re.search(
        r"\bчерез\s+(\d+)\s*(?:ч|час\w*)\s+(\d+)\s*(?:мин|минут\w*)\b",
        lower,
    )
    if composite:
        return timedelta(hours=int(composite.group(1)), minutes=int(composite.group(2)))
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
    "_extract_duration",
    r'''def _extract_duration(text: str) -> timedelta:
    lower = _normalise(text)
    composite = re.search(
        r"\bна\s+(\d+)\s*(?:ч|час\w*)\s+(\d+)\s*(?:мин|минут\w*)\b",
        lower,
    )
    if composite:
        return timedelta(hours=int(composite.group(1)), minutes=int(composite.group(2)))
    if re.search(r"\bна\s+полчаса\b", lower):
        return timedelta(minutes=30)
    if re.search(r"\bна\s+полтора\s+часа\b", lower):
        return timedelta(minutes=90)
    if re.search(r"\bна\s+час\b", lower):
        return timedelta(hours=1)
    match = re.search(
        r"\bна\s+(\d+(?:[.,]\d+)?)\s*(мин(?:ут\w*)?|ч(?:ас\w*)?)\b",
        lower,
    )
    if match:
        amount = float(match.group(1).replace(",", "."))
        unit = match.group(2)
        if unit.startswith("мин"):
            return timedelta(minutes=amount)
        return timedelta(hours=amount)
    return DEFAULT_EVENT_DURATION''',
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
    candidate = now.replace(
        year=base_date.year,
        month=base_date.month,
        day=base_date.day,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if re.search(r"\bсегодня\b", lower) and candidate <= now:
        return None
    return candidate''',
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
    title = COMPACT_HHMM_RE.sub(" ", title)
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
        r"\bчерез\s+(?:\d+\s*(?:ч|час\w*)\s+\d+\s*(?:мин|минут\w*)|полчаса|полтора\s+часа|пару\s+час\w*|"
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
        r"\bна\s+(?:\d+\s*(?:ч|час\w*)\s+\d+\s*(?:мин|минут\w*)|полчаса|полтора\s+часа|час|\d+(?:[.,]\d+)?\s*(?:мин(?:ут\w*)?|ч(?:ас\w*)?))\b",
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

# modules/calendar_event_features.py
replace_once(
    "modules/calendar_event_features.py",
    'SAME_MONTH_RANGE_RE = re.compile(\n    rf"\\bс\\s+(?P<start_day>\\d{{1,2}})\\s+по\\s+(?P<end_day>\\d{{1,2}})\\s+"\n    rf"(?P<month>(?:{MONTHS_PATTERN}))\\b",\n    re.IGNORECASE,\n)\n',
    'SAME_MONTH_RANGE_RE = re.compile(\n    rf"\\bс\\s+(?P<start_day>\\d{{1,2}})\\s+по\\s+(?P<end_day>\\d{{1,2}})\\s+"\n    rf"(?P<month>(?:{MONTHS_PATTERN}))\\b",\n    re.IGNORECASE,\n)\nSAME_MONTH_DASH_RANGE_RE = re.compile(\n    rf"\\b(?P<start_day>\\d{{1,2}})\\s*[-–—]\\s*(?P<end_day>\\d{{1,2}})\\s+"\n    rf"(?P<month>(?:{MONTHS_PATTERN}))\\b",\n    re.IGNORECASE,\n)\n',
)
replace_once(
    "modules/calendar_event_features.py",
    'REMINDER_RE = re.compile(\n    r"\\bза\\s+(?:(?P<amount>\\d+)\\s*(?P<unit>мин(?:ут\\w*)?|ч(?:ас\\w*)?|дн\\w*)|"\n    r"(?P<half>полчаса)|(?P<hour>час)|(?P<day>день)|(?P<day_alias>сутки|суток))\\b",\n    re.IGNORECASE,\n)\n',
    'REMINDER_RE = re.compile(\n    r"\\bза\\s+(?:(?P<amount>\\d+)\\s*(?P<unit>мин(?:ут\\w*)?|ч(?:ас\\w*)?|день|дня|дн\\w*|недел\\w*)|"\n    r"(?P<half>полчаса)|(?P<hour>час)|(?P<day>день)|(?P<day_alias>сутки|суток)|(?P<week>недел\\w*))\\b",\n    re.IGNORECASE,\n)\n',
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
        dash = SAME_MONTH_DASH_RANGE_RE.search(text)
        if cross:
            start_date = parse_fragment(f"{cross.group('start_day')} {cross.group('start_month')}")
            end_inclusive = parse_fragment(f"{cross.group('end_day')} {cross.group('end_month')}")
            if start_date and end_inclusive and end_inclusive < start_date:
                try:
                    end_inclusive = end_inclusive.replace(year=end_inclusive.year + 1)
                except ValueError:
                    return None
            end_date = end_inclusive + timedelta(days=1) if end_inclusive else None
        elif same or dash:
            match = same or dash
            start_date = parse_fragment(f"{match.group('start_day')} {match.group('month')}")
            end_inclusive = parse_fragment(f"{match.group('end_day')} {match.group('month')}")
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
    "_reminder_minutes",
    r'''def _reminder_minutes(text: str) -> list[int]:
    values: list[int] = []
    for match in REMINDER_RE.finditer(text):
        if match.group("half"):
            minutes = 30
        elif match.group("hour"):
            minutes = 60
        elif match.group("day") or match.group("day_alias"):
            minutes = 1440
        elif match.group("week"):
            minutes = 10080
        else:
            amount = int(match.group("amount"))
            unit = match.group("unit").lower()
            if unit.startswith("мин"):
                minutes = amount
            elif unit == "ч" or unit.startswith("час"):
                minutes = amount * 60
            elif unit.startswith("недел"):
                minutes = amount * 10080
            else:
                minutes = amount * 1440
        if 0 <= minutes <= 40320 and minutes not in values:
            values.append(minutes)
    return sorted(values, reverse=True)[:5]''',
)
replace_once(
    "modules/calendar_event_features.py",
    '    cleaned = SAME_MONTH_RANGE_RE.sub(" ", cleaned)\n',
    '    cleaned = SAME_MONTH_RANGE_RE.sub(" ", cleaned)\n    cleaned = SAME_MONTH_DASH_RANGE_RE.sub(" ", cleaned)\n',
)

# modules/router.py
replace_once(
    "modules/router.py",
    'INFO_CREATE_QUESTION_RE = re.compile(\n',
    'NON_EVENT_STATEMENT_RE = re.compile(\n    r"\\b(?:погод\\w*|прогноз\\s+погоды|температур\\w*|дожд\\w*|снег\\w*|градус\\w*)\\b",\n    re.IGNORECASE,\n)\nINFO_CREATE_QUESTION_RE = re.compile(\n',
)
replace_once(
    "modules/router.py",
    '    if any(word in lower for word in CREATE_WORDS):\n        return IntentResult(INTENT_CREATE, 0.99)\n\n    has_event = any(word in lower for word in EVENT_WORDS)\n',
    '    if any(word in lower for word in CREATE_WORDS):\n        return IntentResult(INTENT_CREATE, 0.99)\n    if NON_EVENT_STATEMENT_RE.search(lower):\n        return IntentResult(INTENT_UNKNOWN, 0.0)\n\n    has_event = any(word in lower for word in EVENT_WORDS)\n',
)
