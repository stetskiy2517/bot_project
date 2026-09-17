"""Bilingual input normalization for deterministic planner parsers.

English control, date and time phrases are converted into the Russian grammar
already handled by the planner. User content such as names, note bodies and
event titles stays in the original language whenever possible.
"""

from __future__ import annotations

import re
import sys

_LATIN_RE = re.compile(r"[A-Za-z]")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

_WEEKDAYS = {
    "monday": "понедельник",
    "tuesday": "вторник",
    "wednesday": "среда",
    "thursday": "четверг",
    "friday": "пятница",
    "saturday": "суббота",
    "sunday": "воскресенье",
}
_MONTHS = {
    "january": "января",
    "february": "февраля",
    "march": "марта",
    "april": "апреля",
    "may": "мая",
    "june": "июня",
    "july": "июля",
    "august": "августа",
    "september": "сентября",
    "october": "октября",
    "november": "ноября",
    "december": "декабря",
}
_MONTHS_RU_PATTERN = (
    r"января|февраля|марта|апреля|мая|июня|июля|августа|"
    r"сентября|октября|ноября|декабря"
)
_WEEKDAYS_RU_PATTERN = (
    r"понедельник|вторник|среда|четверг|пятница|суббота|воскресенье"
)
_ORDINALS = {
    "first": "первый",
    "second": "второй",
    "third": "третий",
    "fourth": "четвертый",
    "fifth": "пятый",
}
_HOUR_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

# Token-aware English classification. Family, health and travel intentionally
# precede work so "doctor appointment" and "business trip" are not misclassified.
_ENGLISH_CATEGORY_PATTERNS = (
    (
        "family",
        re.compile(
            r"\b(?:family|parents?|children?|child|kids?|son|daughter|mom|mum|mother|dad|father|wife|husband)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "health",
        re.compile(
            r"\b(?:doctor|dentist|clinic|hospital|check-?up|mri|ultrasound|massage|physio(?:therapy)?|"
            r"medicine|medication|medical|health|therapist|pharmacy)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "travel",
        re.compile(
            r"\b(?:flight|plane|airport|taxi|travel|trip|boarding|departure|arrival|railway|train)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "work",
        re.compile(
            r"\b(?:work|meeting|call|client|office|project|presentation|report|conference|negotiation|"
            r"stand-?up|planning|business)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "rest",
        re.compile(
            r"\b(?:rest|vacation|holiday|movie|cinema|theat(?:er|re)|restaurant|cafe|walk|sauna|gym|"
            r"workout|training|sport|friends?|concert|museum|yoga|pilates|pool)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "personal",
        re.compile(
            r"\b(?:personal|home|shopping|shop|buy|birthday|pick-?up|errand|haircut|barber|manicure)\b",
            re.IGNORECASE,
        ),
    ),
)

# Safe substring keywords are appended to the existing category table as a
# compatibility fallback for modules that imported _detect_category before the
# bilingual wrapper was installed.
_ENGLISH_SAFE_CATEGORY_KEYWORDS = {
    "work": (
        "meeting",
        "client",
        "office",
        "project",
        "presentation",
        "report",
        "conference",
        "negotiation",
        "standup",
        "stand-up",
        "call",
    ),
    "health": (
        "doctor",
        "dentist",
        "clinic",
        "hospital",
        "checkup",
        "check-up",
        "mri",
        "ultrasound",
        "massage",
        "physio",
        "medicine",
        "medication",
        "medical",
        "pharmacy",
    ),
    "rest": (
        "vacation",
        "holiday",
        "movie",
        "cinema",
        "theater",
        "theatre",
        "restaurant",
        "cafe",
        "workout",
        "gym",
        "concert",
        "museum",
        "yoga",
        "pilates",
        "sauna",
    ),
    "travel": (
        "flight",
        "airport",
        "taxi",
        "travel",
        "trip",
        "boarding",
        "departure",
        "arrival",
        "plane",
    ),
    "family": (
        "family",
        "parent",
        "children",
        "child",
        "daughter",
        "mother",
        "father",
        "wife",
        "husband",
        "mom",
        "mum",
        "dad",
    ),
    "personal": (
        "personal",
        "shopping",
        "shop",
        "birthday",
        "pickup",
        "pick-up",
        "errand",
        "haircut",
        "barber",
        "manicure",
    ),
}


def detect_input_language(text: str) -> str:
    """Return ``en``, ``ru`` or ``unknown`` for routing/localization hints."""
    value = str(text or "")
    latin = len(_LATIN_RE.findall(value))
    cyrillic = len(_CYRILLIC_RE.findall(value))
    if latin == 0 and cyrillic == 0:
        return "unknown"
    return "en" if latin > cyrillic else "ru"


def _replace(pattern: str, replacement, text: str) -> str:
    return re.sub(pattern, replacement, text, flags=re.IGNORECASE)


def _clock_12h(match: re.Match) -> str:
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    suffix = match.group("suffix").lower().replace(".", "")
    if suffix == "pm" and hour != 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    return f"в {hour:02d}:{minute:02d}"


def _word_clock_12h(match: re.Match) -> str:
    hour = _HOUR_WORDS[match.group("hour").lower()]
    suffix = match.group("suffix").lower().replace(".", "")
    if suffix == "pm" and hour != 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    return f"в {hour:02d}:00"


def _daypart_clock(match: re.Match) -> str:
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    part = match.group("part").lower()
    if part in {"afternoon", "evening"} and hour < 12:
        hour += 12
    elif part in {"morning", "night"} and hour == 12:
        hour = 0
    return f"в {hour:02d}:{minute:02d}"


def _range_12h(match: re.Match) -> str:
    def convert(hour_raw: str, minute_raw: str | None, suffix_raw: str) -> tuple[int, int]:
        hour = int(hour_raw)
        minute = int(minute_raw or 0)
        suffix = suffix_raw.lower().replace(".", "")
        if suffix == "pm" and hour != 12:
            hour += 12
        elif suffix == "am" and hour == 12:
            hour = 0
        return hour, minute

    sh, sm = convert(match.group("sh"), match.group("sm"), match.group("ss"))
    eh, em = convert(match.group("eh"), match.group("em"), match.group("es"))
    return f"с {sh:02d}:{sm:02d} до {eh:02d}:{em:02d}"


def _month_day(match: re.Match) -> str:
    month = _MONTHS[match.group("month").lower()]
    return f"{int(match.group('day'))} {month}{match.group('year') or ''}"


def _day_month(match: re.Match) -> str:
    month = _MONTHS[match.group("month").lower()]
    return f"{int(match.group('day'))} {month}{match.group('year') or ''}"


def _relative_amount(match: re.Match) -> str:
    amount = match.group("amount")
    unit = match.group("unit").lower()
    if unit.startswith("minute") or unit in {"min", "mins"}:
        target = "минут"
    elif unit.startswith("hour") or unit in {"hr", "hrs"}:
        target = "часов"
    elif unit.startswith("day"):
        target = "дней"
    else:
        target = "недель"
    return f"через {amount} {target}"


def _duration_amount(match: re.Match) -> str:
    amount = match.group("amount")
    unit = match.group("unit").lower()
    target = "минут" if unit.startswith("minute") or unit in {"min", "mins"} else "часов"
    return f"на {amount} {target}"


def _before_amount(match: re.Match) -> str:
    amount = match.group("amount")
    unit = match.group("unit").lower()
    if unit.startswith("minute") or unit in {"min", "mins"}:
        target = "минут"
    elif unit.startswith("hour") or unit in {"hr", "hrs"}:
        target = "часов"
    elif unit.startswith("day"):
        target = "дней"
    else:
        target = "недель"
    return f"за {amount} {target}"


def _category_color(category: str, category_colors: dict[str, str | None] | None, calendar_module) -> str | None:
    if category_colors is not None:
        return category_colors.get(category)
    config = calendar_module.EVENT_CATEGORIES.get(category) or {}
    return config.get("color_id")


def install_english_category_support() -> None:
    """Make calendar and reminder category detection bilingual without duplication."""
    from modules import calendar

    for category, keywords in _ENGLISH_SAFE_CATEGORY_KEYWORDS.items():
        config = calendar.EVENT_CATEGORIES.get(category)
        if not config:
            continue
        current = tuple(config.get("keywords") or ())
        config["keywords"] = tuple(dict.fromkeys((*current, *keywords)))

    current_detector = calendar._detect_category
    if getattr(current_detector, "_english_category_support", False):
        return

    original_detector = current_detector

    def bilingual_detect_category(
        text: str,
        category_colors: dict[str, str | None] | None = None,
    ) -> tuple[str, str | None]:
        value = str(text or "")
        if _LATIN_RE.search(value):
            for category, pattern in _ENGLISH_CATEGORY_PATTERNS:
                if pattern.search(value):
                    return category, _category_color(category, category_colors, calendar)
        return original_detector(value, category_colors)

    bilingual_detect_category._english_category_support = True
    bilingual_detect_category._original_detector = original_detector
    calendar._detect_category = bilingual_detect_category

    # Fix modules that may have imported the function before router startup.
    for module_name in ("modules.reminder_categories", "modules.calendar_event_features", "modules.life_wheel"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "_detect_category"):
            module._detect_category = bilingual_detect_category


def canonicalize_english(text: str) -> str:
    """Convert English planner grammar to the canonical deterministic grammar."""
    value = str(text or "").strip()
    if not value or not _LATIN_RE.search(value):
        return value

    # Pending-dialog controls are exact replies and must be handled before any
    # sentence-level transformations.
    stripped = value.strip(" \t\r\n.,!?;:").lower()
    direct = {
        "yes": "да",
        "yeah": "да",
        "yep": "да",
        "sure": "да",
        "yes please": "да",
        "ok": "ок",
        "okay": "окей",
        "no": "нет",
        "no thanks": "нет",
        "cancel": "отмена",
        "stop": "стоп",
        **_ORDINALS,
    }
    if stripped in direct:
        return direct[stripped]

    # Reordered update forms must be handled before generic command prefixes.
    value = _replace(
        r"^\s*(?:move|reschedule)\s+(?:the\s+|my\s+|an?\s+)?(?P<target>.+?)\s+to\s+(?P<when>.+)$",
        lambda m: f"перенеси {m.group('target')} на {m.group('when')}",
        value,
    )
    value = _replace(
        r"^\s*rename\s+(?:the\s+|my\s+|an?\s+)?(?P<target>.+?)\s+to\s+(?P<title>.+)$",
        lambda m: f"переименуй {m.group('target')} в {m.group('title')}",
        value,
    )
    value = _replace(
        r"^\s*(?:set|change|update)\s+(?:the\s+)?category\s+(?:of|for)\s+"
        r"(?P<target>.+?)\s+to\s+(?P<category>work|health|rest|travel|family|personal|other)\s*$",
        lambda m: f"измени категорию у {m.group('target')} на {m.group('category')}",
        value,
    )
    value = _replace(
        r"^\s*(?:set|change|update)\s+(?:the\s+)?priority\s+(?:of|for)\s+"
        r"(?P<target>.+?)\s+to\s+(?P<priority>high|medium|normal|low)\s*$",
        lambda m: f"измени приоритет у {m.group('target')} на {m.group('priority')}",
        value,
    )
    value = _replace(
        r"^\s*(?:set|change|update)\s+(?:the\s+|my\s+)?(?P<target>.+?)\s+"
        r"category\s+to\s+(?P<category>work|health|rest|travel|family|personal|other)\s*$",
        lambda m: f"измени категорию у {m.group('target')} на {m.group('category')}",
        value,
    )
    value = _replace(
        r"^\s*(?:set|change|update)\s+(?:the\s+|my\s+)?(?P<target>.+?)\s+"
        r"priority\s+to\s+(?P<priority>high|medium|normal|low)\s*$",
        lambda m: f"измени приоритет у {m.group('target')} на {m.group('priority')}",
        value,
    )

    # Phrases that express note operations need reordering.
    value = _replace(
        r"^\s*add\s+(?P<addition>.+?)\s+to\s+(?:my\s+)?(?P<target>.+?\b(?:note|list))\s*[.!?]*$",
        lambda m: f"добавь в {m.group('target')}: {m.group('addition')}",
        value,
    )
    value = _replace(
        r"^\s*(?:find|search\s+for|show)\s+(?:me\s+)?(?:a\s+|my\s+)?notes?\s+(?:about|on)\s+",
        "найди заметки про ",
        value,
    )

    # Calendar and free-time questions.
    phrase_rules = (
        (r"^\s*what(?:'s|\s+is)?\s+on\s+my\s+calendar\b", "что у меня"),
        (r"^\s*what(?:'s|\s+is)\s+on\b", "что у меня"),
        (r"^\s*what\s+do\s+i\s+have\b", "что у меня"),
        (r"^\s*what\s+have\s+i\s+got\b", "что у меня"),
        (r"^\s*do\s+i\s+have\s+anything\b", "что у меня"),
        (r"^\s*show\s+(?:me\s+)?my\s+calendar\b", "покажи календарь"),
        (r"^\s*when\s+am\s+i\s+free\b", "когда я свободен"),
        (r"^\s*when\s+do\s+i\s+have\s+(?:a\s+)?free\s+(?:slot|window|time)\b", "когда я свободен"),
        (r"^\s*(?:find|show\s+me)\s+(?:a\s+)?free\s+(?:slot|window|time)\b", "найди окно"),
        (r"^\s*do\s+i\s+have\s+(?:a\s+)?free\s+(?:slot|window|time)\b", "есть ли окно"),
        (r"^\s*when\s+is\s+my\b", "когда у меня"),
        (r"^\s*when\s+is\b", "когда"),
        (r"^\s*what\s+time\s+is\b", "во сколько"),
        (r"^\s*where\s+is\b", "покажи где"),
    )
    for pattern, replacement in phrase_rules:
        value = _replace(pattern, replacement, value)

    # Reminder commands.
    reminder_rules = (
        (r"^\s*remind\s+me\s+to\b", "напомни"),
        (r"^\s*remind\s+me\b", "напомни"),
        (r"^\s*(?:create|add|set)\s+(?:a\s+|new\s+)?reminder\b", "создай напоминание"),
        (r"^\s*(?:show|list)\s+(?:me\s+)?(?:my\s+)?reminders\b", "покажи мои напоминания"),
        (r"^\s*(?:delete|remove|cancel)\s+(?:my\s+|the\s+)?reminder\b", "удали напоминание"),
        (r"^\s*(?:change|update|edit|reschedule)\s+(?:my\s+|the\s+)?reminder\b", "измени напоминание"),
    )
    for pattern, replacement in reminder_rules:
        value = _replace(pattern, replacement, value)

    # Notes.
    note_rules = (
        (r"^\s*(?:create|add|save|write)\s+(?:me\s+)?(?:a\s+|new\s+)?note\b", "создай заметку"),
        (r"^\s*note\s*[:\-]", "заметка:"),
        (r"^\s*(?:show|list|open)\s+(?:me\s+)?(?:my\s+)?notes\b", "покажи мои заметки"),
        (r"^\s*(?:delete|remove)\s+(?:my\s+|the\s+)?note\b", "удали заметку"),
        (r"^\s*(?:append|add)\s+to\s+(?:my\s+|the\s+)?note\b", "добавь в заметку"),
        (r"^\s*(?:create|make|write)\s+(?:me\s+)?(?:a\s+|new\s+)?list\b", "создай список"),
    )
    for pattern, replacement in note_rules:
        value = _replace(pattern, replacement, value)

    # Tasks.
    task_rules = (
        (r"^\s*(?:create|add|make)\s+(?:a\s+|new\s+)?task\b", "создай задачу"),
        (r"^\s*(?:show|list)\s+(?:me\s+)?(?:my\s+)?tasks\b", "покажи мои задачи"),
        (r"^\s*(?:delete|remove)\s+(?:my\s+|the\s+)?task\b", "удали задачу"),
        (r"^\s*(?:complete|finish|close|mark)\s+(?:my\s+|the\s+)?task\b", "заверши задачу"),
        (r"^\s*what\s+do\s+i\s+(?:need|have)\s+to\s+do\b", "что мне нужно сделать"),
    )
    for pattern, replacement in task_rules:
        value = _replace(pattern, replacement, value)

    # Generic calendar mutations/search. User subject/title is preserved.
    calendar_rules = (
        (r"^\s*(?:schedule|plan|book|create|add|set\s+up)\s+(?:the\s+|my\s+|an?\s+)?", "запланируй "),
        (r"^\s*(?:delete|remove|cancel)\s+(?:the\s+|my\s+|an?\s+)?", "удали "),
        (r"^\s*(?:move|reschedule)\s+(?:the\s+|my\s+|an?\s+)?", "перенеси "),
        (r"^\s*(?:change|update|edit)\s+(?:the\s+|my\s+|an?\s+)?", "измени "),
        (r"^\s*rename\s+(?:the\s+|my\s+|an?\s+)?", "переименуй "),
        (r"^\s*find\s+(?:the\s+|my\s+|an?\s+)?", "найди "),
        (r"^\s*show\s+me\s+", "покажи "),
    )
    for pattern, replacement in calendar_rules:
        value = _replace(pattern, replacement, value)

    # Explicit category names and event properties.
    category_names = {
        "work": "работа",
        "health": "здоровье",
        "rest": "отдых",
        "travel": "поездки",
        "family": "семья",
        "personal": "личное",
        "other": "прочее",
    }
    for english, russian in category_names.items():
        value = _replace(rf"\bcategory\s*[:\-]?\s*{english}\b", f"категория {russian}", value)
        value = _replace(
            rf"(\bкатегор\w*\b[^.!?]{{0,120}}\b(?:на|to)\s+){english}\b",
            lambda m, target=russian: f"{m.group(1)}{target}",
            value,
        )

    priority_names = {
        "high": "высокий приоритет",
        "medium": "обычный приоритет",
        "normal": "обычный приоритет",
        "low": "низкий приоритет",
    }
    for english, russian in priority_names.items():
        value = _replace(
            rf"(\bприоритет\w*\b[^.!?]{{0,120}}\b(?:на|to)\s+){english}\b",
            lambda m, target=russian: f"{m.group(1)}{target}",
            value,
        )

    property_rules = (
        (r"\bhigh\s+priority\b|\burgent\b", "высокий приоритет"),
        (r"\blow\s+priority\b|\bnot\s+urgent\b", "низкий приоритет"),
        (r"\b(?:normal|medium)\s+priority\b", "обычный приоритет"),
        (r"\ball[ -]?day\b", "весь день"),
        (r"\blocation\s*[:\-]", "место:"),
        (r"\baddress\s*[:\-]", "адрес:"),
        (r"\bbirthday\b", "день рождения"),
        (r"\bbusiness\s+trip\b", "командировка"),
        (r"\bvacation\b", "отпуск"),
    )
    for pattern, replacement in property_rules:
        value = _replace(pattern, replacement, value)

    # Inline event reminder phrasing.
    value = _replace(
        r"\bremind\s+me\s+(?P<amount>\d+)\s*(?P<unit>minutes?|mins?|hours?|hrs?|days?|weeks?)\s+before\b",
        lambda m: "напомни " + _before_amount(m),
        value,
    )
    value = _replace(
        r"\b(?P<amount>\d+)\s*(?P<unit>minutes?|mins?|hours?|hrs?|days?|weeks?)\s+before\b",
        _before_amount,
        value,
    )

    # 12-hour ranges and clock forms.
    value = _replace(
        r"\bfrom\s+(?P<sh>\d{1,2})(?::(?P<sm>\d{2}))?\s*(?P<ss>a\.?m\.?|p\.?m\.?)\s+"
        r"to\s+(?P<eh>\d{1,2})(?::(?P<em>\d{2}))?\s*(?P<es>a\.?m\.?|p\.?m\.?)\b",
        _range_12h,
        value,
    )
    value = _replace(
        r"\b(?:at\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s+in\s+the\s+"
        r"(?P<part>morning|afternoon|evening|night)\b",
        _daypart_clock,
        value,
    )
    value = _replace(
        r"\b(?:at\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<suffix>a\.?m\.?|p\.?m\.?)\b",
        _clock_12h,
        value,
    )
    hour_words_alt = "|".join(_HOUR_WORDS)
    value = _replace(
        rf"\b(?:at\s+)?(?P<hour>{hour_words_alt})\s*(?P<suffix>a\.?m\.?|p\.?m\.?)\b",
        _word_clock_12h,
        value,
    )
    value = _replace(r"\bat\s+(?=\d{1,2}(?::\d{2})?\b)", "в ", value)
    value = _replace(r"\bnoon\b", "полдень", value)
    value = _replace(r"\bmidnight\b", "полночь", value)

    # Relative times and durations.
    value = _replace(
        r"\bin\s+(?P<amount>\d+)\s*(?P<unit>minutes?|mins?|hours?|hrs?|days?|weeks?)\b",
        _relative_amount,
        value,
    )
    value = _replace(r"\bin\s+half\s+an?\s+hour\b", "через полчаса", value)
    value = _replace(r"\bin\s+an?\s+hour\b", "через 1 час", value)
    value = _replace(r"\bin\s+an?\s+day\b", "через 1 день", value)
    value = _replace(r"\bin\s+an?\s+week\b", "через 1 неделю", value)
    value = _replace(
        r"\bfor\s+(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<unit>minutes?|mins?|hours?|hrs?)\b",
        _duration_amount,
        value,
    )

    # Dates. Normalize month-name forms before weekday/day aliases.
    month_alt = "|".join(_MONTHS)
    value = _replace(
        rf"\b(?P<month>{month_alt})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?P<year>\s+\d{{4}})?\b",
        _month_day,
        value,
    )
    value = _replace(
        rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month>{month_alt})(?P<year>\s+\d{{4}})?\b",
        _day_month,
        value,
    )
    value = _replace(r"\bday\s+after\s+tomorrow\b", "послезавтра", value)
    value = _replace(r"\btomorrow\b", "завтра", value)
    value = _replace(r"\btoday\b", "сегодня", value)
    value = _replace(r"\bnext\s+week\b", "следующая неделя", value)
    value = _replace(r"\bthis\s+week\b", "эта неделя", value)

    # Recurring weekday phrases before plain weekday names.
    for english, russian in _WEEKDAYS.items():
        value = _replace(rf"\bevery\s+{english}\b", f"каждый {russian}", value)
    for english, russian in _WEEKDAYS.items():
        value = _replace(rf"\bnext\s+{english}\b", f"следующий {russian}", value)
        value = _replace(rf"\bthis\s+{english}\b", russian, value)
        value = _replace(rf"\b{english}\b", russian, value)

    # Remove English "on" only when it is a temporal preposition.
    value = _replace(
        rf"\bon\s+(?=(?:следующ\w*\s+)?(?:{_WEEKDAYS_RU_PATTERN})\b|\d{{1,2}}\s+(?:{_MONTHS_RU_PATTERN})\b)",
        "",
        value,
    )

    # Date ranges after English month names have been normalized.
    value = _replace(
        rf"\bfrom\s+(?P<start>\d{{1,2}}\s+(?:{_MONTHS_RU_PATTERN}))\s+"
        rf"to\s+(?P<end>\d{{1,2}}\s+(?:{_MONTHS_RU_PATTERN}))\b",
        lambda m: f"с {m.group('start')} по {m.group('end')}",
        value,
    )

    # Recurrence and dayparts.
    recurrence_rules = (
        (r"\bevery\s+weekday\b|\bon\s+weekdays\b", "по будням"),
        (r"\bevery\s+weekend\b|\bon\s+weekends\b", "по выходным"),
        (r"\bevery\s+day\b|\bdaily\b", "каждый день"),
        (r"\bevery\s+week\b|\bweekly\b", "еженедельно"),
        (r"\bevery\s+month\b|\bmonthly\b", "ежемесячно"),
        (r"\bevery\s+year\b|\byearly\b|\bannually\b", "ежегодно"),
        (r"\bevery\s+morning\b", "каждое утро"),
        (r"\bevery\s+evening\b", "каждый вечер"),
        (r"\bevery\s+night\b", "каждую ночь"),
        (r"\bin\s+the\s+morning\b", "утром"),
        (r"\bin\s+the\s+afternoon\b", "днем"),
        (r"\bin\s+the\s+evening\b", "вечером"),
        (r"\bat\s+night\b", "ночью"),
    )
    for pattern, replacement in recurrence_rules:
        value = _replace(pattern, replacement, value)

    # Slot choices like "the second option".
    for english, russian in _ORDINALS.items():
        value = _replace(rf"\b(?:the\s+)?{english}\s+(?:option|slot)\b", f"{russian} вариант", value)

    # Common filler/politeness that otherwise leaks into titles.
    value = _replace(r"^\s*(?:please|could\s+you|can\s+you)\s+", "", value)

    value = re.sub(r"\s+", " ", value).strip()
    return value
