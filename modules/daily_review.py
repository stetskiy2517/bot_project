"""Deterministic day reviews with tasks, travel and cached email planning signals."""

from __future__ import annotations

from datetime import datetime, timedelta, time as dt_time, timezone
import logging
from zoneinfo import ZoneInfo

from core.assistant_preferences import get_assistant_preferences, quiet_until
from core.db import conn, db_lock, get_calendar_preferences, get_user_timezone
from core.email_auto_store import email_auto_enabled, recent_auto_plans
from core.library_store import list_saved_reminders
from core.task_planner_store import list_planner_tasks, task_summary
from modules.calendar_availability import find_free_slots, _parse_hhmm
from modules.calendar_user import _list_events, _event_start

logger = logging.getLogger(__name__)


def _is_travel_event(event: dict) -> bool:
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    return private.get("smartPlannerType") == "travel"


def _parse_task_due(value: object, zone: ZoneInfo) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _task_lines(user_id: int, zone: ZoneInfo, start: datetime, end: datetime, *, kind: str) -> list[str]:
    summary = task_summary(user_id, now=start)
    tasks = list_planner_tasks(user_id, status="open", limit=200)
    relevant = []
    for task in tasks:
        due = _parse_task_due(task.get("due_at"), zone)
        if due is None:
            if kind == "morning" and task.get("priority") == "high":
                relevant.append((datetime.max.replace(tzinfo=zone), task))
            continue
        if due < end or (kind == "evening" and due < start + timedelta(days=1)):
            relevant.append((due, task))
    relevant.sort(key=lambda item: (0 if item[1].get("priority") == "high" else 1, item[0]))
    lines = [
        f"Задачи: {summary['open']} открыто, {summary['overdue']} просрочено, {summary['scheduled']} уже в календаре."
    ]
    for due, task in relevant[:3]:
        suffix = ""
        if due.year < 9999:
            suffix = f" · до {due:%H:%M}" if due.date() == start.date() else f" · до {due:%d.%m %H:%M}"
        estimate = f" · {task['estimate_minutes']} мин" if task.get("estimate_minutes") else ""
        lines.append(f"• {task['title']}{suffix}{estimate}")
    return lines


def _email_lines(user_id: int) -> list[str]:
    """Read cached background findings; never spend fresh AI tokens during a review."""
    if not email_auto_enabled(user_id):
        return []
    try:
        plans = recent_auto_plans(user_id, hours=48, limit=8)
    except Exception:
        logger.exception("Cached email planning unavailable for user %s", user_id)
        return ["Почта: временно не удалось прочитать результаты авторазбора."]
    actions = []
    seen = set()
    for plan in plans:
        for action in plan.get("actions") or []:
            if not isinstance(action, dict):
                continue
            key = (str(action.get("action_type") or ""), str(action.get("title") or ""), str(action.get("due_at") or ""))
            if key in seen:
                continue
            seen.add(key)
            actions.append(action)
    if not actions:
        return []
    lines = [f"Почта: авторазбор нашёл {len(actions)} актуальных сигналов за последние 48 часов."]
    for action in actions[:3]:
        title = str(action.get("title") or "Действие")[:140]
        if action.get("auto_created"):
            lines.append(f"• {title} · уже добавлено автоматически")
        elif action.get("already_in_calendar"):
            lines.append(f"• {title} · уже есть в календаре")
        else:
            lines.append(f"• Предложение: {title}")
    if any(not item.get("auto_created") and not item.get("already_in_calendar") for item in actions):
        lines.append("Открой почту в приложении, чтобы подтвердить остальные предложения.")
    return lines


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

    travel_events = [event for event in events if _is_travel_event(event)]
    regular_events = [event for event in events if not _is_travel_event(event)]
    saved_reminders = list_saved_reminders(user_id)
    reminders = [
        item for item in saved_reminders
        if item["status"] != "completed" and datetime.fromisoformat(item["remind_at"]) < end
    ]
    lines = [("Обзор дня" if kind == "morning" else "Вечерний обзор") + f" · {local:%d.%m}"]
    if not calendar_ok:
        lines.append("Календарь временно недоступен. Занятость не проверена.")
    elif not regular_events:
        lines.append("В календаре на этот день встреч нет.")
    else:
        lines.append(f"Событий в календаре: {len(regular_events)}.")
        selected = regular_events if kind == "evening" else [
            event for event in regular_events if (_event_start(event, zone_name)[0] or local) >= local
        ]
        for event in selected[:3]:
            when, all_day = _event_start(event, zone_name)
            label = "весь день" if all_day else (when.strftime("%H:%M") if when else "время не указано")
            lines.append(f"{label} · {str(event.get('summary', 'Событие'))[:100]}")
    if calendar_ok and travel_events:
        upcoming_travel = [event for event in travel_events if (_event_start(event, zone_name)[0] or local) >= local]
        for event in upcoming_travel[:1]:
            when, _ = _event_start(event, zone_name)
            lines.append(f"Дорога: выезд {when:%H:%M} · {str(event.get('location') or event.get('summary') or '')[:120]}")
    if calendar_ok and kind == "morning" and slots:
        lines.append("Окна по 30 минут: " + ", ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in slots))

    try:
        lines.extend(_task_lines(user_id, zone, start, end, kind=kind))
    except Exception:
        logger.exception("Review tasks unavailable for user %s", user_id)
        lines.append("Задачи: временно не удалось проверить.")

    lines.append(f"Незавершённых напоминаний до конца дня: {len(reminders)}" + (" (первые 500 записей)." if len(saved_reminders) >= 500 else "."))
    for reminder in reminders[:3]:
        lines.append("• " + reminder["text"][:100])
    if kind == "evening" and reminders:
        lines.append("Отметь выполненное, перенеси или оставь как есть.")
    if kind == "morning":
        lines.extend(_email_lines(user_id))
    return {
        "kind": kind,
        "date": str(local.date()),
        "calendar_ok": calendar_ok,
        "text": "\n".join(lines),
        "reminders": [
            {"id": item["reminder_id"], "text": item["text"], "remind_at": item["remind_at"]}
            for item in reminders[:20]
        ],
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
