"""Deterministic day reviews and opt-in delivery receipts."""

from __future__ import annotations

from datetime import datetime, timedelta, time as dt_time, timezone
import logging
from zoneinfo import ZoneInfo

from core.assistant_preferences import get_assistant_preferences, quiet_until
from core.db import conn, db_lock, get_calendar_preferences, get_user_timezone
from core.library_store import list_saved_reminders
from modules.calendar_availability import find_free_slots, _parse_hhmm
from modules.calendar_user import _list_events, _event_start

logger = logging.getLogger(__name__)


def build_day_review(user_id: int, kind: str = "morning", *, now: datetime | None = None) -> dict:
    if kind not in {"morning", "evening"}:
        raise ValueError("Неизвестный вид обзора")
    zone_name = get_user_timezone(user_id) or "Europe/Moscow"
    zone = ZoneInfo(zone_name)
    local = (now or datetime.now(timezone.utc)).astimezone(zone)
    start = datetime.combine(local.date(), dt_time.min, tzinfo=zone)
    end = start + timedelta(days=1)
    calendar_ok = True
    events, slots = [], []
    try:
        events = _list_events(user_id, start, end)
        prefs = get_calendar_preferences(user_id)
        slots = find_free_slots(
            events, zone_name, start, end, timedelta(minutes=30),
            work_start=_parse_hhmm(prefs["work_start"]), work_end=_parse_hhmm(prefs["work_end"]),
            work_days=prefs["work_days"], buffer=timedelta(minutes=prefs["buffer_minutes"]),
            now=local, limit=3,
        )
    except Exception as exc:
        calendar_ok = False
        logger.warning("Review calendar unavailable for user %s (%s)", user_id, type(exc).__name__)
    saved_reminders = list_saved_reminders(user_id)
    reminders = [item for item in saved_reminders
                 if item["status"] != "completed" and datetime.fromisoformat(item["remind_at"]) < end]
    lines = [("Обзор дня" if kind == "morning" else "Вечерний обзор") + f" · {local:%d.%m}"]
    if not calendar_ok:
        lines.append("Календарь временно недоступен. Занятость не проверена.")
    elif not events:
        lines.append("В календаре на этот день встреч нет.")
    else:
        lines.append(f"Событий в календаре: {len(events)}.")
        selected = events if kind == "evening" else [
            event for event in events if (_event_start(event, zone_name)[0] or local) >= local
        ]
        for event in selected[:3]:
            when, all_day = _event_start(event, zone_name)
            label = "весь день" if all_day else (when.strftime("%H:%M") if when else "время не указано")
            lines.append(f"{label} · {str(event.get('summary', 'Событие'))[:100]}")
    if calendar_ok and kind == "morning" and slots:
        lines.append("Окна по 30 минут: " + ", ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in slots))
    lines.append(f"Незавершённых напоминаний до конца дня: {len(reminders)}" + (" (первые 500 записей)." if len(saved_reminders) >= 500 else "."))
    for reminder in reminders[:3]:
        lines.append("• " + reminder["text"][:100])
    if kind == "evening" and reminders:
        lines.append("Отметь выполненное, перенеси или оставь как есть.")
    return {
        "kind": kind, "date": str(local.date()), "calendar_ok": calendar_ok,
        "text": "\n".join(lines),
        "reminders": [{"id": item["reminder_id"], "text": item["text"], "remind_at": item["remind_at"]} for item in reminders[:20]],
    }


def deliver_reviews_for_user(user_id: int, sender, *, now: datetime | None = None) -> int:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if quiet_until(user_id, current):
        return 0
    prefs = get_assistant_preferences(user_id)
    zone = ZoneInfo(get_user_timezone(user_id) or "Europe/Moscow")
    local = current.astimezone(zone)
    delivered = 0
    for kind in ("morning", "evening"):
        if not prefs[kind + "_enabled"]:
            continue
        scheduled = datetime.combine(local.date(), dt_time.fromisoformat(prefs[kind + "_time"]), tzinfo=zone)
        scheduled = quiet_until(user_id, scheduled) or scheduled
        if scheduled.astimezone(zone).date() != local.date() or current < scheduled:
            continue
        phase = "missed" if current - scheduled > timedelta(hours=2) else "sending"
        with db_lock:
            conn.execute("DELETE FROM review_deliveries WHERE day<?", (str(local.date() - timedelta(days=30)),))
            cur = conn.execute("INSERT OR IGNORE INTO review_deliveries VALUES (?,?,?,?,?)",
                               (user_id, str(local.date()), kind, phase, current.isoformat()))
            conn.commit()
        if not cur.rowcount or phase == "missed":
            continue
        try:
            review = build_day_review(user_id, kind, now=current)
            result = sender(user_id, review["text"], f"review-{kind}-{local.date()}")
            phase = "accepted" if result else "failed"
        except Exception as exc:
            phase = "failed"
            logger.warning("Review delivery failed for user %s (%s)", user_id, type(exc).__name__)
        with db_lock:
            conn.execute("UPDATE review_deliveries SET phase=? WHERE user_id=? AND day=? AND kind=?",
                         (phase, user_id, str(local.date()), kind))
            conn.commit()
        delivered += phase == "accepted"
    return delivered
