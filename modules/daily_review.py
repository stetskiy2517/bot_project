"""Daily briefings: deterministic free summary with optional paid AI presentation."""

from __future__ import annotations

from datetime import datetime, timedelta, time as dt_time, timezone
import hashlib
import logging
from zoneinfo import ZoneInfo

from core.assistant_preferences import get_assistant_preferences, quiet_until
from core.attention_store import upsert_attention_item
from core.db import conn, db_lock, get_calendar_preferences, get_user_timezone
from core.email_auto_store import email_auto_enabled, recent_auto_plans
from core.feature_access import has_ai_access
from core.library_store import list_saved_reminders
from core.task_planner_store import list_planner_tasks, task_summary
from integrations.ai import AIError, complete, is_ai_available
from modules.calendar_availability import find_free_slots, _parse_hhmm
from modules.calendar_user import _list_events, _event_start

logger = logging.getLogger(__name__)

AI_REVIEW_PROMPT_VERSION = "daily-review-v1"
AI_REVIEW_MAX_CHARS = 1800


def _init_ai_review_cache() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS daily_review_ai_cache (
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                kind TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id, day, kind)
            )"""
        )
        conn.commit()


def _review_title(kind: str) -> str:
    return "Утренняя сводка" if kind == "morning" else "Вечерний разбор"


def _plural_ru(value: int, one: str, few: str, many: str) -> str:
    number = abs(int(value)) % 100
    if 11 <= number <= 14:
        return many
    last = number % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def _strip_review_heading(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    first = lines[0]
    if first in {"Утренняя сводка", "Вечерний разбор"} or first.startswith("Обзор дня ·") or first.startswith("Вечерний обзор ·"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _merge_slot_ranges(slots: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not slots:
        return []
    ordered = sorted(slots, key=lambda item: item[0])
    merged: list[tuple[datetime, datetime]] = []
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            if end > current_end:
                current_end = end
            continue
        merged.append((current_start, current_end))
        current_start, current_end = start, end
    merged.append((current_start, current_end))
    return merged


def _format_free_slots(slots: list[tuple[datetime, datetime]]) -> str:
    merged = _merge_slot_ranges(slots)
    if not merged:
        return ""
    ranges = ", ".join(f"{start:%H:%M}–{end:%H:%M}" for start, end in merged[:3])
    prefix = "Ближайшее свободное время" if len(merged) == 1 else "Ближайшие свободные окна"
    return f"{prefix}: {ranges}."


def _ai_source_hash(text: str) -> str:
    payload = f"{AI_REVIEW_PROMPT_VERSION}\n{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _cached_ai_review(user_id: int, day: str, kind: str, source_hash: str) -> str | None:
    with db_lock:
        row = conn.execute(
            "SELECT text FROM daily_review_ai_cache WHERE user_id=? AND day=? AND kind=? AND source_hash=?",
            (int(user_id), day, kind, source_hash),
        ).fetchone()
    return str(row[0]).strip() if row and row[0] else None


def _store_ai_review(user_id: int, day: str, kind: str, source_hash: str, text: str) -> None:
    cutoff = str(datetime.now(timezone.utc).date() - timedelta(days=14))
    with db_lock:
        conn.execute("DELETE FROM daily_review_ai_cache WHERE day<?", (cutoff,))
        conn.execute(
            "INSERT INTO daily_review_ai_cache (user_id,day,kind,source_hash,text,created_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id,day,kind) DO UPDATE SET "
            "source_hash=excluded.source_hash,text=excluded.text,created_at=excluded.created_at",
            (int(user_id), day, kind, source_hash, text, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def _normalize_ai_review(kind: str, value: object) -> str:
    title = _review_title(kind)
    text = str(value or "").strip().replace("```text", "").replace("```", "").strip()
    body = _strip_review_heading(text)
    if not body:
        return ""
    body = body[:AI_REVIEW_MAX_CHARS].rstrip()
    return f"{title}\n{body}"


def _enhance_review_text(user_id: int, kind: str, day: str, free_text: str) -> tuple[str, str]:
    """Return AI presentation only for entitled users; deterministic text is always the fallback."""
    if not has_ai_access(user_id) or not is_ai_available():
        return free_text, "free"

    source_hash = _ai_source_hash(free_text)
    cached = _cached_ai_review(user_id, day, kind, source_hash)
    if cached:
        return cached, "ai"

    title = _review_title(kind)
    greeting = "Доброе утро" if kind == "morning" else "Добрый вечер"
    system = (
        "Ты персональный секретарь. Перепиши фактическую сводку естественным, спокойным русским языком. "
        "Используй ТОЛЬКО факты из исходной сводки: не придумывай события, причины, места, привычки, "
        "сроки, приоритеты или состояние пользователя. Можно расставить акценты по времени, просрочке, "
        "количеству дел и свободным окнам, но нельзя добавлять факты. Пиши как внимательный помощник, "
        "а не как отчёт системы. Не используй слова «пользователь», «данные показывают», «ИИ». "
        "Сохраняй конкретные названия дел и время. Напоминания называй делами или задачами с уведомлением. "
        "Ответ должен быть коротким: 3–6 предложений или коротких абзацев, без Markdown-заголовков. "
        f"Первая строка ответа должна быть строго «{title}». Текст после неё начни с «{greeting}»."
    )
    user = (
        "Вот проверенная сводка приложения. Сделай её живой и полезной, ничего не добавляя от себя:\n\n"
        + free_text
    )
    try:
        enhanced = complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.45,
            max_tokens=360,
        )
        normalized = _normalize_ai_review(kind, enhanced)
        if not normalized:
            return free_text, "free"
        _store_ai_review(user_id, day, kind, source_hash, normalized)
        return normalized, "ai"
    except AIError as exc:
        logger.warning("AI daily review unavailable for user %s: %s", user_id, type(exc).__name__)
    except Exception:
        logger.exception("Unexpected AI daily review failure for user %s", user_id)
    return free_text, "free"


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

    open_count = int(summary.get("open") or 0)
    overdue_count = int(summary.get("overdue") or 0)
    scheduled_count = int(summary.get("scheduled") or 0)
    if open_count == 0:
        lead = "Открытых задач сейчас нет."
    elif overdue_count:
        lead = f"По задачам: открыто {open_count}, из них просрочено {overdue_count}."
    else:
        lead = f"По задачам всё спокойно: открыто {open_count}, просроченных нет."
    if scheduled_count:
        lead = lead.rstrip(".") + f" В календаре уже {scheduled_count}."
    lines = [lead]

    for due, task in relevant[:3]:
        suffix = ""
        if due.year < 9999:
            suffix = f" · до {due:%H:%M}" if due.date() == start.date() else f" · до {due:%d.%m %H:%M}"
        estimate = f" · {task['estimate_minutes']} мин" if task.get("estimate_minutes") else ""
        lines.append(f"• {task['title']}{suffix}{estimate}")
    return lines


def _email_lines(user_id: int) -> list[str]:
    """Use only cached background findings; rendering a review never starts a new email AI request."""
    if not email_auto_enabled(user_id):
        return []
    try:
        plans = recent_auto_plans(user_id, hours=48, limit=8)
    except Exception:
        logger.exception("Cached email planning unavailable for user %s", user_id)
        return ["По почте сейчас не удалось прочитать результаты последнего разбора."]
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
    lines = [f"По почте есть {len(actions)} {_plural_ru(len(actions), 'актуальный сигнал', 'актуальных сигнала', 'актуальных сигналов')}. "]
    lines[0] = lines[0].strip()
    for action in actions[:3]:
        title = str(action.get("title") or "Действие")[:140]
        if action.get("auto_created"):
            lines.append(f"• {title} · уже добавлено")
        elif action.get("already_in_calendar"):
            lines.append(f"• {title} · уже в календаре")
        else:
            lines.append(f"• Стоит проверить: {title}")
    if any(not item.get("auto_created") and not item.get("already_in_calendar") for item in actions):
        lines.append("Остальные предложения можно подтвердить в разделе почты.")
    return lines


def _tomorrow_lines(user_id: int, zone_name: str, start: datetime) -> list[str]:
    tomorrow_start = start + timedelta(days=1)
    tomorrow_end = tomorrow_start + timedelta(days=1)
    try:
        events = _list_events(user_id, tomorrow_start, tomorrow_end)
    except Exception as exc:
        logger.warning("Tomorrow calendar preview unavailable for user %s (%s)", user_id, type(exc).__name__)
        return ["Завтра: календарь пока не удалось проверить."]

    regular = []
    travel = []
    for event in events:
        when, all_day = _event_start(event, zone_name)
        if when is None:
            continue
        target = travel if _is_travel_event(event) else regular
        target.append((when, all_day, event))
    regular.sort(key=lambda item: item[0])
    travel.sort(key=lambda item: item[0])

    if not regular and not travel:
        return ["Завтра: встреч в календаре пока нет."]

    lines = ["Завтра:"]
    for when, all_day, event in regular[:2]:
        label = "весь день" if all_day else when.strftime("%H:%M")
        lines.append(f"• {label} · {str(event.get('summary') or 'Событие')[:100]}")
    if travel:
        when, _, event = travel[0]
        lines.append(f"• Выезд {when:%H:%M} · {str(event.get('location') or event.get('summary') or 'дорога')[:100]}")
    return lines


def _review_due_at(user_id: int, kind: str, current: datetime) -> datetime | None:
    prefs = get_assistant_preferences(user_id)
    if kind not in {"morning", "evening"} or not prefs.get(kind + "_enabled", False):
        return None
    zone = ZoneInfo(get_user_timezone(user_id) or "Europe/Moscow")
    local = current.astimezone(zone)
    scheduled = datetime.combine(local.date(), dt_time.fromisoformat(prefs[kind + "_time"]), tzinfo=zone)
    return quiet_until(user_id, scheduled.astimezone(timezone.utc)) or scheduled.astimezone(timezone.utc)


def capture_review_attention(
    user_id: int,
    review: dict,
    *,
    kind: str | None = None,
    now: datetime | None = None,
) -> dict | None:
    """Keep a scheduled briefing in the in-app attention center without another data/AI pass."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    review_kind = str(kind or review.get("kind") or "").strip()
    due = _review_due_at(user_id, review_kind, current)
    if due is None or current < due:
        return None
    zone = ZoneInfo(get_user_timezone(user_id) or "Europe/Moscow")
    local = current.astimezone(zone)
    day = str(review.get("date") or local.date())
    if day != str(local.date()):
        return None
    title = _review_title(review_kind)
    body = _strip_review_heading(str(review.get("text") or "").strip())
    if not body:
        return None
    return upsert_attention_item(
        user_id,
        source_type="daily_review",
        source_key=f"{review_kind}:{day}",
        category="assistant",
        priority="info",
        title=title,
        body=body[:1600],
        action_type="none",
    )


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
            events,
            zone_name,
            start,
            end,
            timedelta(minutes=30),
            work_start=_parse_hhmm(prefs["work_start"]),
            work_end=_parse_hhmm(prefs["work_end"]),
            work_days=prefs["work_days"],
            buffer=timedelta(minutes=prefs["buffer_minutes"]),
            now=local,
            limit=3,
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

    lines = [_review_title(kind)]
    lines.append("Доброе утро. Коротко о главном на сегодня." if kind == "morning" else "Добрый вечер. Коротко по итогам дня.")
    if not calendar_ok:
        lines.append("Календарь сейчас недоступен, поэтому занятость в этой сводке не проверена.")
    elif not regular_events:
        lines.append("Сегодня встреч в календаре нет.")
    else:
        count = len(regular_events)
        lines.append(f"Сегодня в календаре {count} {_plural_ru(count, 'событие', 'события', 'событий')}.")
        selected = regular_events if kind == "evening" else [
            event for event in regular_events if (_event_start(event, zone_name)[0] or local) >= local
        ]
        for event in selected[:3]:
            when, all_day = _event_start(event, zone_name)
            label = "весь день" if all_day else (when.strftime("%H:%M") if when else "время не указано")
            lines.append(f"• {label} · {str(event.get('summary', 'Событие'))[:100]}")

    if calendar_ok and travel_events:
        upcoming_travel = [event for event in travel_events if (_event_start(event, zone_name)[0] or local) >= local]
        for event in upcoming_travel[:1]:
            when, _ = _event_start(event, zone_name)
            lines.append(f"По дороге: выезд в {when:%H:%M} · {str(event.get('location') or event.get('summary') or '')[:120]}")
    if calendar_ok and kind == "morning" and slots:
        free_line = _format_free_slots(slots)
        if free_line:
            lines.append(free_line)

    try:
        lines.extend(_task_lines(user_id, zone, start, end, kind=kind))
    except Exception:
        logger.exception("Review tasks unavailable for user %s", user_id)
        lines.append("Задачи сейчас не удалось проверить.")

    reminder_count = len(reminders)
    if reminder_count:
        names = [str(item.get("text") or "")[:100].strip() for item in reminders[:3] if str(item.get("text") or "").strip()]
        tail = ""
        if reminder_count > len(names):
            tail = f" и ещё {reminder_count - len(names)}"
        description = "; ".join(names) + tail
        lines.append(
            f"До конца дня осталось {reminder_count} {_plural_ru(reminder_count, 'дело', 'дела', 'дел')} с уведомлением"
            + (f": {description}." if description else ".")
        )
    elif kind == "morning":
        lines.append("Отдельных дел с уведомлением до конца дня нет.")

    if kind == "morning":
        lines.extend(_email_lines(user_id))
    elif calendar_ok:
        lines.extend(_tomorrow_lines(user_id, zone_name, start))

    free_text = "\n".join(lines)
    text, presentation = _enhance_review_text(user_id, kind, str(local.date()), free_text)
    return {
        "kind": kind,
        "date": str(local.date()),
        "calendar_ok": calendar_ok,
        "text": text,
        "presentation": presentation,
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
        scheduled = quiet_until(user_id, scheduled.astimezone(timezone.utc)) or scheduled.astimezone(timezone.utc)
        if scheduled.astimezone(zone).date() != local.date() or current < scheduled:
            continue
        phase = "missed" if current - scheduled > timedelta(hours=2) else "sending"
        with db_lock:
            conn.execute("DELETE FROM review_deliveries WHERE day<?", (str(local.date() - timedelta(days=30)),))
            cur = conn.execute(
                "INSERT OR IGNORE INTO review_deliveries VALUES (?,?,?,?,?)",
                (user_id, str(local.date()), kind, phase, current.isoformat()),
            )
            conn.commit()
        if not cur.rowcount or phase == "missed":
            continue
        try:
            review = build_day_review(user_id, kind, now=current)
            capture_review_attention(user_id, review, kind=kind, now=current)
            result = sender(user_id, review["text"], f"review-{kind}-{local.date()}")
            phase = "accepted" if result else "failed"
        except Exception as exc:
            phase = "failed"
            logger.warning("Review delivery failed for user %s (%s)", user_id, type(exc).__name__)
        with db_lock:
            conn.execute(
                "UPDATE review_deliveries SET phase=? WHERE user_id=? AND day=? AND kind=?",
                (phase, user_id, str(local.date()), kind),
            )
            conn.commit()
        delivered += phase == "accepted"
    return delivered


_init_ai_review_cache()
