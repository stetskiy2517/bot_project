"""Editable details for saved reminders without changing delivery semantics."""

from __future__ import annotations

from datetime import datetime, timezone

from core.ai_memory_store import record_ai_memory_event
from core.db import conn, db_lock
from core.library_store import get_saved_reminder
from core.reminder_recurrence import next_repeat_after, validate_repeat_rule

VALID_REMINDER_CATEGORIES = frozenset(
    {"work", "health", "rest", "travel", "family", "personal", "other"}
)
_UNSET = object()
_initialized = False


def init_reminder_detail_store() -> None:
    global _initialized
    if _initialized:
        return
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS reminder_details (
                reminder_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                category TEXT,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminder_details_user "
            "ON reminder_details(user_id, reminder_id)"
        )
        conn.commit()
        _initialized = True


def _owned_reminder_row(user_id: int, reminder_id: int):
    return conn.execute(
        "SELECT reminder_id,text,remind_at,status,repeat_rule,repeat_timezone "
        "FROM reminders WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
        (int(user_id), int(reminder_id)),
    ).fetchone()


def get_reminder_category_override(user_id: int, reminder_id: int) -> str | None:
    init_reminder_detail_store()
    with db_lock:
        row = conn.execute(
            "SELECT category FROM reminder_details WHERE user_id=? AND reminder_id=?",
            (int(user_id), int(reminder_id)),
        ).fetchone()
    if not row:
        return None
    value = str(row[0] or "").strip().lower()
    return value if value in VALID_REMINDER_CATEGORIES else None


def edit_saved_reminder(
    user_id: int,
    reminder_id: int,
    *,
    text: object = _UNSET,
    category: object = _UNSET,
    remind_at: object = _UNSET,
    repeat_rule: object = _UNSET,
    repeat_timezone: str | None = None,
) -> dict | None:
    """Edit user-owned reminder fields in one transaction.

    ``category=None`` resets the manual category back to automatic detection.
    ``repeat_rule=None`` disables recurrence. Changing the date/time reactivates
    the reminder just like the existing reschedule action.
    """
    init_reminder_detail_store()
    now = datetime.now(timezone.utc)

    normalized_text: str | None = None
    if text is not _UNSET:
        normalized_text = " ".join(str(text or "").split()).strip()
        if not normalized_text:
            raise ValueError("Текст напоминания не может быть пустым")
        if len(normalized_text) > 500:
            raise ValueError("Текст напоминания слишком длинный")

    normalized_category: str | None = None
    if category is not _UNSET and category is not None:
        normalized_category = str(category).strip().lower()
        if normalized_category not in VALID_REMINDER_CATEGORIES:
            raise ValueError("Неизвестная категория напоминания")

    parsed_remind_at: datetime | None = None
    if remind_at is not _UNSET:
        if isinstance(remind_at, datetime):
            parsed_remind_at = remind_at
        else:
            try:
                parsed_remind_at = datetime.fromisoformat(str(remind_at or "").replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Некорректная дата или время") from exc
        if parsed_remind_at.tzinfo is None:
            raise ValueError("Время должно содержать часовой пояс")
        parsed_remind_at = parsed_remind_at.astimezone(timezone.utc)
        if parsed_remind_at <= now:
            raise ValueError("Новое время должно быть в будущем")

    normalized_repeat: str | None = None
    if repeat_rule is not _UNSET:
        normalized_repeat = validate_repeat_rule(repeat_rule)

    with db_lock:
        try:
            row = _owned_reminder_row(user_id, reminder_id)
            if not row:
                return None

            _, current_text, current_remind_at, _status, current_repeat, current_repeat_tz = row
            effective_time = parsed_remind_at
            if effective_time is None:
                effective_time = datetime.fromisoformat(str(current_remind_at).replace("Z", "+00:00"))
                if effective_time.tzinfo is None:
                    effective_time = effective_time.replace(tzinfo=timezone.utc)
                effective_time = effective_time.astimezone(timezone.utc)

            effective_repeat = normalized_repeat if repeat_rule is not _UNSET else current_repeat
            effective_timezone = str(repeat_timezone or current_repeat_tz or "UTC") if effective_repeat else None
            next_value = (
                next_repeat_after(effective_time, effective_repeat, effective_timezone, now).isoformat()
                if effective_repeat
                else None
            )

            updates: list[str] = []
            params: list[object] = []
            if text is not _UNSET:
                updates.append("text=?")
                params.append(normalized_text)
            if remind_at is not _UNSET:
                updates.extend(
                    [
                        "remind_at=?",
                        "status='pending'",
                        "delivered_at=NULL",
                        "completed_at=NULL",
                        "lease_until=NULL",
                        "delivery_attempts=0",
                        "last_error=NULL",
                    ]
                )
                params.append(effective_time.isoformat())
            if repeat_rule is not _UNSET or remind_at is not _UNSET:
                updates.extend(["repeat_rule=?", "repeat_timezone=?", "next_remind_at=?"])
                params.extend([effective_repeat, effective_timezone, next_value])

            if updates:
                params.extend([int(user_id), int(reminder_id)])
                conn.execute(
                    "UPDATE reminders SET " + ",".join(updates) +
                    " WHERE user_id=? AND reminder_id=? AND deleted_at IS NULL",
                    params,
                )

            if category is not _UNSET:
                if category is None:
                    conn.execute(
                        "DELETE FROM reminder_details WHERE user_id=? AND reminder_id=?",
                        (int(user_id), int(reminder_id)),
                    )
                else:
                    conn.execute(
                        "INSERT INTO reminder_details (reminder_id,user_id,category,updated_at) "
                        "VALUES (?,?,?,?) ON CONFLICT(reminder_id) DO UPDATE SET "
                        "user_id=excluded.user_id,category=excluded.category,updated_at=excluded.updated_at",
                        (
                            int(reminder_id),
                            int(user_id),
                            normalized_category,
                            now.isoformat(),
                        ),
                    )

            snapshot = {
                "reminder_id": int(reminder_id),
                "text": normalized_text if text is not _UNSET else current_text,
                "remind_at": effective_time.isoformat(),
                "repeat_rule": effective_repeat,
                "repeat_timezone": effective_timezone,
                "category": normalized_category if category is not _UNSET else get_reminder_category_override(user_id, reminder_id),
            }
            record_ai_memory_event(
                int(user_id),
                "reminder",
                int(reminder_id),
                "updated",
                snapshot,
                commit=False,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return get_saved_reminder(user_id, reminder_id)
