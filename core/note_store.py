"""Persistent storage for personal notes."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from core.ai_memory_store import record_ai_memory_event
from core.db import conn, db_lock

MAX_NOTE_LENGTH = 5000
MAX_NOTE_TITLE_LENGTH = 120
TITLE_WORD_LIMIT = 6
SELECT_COLUMNS = (
    "note_id,user_id,title,normalized_title,text,normalized_text,created_at,updated_at,deleted_at"
)
SEARCH_STOP_WORDS = {
    "про",
    "по",
    "заметка",
    "заметки",
    "заметку",
    "заметках",
    "запись",
    "записи",
}


def normalize_note_text(value: str) -> str:
    return " ".join(str(value).split()).strip().casefold().replace("ё", "е")


def _clean_note_text(value: str) -> str:
    return " ".join(str(value).split()).strip()


def _clean_note_title(value: str) -> str:
    return " ".join(str(value).replace("\n", " ").replace("\r", " ").split()).strip()


def derive_note_title(text: str) -> str:
    """Build a short deterministic title without calling AI."""
    raw = str(text or "").strip()
    if not raw:
        return "Без названия"

    first_line = raw.splitlines()[0].strip()
    first_line = _clean_note_title(first_line)
    if not first_line:
        first_line = _clean_note_text(raw)

    sentence = re.match(r"^(?P<title>.{2,80}?)[.!?](?:\s|$)", first_line)
    if sentence:
        candidate = _clean_note_title(sentence.group("title"))
        if 2 <= len(candidate) <= MAX_NOTE_TITLE_LENGTH:
            return candidate

    for separator in (":", " — ", " – "):
        if separator in first_line:
            candidate = _clean_note_title(first_line.split(separator, 1)[0])
            if 2 <= len(candidate) <= MAX_NOTE_TITLE_LENGTH:
                return candidate

    words = first_line.split()
    if len(words) <= TITLE_WORD_LIMIT and len(first_line) <= MAX_NOTE_TITLE_LENGTH:
        return first_line
    return " ".join(words[:TITLE_WORD_LIMIT])[:MAX_NOTE_TITLE_LENGTH].rstrip()


def init_note_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS notes (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            )"""
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(notes)").fetchall()}
        migrations = {
            "title": "TEXT",
            "normalized_title": "TEXT",
            "normalized_text": "TEXT",
            "updated_at": "TEXT",
            "deleted_at": "TEXT",
        }
        for column, sql_type in migrations.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE notes ADD COLUMN {column} {sql_type}")

        rows = conn.execute(
            "SELECT note_id,text,title,normalized_title,normalized_text,created_at,updated_at FROM notes"
        ).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        for note_id, text, title, normalized_title, normalized_text, created_at, updated_at in rows:
            resolved_title = _clean_note_title(title) if title else derive_note_title(text)
            resolved_normalized_title = normalized_title or normalize_note_text(resolved_title)
            resolved_normalized_text = normalized_text or normalize_note_text(text)
            resolved_updated_at = updated_at or created_at or now
            if (
                title != resolved_title
                or normalized_title != resolved_normalized_title
                or normalized_text != resolved_normalized_text
                or updated_at != resolved_updated_at
            ):
                conn.execute(
                    "UPDATE notes SET title=?,normalized_title=?,normalized_text=?,updated_at=? WHERE note_id=?",
                    (
                        resolved_title,
                        resolved_normalized_title,
                        resolved_normalized_text,
                        resolved_updated_at,
                        int(note_id),
                    ),
                )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notes_user_created "
            "ON notes(user_id, created_at DESC, note_id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notes_user_updated "
            "ON notes(user_id, updated_at DESC, note_id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notes_user_title "
            "ON notes(user_id, normalized_title)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notes_user_deleted_updated "
            "ON notes(user_id, deleted_at, updated_at DESC, note_id DESC)"
        )
        conn.commit()


def _from_row(row) -> dict:
    return {
        "note_id": int(row[0]),
        "user_id": int(row[1]),
        "title": row[2],
        "normalized_title": row[3],
        "text": row[4],
        "normalized_text": row[5],
        "created_at": row[6],
        "updated_at": row[7],
        "deleted_at": row[8],
    }


def create_note(user_id: int, text: str, *, title: str | None = None) -> dict:
    cleaned = _clean_note_text(text)
    if not cleaned:
        raise ValueError("Текст заметки пустой")
    if len(cleaned) > MAX_NOTE_LENGTH:
        raise ValueError(f"Заметка слишком длинная. Максимум {MAX_NOTE_LENGTH} символов")

    cleaned_title = _clean_note_title(title) if title else derive_note_title(cleaned)
    if not cleaned_title:
        cleaned_title = derive_note_title(cleaned)
    if len(cleaned_title) > MAX_NOTE_TITLE_LENGTH:
        raise ValueError(f"Название заметки слишком длинное. Максимум {MAX_NOTE_TITLE_LENGTH} символов")

    now = datetime.now(timezone.utc).isoformat()
    normalized = normalize_note_text(cleaned)
    normalized_title = normalize_note_text(cleaned_title)
    with db_lock:
        try:
            cur = conn.execute(
                "INSERT INTO notes "
                "(user_id,title,normalized_title,text,normalized_text,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (int(user_id), cleaned_title, normalized_title, cleaned, normalized, now, now),
            )
            row = conn.execute(
                f"SELECT {SELECT_COLUMNS} FROM notes WHERE note_id=?",
                (cur.lastrowid,),
            ).fetchone()
            note = _from_row(row)
            record_ai_memory_event(
                user_id,
                "note",
                note["note_id"],
                "created",
                note,
                commit=False,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return note


def append_note(user_id: int, note_id: int, addition: str) -> dict | None:
    addition = _clean_note_text(addition)
    if not addition:
        raise ValueError("Текст для дополнения заметки пустой")
    with db_lock:
        try:
            row = conn.execute(
                f"SELECT {SELECT_COLUMNS} FROM notes "
                "WHERE user_id=? AND note_id=? AND deleted_at IS NULL",
                (int(user_id), int(note_id)),
            ).fetchone()
            if not row:
                return None
            current = _from_row(row)
            combined = f"{current['text'].rstrip()}\n{addition}".strip()
            if len(combined) > MAX_NOTE_LENGTH:
                raise ValueError(f"Заметка слишком длинная. Максимум {MAX_NOTE_LENGTH} символов")
            updated_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE notes SET text=?,normalized_text=?,updated_at=? "
                "WHERE user_id=? AND note_id=? AND deleted_at IS NULL",
                (
                    combined,
                    normalize_note_text(combined),
                    updated_at,
                    int(user_id),
                    int(note_id),
                ),
            )
            row = conn.execute(
                f"SELECT {SELECT_COLUMNS} FROM notes "
                "WHERE user_id=? AND note_id=? AND deleted_at IS NULL",
                (int(user_id), int(note_id)),
            ).fetchone()
            note = _from_row(row)
            record_ai_memory_event(
                user_id,
                "note",
                note["note_id"],
                "updated",
                note,
                commit=False,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return note


def list_notes(user_id: int, *, limit: int = 100) -> list[dict]:
    safe_limit = max(1, min(int(limit), 500))
    with db_lock:
        rows = conn.execute(
            f"SELECT {SELECT_COLUMNS} FROM notes WHERE user_id=? AND deleted_at IS NULL "
            "ORDER BY updated_at DESC,created_at DESC,note_id DESC LIMIT ?",
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

    clauses = ["user_id=?", "deleted_at IS NULL"]
    values: list[object] = [int(user_id)]
    for token in tokens:
        prefix_len = 4 if len(token) >= 4 else len(token)
        pattern = f"%{token[:prefix_len]}%"
        clauses.append("(normalized_title LIKE ? OR normalized_text LIKE ?)")
        values.extend((pattern, pattern))

    normalized_query = normalize_note_text(query)
    values.extend(
        (
            normalized_query,
            f"{normalized_query}%",
            max(1, min(int(limit), 200)),
        )
    )
    with db_lock:
        rows = conn.execute(
            f"SELECT {SELECT_COLUMNS} FROM notes WHERE {' AND '.join(clauses)} "
            "ORDER BY CASE "
            "WHEN normalized_title=? THEN 0 "
            "WHEN normalized_title LIKE ? THEN 1 "
            "ELSE 2 END, "
            "updated_at DESC,created_at DESC,note_id DESC LIMIT ?",
            values,
        ).fetchall()
    return [_from_row(row) for row in rows]


def get_note(user_id: int, note_id: int) -> dict | None:
    with db_lock:
        row = conn.execute(
            f"SELECT {SELECT_COLUMNS} FROM notes "
            "WHERE user_id=? AND note_id=? AND deleted_at IS NULL",
            (int(user_id), int(note_id)),
        ).fetchone()
    return _from_row(row) if row else None


def delete_note(user_id: int, note_id: int) -> bool:
    """Hide a note from the user while retaining it and its deletion event for AI memory."""
    deleted_at = datetime.now(timezone.utc).isoformat()
    with db_lock:
        try:
            row = conn.execute(
                f"SELECT {SELECT_COLUMNS} FROM notes "
                "WHERE user_id=? AND note_id=? AND deleted_at IS NULL",
                (int(user_id), int(note_id)),
            ).fetchone()
            if not row:
                return False
            note = _from_row(row)
            snapshot = {**note, "deleted_at": deleted_at}
            cur = conn.execute(
                "UPDATE notes SET deleted_at=?,updated_at=? "
                "WHERE user_id=? AND note_id=? AND deleted_at IS NULL",
                (deleted_at, deleted_at, int(user_id), int(note_id)),
            )
            if cur.rowcount <= 0:
                conn.rollback()
                return False
            record_ai_memory_event(
                user_id,
                "note",
                note["note_id"],
                "deleted",
                snapshot,
                commit=False,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return True


init_note_store()
