"""Opt-in review delivery and quiet-hour preferences."""

from __future__ import annotations

from datetime import datetime, timedelta, time as dt_time, timezone
import json
import re
from zoneinfo import ZoneInfo

from core.db import conn, db_lock, get_user_timezone
from core.feature_access import has_ai_access

DEFAULTS = {
    "morning_enabled": False, "morning_time": "08:00",
    "evening_enabled": False, "evening_time": "20:00",
    "quiet_enabled": False, "quiet_start": "22:00", "quiet_end": "08:00",
    "proactive_reminders_enabled": False,
    "proactive_calendar_events_enabled": False,
}
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def init_assistant_preferences():
    with db_lock:
        conn.execute("CREATE TABLE IF NOT EXISTS assistant_preferences (user_id INTEGER PRIMARY KEY, settings_json TEXT NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS review_deliveries (
            user_id INTEGER NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL,
            phase TEXT NOT NULL, attempted_at TEXT NOT NULL,
            PRIMARY KEY(user_id,day,kind)
        )""")
        conn.commit()


def get_assistant_preferences(user_id: int) -> dict:
    with db_lock:
        row = conn.execute("SELECT settings_json FROM assistant_preferences WHERE user_id=?", (user_id,)).fetchone()
    values = {**DEFAULTS, **(json.loads(row[0]) if row else {})}
    if not has_ai_access(user_id):
        values["proactive_reminders_enabled"] = False
        values["proactive_calendar_events_enabled"] = False
    return values


def save_assistant_preferences(user_id: int, changes: dict) -> dict:
    if not isinstance(changes, dict) or set(changes) - set(DEFAULTS):
        raise ValueError("Неизвестные настройки уведомлений")
    values = get_assistant_preferences(user_id)
    for key, value in changes.items():
        if key.endswith("_enabled"):
            if not isinstance(value, bool):
                raise ValueError("Включение должно быть true или false")
        elif not isinstance(value, str) or not TIME_RE.fullmatch(value):
            raise ValueError("Время должно быть в формате HH:MM")
        values[key] = value
    if values["quiet_enabled"] and values["quiet_start"] == values["quiet_end"]:
        raise ValueError("Начало и конец тихих часов должны различаться")
    with db_lock:
        conn.execute("INSERT INTO assistant_preferences VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json",
                     (user_id, json.dumps(values)))
        conn.commit()
    return get_assistant_preferences(user_id)


def quiet_until(user_id: int, now: datetime) -> datetime | None:
    if now.tzinfo is None:
        raise ValueError("Timezone-aware time required")
    prefs = get_assistant_preferences(user_id)
    if not prefs["quiet_enabled"]:
        return None
    zone = ZoneInfo(get_user_timezone(user_id) or "Europe/Moscow")
    local = now.astimezone(zone)
    start = dt_time.fromisoformat(prefs["quiet_start"])
    end = dt_time.fromisoformat(prefs["quiet_end"])
    current = local.time()
    quiet = start <= current < end if start < end else current >= start or current < end
    if not quiet:
        return None
    day = local.date() + (timedelta(days=1) if start > end and current >= start else timedelta(0))
    candidate = datetime.combine(day, end, tzinfo=zone).astimezone(timezone.utc)
    return max(candidate, now.astimezone(timezone.utc) + timedelta(seconds=1))


def review_history(user_id: int) -> list[dict]:
    with db_lock:
        rows = conn.execute(
            "SELECT day,kind,phase,attempted_at FROM review_deliveries WHERE user_id=? ORDER BY day DESC,kind LIMIT 14",
            (user_id,),
        ).fetchall()
    return [dict(zip(("day", "kind", "phase", "attempted_at"), row)) for row in rows]


init_assistant_preferences()
