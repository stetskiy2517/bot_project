"""Safe proactive actions based on high-confidence learned habits."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import re
import threading
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.assistant_preferences import get_assistant_preferences
from core.db import conn, db_lock, get_category_colors, get_user_timezone
from core.memory_store import list_memories
from core.proactive_store import (
    get_proactive_decision,
    proactive_status,
    record_proactive_decision,
    should_evaluate_memory,
)
from core.reminder_recurrence import next_repeat_at
from core.reminder_store import create_reminder, list_active_reminders
from modules.calendar import _create_event, _detect_category
from modules.calendar_user import _list_events
from modules.reminders import _first_repeat_due, _reminder_clock, _repeat_rule

logger = logging.getLogger(__name__)

MIN_AUTO_CONFIDENCE = 0.90
MIN_ACTION_CONFIDENCE = 0.90
AUTO_SOURCE_TYPES = {"note", "voice_transcript"}
ALLOWED_REPEAT_RULES = {"daily", "weekdays", "weekends"}
WEEKLY_DAY_RE = re.compile(r"^weekly:[0-6]$")
CLOCK_RE = re.compile(
    r"(?:\b(?:в|к)\s+)?(?:[01]?\d|2[0-3])[:.]\s*[0-5]\d\b|"
    r"\b(?:в|к)\s+(?:[01]?\d|2[0-3])\b",
    re.IGNORECASE,
)
STRUCTURED_CLOCK_RE = re.compile(r"^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)$")
VAGUE_TIME_RE = re.compile(r"\b(?:около|примерно|приблизительно|после|до|ближе\s+к)\s*$", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
STOP_WORDS = {
    "каждый", "каждая", "каждое", "каждую", "каждые", "день", "вечер", "утро", "ночь",
    "будням", "выходным", "неделю", "недели", "пользователь", "обычно", "всегда", "нужно",
    "надо", "после", "перед", "около", "примерно", "напомнить", "напоминание",
}

LEADING_SUBJECT_RE = re.compile(r"^\s*(?:пользователь|я)\s+", re.IGNORECASE)
LEADING_FILLER_RE = re.compile(r"^\s*(?:(?:обычно|всегда|регулярно|как\s+правило)\s+)+", re.IGNORECASE)
HABIT_RECURRENCE_RE = re.compile(
    r"\b(?:"
    r"ежедневно|еженедельно|"
    r"по\s+(?:будням|выходным)|"
    r"кажд(?:ый|ая|ое|ую|ые)\s+(?:"
    r"день|вечер|утро|ночь|недел\w*|"
    r"понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*"
    r")"
    r")\b",
    re.IGNORECASE,
)
HABIT_CLOCK_RE = re.compile(
    r"\b(?:в|к)\s+(?:[01]?\d|2[0-3])(?:(?::|\.)[0-5]\d)?\b",
    re.IGNORECASE,
)
MEDICINE_TABLET_RE = re.compile(
    r"^(?:принимаю|принимает|принимать)\s+таблетк(?:и|у)\b(?P<tail>.*)$",
    re.IGNORECASE,
)
VERB_REWRITES = (
    (re.compile(r"^принимаю\b", re.IGNORECASE), "Принять"),
    (re.compile(r"^принимает\b", re.IGNORECASE), "Принять"),
    (re.compile(r"^принимать\b", re.IGNORECASE), "Принять"),
    (re.compile(r"^пью\b", re.IGNORECASE), "Выпить"),
    (re.compile(r"^выпиваю\b", re.IGNORECASE), "Выпить"),
    (re.compile(r"^проверяю\b", re.IGNORECASE), "Проверить"),
    (re.compile(r"^делаю\b", re.IGNORECASE), "Сделать"),
    (re.compile(r"^гуляю\b", re.IGNORECASE), "Погулять"),
    (re.compile(r"^тренируюсь\b", re.IGNORECASE), "Тренироваться"),
    (re.compile(r"^занимаюсь\b", re.IGNORECASE), "Заняться"),
    (re.compile(r"^читаю\b", re.IGNORECASE), "Читать"),
    (re.compile(r"^звоню\b", re.IGNORECASE), "Позвонить"),
    (re.compile(r"^пишу\b", re.IGNORECASE), "Написать"),
    (re.compile(r"^ложусь\b", re.IGNORECASE), "Лечь"),
    (re.compile(r"^встаю\b", re.IGNORECASE), "Встать"),
    (re.compile(r"^завтракаю\b", re.IGNORECASE), "Позавтракать"),
    (re.compile(r"^обедаю\b", re.IGNORECASE), "Пообедать"),
    (re.compile(r"^ужинаю\b", re.IGNORECASE), "Поужинать"),
    (re.compile(r"^медитирую\b", re.IGNORECASE), "Медитировать"),
    (re.compile(r"^чищу\b", re.IGNORECASE), "Почистить"),
    (re.compile(r"^кормлю\b", re.IGNORECASE), "Покормить"),
    (re.compile(r"^поливаю\b", re.IGNORECASE), "Полить"),
    (re.compile(r"^выгуливаю\b", re.IGNORECASE), "Выгулять"),
)

WEEKDAY_CODES = {0: "MO", 1: "TU", 2: "WE", 3: "TH", 4: "FR", 5: "SA", 6: "SU"}

_worker_lock = threading.Lock()
_worker_started = False


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no", "disabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def proactive_worker_enabled() -> bool:
    return _env_bool("AI_PROACTIVE_WORKER_ENABLED", True)


def _has_explicit_clock(text: str) -> bool:
    for match in CLOCK_RE.finditer(text):
        prefix = text[max(0, match.start() - 24) : match.start()]
        if not VAGUE_TIME_RE.search(prefix):
            return True
    return False


def _allowed_repeat_rule(text: str) -> str | None:
    rule = _repeat_rule(text)
    if rule in ALLOWED_REPEAT_RULES or (rule and WEEKLY_DAY_RE.fullmatch(rule)):
        return rule
    return None


def _habit_statement(memory: dict) -> str:
    value = memory.get("value")
    if isinstance(value, dict):
        return " ".join(str(value.get("statement") or "").split()).strip()
    return " ".join(str(value or "").split()).strip()


def _memory_text(memory: dict) -> str:
    statement = _habit_statement(memory)
    evidence = str(memory.get("evidence") or "").strip()
    return " ".join(part for part in (statement, evidence) if part)


def _legacy_reminder_text(memory: dict) -> str:
    text = _habit_statement(memory)
    if not text:
        text = "Не забудь о привычке"
    text = re.sub(r"^пользователь\s+", "", text, flags=re.IGNORECASE)
    return text[:300].strip(" .")


def _structured_action_title(memory: dict) -> str | None:
    value = memory.get("value")
    if not isinstance(value, dict):
        return None
    title = " ".join(str(value.get("action_title") or "").split()).strip(" .,:;-")
    return title[:300] if title else None


def _reminder_text(memory: dict) -> str:
    """Return structured AI title for new habits, with deterministic legacy fallback."""
    structured = _structured_action_title(memory)
    if structured:
        return structured

    legacy = _legacy_reminder_text(memory)
    text = LEADING_SUBJECT_RE.sub("", legacy, count=1)
    text = LEADING_FILLER_RE.sub("", text, count=1)
    text = HABIT_RECURRENCE_RE.sub(" ", text)
    text = HABIT_CLOCK_RE.sub(" ", text)
    text = LEADING_SUBJECT_RE.sub("", text, count=1)
    text = LEADING_FILLER_RE.sub("", text, count=1)
    text = re.sub(r"\s+", " ", text).strip(" ,.;:-")

    tablet = MEDICINE_TABLET_RE.match(text)
    if tablet:
        tail = re.sub(r"\s+", " ", tablet.group("tail") or "").strip(" ,.;:-")
        text = "Принять таблетку" + (f" {tail}" if tail else "")
    else:
        for pattern, replacement in VERB_REWRITES:
            if pattern.search(text):
                text = pattern.sub(replacement, text, count=1)
                break

    text = re.sub(r"\s+", " ", text).strip(" ,.;:-")
    if not text:
        return legacy
    if text[:1].islower():
        text = text[:1].upper() + text[1:]
    return text[:300]


def _repair_existing_proactive_title(user_id: int, memory: dict) -> bool:
    """Fix titles created by the old raw-habit formatter without touching user edits."""
    decision = get_proactive_decision(user_id, int(memory["memory_id"]), "reminder")
    if not decision or decision.get("status") != "created" or not decision.get("reminder_id"):
        return False

    legacy = _legacy_reminder_text(memory)
    desired = _reminder_text(memory)
    if not desired or desired == legacy:
        return False

    reminder_id = int(decision["reminder_id"])
    with db_lock:
        try:
            row = conn.execute(
                "SELECT text FROM reminders WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                (int(user_id), reminder_id),
            ).fetchone()
            if not row or str(row[0]) != legacy:
                return False
            conn.execute(
                "UPDATE reminders SET text=? WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                (desired, int(user_id), reminder_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    record_proactive_decision(
        user_id,
        int(memory["memory_id"]),
        memory.get("updated_at") or "",
        status="created",
        reminder_id=reminder_id,
        reason="Нормализован короткий заголовок автоматически созданного напоминания.",
        confidence=float(memory.get("confidence") or 0),
        action_type="reminder",
    )
    logger.info("Normalized proactive reminder title user=%s reminder=%s", user_id, reminder_id)
    return True


def _stems(text: str) -> set[str]:
    result = set()
    for token in TOKEN_RE.findall(str(text or "").casefold().replace("ё", "е")):
        if len(token) < 4 or token in STOP_WORDS or token.isdigit():
            continue
        result.add(token[:5] if len(token) >= 6 else token)
    return result


def _same_clock(left: datetime, right: datetime, zone: ZoneInfo) -> bool:
    a = left.astimezone(zone)
    b = right.astimezone(zone)
    return abs((a.hour * 60 + a.minute) - (b.hour * 60 + b.minute)) <= 20


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _find_reminder_coverage(user_id: int, text: str, due: datetime, rule: str, timezone_name: str) -> tuple[str | None, dict | None]:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("Europe/Moscow")
    target_tokens = _stems(text)
    for reminder in list_active_reminders(user_id, limit=300):
        reminder_tokens = _stems(reminder.get("text") or "")
        if target_tokens and reminder_tokens and not (target_tokens & reminder_tokens):
            continue
        current = _parse_datetime(reminder.get("remind_at"))
        if current is None or not _same_clock(due, current, zone):
            continue
        if reminder.get("repeat_rule") == rule:
            return "recurring", reminder
        if not reminder.get("repeat_rule") and abs((current - due.astimezone(timezone.utc)).total_seconds()) <= 45 * 60:
            return "one_time", reminder
    return None, None


def _event_datetime(event: dict, field: str, zone: ZoneInfo) -> datetime | None:
    payload = event.get(field) or {}
    raw = payload.get("dateTime")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _find_calendar_coverage(
    user_id: int,
    title: str,
    start: datetime,
    duration_minutes: int,
    timezone_name: str,
) -> tuple[str | None, dict | None]:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("Europe/Moscow")
    target_tokens = _stems(title)
    window_start = start - timedelta(minutes=30)
    window_end = start + timedelta(minutes=duration_minutes + 30)
    for event in _list_events(user_id, window_start, window_end):
        event_tokens = _stems(event.get("summary") or "")
        if target_tokens and event_tokens and not (target_tokens & event_tokens):
            continue
        event_start = _event_datetime(event, "start", zone)
        event_end = _event_datetime(event, "end", zone)
        if event_start is None or not _same_clock(start, event_start, zone):
            continue
        if event_end is not None:
            actual_duration = max(0, int((event_end - event_start).total_seconds() // 60))
            if abs(actual_duration - duration_minutes) > 30:
                continue
        if event.get("recurringEventId") or event.get("recurrence"):
            return "recurring", event
        return "one_time", event
    return None, None


def _structured_schedule(memory: dict) -> tuple[str, int, int] | None:
    value = memory.get("value")
    if not isinstance(value, dict):
        return None
    schedule = value.get("schedule")
    if not isinstance(schedule, dict):
        return None
    repeat = str(schedule.get("repeat") or "").strip().lower()
    match = STRUCTURED_CLOCK_RE.fullmatch(str(schedule.get("time") or "").strip())
    if repeat not in {"daily", "weekdays", "weekends", "weekly"} or match is None:
        return None
    rule = repeat
    if repeat == "weekly":
        try:
            weekday = int(schedule.get("weekday"))
        except (TypeError, ValueError):
            return None
        if not 0 <= weekday <= 6:
            return None
        rule = f"weekly:{weekday}"
    return rule, int(match.group("hour")), int(match.group("minute"))


def _structured_due(rule: str, hour: int, minute: int, zone: ZoneInfo, now: datetime) -> datetime:
    current = now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if rule == "daily":
        if candidate <= current:
            candidate += timedelta(days=1)
        return candidate
    if rule == "weekdays":
        while candidate <= current or candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate
    if rule == "weekends":
        while candidate <= current or candidate.weekday() < 5:
            candidate += timedelta(days=1)
        return candidate
    target = int(WEEKLY_DAY_RE.fullmatch(rule).group(0).split(":", 1)[1])
    days = (target - current.weekday()) % 7
    candidate = (current + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(days=7)
    return candidate


def _structured_candidate(memory: dict, timezone_name: str, now: datetime) -> dict | None:
    value = memory.get("value")
    if not isinstance(value, dict):
        return None
    title = _structured_action_title(memory)
    schedule = _structured_schedule(memory)
    action_type = str(value.get("action_type") or "").strip().lower()
    try:
        action_confidence = float(value.get("action_confidence"))
    except (TypeError, ValueError):
        return None
    if (
        not title
        or schedule is None
        or action_type not in {"reminder", "calendar_event"}
        or action_confidence < MIN_ACTION_CONFIDENCE
    ):
        return None
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("Europe/Moscow")
    rule, hour, minute = schedule
    candidate = {
        "action_type": action_type,
        "text": title,
        "due": _structured_due(rule, hour, minute, zone, now),
        "repeat_rule": rule,
        "timezone": timezone_name,
        "action_confidence": action_confidence,
    }
    if action_type == "calendar_event":
        try:
            duration_minutes = int(value.get("duration_minutes", 60))
        except (TypeError, ValueError):
            return None
        if not 15 <= duration_minutes <= 480:
            return None
        candidate["duration_minutes"] = duration_minutes
    return candidate


def _candidate(memory: dict, timezone_name: str, now: datetime) -> dict | None:
    if memory.get("kind") != "habit" or float(memory.get("confidence") or 0) < MIN_AUTO_CONFIDENCE:
        return None
    if memory.get("source_type") not in AUTO_SOURCE_TYPES:
        return None

    if isinstance(memory.get("value"), dict):
        return _structured_candidate(memory, timezone_name, now)

    combined = _memory_text(memory)
    if not combined or not _has_explicit_clock(combined):
        return None
    rule = _allowed_repeat_rule(combined)
    if not rule:
        return None
    if _reminder_clock(combined) is None:
        return None
    due = _first_repeat_due(combined, timezone_name, rule, now)
    if due is None:
        return None
    return {
        "action_type": "reminder",
        "text": _reminder_text(memory),
        "due": due,
        "repeat_rule": rule,
        "timezone": timezone_name,
        "action_confidence": 1.0,
    }


def _calendar_rrule(rule: str) -> str:
    if rule == "daily":
        return "RRULE:FREQ=DAILY"
    if rule == "weekdays":
        return "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    if rule == "weekends":
        return "RRULE:FREQ=WEEKLY;BYDAY=SA,SU"
    match = WEEKLY_DAY_RE.fullmatch(rule)
    if match:
        weekday = int(rule.split(":", 1)[1])
        return f"RRULE:FREQ=WEEKLY;BYDAY={WEEKDAY_CODES[weekday]}"
    raise ValueError("Unsupported calendar recurrence rule")


def _create_calendar_action(user_id: int, memory: dict, candidate: dict) -> dict:
    timezone_name = candidate["timezone"]
    start = candidate["due"]
    end = start + timedelta(minutes=int(candidate["duration_minutes"]))
    category, color_id = _detect_category(candidate["text"], get_category_colors(user_id))
    event = {
        "summary": candidate["text"],
        "description": f"AI Smart Planner category: {category}",
        "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
        "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
        "recurrence": [_calendar_rrule(candidate["repeat_rule"])],
        "extendedProperties": {
            "private": {"smartPlannerProactiveMemory": str(memory["memory_id"])}
        },
    }
    if color_id:
        event["colorId"] = color_id
    return _create_event(user_id, event)


def evaluate_user_proactive(user_id: int, *, now: datetime | None = None) -> dict:
    prefs = get_assistant_preferences(user_id)
    reminders_enabled = bool(prefs.get("proactive_reminders_enabled", False))
    calendar_enabled = bool(prefs.get("proactive_calendar_events_enabled", False))
    if not reminders_enabled and not calendar_enabled:
        return {"created": 0, "created_reminders": 0, "created_calendar_events": 0, "covered": 0, "skipped": 0}

    timezone_name = get_user_timezone(user_id) or "Europe/Moscow"
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = "Europe/Moscow"
        zone = ZoneInfo(timezone_name)
    current = now or datetime.now(zone)
    current = current.astimezone(zone) if current.tzinfo else current.replace(tzinfo=zone)

    created = created_reminders = created_events = covered = skipped = 0
    for memory in list_memories(user_id, limit=200):
        if memory.get("kind") != "habit" or float(memory.get("confidence") or 0) < MIN_AUTO_CONFIDENCE:
            continue
        if memory.get("source_type") not in AUTO_SOURCE_TYPES:
            continue
        try:
            _repair_existing_proactive_title(user_id, memory)
        except Exception:
            logger.exception("Could not normalize proactive reminder title for user %s memory %s", user_id, memory.get("memory_id"))

        candidate = _candidate(memory, timezone_name, current)
        if not candidate:
            if should_evaluate_memory(user_id, memory, "none"):
                record_proactive_decision(
                    user_id,
                    memory["memory_id"],
                    memory.get("updated_at") or "",
                    status="not_actionable",
                    reason="ИИ не определил безопасный тип автодействия и точное расписание с достаточной уверенностью.",
                    confidence=float(memory.get("confidence") or 0),
                    action_type="none",
                )
                skipped += 1
            continue

        action_type = candidate["action_type"]
        if action_type == "reminder" and not reminders_enabled:
            continue
        if action_type == "calendar_event" and not calendar_enabled:
            continue
        if not should_evaluate_memory(user_id, memory, action_type):
            continue

        if action_type == "reminder":
            coverage, existing = _find_reminder_coverage(
                user_id,
                candidate["text"],
                candidate["due"],
                candidate["repeat_rule"],
                timezone_name,
            )
            if coverage == "recurring":
                record_proactive_decision(
                    user_id,
                    memory["memory_id"],
                    memory.get("updated_at") or "",
                    status="covered",
                    reminder_id=existing.get("reminder_id") if existing else None,
                    reason="Подходящее повторяющееся напоминание уже существует.",
                    confidence=float(memory.get("confidence") or 0),
                    action_type="reminder",
                )
                covered += 1
                continue
            if coverage == "one_time":
                candidate["due"] = next_repeat_at(candidate["due"], candidate["repeat_rule"], timezone_name)
            try:
                reminder = create_reminder(
                    user_id,
                    candidate["text"],
                    candidate["due"],
                    repeat_rule=candidate["repeat_rule"],
                    repeat_timezone=timezone_name,
                )
            except Exception as exc:
                record_proactive_decision(
                    user_id,
                    memory["memory_id"],
                    memory.get("updated_at") or "",
                    status="failed",
                    reason=f"Не удалось создать напоминание: {type(exc).__name__}",
                    confidence=float(memory.get("confidence") or 0),
                    action_type="reminder",
                )
                logger.exception("Proactive reminder creation failed for user %s memory %s", user_id, memory["memory_id"])
                continue
            record_proactive_decision(
                user_id,
                memory["memory_id"],
                memory.get("updated_at") or "",
                status="created",
                reminder_id=reminder["reminder_id"],
                reason="Создано напоминание по явно указанной привычке с точным временем и высокой уверенностью.",
                confidence=float(memory.get("confidence") or 0),
                action_type="reminder",
            )
            created += 1
            created_reminders += 1
            continue

        coverage, existing = _find_calendar_coverage(
            user_id,
            candidate["text"],
            candidate["due"],
            int(candidate["duration_minutes"]),
            timezone_name,
        )
        if coverage == "recurring":
            record_proactive_decision(
                user_id,
                memory["memory_id"],
                memory.get("updated_at") or "",
                status="covered",
                calendar_event_id=(existing or {}).get("recurringEventId") or (existing or {}).get("id"),
                reason="Подходящее повторяющееся событие уже есть в календаре.",
                confidence=float(memory.get("confidence") or 0),
                action_type="calendar_event",
            )
            covered += 1
            continue
        if coverage == "one_time":
            candidate["due"] = next_repeat_at(candidate["due"], candidate["repeat_rule"], timezone_name)

        try:
            event = _create_calendar_action(user_id, memory, candidate)
        except Exception as exc:
            record_proactive_decision(
                user_id,
                memory["memory_id"],
                memory.get("updated_at") or "",
                status="failed",
                reason=f"Не удалось создать событие календаря: {type(exc).__name__}",
                confidence=float(memory.get("confidence") or 0),
                action_type="calendar_event",
            )
            logger.exception("Proactive calendar creation failed for user %s memory %s", user_id, memory["memory_id"])
            continue
        record_proactive_decision(
            user_id,
            memory["memory_id"],
            memory.get("updated_at") or "",
            status="created",
            calendar_event_id=event.get("id"),
            reason="Создано событие календаря: привычка занимает интервал времени и должна блокировать занятость.",
            confidence=float(memory.get("confidence") or 0),
            action_type="calendar_event",
        )
        created += 1
        created_events += 1

    return {
        "created": created,
        "created_reminders": created_reminders,
        "created_calendar_events": created_events,
        "covered": covered,
        "skipped": skipped,
    }


def _enabled_user_ids() -> list[int]:
    with db_lock:
        rows = conn.execute("SELECT user_id FROM users ORDER BY user_id LIMIT 1000").fetchall()
    result = []
    for row in rows:
        user_id = int(row[0])
        try:
            prefs = get_assistant_preferences(user_id)
            if prefs.get("proactive_reminders_enabled", False) or prefs.get("proactive_calendar_events_enabled", False):
                result.append(user_id)
        except Exception:
            logger.exception("Could not read proactive preferences for user %s", user_id)
    return result


def run_proactive_cycle() -> dict:
    totals = {
        "users": 0,
        "created": 0,
        "created_reminders": 0,
        "created_calendar_events": 0,
        "covered": 0,
        "skipped": 0,
    }
    for user_id in _enabled_user_ids():
        try:
            result = evaluate_user_proactive(user_id)
        except Exception:
            logger.exception("Proactive cycle failed for user %s", user_id)
            continue
        totals["users"] += 1
        for key in ("created", "created_reminders", "created_calendar_events", "covered", "skipped"):
            totals[key] += int(result.get(key, 0))
    return totals


def _worker_loop() -> None:
    interval = _env_int("AI_PROACTIVE_INTERVAL_SECONDS", 60, 15, 3600)
    while True:
        try:
            run_proactive_cycle()
        except Exception:
            logger.exception("Proactive worker iteration failed")
        time.sleep(interval)


def start_proactive_worker() -> None:
    global _worker_started
    if not proactive_worker_enabled():
        logger.info("Proactive worker disabled")
        return
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        thread = threading.Thread(target=_worker_loop, name="proactive-worker", daemon=True)
        thread.start()
        logger.info("Proactive worker started")


__all__ = [
    "evaluate_user_proactive",
    "proactive_status",
    "run_proactive_cycle",
    "start_proactive_worker",
]
