"""Persistent storage for personal notes."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from core.db import conn, db_lock

MAX_NOTE_LENGTH = 5000
SELECT_COLUMNS = "note_id,user_id,text,normalized_text,created_at,updated_at"
SEARCH_STOP_WORDS = {
    "про",
    "заметка",
    "заметки",
    "заметку",
    "заметках",
    "запись",
    "записи",
}


def normalize_note_text(value: str) -> str:
    return " ".join(str(value).split()).strip().casefold().replace("ё", "е")


def init_note_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS notes (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(notes)").fetchall()}
        migrations = {
            "normalized_text": "TEXT",
            "updated_at": "TEXT",
        }
        for column, sql_type in migrations.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE notes ADD COLUMN {column} {sql_type}")

        rows = conn.execute(
            "SELECT note_id,text,created_at FROM notes "
            "WHERE normalized_text IS NULL OR normalized_text='' OR updated_at IS NULL OR updated_at=''"
        ).fetchall()
        for note_id, text, created_at in rows:
            timestamp = created_at or datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE notes SET normalized_text=?,updated_at=? WHERE note_id=?",
                (normalize_note_text(text), timestamp, int(note_id)),
            )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notes_user_created "
            "ON notes(user_id, created_at DESC, note_id DESC)"
        )
        conn.commit()


def _from_row(row) -> dict:
    return {
        "note_id": int(row[0]),
        "user_id": int(row[1]),
        "text": row[2],
        "normalized_text": row[3],
        "created_at": row[4],
        "updated_at": row[5],
    }


def create_note(user_id: int, text: str) -> dict:
    cleaned = " ".join(str(text).split()).strip()
    if not cleaned:
        raise ValueError("Note text is required")
    if len(cleaned) > MAX_NOTE_LENGTH:
        raise ValueError(f"Note is too long; maximum is {MAX_NOTE_LENGTH} characters")
    now = datetime.now(timezone.utc).isoformat()
    normalized = normalize_note_text(cleaned)
    with db_lock:
        cur = conn.execute(
            "INSERT INTO notes (user_id,text,normalized_text,created_at,updated_at) VALUES (?,?,?,?,?)",
            (int(user_id), cleaned, normalized, now, now),
        )
        conn.commit()
        row = conn.execute(
            f"SELECT {SELECT_COLUMNS} FROM notes WHERE note_id=?",
            (cur.lastrowid,),
        ).fetchone()
    return _from_row(row)


def list_notes(user_id: int, *, limit: int = 100) -> list[dict]:
    safe_limit = max(1, min(int(limit), 500))
    with db_lock:
        rows = conn.execute(
            f"SELECT {SELECT_COLUMNS} FROM notes WHERE user_id=? "
            "ORDER BY created_at DESC,note_id DESC LIMIT ?",
            (int(user_id), safe_limit),
        ).fetchall()
    return [_from_row(row) for row in rows]


def _search_tokens(query: str) -> list[str]:
    normalized = normalize_note_text(query)
    tokens = [token for token in re.findall(r"[a-zа-я0-9]+", normalized) if len(token) >= 2]
    return [token for token in tokens if token not in SEARCH_STOP_WORDS]


def search_notes(user_id: int, query: str, *, limit: int = 50) -> list[dict]:
    tokens = _search_tokens(query)
    if not tokens:
        return []
    clauses = ["user_id=?"]
    values: list[object] = [int(user_id)]
    for token in tokens:
        prefix_len = 4 if len(token) >= 4 else len(token)
        clauses.append("normalized_text LIKE ?")
        values.append(f"%{token[:prefix_len]}%")
    values.append(max(1, min(int(limit), 200)))
    with db_lock:
        rows = conn.execute(
            f"SELECT {SELECT_COLUMNS} FROM notes WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC,note_id DESC LIMIT ?",
            values,
        ).fetchall()
    return [_from_row(row) for row in rows]


def get_note(user_id: int, note_id: int) -> dict | None:
    with db_lock:
        row = conn.execute(
            f"SELECT {SELECT_COLUMNS} FROM notes WHERE user_id=? AND note_id=?",
            (int(user_id), int(note_id)),
        ).fetchone()
    return _from_row(row) if row else None


def delete_note(user_id: int, note_id: int) -> bool:
    with db_lock:
        cur = conn.execute(
            "DELETE FROM notes WHERE user_id=? AND note_id=?",
            (int(user_id), int(note_id)),
        )
        conn.commit()
    return cur.rowcount > 0


init_note_store()
