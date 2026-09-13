"""Persistent preferences for the navigation module."""

from __future__ import annotations

from datetime import datetime, timezone

from core.db import conn, db_lock

DEFAULT_MODE = "driving"
DEFAULT_ARRIVAL_BUFFER_MINUTES = 15
VALID_MODES = {"driving", "transit", "walking"}
VALID_PLACES = {"office", "home"}


def init_navigation_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS navigation_preferences (
                user_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                default_origin TEXT,
                office_address TEXT,
                home_address TEXT,
                mode TEXT NOT NULL DEFAULT 'driving',
                arrival_buffer_minutes INTEGER NOT NULL DEFAULT 15,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.commit()


def _ensure_row(user_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO navigation_preferences (user_id,updated_at) VALUES (?,?)",
        (int(user_id), now),
    )


def get_navigation_preferences(user_id: int) -> dict:
    init_navigation_store()
    with db_lock:
        row = conn.execute(
            """SELECT enabled,default_origin,office_address,home_address,mode,arrival_buffer_minutes
               FROM navigation_preferences WHERE user_id=?""",
            (int(user_id),),
        ).fetchone()
    if not row:
        return {
            "enabled": True,
            "default_origin": None,
            "office_address": None,
            "home_address": None,
            "mode": DEFAULT_MODE,
            "arrival_buffer_minutes": DEFAULT_ARRIVAL_BUFFER_MINUTES,
        }
    mode = str(row[4] or DEFAULT_MODE)
    if mode not in VALID_MODES:
        mode = DEFAULT_MODE
    try:
        arrival_buffer = int(row[5])
    except (TypeError, ValueError):
        arrival_buffer = DEFAULT_ARRIVAL_BUFFER_MINUTES
    return {
        "enabled": bool(row[0]),
        "default_origin": row[1],
        "office_address": row[2],
        "home_address": row[3],
        "mode": mode,
        "arrival_buffer_minutes": max(0, min(arrival_buffer, 180)),
    }


def save_navigation_place(user_id: int, place: str, address: str, *, make_default: bool = True) -> None:
    if place not in VALID_PLACES:
        raise ValueError("Unknown navigation place")
    value = " ".join(str(address).split()).strip(" ,.;")
    if not value:
        raise ValueError("Address is required")
    if len(value) > 500:
        raise ValueError("Address is too long")
    column = "office_address" if place == "office" else "home_address"
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        _ensure_row(user_id)
        if make_default:
            conn.execute(
                f"UPDATE navigation_preferences SET {column}=?,default_origin=?,updated_at=? WHERE user_id=?",
                (value, value, now, int(user_id)),
            )
        else:
            conn.execute(
                f"UPDATE navigation_preferences SET {column}=?,updated_at=? WHERE user_id=?",
                (value, now, int(user_id)),
            )
        conn.commit()


def save_default_origin(user_id: int, address: str | None) -> None:
    value = " ".join(str(address or "").split()).strip(" ,.;") or None
    if value and len(value) > 500:
        raise ValueError("Address is too long")
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        _ensure_row(user_id)
        conn.execute(
            "UPDATE navigation_preferences SET default_origin=?,updated_at=? WHERE user_id=?",
            (value, now, int(user_id)),
        )
        conn.commit()


def save_navigation_mode(user_id: int, mode: str) -> None:
    if mode not in VALID_MODES:
        raise ValueError("Unknown navigation mode")
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        _ensure_row(user_id)
        conn.execute(
            "UPDATE navigation_preferences SET mode=?,updated_at=? WHERE user_id=?",
            (mode, now, int(user_id)),
        )
        conn.commit()


def save_arrival_buffer(user_id: int, minutes: int) -> None:
    value = int(minutes)
    if not 0 <= value <= 180:
        raise ValueError("Arrival buffer must be between 0 and 180 minutes")
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        _ensure_row(user_id)
        conn.execute(
            "UPDATE navigation_preferences SET arrival_buffer_minutes=?,updated_at=? WHERE user_id=?",
            (value, now, int(user_id)),
        )
        conn.commit()


def set_navigation_enabled(user_id: int, enabled: bool) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        _ensure_row(user_id)
        conn.execute(
            "UPDATE navigation_preferences SET enabled=?,updated_at=? WHERE user_id=?",
            (1 if enabled else 0, now, int(user_id)),
        )
        conn.commit()


init_navigation_store()
