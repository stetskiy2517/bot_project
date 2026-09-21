"""Web API for planner tasks and user-approved flexible scheduling."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory, session

from core.task_planner_store import (
    create_planner_task,
    delete_planner_task,
    get_planner_task,
    list_planner_tasks,
    task_summary,
    update_planner_task,
)
from modules.task_planner import apply_task_slot, preview_flexible_schedule, remove_future_task_block
from modules.task_recurrence import create_next_recurring_task

logger = logging.getLogger(__name__)
task_api = Blueprint("tasks", __name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _user() -> int:
    return int(session["user_id"])


@task_api.after_app_request
def task_ui_hook(response):
    if request.path == "/" and response.status_code == 200 and response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        html = response.get_data(as_text=True)
        scripts = []
        if 'src="/task-editor.js"' not in html:
            scripts.append('<script defer src="/task-editor.js"></script>')
        if 'src="/tasks.js"' not in html:
            scripts.append('<script defer src="/tasks.js"></script>')
        if 'src="/tasks-unified.js"' not in html:
            scripts.append('<script defer src="/tasks-unified.js"></script>')
        if 'src="/task-swipe.js"' not in html:
            scripts.append('<script defer src="/task-swipe.js"></script>')
        if scripts and "</body>" in html:
            block = "\n    ".join(scripts)
            response.set_data(html.replace("</body>", f"    {block}\n  </body>", 1))
    return response


@task_api.get("/task-editor.js")
def task_editor_js():
    return send_from_directory(WEB_DIR, "task-editor.js", mimetype="application/javascript")


@task_api.get("/tasks.js")
def tasks_js():
    return send_from_directory(WEB_DIR, "tasks.js", mimetype="application/javascript")


@task_api.get("/tasks-unified.js")
def tasks_unified_js():
    return send_from_directory(WEB_DIR, "tasks-unified.js", mimetype="application/javascript")


@task_api.get("/task-swipe.js")
def task_swipe_js():
    return send_from_directory(WEB_DIR, "task-swipe.js", mimetype="application/javascript")


@task_api.get("/api/tasks")
def tasks_list():
    raw_status = request.args.get("status", "open")
    status = None if raw_status == "all" else raw_status
    tasks = list_planner_tasks(_user(), status=status, limit=500)

    query = " ".join(str(request.args.get("q") or "").split()).strip().lower()
    category = str(request.args.get("category") or "").strip().lower()
    priority = str(request.args.get("priority") or "").strip().lower()
    parent_raw = request.args.get("parent_task_id")
    if query:
        tasks = [
            task for task in tasks
            if query in str(task.get("title") or "").lower()
            or query in str(task.get("description") or "").lower()
        ]
    if category:
        tasks = [task for task in tasks if str(task.get("category") or "") == category]
    if priority:
        tasks = [task for task in tasks if str(task.get("priority") or "") == priority]
    if parent_raw not in {None, ""}:
        try:
            parent_id = int(parent_raw)
        except (TypeError, ValueError):
            return jsonify(error="invalid_task_filter", message="Некорректный идентификатор родительской задачи"), 400
        tasks = [task for task in tasks if task.get("parent_task_id") == parent_id]

    return {"tasks": tasks, "summary": task_summary(_user())}


@task_api.get("/api/tasks/<int:task_id>")
def tasks_get(task_id: int):
    task = get_planner_task(_user(), task_id)
    if not task:
        return jsonify(error="task_not_found"), 404
    subtasks = list_planner_tasks(_user(), status=None, limit=500, parent_task_id=task_id)
    return {"task": task, "subtasks": subtasks}


@task_api.post("/api/tasks")
def tasks_create():
    payload = request.get_json(silent=True) or {}
    try:
        task = create_planner_task(
            _user(),
            payload.get("title"),
            description=payload.get("description", ""),
            due_at=payload.get("due_at"),
            priority=payload.get("priority", "normal"),
            category=payload.get("category", "other"),
            estimate_minutes=payload.get("estimate_minutes"),
            flexible=payload.get("flexible", True),
            parent_task_id=payload.get("parent_task_id"),
            repeat_rule=payload.get("repeat_rule"),
        )
    except (TypeError, ValueError) as exc:
        return jsonify(error="invalid_task", message=str(exc)), 400
    return {"task": task}, 201


@task_api.patch("/api/tasks/<int:task_id>")
def tasks_update(task_id: int):
    payload = request.get_json(silent=True) or {}
    user_id = _user()
    current = get_planner_task(user_id, task_id)
    if not current:
        return jsonify(error="task_not_found"), 404
    status = payload.get("status")
    try:
        task = update_planner_task(user_id, task_id, payload)
    except (TypeError, ValueError) as exc:
        return jsonify(error="invalid_task", message=str(exc)), 400
    next_task = None
    if status == "done" and current.get("status") != "done":
        remove_future_task_block(user_id, current)
        next_task = create_next_recurring_task(user_id, current)
    return {"task": task, "next_task": next_task}


@task_api.delete("/api/tasks/<int:task_id>")
def tasks_delete(task_id: int):
    user_id = _user()
    current = get_planner_task(user_id, task_id)
    if not current:
        return jsonify(error="task_not_found"), 404
    remove_future_task_block(user_id, current)
    subtasks = list_planner_tasks(user_id, status=None, limit=500, parent_task_id=task_id)
    for subtask in subtasks:
        update_planner_task(user_id, int(subtask["task_id"]), {"parent_task_id": None})
    if not delete_planner_task(user_id, task_id):
        return jsonify(error="task_not_found"), 404
    return {"ok": True, "detached_subtasks": len(subtasks)}


@task_api.get("/api/tasks/schedule/preview")
def task_schedule_preview():
    try:
        return preview_flexible_schedule(_user())
    except PermissionError:
        return jsonify(error="calendar_not_connected", message="Для автопланирования нужен подключённый календарь."), 409
    except Exception:
        logger.exception("Task schedule preview failed for user %s", _user())
        return jsonify(error="task_schedule_failed", message="Не удалось подобрать время для задач."), 503


@task_api.post("/api/tasks/schedule/apply")
def task_schedule_apply():
    payload = request.get_json(silent=True) or {}
    proposals = payload.get("proposals")
    if not isinstance(proposals, list) or not proposals or len(proposals) > 20:
        return jsonify(error="invalid_task_schedule", message="Нужен список предложенных задач."), 400
    applied = []
    errors = []
    seen = set()
    for item in proposals:
        if not isinstance(item, dict):
            errors.append({"error": "invalid_proposal"})
            continue
        try:
            task_id = int(item.get("task_id"))
        except (TypeError, ValueError):
            errors.append({"error": "invalid_task_id"})
            continue
        if task_id in seen:
            continue
        seen.add(task_id)
        try:
            task = apply_task_slot(_user(), task_id, str(item.get("start") or ""))
            applied.append(task)
        except ValueError as exc:
            errors.append({"task_id": task_id, "error": str(exc)})
        except Exception:
            logger.exception("Task scheduling failed user=%s task=%s", _user(), task_id)
            errors.append({"task_id": task_id, "error": "Не удалось записать задачу в календарь"})
    return {
        "applied": applied,
        "errors": errors,
        "applied_count": len(applied),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
