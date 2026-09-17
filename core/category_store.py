"""User-owned calendar categories with stable semantic defaults."""

from __future__ import annotations

import json
import re
import secrets
import sys

from core import db

DEFAULT_CATEGORY_LABELS = {
    "work": "Работа",
    "health": "Здоровье",
    "rest": "Отдых",
    "travel": "Поездки",
    "family": "Семья",
    "personal": "Личное",
    "other": "Прочее",
}
MAX_USER_CATEGORIES = 12
CATEGORY_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

_legacy_get_category_colors = db.get_category_colors


def _init_table() -> None:
    with db.db_lock:
        db.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_categories (
                user_id INTEGER NOT NULL,
                category_key TEXT NOT NULL,
                label TEXT NOT NULL,
                color_id TEXT,
                semantic_key TEXT,
                position INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, category_key)
            )
            """
        )
        db.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_categories_order "
            "ON user_categories(user_id, position, category_key)"
        )
        db.conn.commit()


def _valid_color(value: object) -> str | None:
    if value is None or value == "":
        return None
    color = str(value)
    if color not in db.GOOGLE_EVENT_COLOR_IDS:
        raise ValueError("Неизвестный цвет категории")
    return color


def _clean_label(value: object) -> str:
    label = " ".join(str(value or "").split()).strip()
    if not label:
        raise ValueError("Название категории не может быть пустым")
    if len(label) > 40:
        raise ValueError("Название категории должно быть не длиннее 40 символов")
    return label


def _legacy_colors_locked(user_id: int) -> dict[str, str | None]:
    colors = dict(db.DEFAULT_CATEGORY_COLORS)
    row = db.conn.execute(
        "SELECT category_colors FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if not row or not row[0]:
        return colors
    try:
        stored = json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return colors
    if not isinstance(stored, dict):
        return colors
    for key, value in stored.items():
        if key not in colors:
            continue
        if value is None or value == "":
            colors[key] = None
        elif str(value) in db.GOOGLE_EVENT_COLOR_IDS:
            colors[key] = str(value)
    return colors


def _save_legacy_color_locked(user_id: int, category_key: str, color_id: str | None) -> None:
    if category_key not in db.DEFAULT_CATEGORY_COLORS:
        return
    db.conn.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user_id,))
    colors = _legacy_colors_locked(user_id)
    colors[category_key] = color_id
    db.conn.execute(
        "UPDATE users SET category_colors=? WHERE user_id=?",
        (json.dumps(colors, ensure_ascii=False), user_id),
    )


def _seed_locked(user_id: int) -> None:
    db.conn.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user_id,))
    exists = db.conn.execute(
        "SELECT 1 FROM user_categories WHERE user_id=? LIMIT 1",
        (user_id,),
    ).fetchone()
    if exists:
        return
    colors = _legacy_colors_locked(user_id)
    for position, (key, label) in enumerate(DEFAULT_CATEGORY_LABELS.items()):
        db.conn.execute(
            """
            INSERT INTO user_categories(user_id, category_key, label, color_id, semantic_key, position)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, key, label, colors.get(key), key, position),
        )


def _sync_legacy_colors_locked(user_id: int) -> None:
    colors = _legacy_colors_locked(user_id)
    for key, color_id in colors.items():
        db.conn.execute(
            "UPDATE user_categories SET color_id=? WHERE user_id=? AND category_key=?",
            (color_id, user_id, key),
        )


def _rows_locked(user_id: int):
    _seed_locked(user_id)
    _sync_legacy_colors_locked(user_id)
    return db.conn.execute(
        """
        SELECT category_key, label, color_id, semantic_key, position
        FROM user_categories
        WHERE user_id=?
        ORDER BY position, category_key
        """,
        (user_id,),
    ).fetchall()


def _row_payload(row) -> dict:
    return {
        "key": str(row[0]),
        "label": str(row[1]),
        "color_id": str(row[2]) if row[2] not in {None, ""} else None,
        "semantic_key": str(row[3]) if row[3] else None,
        "position": int(row[4]),
    }


def get_user_categories(user_id: int) -> list[dict]:
    with db.db_lock:
        rows = _rows_locked(int(user_id))
        db.conn.commit()
        return [_row_payload(row) for row in rows]


def get_category_colors(user_id: int) -> dict[str, str | None]:
    return {item["key"]: item["color_id"] for item in get_user_categories(user_id)}


def get_category_labels(user_id: int) -> dict[str, str]:
    return {item["key"]: item["label"] for item in get_user_categories(user_id)}


def create_user_category(user_id: int, label: object, color_id: object = None) -> dict:
    user_id = int(user_id)
    clean_label = _clean_label(label)
    clean_color = _valid_color(color_id)
    with db.db_lock:
        rows = _rows_locked(user_id)
        if len(rows) >= MAX_USER_CATEGORIES:
            raise ValueError(f"Можно создать не больше {MAX_USER_CATEGORIES} категорий")
        if any(str(row[1]).casefold() == clean_label.casefold() for row in rows):
            raise ValueError("Категория с таким названием уже есть")
        key = f"custom_{secrets.token_hex(6)}"
        while db.conn.execute(
            "SELECT 1 FROM user_categories WHERE user_id=? AND category_key=?",
            (user_id, key),
        ).fetchone():
            key = f"custom_{secrets.token_hex(6)}"
        position = max((int(row[4]) for row in rows), default=-1) + 1
        db.conn.execute(
            """
            INSERT INTO user_categories(user_id, category_key, label, color_id, semantic_key, position)
            VALUES (?, ?, ?, ?, NULL, ?)
            """,
            (user_id, key, clean_label, clean_color, position),
        )
        db.conn.commit()
        row = db.conn.execute(
            "SELECT category_key, label, color_id, semantic_key, position FROM user_categories "
            "WHERE user_id=? AND category_key=?",
            (user_id, key),
        ).fetchone()
        return _row_payload(row)


def update_user_category(
    user_id: int,
    category_key: str,
    *,
    label: object | None = None,
    color_id: object = ...,
) -> dict:
    user_id = int(user_id)
    key = str(category_key or "").strip().lower()
    if not CATEGORY_KEY_RE.fullmatch(key):
        raise ValueError("Неизвестная категория")
    with db.db_lock:
        rows = _rows_locked(user_id)
        current = next((row for row in rows if str(row[0]) == key), None)
        if current is None:
            raise KeyError(key)
        next_label = str(current[1]) if label is None else _clean_label(label)
        if any(str(row[0]) != key and str(row[1]).casefold() == next_label.casefold() for row in rows):
            raise ValueError("Категория с таким названием уже есть")
        next_color = current[2] if color_id is ... else _valid_color(color_id)
        db.conn.execute(
            "UPDATE user_categories SET label=?, color_id=? WHERE user_id=? AND category_key=?",
            (next_label, next_color, user_id, key),
        )
        _save_legacy_color_locked(user_id, key, next_color)
        db.conn.commit()
        row = db.conn.execute(
            "SELECT category_key, label, color_id, semantic_key, position FROM user_categories "
            "WHERE user_id=? AND category_key=?",
            (user_id, key),
        ).fetchone()
        return _row_payload(row)


def delete_user_category(user_id: int, category_key: str) -> bool:
    user_id = int(user_id)
    key = str(category_key or "").strip().lower()
    if not CATEGORY_KEY_RE.fullmatch(key):
        return False
    with db.db_lock:
        rows = _rows_locked(user_id)
        if not any(str(row[0]) == key for row in rows):
            return False
        if len(rows) <= 1:
            raise ValueError("Нельзя удалить последнюю категорию")
        db.conn.execute(
            "DELETE FROM user_categories WHERE user_id=? AND category_key=?",
            (user_id, key),
        )
        db.conn.commit()
        return True


def install_dynamic_category_support() -> None:
    """Route existing calendar imports through the user-owned category registry."""
    current = db.get_category_colors
    if current is not get_category_colors:
        db.get_category_colors = get_category_colors
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("modules.") or module is None:
            continue
        if getattr(module, "get_category_colors", None) in {current, _legacy_get_category_colors}:
            setattr(module, "get_category_colors", get_category_colors)


_init_table()
