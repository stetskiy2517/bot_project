"""Explicit, user-owned calendar command templates."""

from __future__ import annotations

import json
import re

from core.db import conn, db_lock, DEFAULT_CATEGORY_COLORS
from modules.calendar import _extract_time, _relative_offset
from modules.calendar_actions import create_from_text

NAME_RE = re.compile(r"^[\wА-Яа-яЁё][\wА-Яа-яЁё -]{1,49}$")
DATE_RE = re.compile(r"\b(?:сегодня|завтра|послезавтра|через|понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*|\d{1,2}[./-]\d{1,2}|\d{1,2}\s+[а-я]+)\b", re.I)
RESERVED_RE = re.compile(r"\b(?:удал\w*|отмен\w*|перенес\w*|покаж\w*|найд\w*|напомн\w*|замет\w*)\b", re.I)


def init_templates():
    with db_lock:
        conn.execute("""CREATE TABLE IF NOT EXISTS command_templates (
            template_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            name TEXT NOT NULL, normalized_name TEXT NOT NULL, spec_json TEXT NOT NULL,
            UNIQUE(user_id,normalized_name)
        )""")
        conn.commit()


def _normal(value):
    return " ".join(value.casefold().replace("ё", "е").split())


def list_templates(user_id: int) -> list[dict]:
    with db_lock:
        rows = conn.execute("SELECT template_id,name,spec_json FROM command_templates WHERE user_id=? ORDER BY normalized_name", (user_id,)).fetchall()
    return [dict(id=row[0], name=row[1], **json.loads(row[2])) for row in rows]


def save_template(user_id: int, data: dict, template_id: int | None = None) -> dict:
    if not isinstance(data, dict) or set(data) - {"name", "title", "duration_minutes", "category"}:
        raise ValueError("Неизвестные поля шаблона")
    name, title = data.get("name"), data.get("title")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name.strip()) or RESERVED_RE.search(name):
        raise ValueError("Название шаблона: 2–50 букв, цифр, пробелов или дефисов, без команд удаления")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
        raise ValueError("Название встречи должно быть от 1 до 200 символов")
    minutes = data.get("duration_minutes", 60)
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 720:
        raise ValueError("Длительность: от 1 до 720 минут")
    category = data.get("category", "work")
    if not isinstance(category, str) or category not in DEFAULT_CATEGORY_COLORS:
        raise ValueError("Неизвестная категория")
    spec = {"title": title.strip(), "duration_minutes": minutes, "category": category}
    name = name.strip()
    with db_lock:
        try:
            existing = conn.execute("SELECT template_id FROM command_templates WHERE user_id=? AND normalized_name=?",
                                    (user_id, _normal(name))).fetchone()
            if existing and existing[0] != template_id:
                raise ValueError("Такое имя уже используется")
            if template_id is None:
                if conn.execute("SELECT count(*) FROM command_templates WHERE user_id=?", (user_id,)).fetchone()[0] >= 30:
                    raise ValueError("Можно сохранить до 30 шаблонов")
                cur = conn.execute("INSERT INTO command_templates(user_id,name,normalized_name,spec_json) VALUES (?,?,?,?)",
                                   (user_id, name, _normal(name), json.dumps(spec, ensure_ascii=False)))
                template_id = cur.lastrowid
            else:
                cur = conn.execute("UPDATE command_templates SET name=?,normalized_name=?,spec_json=? WHERE user_id=? AND template_id=?",
                                   (name, _normal(name), json.dumps(spec, ensure_ascii=False), user_id, template_id))
                if not cur.rowcount:
                    raise ValueError("Шаблон не найден")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return dict(id=template_id, name=name, **spec)


def delete_template(user_id: int, template_id: int) -> bool:
    with db_lock:
        cur = conn.execute("DELETE FROM command_templates WHERE user_id=? AND template_id=?", (user_id, template_id))
        conn.commit()
    return bool(cur.rowcount)


async def handle_template(update, context, text: str) -> bool:
    pending = context.user_data.get("smart_planner_pending") or {}
    user_id = update.effective_user.id
    templates = list_templates(user_id)
    if pending.get("type") == "template_when":
        if _normal(text).strip(" .!?") in {"отмена", "нет", "стоп", "не надо"}:
            context.user_data.pop("smart_planner_pending", None)
            await update.message.reply_text("Шаблон не выполняю.")
            return True
        template = next((item for item in templates if item["id"] == pending["template_id"]), None)
        when = text
        if not template:
            context.user_data.pop("smart_planner_pending", None)
            await update.message.reply_text("Шаблон уже удалён.")
            return True
    else:
        if pending:
            return False
        normal = _normal(text)
        template = next((item for item in sorted(templates, key=lambda x: -len(x["name"]))
                         if normal == _normal(item["name"]) or normal.startswith(_normal(item["name"]) + " ")), None)
        if not template:
            return False
        when = normal[len(_normal(template["name"])):].strip()
    if not DATE_RE.search(when) or (_extract_time(when) is None and _relative_offset(when) is None):
        context.user_data["smart_planner_pending"] = {"type": "template_when", "template_id": template["id"]}
        await update.message.reply_text("На какой день и время? Например: «завтра в 15:00».")
        return True
    context.user_data.pop("smart_planner_pending", None)
    handled = await create_from_text(update, context, when, template=template)
    if not handled:
        context.user_data["smart_planner_pending"] = {"type": "template_when", "template_id": template["id"]}
        await update.message.reply_text("Не удалось разобрать дату. Укажи день и время.")
    return True


init_templates()
