"""Explicit, atomic next steps from commitments using the shared task stores."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.command_store import user_operation
from core.db import conn, db_lock, get_user_timezone
from core.memory_store import get_commitment, get_company, get_contact
from core.task_planner_store import create_planner_task, init_task_planner_store
from core.reminder_store import create_reminder


def create_commitment_action(user_id, commitment_id, payload):
    kind = payload.get("kind")
    if kind not in {"task", "reminder"} or payload.get("confirmed") is not True:
        raise ValueError("Подтверди создание задачи или напоминания")
    with user_operation(user_id), db_lock:
        item = get_commitment(user_id, commitment_id)
        if not item:
            raise LookupError("Договорённость не найдена")
        previous = conn.execute("SELECT target_id FROM commitment_actions WHERE user_id=? AND commitment_id=? AND kind=?", (user_id, commitment_id, kind)).fetchone()
        if previous:
            return {"kind": kind, "target_id": previous[0], "created": False}
        if item["status"] != "open":
            raise ValueError("Договорённость уже завершена")
        raw_due = payload.get("due_at") or item.get("due_at")
        due = None
        if raw_due:
            if not isinstance(raw_due, str):
                raise ValueError("Некорректный срок")
            try:
                due = datetime.fromisoformat(raw_due.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Некорректный срок") from exc
            if due.tzinfo is None:
                zone = ZoneInfo(get_user_timezone(user_id) or "Europe/Moscow")
                local = due.replace(tzinfo=zone)
                if local.fold == 0 and local.utcoffset() != local.replace(fold=1).utcoffset():
                    raise ValueError("Это время попадает на перевод часов. Выбери другое время.")
                if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != due:
                    raise ValueError("Такого местного времени не существует. Выбери другое время.")
                due = local
            due = due.astimezone(timezone.utc)
        if kind == "reminder" and (due is None or due <= datetime.now(timezone.utc)):
            raise ValueError("Для напоминания выбери будущее время")
        company = get_company(user_id, item["company_id"]) if item["company_id"] else None
        contact = get_contact(user_id, item["contact_id"]) if item["contact_id"] else None
        context = " · ".join(x for x in ((company or {}).get("name"), (contact or {}).get("full_name")) if x)
        if kind == "task":
            init_task_planner_store()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if kind == "task":
                target = create_planner_task(user_id, item["title"][:300], description=f"Договорённость: {item['title']}\n{context}\nИсточник: /#memory=commitment:{commitment_id}", due_at=due.isoformat() if due else None, category="work", commit=False)
                target_id = target["task_id"]
            else:
                target = create_reminder(user_id, item["title"] + (f" · {context}" if context else ""), due, commit=False)
                target_id = target["reminder_id"]
            conn.execute("INSERT INTO commitment_actions VALUES (?,?,?,?,?)", (user_id, commitment_id, kind, target_id, datetime.now(timezone.utc).isoformat()))
            if due:
                conn.execute("UPDATE sales_commitments SET due_at=?,updated_at=? WHERE user_id=? AND commitment_id=?", (due.isoformat(), datetime.now(timezone.utc).isoformat(), user_id, commitment_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"kind": kind, "target_id": target_id, "created": True, "item": target}
