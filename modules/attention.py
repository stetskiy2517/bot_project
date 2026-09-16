"""Deterministic attention signals built from already available assistant data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

from core.attention_store import (
    cleanup_attention_store,
    dismiss_missing_source_keys,
    list_attention_items,
    upsert_attention_item,
)
from core.email_auto_store import recent_auto_plans
from core.navigation_store import (
    list_pending_navigation_optimizations,
    list_pending_navigation_origins,
)
from core.proactive_store import list_proactive_actions
from core.task_planner_store import list_planner_tasks

ATTENTION_FRESH_HOURS = 6


def _parse_time(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _short_time(value: object) -> str:
    parsed = _parse_time(value)
    return parsed.strftime("%d.%m, %H:%M UTC") if parsed else ""


def _stable_digest(parts: object) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _email_source_key(action: dict) -> str:
    source = action.get("source") if isinstance(action.get("source"), dict) else {}
    event = action.get("attachment_event") if isinstance(action.get("attachment_event"), dict) else {}
    stable = {
        "account": source.get("account"),
        "message": source.get("provider_message_id") or source.get("message_id") or source.get("subject"),
        "attachment": source.get("attachment_id") or source.get("attachment"),
        "type": action.get("action_type"),
        "due": action.get("due_at"),
        "start": event.get("start"),
        "end": event.get("end"),
    }
    return _stable_digest(stable)


def _email_body(action: dict) -> str:
    source = action.get("source") if isinstance(action.get("source"), dict) else {}
    event = action.get("attachment_event") if isinstance(action.get("attachment_event"), dict) else {}
    bits: list[str] = []
    subject = " ".join(str(source.get("subject") or "").split()).strip()
    attachment = " ".join(str(source.get("attachment") or "").split()).strip()
    due = event.get("start") or action.get("due_at")
    if subject:
        bits.append(f"Письмо: {subject}")
    if attachment:
        bits.append(f"Вложение: {attachment}")
    if due:
        formatted = _short_time(due)
        if formatted:
            bits.append(f"Время: {formatted}")
    return " · ".join(bits)[:1600]


def capture_email_plan_attention(user_id: int, plan: dict) -> int:
    """Persist useful results of automatic email analysis without another AI call."""
    if not isinstance(plan, dict):
        return 0
    created_at = _parse_time(plan.get("created_at"))
    if created_at is not None and created_at < datetime.now(timezone.utc) - timedelta(hours=ATTENTION_FRESH_HOURS):
        return 0
    created = 0
    for action in plan.get("actions") or []:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("action_type") or "").strip()
        if action_type not in {"task", "reminder", "calendar_event"}:
            continue
        if action.get("already_in_calendar"):
            continue
        title = " ".join(str(action.get("title") or "Действие из почты").split()).strip()[:300]
        source_key = _email_source_key(action)
        if action.get("auto_created"):
            upsert_attention_item(
                user_id,
                source_type="email_auto_done",
                source_key=source_key,
                category="email",
                priority="normal",
                title=f"Добавил в календарь: {title}"[:300],
                body=_email_body(action),
                action_type="none",
            )
            created += 1
            continue

        try:
            confidence = float(action.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        due = _parse_time((action.get("attachment_event") or {}).get("start") if isinstance(action.get("attachment_event"), dict) else action.get("due_at"))
        soon = bool(due and due <= datetime.now(timezone.utc) + timedelta(hours=36))
        priority = "high" if confidence >= 0.90 or soon else "normal"
        label = "Нужно решение по письму"
        if action.get("auto_create_error"):
            label = "Не удалось автоматически выполнить действие"
        elif action.get("ready") is False:
            label = "Проверь данные из письма"
        upsert_attention_item(
            user_id,
            source_type="email_action",
            source_key=source_key,
            category="email",
            priority=priority,
            title=f"{label}: {title}"[:300],
            body=_email_body(action),
            action_type="email_action" if action.get("ready") is not False else "none",
            action=action if action.get("ready") is not False else None,
        )
        created += 1
    return created


def sync_proactive_action_attention(user_id: int, *, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    cutoff = current - timedelta(hours=ATTENTION_FRESH_HOURS)
    count = 0
    for action in list_proactive_actions(user_id, limit=30):
        if action.get("status") != "created":
            continue
        updated = _parse_time(action.get("updated_at"))
        if updated is None or updated < cutoff:
            continue
        action_type = str(action.get("action_type") or "")
        noun = "напоминание" if action_type == "reminder" else "событие календаря"
        upsert_attention_item(
            user_id,
            source_type="proactive_created",
            source_key=str(int(action["action_id"])),
            category="assistant",
            priority="normal",
            title=f"Секретарь создал {noun}",
            body=str(action.get("reason") or "")[:1600],
            action_type="proactive",
            action={
                "action_type": action_type,
                "reminder_id": action.get("reminder_id"),
                "calendar_event_id": action.get("calendar_event_id"),
            },
        )
        count += 1
    return count


def sync_overdue_task_attention(user_id: int, *, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    overdue: list[tuple[datetime, dict]] = []
    for task in list_planner_tasks(user_id, status="open", limit=500):
        if task.get("parent_task_id") is not None:
            continue
        due = _parse_time(task.get("due_at"))
        if due is None or due >= current:
            continue
        overdue.append((due, task))
    overdue.sort(key=lambda pair: (0 if pair[1].get("priority") == "high" else 1, pair[0], int(pair[1]["task_id"])))
    selected = overdue[:3]
    active_keys: set[str] = set()
    for due, task in selected:
        key = f"{int(task['task_id'])}:{due.replace(microsecond=0).isoformat()}"
        active_keys.add(key)
        age = current - due
        priority = "high" if task.get("priority") == "high" or age >= timedelta(hours=24) else "normal"
        overdue_text = "Просрочено больше суток" if age >= timedelta(hours=24) else "Срок уже прошёл"
        upsert_attention_item(
            user_id,
            source_type="task_overdue",
            source_key=key,
            category="task",
            priority=priority,
            title=f"Просрочена задача: {task.get('title') or 'Без названия'}"[:300],
            body=f"{overdue_text}. Срок: {_short_time(task.get('due_at'))}"[:1600],
            action_type="task",
            action={"task_id": int(task["task_id"])},
        )
    dismiss_missing_source_keys(user_id, "task_overdue", active_keys)
    return len(selected)


def sync_navigation_attention(user_id: int) -> int:
    count = 0
    origin_keys: set[str] = set()
    for request in list_pending_navigation_origins(user_id)[-3:]:
        key = str(request.get("event_id") or "")
        if not key:
            continue
        origin_keys.add(key)
        upsert_attention_item(
            user_id,
            source_type="navigation_origin",
            source_key=key,
            category="navigation",
            priority="normal",
            title=f"Откуда поедете на «{request.get('title') or 'событие'}»?"[:300],
            body=str(request.get("destination") or "")[:1600],
            action_type="navigation",
            action={"kind": "origin", "event_id": key},
        )
        count += 1
    dismiss_missing_source_keys(user_id, "navigation_origin", origin_keys)

    optimization_keys: set[str] = set()
    for request in list_pending_navigation_optimizations(user_id)[-3:]:
        key = str(request.get("event_id") or "")
        if not key:
            continue
        optimization_keys.add(key)
        missing = int(request.get("missing_minutes") or 0)
        upsert_attention_item(
            user_id,
            source_type="navigation_optimization",
            source_key=key,
            category="navigation",
            priority="high",
            title=f"Не хватает {missing} мин на дорогу до «{request.get('title') or 'события'}»"[:300],
            body=f"После «{request.get('previous_title') or 'предыдущего события'}» нужно скорректировать расписание."[:1600],
            action_type="navigation",
            action={"kind": "optimization", "event_id": key},
        )
        count += 1
    dismiss_missing_source_keys(user_id, "navigation_optimization", optimization_keys)
    return count


def sync_attention_context(user_id: int, *, now: datetime | None = None) -> None:
    for plan in recent_auto_plans(user_id, hours=ATTENTION_FRESH_HOURS, limit=12):
        capture_email_plan_attention(user_id, plan)
    sync_proactive_action_attention(user_id, now=now)
    sync_overdue_task_attention(user_id, now=now)
    sync_navigation_attention(user_id)


def attention_snapshot(user_id: int, *, now: datetime | None = None, limit: int = 8) -> list[dict]:
    sync_attention_context(user_id, now=now)
    cleanup_attention_store()
    return list_attention_items(user_id, limit=limit)


__all__ = [
    "attention_snapshot",
    "capture_email_plan_attention",
    "sync_attention_context",
    "sync_navigation_attention",
    "sync_overdue_task_attention",
    "sync_proactive_action_attention",
]
