"""Export and confirmed deletion of the current account's local data."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import secrets
import time

from core.db import conn, db_lock
from core.user_operations import session_hash

DELETE_PHRASE = "УДАЛИТЬ МОИ ДАННЫЕ"
OWNED_TABLES = (
    "notes", "reminders", "tasks", "ai_memory_events", "push_subscriptions",
    "navigation_preferences", "assistant_preferences", "command_templates",
    "reminder_alerts", "notification_attempts", "undo_actions",
    "conversation_states", "command_requests", "web_sessions",
    "privacy_delete_tickets", "oauth_states", "users", "google_accounts",
)
EXPORT_COLUMNS = {
    "notes": "note_id,title,text,created_at,updated_at,deleted_at",
    "reminders": "reminder_id,text,remind_at,status,created_at,delivered_at,completed_at,"
                 "deleted_at,repeat_rule,repeat_timezone,next_remind_at",
    "tasks": "task_id,title,due_at,status,priority,created_at,completed_at",
    "users": "name,timezone,work_start,work_end,work_days,buffer_minutes,category_colors",
    "navigation_preferences": "enabled,default_origin,home_address,office_address,mode,arrival_buffer_minutes",
    "assistant_preferences": "settings_json",
    "command_templates": "template_id,name,title,duration_minutes,category,updated_at",
    "ai_memory_events": "event_id,entity_type,entity_id,event_type,snapshot_json,created_at",
    "reminder_alerts": "reminder_id,interval_minutes,max_repeats,attempts,next_at",
}


def privacy_notice() -> dict:
    try:
        days = max(1, int(os.getenv("BACKUP_RETENTION_DAYS", "14")))
    except ValueError:
        days = 14
    return {
        "confirmation": DELETE_PHRASE,
        "local_only": True,
        "backup_retention_days": days,
        "message": "Будут удалены локальные заметки, напоминания, настройки, журнал памяти, "
                   "шаблоны, подписки Push и сохранённые токены. Все сессии завершатся. "
                   "События в Google Calendar останутся. Отменить удаление аккаунта нельзя. "
                   f"Копии на сервере хранятся до {days} дней при штатной очистке; "
                   "отдельные выгруженные администратором копии этим действием не стираются.",
    }


def _rows(table: str, columns: str, user_id: int) -> list[dict]:
    cursor = conn.execute(f"SELECT {columns} FROM {table} WHERE user_id=?", (int(user_id),))
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def export_local_data(user_id: int) -> dict:
    with db_lock:
        conn.execute("BEGIN")
        try:
            result = {table: _rows(table, columns, user_id) for table, columns in EXPORT_COLUMNS.items()}
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    # Internal delivery errors can include service details: do not export them via old snapshots.
    for event in result["ai_memory_events"]:
        snapshot = json.loads(event.pop("snapshot_json"))
        event["snapshot"] = {key: value for key, value in snapshot.items()
                             if key not in {"last_error", "google_token", "user_id"}}
    return {"format": "personal-secretary-export-v1",
            "created_at": datetime.now(timezone.utc).isoformat(), "data": result,
            "scope": "Локальные данные. Без токенов, Push-ключей и событий Google Calendar."}


def delete_ticket(user_id: int, sid: str) -> str:
    value = secrets.token_urlsafe(32)
    with db_lock:
        conn.execute("DELETE FROM privacy_delete_tickets WHERE expires_at<? OR user_id=?",
                     (time.time(), int(user_id)))
        conn.execute(
            "INSERT INTO privacy_delete_tickets(token_hash,user_id,session_hash,expires_at) VALUES (?,?,?,?)",
            (hashlib.sha256(value.encode()).hexdigest(), int(user_id), session_hash(sid), time.time() + 300),
        )
        conn.commit()
    return value


def purge_local_account(user_id: int, sid: str, ticket: str, phrase: str) -> None:
    if phrase != DELETE_PHRASE or not isinstance(ticket, str) or not ticket:
        raise ValueError("Подтверждение удаления не совпало.")
    token = hashlib.sha256(ticket.encode()).hexdigest()
    with db_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            valid = conn.execute(
                "SELECT 1 FROM privacy_delete_tickets WHERE token_hash=? AND user_id=? "
                "AND session_hash=? AND expires_at>?",
                (token, int(user_id), session_hash(sid), time.time()),
            ).fetchone()
            if not valid:
                raise ValueError("Подтверждение устарело. Начни удаление заново.")
            for table in OWNED_TABLES:
                conn.execute(f"DELETE FROM {table} WHERE user_id=?", (int(user_id),))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
