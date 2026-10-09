"""AI-assisted planning of natural-language task lists.

The model may extract tasks and estimate practical durations, but it never writes
calendar data directly. All writes go through the existing task store and
Smart Planner scheduling/validation layer.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import logging
import re
from zoneinfo import ZoneInfo

from core.conversation_context import clear_pending, set_pending
from core.db import get_user_timezone
from core.feature_access import has_ai_access
from core.task_planner_store import create_planner_task
from core.user_activity_store import set_request_diagnostic
from integrations.ai import AIError, complete_structured, is_ai_available
from modules.calendar import _date_from_text
from modules.calendar_user import _list_events
from modules.task_planner import apply_task_slot, preview_flexible_schedule

logger = logging.getLogger(__name__)

MAX_PLAN_ITEMS = 10
MAX_ESTIMATE_MINUTES = 4 * 60
TASK_CATEGORIES = {"work", "health", "rest", "travel", "family", "personal", "other"}
TASK_PRIORITIES = {"low", "normal", "high"}

TASK_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["none", "task_list"]},
        "schedule": {"type": "boolean"},
        "day": {"type": "string"},
        "items": {
            "type": "array",
            "maxItems": MAX_PLAN_ITEMS,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "estimate_minutes": {
                        "type": "integer",
                        "minimum": 5,
                        "maximum": MAX_ESTIMATE_MINUTES,
                    },
                    "category": {
                        "type": "string",
                        "enum": sorted(TASK_CATEGORIES),
                    },
                    "priority": {
                        "type": "string",
                        "enum": sorted(TASK_PRIORITIES),
                    },
                    "source_text": {"type": "string"},
                },
                "required": ["title", "estimate_minutes", "category", "priority", "source_text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["intent", "schedule", "day", "items"],
    "additionalProperties": False,
}

PLAN_KEYWORDS = (
    "расплан", "список дел", "список задач", "разложи по времени",
    "расставь по времени", "внеси в календар", "запланируй все",
    "запланировать все", "раскидай по дню",
)
AFFIRMATIVE = {"да", "ага", "ок", "окей", "давай", "распланируй", "планируй", "запланируй"}
REFERENCE_TO_PRIOR_LIST_RE = re.compile(
    r"\b(?:эт(?:от|и)\s+(?:список|дела|задачи)|предыдущ(?:ий|ие)\s+(?:список|дела|задачи)|"
    r"их\s+(?:распланируй|запланируй|расставь)|все\s+это|из\s+списка\s+выше)\b",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
SOURCE_STOPWORDS = {
    "это", "этот", "эти", "дело", "дела", "задача", "задачи", "список",
    "сегодня", "завтра", "послезавтра", "выходные", "выходной",
    "запланируй", "распланируй", "планируй", "сделай", "нужно", "надо",
}


def looks_like_task_plan_candidate(text: str) -> bool:
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return False
    normal = clean.casefold().replace("ё", "е")
    if any(keyword in normal for keyword in PLAN_KEYWORDS):
        return True
    parts = [part.strip() for part in re.split(r"[,;\n]+", clean) if part.strip()]
    return len(parts) >= 2 and len(clean.split()) >= 4


def _clean_title(value: object) -> str:
    title = " ".join(str(value or "").split()).strip(" .,:;-")
    if not title:
        return ""
    title = title[:300]
    return title[:1].upper() + title[1:]


def _clean_estimate(value: object) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = 30
    minutes = max(5, min(MAX_ESTIMATE_MINUTES, minutes))
    return max(5, int(round(minutes / 5.0) * 5))


def _normalise_source(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split()).strip(" .,:;!?-—")


def _content_roots(value: object) -> set[str]:
    roots = set()
    for token in WORD_RE.findall(_normalise_source(value)):
        if token in SOURCE_STOPWORDS or len(token) < 4 or token.isdigit():
            continue
        roots.add(token[:4])
    return roots


def _source_is_grounded(title: str, source_text: str, allowed_sources: list[str]) -> bool:
    source = _normalise_source(source_text)
    if not source:
        return False
    if not any(source in _normalise_source(candidate) for candidate in allowed_sources):
        return False
    title_roots = _content_roots(title)
    source_roots = _content_roots(source_text)
    return bool(title_roots and source_roots and title_roots.intersection(source_roots))


def _clean_items(raw_items: object, *, allowed_sources: list[str] | None = None) -> list[dict]:
    if not isinstance(raw_items, list):
        return []
    sources = [str(item) for item in (allowed_sources or []) if str(item).strip()]
    result = []
    seen = set()
    for raw in raw_items[:MAX_PLAN_ITEMS]:
        if not isinstance(raw, dict):
            continue
        title = _clean_title(raw.get("title"))
        source_text = " ".join(str(raw.get("source_text") or "").split()).strip()
        key = title.casefold()
        if not title or key in seen:
            continue
        if sources and not _source_is_grounded(title, source_text, sources):
            logger.warning("Rejected ungrounded AI task title=%r source=%r", title, source_text)
            continue
        seen.add(key)
        category = str(raw.get("category") or "other").strip().lower()
        priority = str(raw.get("priority") or "normal").strip().lower()
        result.append(
            {
                "title": title,
                "estimate_minutes": _clean_estimate(raw.get("estimate_minutes")),
                "category": category if category in TASK_CATEGORIES else "other",
                "priority": priority if priority in TASK_PRIORITIES else "normal",
            }
        )
    return result


def _planning_sources(candidate: str, history: list[dict] | None) -> tuple[list[str], list[dict]]:
    sources = [candidate]
    if not REFERENCE_TO_PRIOR_LIST_RE.search(candidate):
        return sources, []

    prior_user_messages = []
    for item in reversed(history or []):
        if not isinstance(item, dict) or str(item.get("role") or "") != "user":
            continue
        content = " ".join(str(item.get("content") or "").split()).strip()
        if not content:
            continue
        prior_user_messages.append(content[:3000])
        if len(prior_user_messages) >= 2:
            break
    prior_user_messages.reverse()
    sources.extend(prior_user_messages)
    return sources, [{"role": "user", "content": item} for item in prior_user_messages]


def _history_messages(history: list[dict] | None) -> list[dict]:
    result = []
    for item in (history or [])[-6:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = " ".join(str(item.get("content") or "").split()).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        result.append({"role": role, "content": content[:3000]})
    return result


def _local_now(user_id: int, now: datetime | None = None) -> datetime:
    zone = ZoneInfo(get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow")
    current = now or datetime.now(timezone.utc)
    return current.astimezone(zone) if current.tzinfo else current.replace(tzinfo=zone)


def _date_from_user_text(user_id: int, text: str, now: datetime | None = None) -> date | None:
    local = _local_now(user_id, now)
    return _date_from_text(text, local.replace(tzinfo=None), 23, 59)


def _normalize_day(user_id: int, value: object, text: str, now: datetime | None = None) -> str:
    # Never trust an AI-generated date unless the deterministic parser can find
    # that date in the user's own message.
    parsed = _date_from_user_text(user_id, text, now)
    return parsed.isoformat() if parsed else ""


def interpret_task_plan(
    text: str,
    *,
    user_id: int,
    history: list[dict] | None = None,
    now: datetime | None = None,
) -> dict | None:
    """Extract a multi-item task plan from otherwise unhandled natural language."""
    candidate = " ".join(str(text or "").split()).strip()
    if (
        not candidate
        or not looks_like_task_plan_candidate(candidate)
        or not has_ai_access(user_id)
        or not is_ai_available()
    ):
        return None

    local = _local_now(user_id, now)
    allowed_sources, reference_history = _planning_sources(candidate, history)
    system = (
        "Ты классификатор и оценщик списка дел для персонального планировщика. "
        "Ничего не выполняй сам. Верни только структуру по заданной JSON-схеме. "
        "intent=task_list только если пользователь перечисляет минимум два самостоятельных дела "
        "или явно просит распланировать ранее перечисленный список. Иначе intent=none. "
        "schedule=true только если пользователь прямо просит распланировать, разложить по времени, "
        "запланировать все дела или внести их в календарь. "
        "Поле day не выдумывай: дата допустима только если она явно названа пользователем. "
        "Если даты нет — пустая строка. "
        f"Локальные дата и время пользователя: {local.isoformat()}. "
        "Разбивай список на отдельные короткие действия, не объединяй несколько дел в одно. "
        "estimate_minutes — реалистичный активный блок времени и всегда кратен 5. "
        "Быстрое цифровое действие вроде отправить/переслать файл или документ обычно 5 минут; "
        "короткий звонок 10–20 минут; простая административная задача 10–20 минут; "
        "поездка/поручение 30–60 минут; сосредоточенная работа или учеба 30–120 минут. "
        "Не раздувай длительность из-за важности задачи. Если не уверен — 30 минут. "
        "Используй только категории work, health, rest, travel, family, personal, other "
        "и приоритеты low, normal, high. "
        "Для каждого дела source_text — точная короткая цитата из сообщения пользователя, "
        "которая прямо называет это действие. Нельзя придумывать действие по ассоциации. "
        "Например, из слова «выходные» нельзя делать «поездка на дачу». "
        "Слова «сегодня», «завтра», «выходные» сами по себе — время или контекст, а не отдельное дело. "
        "Историю используй только когда текущий запрос явно ссылается на предыдущий список."
    )
    messages = [{"role": "system", "content": system}]
    messages.extend(reference_history)
    messages.append({"role": "user", "content": candidate[:10000]})

    try:
        payload = complete_structured(messages, TASK_PLAN_SCHEMA, max_tokens=700)
    except AIError as exc:
        logger.warning("AI task-list planner unavailable: %s", exc)
        return None
    except Exception:
        logger.exception("Unexpected AI task-list planner failure")
        return None

    if not isinstance(payload, dict) or str(payload.get("intent") or "") != "task_list":
        return None
    items = _clean_items(payload.get("items"), allowed_sources=allowed_sources)
    if len(items) < 2:
        return None
    return {
        "items": items,
        "schedule": bool(payload.get("schedule")),
        "day": _normalize_day(user_id, payload.get("day"), candidate, now),
    }


def _target_window(user_id: int, target_day: date, now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    zone = ZoneInfo(get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow")
    current = (now or datetime.now(timezone.utc)).astimezone(zone)
    start = datetime.combine(target_day, time.min, tzinfo=zone)
    end = start + timedelta(days=1)
    if target_day < current.date() or end <= current:
        raise ValueError("Этот день уже прошёл. Назови будущий день.")
    if target_day > current.date() + timedelta(days=30):
        raise ValueError("Автопланирование доступно максимум на 30 дней вперёд.")
    return current, start, end


def execute_task_plan(
    user_id: int,
    items: list[dict],
    target_day: date,
    *,
    now: datetime | None = None,
) -> dict:
    """Create validated tasks and place them into free calendar slots on one day."""
    clean_items = _clean_items(items)
    if not clean_items:
        raise ValueError("Не нашёл дел для планирования.")

    current, day_start, day_end = _target_window(user_id, target_day, now)
    # Fail before writing any tasks if the calendar is not available.
    _list_events(user_id, max(current, day_start), day_end)

    due_at = (day_end - timedelta(minutes=1)).isoformat()
    created = []
    for item in clean_items:
        created.append(
            create_planner_task(
                user_id,
                item["title"],
                due_at=due_at,
                priority=item["priority"],
                category=item["category"],
                estimate_minutes=item["estimate_minutes"],
                flexible=True,
            )
        )

    task_ids = {int(task["task_id"]) for task in created if task.get("task_id") is not None}
    preview = preview_flexible_schedule(
        user_id,
        now=now,
        task_ids=task_ids,
        window_start=day_start,
        window_end=day_end,
    )

    applied = []
    errors = []
    for proposal in preview.get("proposals") or []:
        try:
            task = apply_task_slot(user_id, int(proposal["task_id"]), str(proposal["start"]))
            applied.append({**proposal, "task": task})
        except Exception as exc:
            logger.exception("Could not apply AI task-plan slot user=%s task=%s", user_id, proposal.get("task_id"))
            errors.append({"task_id": proposal.get("task_id"), "error": str(exc)})

    applied_ids = {int(item["task_id"]) for item in applied}
    unscheduled = [
        {
            "task_id": int(task["task_id"]),
            "title": task["title"],
            "estimate_minutes": task.get("estimate_minutes"),
        }
        for task in created
        if int(task["task_id"]) not in applied_ids
    ]
    set_request_diagnostic(
        route="ai_task_plan",
        intent="schedule_task_list",
        task_count=len(created),
        scheduled_count=len(applied),
        target_day=target_day.isoformat(),
    )
    return {
        "target_day": target_day.isoformat(),
        "created": created,
        "applied": applied,
        "unscheduled": unscheduled,
        "errors": errors,
    }


def _day_label(user_id: int, target_day: date) -> str:
    today = _local_now(user_id).date()
    if target_day == today:
        return "сегодня"
    if target_day == today + timedelta(days=1):
        return "завтра"
    return target_day.strftime("%d.%m")


def format_task_plan_result(user_id: int, result: dict) -> str:
    target_day = date.fromisoformat(str(result["target_day"]))
    applied = result.get("applied") or []
    unscheduled = result.get("unscheduled") or []
    lines = [f"Распланировал на {_day_label(user_id, target_day)}:"]
    zone = ZoneInfo(get_user_timezone(user_id, default="Europe/Moscow") or "Europe/Moscow")

    for item in applied:
        start = datetime.fromisoformat(str(item["start"]).replace("Z", "+00:00")).astimezone(zone)
        end = datetime.fromisoformat(str(item["end"]).replace("Z", "+00:00")).astimezone(zone)
        lines.append(
            f"• {start:%H:%M}–{end:%H:%M} — {item['title']} · {int(item['estimate_minutes'])} мин"
        )
    if unscheduled:
        names = ", ".join(str(item.get("title") or "задача") for item in unscheduled[:5])
        lines.append(f"Не нашёл свободного окна: {names}. Они остались в задачах.")
    if not applied:
        lines[0] = f"На {_day_label(user_id, target_day)} свободных окон не нашёл."
    return "\n".join(lines)


async def handle_unhandled_task_plan(
    update,
    context,
    text: str,
    *,
    history: list[dict] | None = None,
) -> bool:
    user_id = int(update.effective_user.id)
    plan = interpret_task_plan(text, user_id=user_id, history=history)
    if not plan:
        return False

    day_value = str(plan.get("day") or "")
    if plan.get("schedule") and day_value:
        try:
            result = execute_task_plan(user_id, plan["items"], date.fromisoformat(day_value))
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return True
        except Exception:
            logger.exception("AI task-list scheduling failed user=%s", user_id)
            await update.message.reply_text("Не удалось распланировать дела по календарю. Попробуй ещё раз.")
            return True
        await update.message.reply_text(format_task_plan_result(user_id, result))
        return True

    set_pending(
        context,
        {
            "type": "task_plan_date",
            "items": plan["items"],
            "day": day_value or None,
        },
    )
    set_request_diagnostic(route="ai_task_plan", intent="task_list_detected", task_count=len(plan["items"]))
    if day_value:
        label = _day_label(user_id, date.fromisoformat(day_value))
        await update.message.reply_text(
            f"Вижу {len(plan['items'])} дела. Распланировать их на {label} по свободным окнам?"
        )
    else:
        await update.message.reply_text(
            f"Вижу {len(plan['items'])} дела. На какой день распланировать их по календарю?"
        )
    return True


async def resume_pending_task_plan(update, context, text: str, pending: dict) -> bool:
    user_id = int(update.effective_user.id)
    normal = " ".join(str(text or "").casefold().replace("ё", "е").split()).strip(" .,!?:;«»\"'")
    stored_day = str(pending.get("day") or "").strip()

    target = _date_from_user_text(user_id, text)
    if target is None and normal in AFFIRMATIVE and stored_day:
        try:
            target = date.fromisoformat(stored_day)
        except ValueError:
            target = None
    if target is None:
        await update.message.reply_text("На какой день? Например: «сегодня», «завтра» или «12.10».")
        return True

    try:
        result = execute_task_plan(user_id, pending.get("items") or [], target)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return True
    except Exception:
        logger.exception("Pending AI task-list scheduling failed user=%s", user_id)
        await update.message.reply_text("Не удалось распланировать дела по календарю. Попробуй ещё раз.")
        return True

    clear_pending(context)
    await update.message.reply_text(format_task_plan_result(user_id, result))
    return True
