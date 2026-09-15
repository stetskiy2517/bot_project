"""User feedback for proactive assistant decisions."""

from __future__ import annotations

from datetime import datetime, timezone

from core.db import conn, db_lock

FEEDBACK_VALUES = {"useful", "dismiss", "never"}


def init_proactive_feedback_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS proactive_feedback (
                user_id INTEGER NOT NULL,
                action_id INTEGER NOT NULL,
                memory_id INTEGER NOT NULL,
                feedback TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id,action_id)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proactive_feedback_user "
            "ON proactive_feedback(user_id,updated_at DESC)"
        )
        conn.commit()


def save_proactive_feedback(user_id: int, action_id: int, memory_id: int, feedback: str) -> dict:
    value = str(feedback or "").strip().lower()
    if value not in FEEDBACK_VALUES:
        raise ValueError("Неизвестная оценка предложения")
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        conn.execute(
            "INSERT INTO proactive_feedback(user_id,action_id,memory_id,feedback,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id,action_id) DO UPDATE SET "
            "feedback=excluded.feedback,memory_id=excluded.memory_id,updated_at=excluded.updated_at",
            (int(user_id), int(action_id), int(memory_id), value, now, now),
        )
        conn.commit()
    return {
        "action_id": int(action_id),
        "memory_id": int(memory_id),
        "feedback": value,
        "updated_at": now,
    }


def feedback_for_actions(user_id: int, action_ids: list[int]) -> dict[int, str]:
    init_proactive_feedback_store()
    ids = sorted({int(value) for value in action_ids if int(value) > 0})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with db_lock:
        rows = conn.execute(
            f"SELECT action_id,feedback FROM proactive_feedback WHERE user_id=? AND action_id IN ({placeholders})",
            [int(user_id), *ids],
        ).fetchall()
    return {int(row[0]): str(row[1]) for row in rows}


def feedback_summary(user_id: int) -> dict:
    init_proactive_feedback_store()
    with db_lock:
        rows = conn.execute(
            "SELECT feedback,COUNT(*) FROM proactive_feedback WHERE user_id=? GROUP BY feedback",
            (int(user_id),),
        ).fetchall()
    counts = {value: 0 for value in FEEDBACK_VALUES}
    for value, count in rows:
        if value in counts:
            counts[value] = int(count)
    return counts


init_proactive_feedback_store()
