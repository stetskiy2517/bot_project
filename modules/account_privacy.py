"""Authenticated export and confirmed local-account erasure."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import time

from core.attention_store import init_attention_store
from core.command_store import user_operation
from core.db import conn, db_lock, get_google_account
from core.feature_access import init_feature_access_store
from core.location_context import clear_current_location
from core.memory_store import init_memory_store
from core.proactive_store import init_proactive_store
from scripts.backup_state import retention_days, MIN_SNAPSHOTS_TO_KEEP

ERASE_CONFIRMATION = "УДАЛИТЬ МОИ ДАННЫЕ"
USER_TABLES = (
    "command_effects", "command_requests", "conversation_state", "undo_actions",
    "reminder_push_policy", "review_deliveries", "assistant_preferences",
    "command_templates", "proactive_actions", "proactive_feedback", "attention_items",
    "ai_memory_event_processing", "ai_calendar_sync", "user_memories", "ai_memory_events",
    "daily_review_ai_cache", "feature_entitlements", "notes", "note_metadata", "reminder_details", "reminders", "tasks",
    "life_balance_ratings", "user_categories", "push_subscriptions", "navigation_preferences", "privacy_challenges",
    "email_auto_messages", "email_auto_accounts", "email_auto_runs", "email_auto_preferences",
    "email_accounts", "email_oauth_states", "identity_accounts",
    "oauth_states", "users", "google_accounts",
)


def init_privacy():
    init_memory_store()
    init_proactive_store()
    init_attention_store()
    init_feature_access_store()
    with db_lock:
        conn.execute("""CREATE TABLE IF NOT EXISTS privacy_challenges (
            user_id INTEGER PRIMARY KEY, digest TEXT NOT NULL, expires_at REAL NOT NULL
        )""")
        conn.commit()


def privacy_policy() -> dict:
    return {
        "backup_retention_days": retention_days(),
        "minimum_backups_kept": MIN_SNAPSHOTS_TO_KEEP,
        "notice": (
            "Стираются локальная учётная запись, заметки, задачи, напоминания, история, изученная ИИ-память, "
            "журнал и оценки проактивных действий, центр внимания, настройки, пользовательские категории, "
            "оценки жизненного баланса, доступ к ИИ, почтовые подключения и состояние автоматического разбора почты, "
            "push-подписки и локальные данные входа. События во внешнем календаре и письма в почтовых ящиках остаются. "
            "Уже отправленный push нельзя отозвать. Резервные копии не стираются этим действием: "
            "очистка выполняется при следующих резервных копированиях, последние две копии сохраняются. "
            "Поэтому срок существования старой копии может превышать настроенный срок хранения. "
            "После восстановления старой копии оператору необходимо повторно применить подтверждённые удаления."
        ),
    }


def create_erase_challenge(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with db_lock:
        conn.execute("DELETE FROM privacy_challenges WHERE expires_at<=?", (time.time(),))
        conn.execute("INSERT INTO privacy_challenges VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET digest=excluded.digest,expires_at=excluded.expires_at",
                     (user_id, hashlib.sha256(token.encode()).hexdigest(), time.time() + 300))
        conn.commit()
    return token


def export_account(user_id: int) -> dict:
    with user_operation(user_id), db_lock:
        account = get_google_account(user_id)
        if not account:
            raise ValueError("Учётная запись уже удалена")
        conn.execute("BEGIN")
        try:
            result = {
                "format_version": 3, "exported_at": datetime.now(timezone.utc).isoformat(),
                "profile": {key: account[key] for key in ("user_id", "email", "name")},
                "privacy": privacy_policy(),
            }
            selectors = {
                "identity_accounts": "provider,subject,email,name,created_at,updated_at",
                "notes": "note_id,title,text,created_at,updated_at,deleted_at",
                "note_metadata": "note_id,pinned,tags_json,checklist_json,category,updated_at",
                "reminder_details": "reminder_id,category,updated_at",
                "reminders": "reminder_id,text,remind_at,status,created_at,delivered_at,completed_at,deleted_at,repeat_rule,repeat_timezone,next_remind_at",
                "tasks": "task_id,title,due_at,status,priority,created_at,completed_at,category,estimate_minutes,flexible,calendar_event_id,scheduled_start,parent_task_id,repeat_rule,updated_at",
                "users": "timezone,work_start,work_end,work_days,buffer_minutes,category_colors",
                "user_categories": "category_key,label,color_id,semantic_key,position",
                "navigation_preferences": "enabled,default_origin,office_address,home_address,mode,arrival_buffer_minutes,parking_buffer_minutes,walking_buffer_minutes,pending_origin_json",
                "assistant_preferences": "settings_json",
                "life_balance_ratings": "category,rating,target,updated_at",
                "command_templates": "template_id,name,spec_json",
                "feature_entitlements": "feature,enabled,source,updated_at",
                "daily_review_ai_cache": "day,kind,source_hash,text,created_at",
                "email_accounts": "account_id,provider,email,display_name,enabled,created_at,updated_at",
                "email_auto_preferences": "enabled,updated_at",
                "email_auto_accounts": "account_id,initialized_at,last_scan_at,last_error",
                "email_auto_messages": "account_id,fingerprint,provider_message_id,state,reason,processed_at",
                "email_auto_runs": "run_id,created_at,new_messages,candidates,ai_text_calls,attachments_analyzed,auto_created,already_present,failed,plan_json",
                "user_memories": "memory_id,kind,memory_key,value_json,confidence,source_type,source_id,evidence,status,created_at,updated_at",
                "ai_memory_events": "entity_type,entity_id,event_type,snapshot_json,created_at",
                "ai_calendar_sync": "google_event_id,fingerprint,last_seen_at",
                "proactive_actions": "action_id,memory_id,action_type,status,reminder_id,calendar_event_id,reason,confidence,created_at,updated_at",
                "proactive_feedback": "action_id,memory_id,feedback,created_at,updated_at",
                "attention_items": "attention_id,source_type,source_key,category,priority,title,body,action_type,created_at,updated_at,seen_at,dismissed_at,pushed_at,push_attempts,push_error",
                "reminder_push_policy": "reminder_id,interval_minutes,max_repeats,repeat_count,next_repeat_at",
            }
            existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, fields in selectors.items():
                if table not in existing:
                    result[table] = []
                    continue
                cursor = conn.execute(f"SELECT {fields} FROM {table} WHERE user_id=? LIMIT 10001", (user_id,))
                rows = cursor.fetchall()
                if len(rows) > 10000:
                    raise ValueError("Экспорт слишком большой для одного ответа. Нужна выгрузка оператором.")
                names = [column[0] for column in cursor.description]
                result[table] = [dict(zip(names, row)) for row in rows]
            if len(json.dumps(result, ensure_ascii=False).encode()) > 20 * 1024 * 1024:
                raise ValueError("Экспорт превышает 20 МБ. Нужна выгрузка оператором.")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return result


def erase_account(user_id: int, token: str, confirmation: str) -> None:
    if not isinstance(token, str) or len(token) > 200 or confirmation != ERASE_CONFIRMATION:
        raise ValueError("Нужно отдельное подтверждение удаления")
    with user_operation(user_id), db_lock:
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT digest,expires_at FROM privacy_challenges WHERE user_id=?", (user_id,)).fetchone()
            if not row or row[1] < time.time() or not secrets.compare_digest(row[0], hashlib.sha256(token.encode()).hexdigest()):
                raise ValueError("Подтверждение устарело. Начни удаление заново.")
            existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in existing:
                if table.startswith("sqlite_") or not re.fullmatch(r"[a-z_]+", table):
                    continue
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                if "user_id" in columns and table not in USER_TABLES:
                    raise RuntimeError("Account erasure schema requires an update")
            for table in USER_TABLES:
                if table in existing:
                    conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
            conn.commit()
            clear_current_location(user_id)
        except Exception:
            conn.rollback()
            raise


init_privacy()
