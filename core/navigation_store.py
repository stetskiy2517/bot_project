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


def _clean_address(value: str | None) -> str | None:
    cleaned = " ".join(str(value or "").split()).strip(" ,.;")
    if not cleaned:
        return None
    if len(cleaned) > 500:
        raise ValueError("Address is too long")
    return cleaned


def _default_place(default_origin: str | None, home_address: str | None, office_address: str | None) -> str:
    origin = (default_origin or "").casefold()
    if office_address and origin == office_address.casefold():
        return "office"
    if home_address and origin == home_address.casefold():
        return "home"
    if home_address:
        return "home"
    if office_address:
        return "office"
    return "home"


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
            "default_place": "home",
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
    default_origin = row[1]
    office_address = row[2]
    home_address = row[3]
    return {
        "enabled": bool(row[0]),
        "default_origin": default_origin,
        "default_place": _default_place(default_origin, home_address, office_address),
        "office_address": office_address,
        "home_address": home_address,
        "mode": mode,
        "arrival_buffer_minutes": max(0, min(arrival_buffer, 180)),
    }


def list_navigation_user_ids() -> list[int]:
    """Return Google-connected users whose navigation is not explicitly disabled."""
    init_navigation_store()
    with db_lock:
        rows = conn.execute(
            """SELECT u.user_id
               FROM users AS u
               LEFT JOIN navigation_preferences AS n ON n.user_id=u.user_id
               WHERE u.google_token IS NOT NULL AND COALESCE(n.enabled, 1)=1
               ORDER BY u.user_id"""
        ).fetchall()
    return [int(row[0]) for row in rows]


def save_navigation_settings(
    user_id: int,
    *,
    enabled: bool,
    home_address: str | None,
    office_address: str | None,
    default_place: str,
    mode: str,
    arrival_buffer_minutes: int,
) -> None:
    if mode not in VALID_MODES:
        raise ValueError("Unknown navigation mode")
    if default_place not in VALID_PLACES:
        raise ValueError("Unknown default navigation place")
    buffer_value = int(arrival_buffer_minutes)
    if not 0 <= buffer_value <= 180:
        raise ValueError("Arrival buffer must be between 0 and 180 minutes")

    home = _clean_address(home_address)
    office = _clean_address(office_address)
    default_origin = home if default_place == "home" else office
    if not default_origin:
        default_origin = office if default_place == "home" else home

    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        _ensure_row(user_id)
        conn.execute(
            """UPDATE navigation_preferences
               SET enabled=?,default_origin=?,office_address=?,home_address=?,mode=?,arrival_buffer_minutes=?,updated_at=?
               WHERE user_id=?""",
            (
                1 if enabled else 0,
                default_origin,
                office,
                home,
                mode,
                buffer_value,
                now,
                int(user_id),
            ),
        )
        conn.commit()


def save_navigation_place(user_id: int, place: str, address: str, *, make_default: bool = True) -> None:
    if place not in VALID_PLACES:
        raise ValueError("Unknown navigation place")
    value = _clean_address(address)
    if not value:
        raise ValueError("Address is required")
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
    value = _clean_address(address)
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
