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
from core.db import conn, db_lock, get_user_timezone
from core.memory_store import list_memories
from core.proactive_store import (
    proactive_status,
    record_proactive_decision,
    should_evaluate_memory,
)
from core.reminder_recurrence import next_repeat_at
from core.reminder_store import create_reminder, list_active_reminders
from modules.reminders import _first_repeat_due, _reminder_clock, _repeat_rule

logger = logging.getLogger(__name__)

MIN_AUTO_CONFIDENCE = 0.90
AUTO_SOURCE_TYPES = {"note", "voice_transcript"}
ALLOWED_REPEAT_RULES = {"daily", "weekdays", "weekends"}
WEEKLY_DAY_RE = re.compile(r"^weekly:[0-6]$")
CLOCK_RE = re.compile(
    r"(?:\b(?:в|к)\s+)?(?:[01]?\d|2[0-3])[:.]\s*[0-5]\d\b|"
    r"\b(?:в|к)\s+(?:[01]?\d|2[0-3])\b",
    re.IGNORECASE,
)
VAGUE_TIME_RE = re.compile(r"\b(?:около|примерно|приблизительно|после|до|ближе\s+к)\s*$", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
STOP_WORDS = {
    "каждый", "каждая", "каждое", "каждую", "каждые", "день", "вечер", "утро", "ночь",
    "будням", "выходным", "неделю", "недели", "пользователь", "обычно", "всегда", "нужно",
    "надо", "после", "перед", "около", "примерно", "напомнить", "напоминание",
}

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


def _memory_text(memory: dict) -> str:
    value = str(memory.get("value") or "").strip()
    evidence = str(memory.get("evidence") or "").strip()
    return " ".join(part for part in (value, evidence) if part)


def _reminder_text(memory: dict) -> str:
    text = " ".join(str(memory.get("value") or "").split()).strip()
    if not text:
        text = "Не забудь о привычке"
    text = re.sub(r"^пользователь\s+", "", text, flags=re.IGNORECASE)
    return text[:300].strip(" .")


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


def _parse_reminder_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _find_coverage(user_id: int, text: str, due: datetime, rule: str, timezone_name: str) -> tuple[str | None, dict | None]:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("Europe/Moscow")
    target_tokens = _stems(text)
    for reminder in list_active_reminders(user_id, limit=300):
        reminder_tokens = _stems(reminder.get("text") or "")
        if target_tokens and reminder_tokens and not (target_tokens & reminder_tokens):
            continue
        current = _parse_reminder_time(reminder.get("remind_at"))
        if current is None or not _same_clock(due, current, zone):
            continue
        if reminder.get("repeat_rule") == rule:
            return "recurring", reminder
        if not reminder.get("repeat_rule") and abs((current - due.astimezone(timezone.utc)).total_seconds()) <= 45 * 60:
            return "one_time", reminder
    return None, None


def _candidate(memory: dict, timezone_name: str, now: datetime) -> dict | None:
    if memory.get("kind") != "habit" or float(memory.get("confidence") or 0) < MIN_AUTO_CONFIDENCE:
        return None
    if memory.get("source_type") not in AUTO_SOURCE_TYPES:
        return None
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
        "text": _reminder_text(memory),
        "due": due,
        "repeat_rule": rule,
        "timezone": timezone_name,
    }


def evaluate_user_proactive(user_id: int, *, now: datetime | None = None) -> dict:
    prefs = get_assistant_preferences(user_id)
    if not prefs.get("proactive_reminders_enabled", False):
        return {"created": 0, "covered": 0, "skipped": 0}

    timezone_name = get_user_timezone(user_id) or "Europe/Moscow"
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = "Europe/Moscow"
        zone = ZoneInfo(timezone_name)
    current = now or datetime.now(zone)
    current = current.astimezone(zone) if current.tzinfo else current.replace(tzinfo=zone)

    created = covered = skipped = 0
    for memory in list_memories(user_id, limit=200):
        if memory.get("kind") != "habit" or float(memory.get("confidence") or 0) < MIN_AUTO_CONFIDENCE:
            continue
        if memory.get("source_type") not in AUTO_SOURCE_TYPES:
            continue
        if not should_evaluate_memory(user_id, memory):
            continue

        candidate = _candidate(memory, timezone_name, current)
        if not candidate:
            record_proactive_decision(
                user_id,
                memory["memory_id"],
                memory.get("updated_at") or "",
                status="not_actionable",
                reason="Нет точного времени и безопасного повторяющегося расписания для автодействия.",
                confidence=float(memory.get("confidence") or 0),
            )
            skipped += 1
            continue

        coverage, existing = _find_coverage(
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
            )
            covered += 1
            continue
        if coverage == "one_time":
            candidate["due"] = next_repeat_at(
                candidate["due"], candidate["repeat_rule"], timezone_name
            )

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
            )
            logger.exception("Proactive reminder creation failed for user %s memory %s", user_id, memory["memory_id"])
            continue

        reason = "Создано по явно указанной привычке с точным временем и высокой уверенностью."
        record_proactive_decision(
            user_id,
            memory["memory_id"],
            memory.get("updated_at") or "",
            status="created",
            reminder_id=reminder["reminder_id"],
            reason=reason,
            confidence=float(memory.get("confidence") or 0),
        )
        created += 1
    return {"created": created, "covered": covered, "skipped": skipped}


def _enabled_user_ids() -> list[int]:
    with db_lock:
        rows = conn.execute("SELECT user_id FROM users ORDER BY user_id LIMIT 1000").fetchall()
    result = []
    for row in rows:
        user_id = int(row[0])
        try:
            if get_assistant_preferences(user_id).get("proactive_reminders_enabled", False):
                result.append(user_id)
        except Exception:
            logger.exception("Could not read proactive preferences for user %s", user_id)
    return result


def run_proactive_cycle() -> dict:
    totals = {"users": 0, "created": 0, "covered": 0, "skipped": 0}
    for user_id in _enabled_user_ids():
        try:
            result = evaluate_user_proactive(user_id)
        except Exception:
            logger.exception("Proactive cycle failed for user %s", user_id)
            continue
        totals["users"] += 1
        for key in ("created", "covered", "skipped"):
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
