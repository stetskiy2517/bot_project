from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:100]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "modules/calendar.py",
    '''    daypart = DAYPART_HOUR_RE.search(lower) or BARE_DAYPART_HOUR_RE.search(lower)
    if daypart:
        hour = _apply_daypart(int(daypart.group("hour")), daypart.group("part"))
        if hour is not None:
            return hour, int(daypart.group("minute") or 0)
''',
    '''    daypart = DAYPART_HOUR_RE.search(lower)
    if not daypart:
        bare_daypart = BARE_DAYPART_HOUR_RE.search(lower)
        if bare_daypart:
            prefix = lower[max(0, bare_daypart.start() - 10):bare_daypart.start()]
            if not re.search(r"\\b(?:через|на|за)\\s*$", prefix):
                daypart = bare_daypart
    if daypart:
        hour = _apply_daypart(int(daypart.group("hour")), daypart.group("part"))
        if hour is not None:
            return hour, int(daypart.group("minute") or 0)
''',
)

replace_once(
    "modules/router.py",
    '''    reply = text.strip()
    if _extract_time(reply) is None and _extract_time(f"в {reply}") is not None:
        reply = f"в {reply}"
''',
    '''    reply = text.strip()
    if not re.match(r"^(?:в|к)\\b", _normalise(reply)) and _extract_time(f"в {reply}") is not None:
        reply = f"в {reply}"
''',
)
