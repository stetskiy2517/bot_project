"""Explicit assistant preferences and user-created calendar templates."""
from __future__ import annotations

import json
import re
import time

from core.db import DEFAULT_CATEGORY_COLORS, conn, db_lock

CLOCK_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
DEFAULTS = {
    "morning_enabled": False, "morning_time": "08:00",
    "evening_enabled": False, "evening_time": "20:00",
    "quiet_enabled": False, "quiet_start": "22:00", "quiet_end": "08:00",
}


def init_assistant_store() -> None:
    with db_lock:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS assistant_preferences (
                user_id INTEGER PRIMARY KEY, settings_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS command_templates (
                template_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, name TEXT NOT NULL,
                normalized_name TEXT NOT NULL, title TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL, category TEXT NOT NULL,
                updated_at REAL NOT NULL, UNIQUE(user_id, normalized_name)
            );
            CREATE TABLE IF NOT EXISTS notification_attempts (
                user_id INTEGER NOT NULL, delivery_key TEXT NOT NULL,
                status TEXT NOT NULL, attempted_at REAL NOT NULL,
                accepted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, delivery_key)
            );
            CREATE TABLE IF NOT EXISTS reminder_alerts (
                user_id INTEGER NOT NULL, reminder_id INTEGER PRIMARY KEY,
                interval_minutes INTEGER NOT NULL, max_repeats INTEGER NOT NULL,
                occurrence TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                next_at REAL
            );
            CREATE TABLE IF NOT EXISTS privacy_delete_tickets (
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                session_hash TEXT NOT NULL, expires_at REAL NOT NULL
            );
        """)
        conn.commit()


def assistant_preferences(user_id: int) -> dict:
    with db_lock:
        row = conn.execute(
            "SELECT settings_json FROM assistant_preferences WHERE user_id=?", (int(user_id),),
        ).fetchone()
    stored = json.loads(row[0]) if row else {}
    return {**DEFAULTS, **stored}


def save_assistant_preferences(user_id: int, values: dict) -> dict:
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError("Неизвестные настройки помощника.")
    parsed = assistant_preferences(user_id)
    for name, value in values.items():
        if name.endswith("_enabled"):
            if not isinstance(value, bool):
                raise ValueError("Переключатель должен быть включён или выключен.")
        elif not isinstance(value, str) or not CLOCK_RE.fullmatch(value):
            raise ValueError("Время должно быть в формате HH:MM.")
        parsed[name] = value
    if parsed["quiet_enabled"] and parsed["quiet_start"] == parsed["quiet_end"]:
        raise ValueError("Начало и конец тихих часов не должны совпадать.")
    with db_lock:
        conn.execute(
            "INSERT INTO assistant_preferences(user_id,settings_json) VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json",
            (int(user_id), json.dumps(parsed)),
        )
        conn.commit()
    return parsed


def normalize_template_name(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def _template(row) -> dict:
    return dict(zip(
        ("id", "name", "title", "duration_minutes", "category", "updated_at"), row,
    ))


def list_templates(user_id: int) -> list[dict]:
    with db_lock:
        rows = conn.execute(
            "SELECT template_id,name,title,duration_minutes,category,updated_at "
            "FROM command_templates WHERE user_id=? ORDER BY normalized_name LIMIT 50",
            (int(user_id),),
        ).fetchall()
    return [_template(row) for row in rows]


def get_template(user_id: int, template_id: int) -> dict | None:
    with db_lock:
        row = conn.execute(
            "SELECT template_id,name,title,duration_minutes,category,updated_at "
            "FROM command_templates WHERE user_id=? AND template_id=?",
            (int(user_id), int(template_id)),
        ).fetchone()
    return _template(row) if row else None


def save_template(user_id: int, values: dict, template_id: int | None = None) -> dict:
    if not isinstance(values, dict) or set(values) - {"name", "title", "duration_minutes", "category"}:
        raise ValueError("Неверные поля шаблона.")
    old = get_template(user_id, template_id) if template_id is not None else None
    if template_id is not None and old is None:
        raise LookupError("Шаблон не найден.")
    parsed = {**(old or {}), **values}
    for name, limit in (("name", 80), ("title", 200)):
        value = parsed.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"Поле {name}: от 1 до {limit} символов.")
        if any(ord(char) < 32 for char in value):
            raise ValueError("Убери управляющие символы из шаблона.")
        parsed[name] = " ".join(value.split())
    duration = parsed.get("duration_minutes", 60)
    if isinstance(duration, bool) or not isinstance(duration, int) or not 5 <= duration <= 720:
        raise ValueError("Длительность: целое число от 5 до 720 минут.")
    category = parsed.get("category", "work")
    if not isinstance(category, str) or category not in DEFAULT_CATEGORY_COLORS:
        raise ValueError("Выбери существующую категорию.")
    normalized = normalize_template_name(parsed["name"])
    if normalized in {
        "да", "нет", "отмена", "стоп", "мой день", "обзор дня", "вечерний разбор",
        "отмени последнее действие", "верни последнее действие",
    }:
        raise ValueError("Это имя занято командой приложения. Выбери другое.")
    now = time.time()
    with db_lock:
        duplicate = conn.execute(
            "SELECT template_id FROM command_templates WHERE user_id=? AND normalized_name=?",
            (int(user_id), normalized),
        ).fetchone()
        if duplicate and duplicate[0] != template_id:
            raise ValueError("Шаблон с таким именем уже есть.")
        if template_id is None:
            count = conn.execute(
                "SELECT COUNT(*) FROM command_templates WHERE user_id=?", (int(user_id),),
            ).fetchone()[0]
            if count >= 50:
                raise ValueError("Можно сохранить не более 50 шаблонов.")
            cur = conn.execute(
                "INSERT INTO command_templates(user_id,name,normalized_name,title,duration_minutes,category,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (int(user_id), parsed["name"], normalized, parsed["title"], duration, category, now),
            )
            template_id = cur.lastrowid
        else:
            conn.execute(
                "UPDATE command_templates SET name=?,normalized_name=?,title=?,duration_minutes=?,"
                "category=?,updated_at=? WHERE user_id=? AND template_id=?",
                (parsed["name"], normalized, parsed["title"], duration, category, now, int(user_id), template_id),
            )
        conn.commit()
    return get_template(user_id, template_id)


def delete_template(user_id: int, template_id: int) -> bool:
    with db_lock:
        deleted = conn.execute(
            "DELETE FROM command_templates WHERE user_id=? AND template_id=?",
            (int(user_id), int(template_id)),
        ).rowcount
        conn.commit()
    return bool(deleted)


def alert_preferences(user_id: int, reminder_id: int) -> dict:
    with db_lock:
        row = conn.execute(
            "SELECT interval_minutes,max_repeats,occurrence,attempts,next_at FROM reminder_alerts "
            "WHERE user_id=? AND reminder_id=?", (int(user_id), int(reminder_id)),
        ).fetchone()
    if not row:
        return {"interval_minutes": 15, "max_repeats": 0, "attempts": 0, "next_at": None}
    return dict(zip(("interval_minutes", "max_repeats", "occurrence", "attempts", "next_at"), row))


def save_alert_preferences(user_id: int, reminder_id: int, values: dict) -> dict:
    if not isinstance(values, dict) or set(values) - {"interval_minutes", "max_repeats"}:
        raise ValueError("Неверные настройки повторных уведомлений.")
    parsed = {**alert_preferences(user_id, reminder_id), **values}
    interval, count = parsed["interval_minutes"], parsed["max_repeats"]
    if (type(interval) is not int or not 5 <= interval <= 180
            or type(count) is not int or not 0 <= count <= 3):
        raise ValueError("Интервал: 5–180 минут. Повторы: от 0 до 3.")
    with db_lock:
        row = conn.execute(
            "SELECT remind_at,delivered_at,status FROM reminders "
            "WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
            (int(user_id), int(reminder_id)),
        ).fetchone()
        if not row:
            raise LookupError("Напоминание не найдено.")
        # Editing the policy must not reset the already used attempt budget.
        conn.execute(
            "INSERT INTO reminder_alerts(user_id,reminder_id,interval_minutes,max_repeats,occurrence,next_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(reminder_id) DO UPDATE SET "
            "interval_minutes=excluded.interval_minutes,max_repeats=excluded.max_repeats,"
            "next_at=CASE WHEN excluded.max_repeats=0 THEN NULL "
            "WHEN reminder_alerts.next_at IS NULL THEN excluded.next_at ELSE reminder_alerts.next_at END",
            (int(user_id), int(reminder_id), interval, count, row[0],
             time.time() + interval * 60 if count and row[2] == "delivered" else None),
        )
        conn.commit()
    return alert_preferences(user_id, reminder_id)


init_assistant_store()
