"""Subjective life-balance ratings and desired targets owned by the user."""

from __future__ import annotations

from datetime import datetime, timezone

from core.db import conn, db_lock

CATEGORIES = {"work", "health", "rest", "travel", "family", "personal", "other"}


def init_life_balance_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS life_balance_ratings (
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                rating REAL,
                target REAL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id,category)
            )"""
        )
        conn.commit()


def _score(value: object, *, allow_none: bool = True) -> float | None:
    if value in {None, ""} and allow_none:
        return None
    if isinstance(value, bool):
        raise ValueError("Оценка должна быть числом от 0 до 10")
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Оценка должна быть числом от 0 до 10") from exc
    if not 0 <= score <= 10:
        raise ValueError("Оценка должна быть от 0 до 10")
    return round(score, 1)


def save_life_balance_rating(user_id: int, category: str, *, rating: object = None, target: object = None) -> dict:
    init_life_balance_store()
    key = str(category or "").strip().lower()
    if key not in CATEGORIES:
        raise ValueError("Неизвестная сфера жизни")
    current = get_life_balance_ratings(user_id).get(key, {})
    clean_rating = _score(rating) if rating is not None else current.get("rating")
    clean_target = _score(target) if target is not None else current.get("target")
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        conn.execute(
            "INSERT INTO life_balance_ratings(user_id,category,rating,target,updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(user_id,category) DO UPDATE SET rating=excluded.rating,target=excluded.target,updated_at=excluded.updated_at",
            (int(user_id), key, clean_rating, clean_target, now),
        )
        conn.commit()
    return {"category": key, "rating": clean_rating, "target": clean_target, "updated_at": now}


def get_life_balance_ratings(user_id: int) -> dict[str, dict]:
    init_life_balance_store()
    with db_lock:
        rows = conn.execute(
            "SELECT category,rating,target,updated_at FROM life_balance_ratings WHERE user_id=?",
            (int(user_id),),
        ).fetchall()
    return {
        str(row[0]): {"rating": row[1], "target": row[2], "updated_at": row[3]}
        for row in rows
    }


init_life_balance_store()
