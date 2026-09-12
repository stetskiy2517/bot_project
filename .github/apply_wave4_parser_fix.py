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
    '    "воскресенье": 6, "воскресенья": 6,\n}\n',
    '    "воскресенье": 6, "воскресенья": 6,\n    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,\n}\n',
)
replace_once(
    "modules/calendar.py",
    'NAMED_DATE_RE = re.compile(\n    rf"\\b\\d{{1,2}}\\s+(?:{MONTHS_PATTERN})(?:\\s+\\d{{4}})?\\b",\n',
    'NAMED_DATE_RE = re.compile(\n    rf"\\b\\d{{1,2}}(?:-?го)?\\s+(?:{MONTHS_PATTERN})(?:\\s+\\d{{4}})?\\b",\n',
)
replace_once(
    "modules/calendar.py",
    'DAYPART_HOUR_RE = re.compile(\n    r"\\b(?:в|к)\\s+(?P<hour>\\d{1,2})(?:\\s*(?::|\\.)\\s*(?P<minute>[0-5]\\d))?\\s+"\n    r"(?P<part>утра|дня|вечера|ночи)\\b",\n    re.IGNORECASE,\n)\n',
    'DAYPART_HOUR_RE = re.compile(\n    r"\\b(?:в|к)\\s+(?P<hour>\\d{1,2})(?:\\s*(?::|\\.)\\s*(?P<minute>[0-5]\\d))?\\s+"\n    r"(?P<part>утра|дня|вечера|ночи)\\b",\n    re.IGNORECASE,\n)\nBARE_DAYPART_HOUR_RE = re.compile(\n    r"(?<!\\d)(?P<hour>\\d{1,2})(?:\\s*(?::|\\.)\\s*(?P<minute>[0-5]\\d))?\\s+"\n    r"(?P<part>утра|дня|вечера|ночи)\\b",\n    re.IGNORECASE,\n)\n',
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

    daypart = DAYPART_HOUR_RE.search(lower) or BARE_DAYPART_HOUR_RE.search(lower)
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
    if re.search(r"\bчерез\s+сутки\b", lower):
        return timedelta(days=1)
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
replace_once(
    "modules/calendar.py",
    '        parsed = dateparser.parse(\n            date_match.group(0),\n',
    '        date_text = re.sub(r"(\\d{1,2})-?го\\b", r"\\1", date_match.group(0), flags=re.IGNORECASE)\n        parsed = dateparser.parse(\n            date_text,\n',
)
replace_once(
    "modules/calendar.py",
    '    title = DAYPART_HOUR_RE.sub(" ", title)\n',
    '    title = DAYPART_HOUR_RE.sub(" ", title)\n    title = BARE_DAYPART_HOUR_RE.sub(" ", title)\n',
)
replace_once(
    "modules/calendar.py",
    '        r"пятница|пятницу|пятницы|суббота|субботу|субботы|воскресенье|воскресенья)\\b",\n',
    '        r"пятница|пятницу|пятницы|суббота|субботу|субботы|воскресенье|воскресенья|пн|вт|ср|чт|пт|сб|вс)\\b",\n',
)

# modules/router.py
replace_once(
    "modules/router.py",
    'NON_EVENT_STATEMENT_RE = re.compile(\n    r"\\b(?:погод\\w*|прогноз\\s+погоды|температур\\w*|дожд\\w*|снег\\w*|градус\\w*)\\b",\n',
    'NON_EVENT_STATEMENT_RE = re.compile(\n    r"\\b(?:погод\\w*|прогноз\\s+погоды|температур\\w*|дожд\\w*|снег\\w*|градус\\w*|"\n    r"курс\\s+(?:доллар\\w*|евро|юан\\w*)|новост\\w*)\\b",\n',
)
replace_once(
    "modules/router.py",
    'NEGATED_CREATE_RE = re.compile(\n    r"\\bне\\s+(?:создавай|создай|добавляй|добавь|ставь|поставь|записывай|запиши|"\n    r"планируй|запланируй|назначай|назначь|вноси|внеси)\\b",\n    re.IGNORECASE,\n)\n',
    'NEGATED_CREATE_RE = re.compile(\n    r"\\b(?:не\\s+(?:создавай|создай|добавляй|добавь|ставь|поставь|записывай|запиши|"\n    r"планируй|запланируй|назначай|назначь|вноси|внеси)|"\n    r"не\\s+надо\\s+(?:создавать|добавлять|ставить|записывать|планировать|назначать|вносить))\\b",\n    re.IGNORECASE,\n)\n',
)
replace_once(
    "modules/router.py",
    '    r"четверг\\w*|пятниц\\w*|суббот\\w*|воскресень\\w*|\\d{1,2}[./-]\\d{1,2}|"\n',
    '    r"четверг\\w*|пятниц\\w*|суббот\\w*|воскресень\\w*|пн|вт|ср|чт|пт|сб|вс|\\d{1,2}[./-]\\d{1,2}|"\n',
)
replace_once(
    "modules/router.py",
    '    r"следующ\\w*\\s+недел\\w*|\\d{1,2}\\s+(?:январ\\w*|феврал\\w*|март\\w*|апрел\\w*|ма[йя]|июн\\w*|июл\\w*|"\n',
    '    r"следующ\\w*\\s+недел\\w*|\\d{1,2}(?:-?го)?\\s+(?:январ\\w*|феврал\\w*|март\\w*|апрел\\w*|ма[йя]|июн\\w*|июл\\w*|"\n',
)
