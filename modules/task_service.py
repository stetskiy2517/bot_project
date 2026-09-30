"""Shared task mutation service used by chat and web UI.

Task state transitions must have identical side effects regardless of entry point.
"""

from __future__ import annotations

from core.task_planner_store import (
    delete_planner_task,
    get_planner_task,
    list_planner_tasks,
    update_planner_task,
)
from modules.task_planner import remove_future_task_block
from modules.task_recurrence import create_next_recurring_task


def complete_task(user_id: int, task_id: int, *, completed: bool = True) -> tuple[dict | None, dict | None]:
    current = get_planner_task(user_id, task_id)
    if not current:
        return None, None

    target_status = "done" if completed else "open"
    task = update_planner_task(user_id, task_id, {"status": target_status})
    next_task = None

    if completed and current.get("status") != "done":
        remove_future_task_block(user_id, current)
        next_task = create_next_recurring_task(user_id, current)

    return task, next_task


def delete_task(user_id: int, task_id: int) -> tuple[bool, int]:
    current = get_planner_task(user_id, task_id)
    if not current:
        return False, 0

    remove_future_task_block(user_id, current)
    subtasks = list_planner_tasks(user_id, status=None, limit=500, parent_task_id=task_id)
    for subtask in subtasks:
        update_planner_task(user_id, int(subtask["task_id"]), {"parent_task_id": None})

    return delete_planner_task(user_id, task_id), len(subtasks)
