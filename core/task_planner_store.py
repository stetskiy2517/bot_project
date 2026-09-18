"""Extended task storage for flexible planning without changing legacy task commands."""

from __future__ import annotations

from datetime import datetime, timezone

from core.db import conn, db_lock

TASK_CATEGORIES = {"work", "health", "rest", "travel", "family", "personal", "other"}
TASK_PRIORITIES = {"low", "normal", "high"}
TASK_STATUSES = {"open", "done"}
MAX_TASK_TITLE = 300
MAX_TASK_DESCRIPTION = 4000
MAX_ESTIMATE_MINUTES = 12 * 60

SELECT_COLUMNS = (
    "task_id,user_id,title,description,due_at,status,priority,created_at,completed_at,"
    "category,estimate_minutes,flexible,calendar_event_id,scheduled_start,parent_task_id,"
    "repeat_rule,updated_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_task_planner_store() -> None:
    with db_lock:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        migrations = {
            "description": "TEXT NOT NULL DEFAULT ''",
            "category": "TEXT NOT NULL DEFAULT 'other'",
            "estimate_minutes": "INTEGER",
            "flexible": "INTEGER NOT NULL DEFAULT 1",
            "calendar_event_id": "TEXT",
            "scheduled_start": "TEXT",
            "parent_task_id": "INTEGER",
            "repeat_rule": "TEXT",
            "updated_at": "TEXT",
        }
        for column, sql_type in migrations.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {sql_type}")
        now = _now()
        conn.execute("UPDATE tasks SET updated_at=COALESCE(updated_at,created_at,?)", (now,))
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_flexible ON tasks(user_id,status,flexible,due_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(user_id,parent_task_id)")
        conn.commit()


def _from_row(row) -> dict:
    return {
        "task_id": int(row[0]),
        "user_id": int(row[1]),
        "title": row[2],
        "description": row[3] or "",
        "due_at": row[4],
        "status": row[5],
        "priority": row[6],
        "created_at": row[7],
        "completed_at": row[8],
        "category": row[9] or "other",
        "estimate_minutes": int(row[10]) if row[10] is not None else None,
        "flexible": bool(row[11]),
        "calendar_event_id": row[12],
        "scheduled_start": row[13],
        "parent_task_id": int(row[14]) if row[14] is not None else None,
        "repeat_rule": row[15],
        "updated_at": row[16],
    }


def _clean_title(value: object) -> str:
    title = " ".join(str(value or "").split()).strip()
    if not title:
        raise ValueError("Название задачи не может быть пустым")
    if len(title) > MAX_TASK_TITLE:
        raise ValueError(f"Название задачи: максимум {MAX_TASK_TITLE} символов")
    return title


def _clean_description(value: object) -> str:
    description = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(description) > MAX_TASK_DESCRIPTION:
        raise ValueError(f"Описание задачи: максимум {MAX_TASK_DESCRIPTION} символов")
    return description


def _clean_category(value: object) -> str:
    category = str(value or "other").strip().lower()
    if category not in TASK_CATEGORIES:
        raise ValueError("Неизвестная категория задачи")
    return category


def _clean_priority(value: object) -> str:
    priority = str(value or "normal").strip().lower()
    if priority not in TASK_PRIORITIES:
        raise ValueError("Неизвестный приоритет задачи")
    return priority


def _clean_estimate(value: object) -> int | None:
    if value in {None, ""}:
        return None
    if isinstance(value, bool):
        raise ValueError("Длительность задачи должна быть числом минут")
    try:
        minutes = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Длительность задачи должна быть числом минут") from exc
    if not 5 <= minutes <= MAX_ESTIMATE_MINUTES:
        raise ValueError("Длительность задачи должна быть от 5 минут до 12 часов")
    return minutes


def _clean_due(value: object) -> str | None:
    if value in {None, ""}:
        return None
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Некорректный срок задачи") from exc
    if parsed.tzinfo is None:
        raise ValueError("Срок задачи должен содержать часовой пояс")
    return parsed.astimezone(timezone.utc).isoformat()


def get_planner_task(user_id: int, task_id: int) -> dict | None:
    init_task_planner_store()
    with db_lock:
        row = conn.execute(
            f"SELECT {SELECT_COLUMNS} FROM tasks WHERE user_id=? AND task_id=?",
            (int(user_id), int(task_id)),
        ).fetchone()
    return _from_row(row) if row else None


def list_planner_tasks(
    user_id: int,
    *,
    status: str | None = "open",
    limit: int = 300,
    parent_task_id: int | None = None,
) -> list[dict]:
    init_task_planner_store()
    clauses = ["user_id=?"]
    values: list[object] = [int(user_id)]
    if status is not None:
        if status not in TASK_STATUSES:
            raise ValueError("Неизвестный статус задачи")
        clauses.append("status=?")
        values.append(status)
    if parent_task_id is not None:
        clauses.append("parent_task_id=?")
        values.append(int(parent_task_id))
    values.append(max(1, min(int(limit), 500)))
    sql = (
        f"SELECT {SELECT_COLUMNS} FROM tasks WHERE {' AND '.join(clauses)} "
        "ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, "
        "CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, "
        "CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,due_at,task_id LIMIT ?"
    )
    with db_lock:
        rows = conn.execute(sql, values).fetchall()
    return [_from_row(row) for row in rows]


def create_planner_task(
    user_id: int,
    title: object,
    *,
    description: object = "",
    due_at: object = None,
    priority: object = "normal",
    category: object = "other",
    estimate_minutes: object = None,
    flexible: bool = True,
    parent_task_id: int | None = None,
    repeat_rule: str | None = None,
) -> dict:
    init_task_planner_store()
    if not isinstance(flexible, bool):
        raise ValueError("Параметр гибкости должен быть true или false")
    clean_title = _clean_title(title)
    clean_description = _clean_description(description)
    clean_due = _clean_due(due_at)
    clean_priority = _clean_priority(priority)
    clean_category = _clean_category(category)
    clean_estimate = _clean_estimate(estimate_minutes)
    clean_repeat = " ".join(str(repeat_rule or "").split()).strip()[:100] or None
    now = _now()
    if parent_task_id is not None and not get_planner_task(user_id, int(parent_task_id)):
        raise ValueError("Родительская задача не найдена")
    with db_lock:
        cursor = conn.execute(
            "INSERT INTO tasks "
            "(user_id,title,description,due_at,status,priority,created_at,category,estimate_minutes,flexible,parent_task_id,repeat_rule,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(user_id), clean_title, clean_description, clean_due, "open", clean_priority, now,
                clean_category, clean_estimate, 1 if flexible else 0,
                int(parent_task_id) if parent_task_id is not None else None,
                clean_repeat, now,
            ),
        )
        conn.commit()
        task_id = int(cursor.lastrowid)
    return get_planner_task(user_id, task_id) or {}


def update_planner_task(user_id: int, task_id: int, changes: dict) -> dict:
    init_task_planner_store()
    if not isinstance(changes, dict):
        raise ValueError("Изменения задачи должны быть объектом")
    allowed = {
        "title", "description", "due_at", "priority", "category", "estimate_minutes", "flexible",
        "status", "parent_task_id", "repeat_rule",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError("Неизвестные поля задачи: " + ", ".join(sorted(unknown)))
    current = get_planner_task(user_id, task_id)
    if not current:
        raise ValueError("Задача не найдена")
    updates: list[str] = []
    values: list[object] = []
    if "title" in changes:
        updates.append("title=?")
        values.append(_clean_title(changes["title"]))
    if "description" in changes:
        updates.append("description=?")
        values.append(_clean_description(changes["description"]))
    if "due_at" in changes:
        updates.append("due_at=?")
        values.append(_clean_due(changes["due_at"]))
    if "priority" in changes:
        updates.append("priority=?")
        values.append(_clean_priority(changes["priority"]))
    if "category" in changes:
        updates.append("category=?")
        values.append(_clean_category(changes["category"]))
    if "estimate_minutes" in changes:
        updates.append("estimate_minutes=?")
        values.append(_clean_estimate(changes["estimate_minutes"]))
    if "flexible" in changes:
        if not isinstance(changes["flexible"], bool):
            raise ValueError("Параметр гибкости должен быть true или false")
        updates.append("flexible=?")
        values.append(1 if changes["flexible"] else 0)
    if "status" in changes:
        status = str(changes["status"] or "").strip().lower()
        if status not in TASK_STATUSES:
            raise ValueError("Неизвестный статус задачи")
        updates.extend(["status=?", "completed_at=?"])
        values.extend([status, _now() if status == "done" else None])
    if "parent_task_id" in changes:
        parent = changes["parent_task_id"]
        if parent in {None, ""}:
            parent_id = None
        else:
            parent_id = int(parent)
            if parent_id == int(task_id) or not get_planner_task(user_id, parent_id):
                raise ValueError("Некорректная родительская задача")
        updates.append("parent_task_id=?")
        values.append(parent_id)
    if "repeat_rule" in changes:
        updates.append("repeat_rule=?")
        values.append(" ".join(str(changes["repeat_rule"] or "").split()).strip()[:100] or None)
    if not updates:
        return current
    updates.append("updated_at=?")
    values.append(_now())
    values.extend([int(user_id), int(task_id)])
    with db_lock:
        conn.execute(
            f"UPDATE tasks SET {', '.join(updates)} WHERE user_id=? AND task_id=?",
            values,
        )
        conn.commit()
    return get_planner_task(user_id, task_id) or {}


def link_task_calendar(
    user_id: int,
    task_id: int,
    *,
    calendar_event_id: str | None,
    scheduled_start: datetime | str | None,
) -> dict:
    event_id = " ".join(str(calendar_event_id or "").split()).strip()[:300] or None
    if isinstance(scheduled_start, datetime):
        scheduled = scheduled_start.astimezone(timezone.utc).isoformat() if scheduled_start.tzinfo else scheduled_start.replace(tzinfo=timezone.utc).isoformat()
    else:
        scheduled = str(scheduled_start or "").strip() or None
    with db_lock:
        cursor = conn.execute(
            "UPDATE tasks SET calendar_event_id=?,scheduled_start=?,updated_at=? WHERE user_id=? AND task_id=?",
            (event_id, scheduled, _now(), int(user_id), int(task_id)),
        )
        if cursor.rowcount == 0:
            raise ValueError("Задача не найдена")
        conn.commit()
    return get_planner_task(user_id, task_id) or {}


def delete_planner_task(user_id: int, task_id: int) -> bool:
    with db_lock:
        cursor = conn.execute("DELETE FROM tasks WHERE user_id=? AND task_id=?", (int(user_id), int(task_id)))
        conn.commit()
    return cursor.rowcount > 0


def task_summary(user_id: int, *, now: datetime | None = None) -> dict:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    tasks = list_planner_tasks(user_id, status="open", limit=500)
    overdue = 0
    high = 0
    scheduled = 0
    for task in tasks:
        if task["priority"] == "high":
            high += 1
        if task.get("calendar_event_id"):
            scheduled += 1
        due = task.get("due_at")
        if due:
            try:
                parsed = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                if parsed.astimezone(timezone.utc) < current:
                    overdue += 1
            except ValueError:
                pass
    return {
        "open": len(tasks),
        "overdue": overdue,
        "high_priority": high,
        "scheduled": scheduled,
    }


init_task_planner_store()
