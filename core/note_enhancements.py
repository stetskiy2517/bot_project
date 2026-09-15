"""Extended note editing, tags and checklists without changing the base note model."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from core.ai_memory_store import record_ai_memory_event
from core.db import conn, db_lock
from core.note_store import (
    MAX_NOTE_LENGTH,
    MAX_NOTE_TITLE_LENGTH,
    get_note,
    normalize_note_text,
)

MAX_TAGS = 20
MAX_CHECKLIST_ITEMS = 100


def init_note_enhancements() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS note_metadata (
                user_id INTEGER NOT NULL,
                note_id INTEGER NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                tags_json TEXT NOT NULL DEFAULT '[]',
                checklist_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id,note_id)
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_note_metadata_user_pinned ON note_metadata(user_id,pinned)")
        conn.commit()


def _metadata_row(user_id: int, note_id: int) -> dict:
    init_note_enhancements()
    with db_lock:
        row = conn.execute(
            "SELECT pinned,tags_json,checklist_json,updated_at FROM note_metadata WHERE user_id=? AND note_id=?",
            (int(user_id), int(note_id)),
        ).fetchone()
    if not row:
        return {"pinned": False, "tags": [], "checklist": [], "metadata_updated_at": None}
    try:
        tags = json.loads(row[1])
    except (TypeError, json.JSONDecodeError):
        tags = []
    try:
        checklist = json.loads(row[2])
    except (TypeError, json.JSONDecodeError):
        checklist = []
    return {
        "pinned": bool(row[0]),
        "tags": tags if isinstance(tags, list) else [],
        "checklist": checklist if isinstance(checklist, list) else [],
        "metadata_updated_at": row[3],
    }


def enhanced_note(user_id: int, note_id: int) -> dict | None:
    note = get_note(user_id, note_id)
    return {**note, **_metadata_row(user_id, note_id)} if note else None


def update_note_content(user_id: int, note_id: int, *, title: object, text: object) -> dict:
    current = get_note(user_id, note_id)
    if not current:
        raise ValueError("Заметка не найдена")
    clean_title = " ".join(str(title or "").replace("\n", " ").split()).strip()
    clean_text = "\n".join(line.rstrip() for line in str(text or "").strip().splitlines()).strip()
    if not clean_title or len(clean_title) > MAX_NOTE_TITLE_LENGTH:
        raise ValueError(f"Название заметки: от 1 до {MAX_NOTE_TITLE_LENGTH} символов")
    if not clean_text or len(clean_text) > MAX_NOTE_LENGTH:
        raise ValueError(f"Текст заметки: от 1 до {MAX_NOTE_LENGTH} символов")
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        try:
            conn.execute(
                "UPDATE notes SET title=?,normalized_title=?,text=?,normalized_text=?,updated_at=? "
                "WHERE user_id=? AND note_id=? AND deleted_at IS NULL",
                (
                    clean_title,
                    normalize_note_text(clean_title),
                    clean_text,
                    normalize_note_text(clean_text),
                    now,
                    int(user_id),
                    int(note_id),
                ),
            )
            row = conn.execute(
                "SELECT note_id,user_id,title,normalized_title,text,normalized_text,created_at,updated_at,deleted_at "
                "FROM notes WHERE user_id=? AND note_id=? AND deleted_at IS NULL",
                (int(user_id), int(note_id)),
            ).fetchone()
            snapshot = {
                "note_id": int(row[0]), "user_id": int(row[1]), "title": row[2],
                "normalized_title": row[3], "text": row[4], "normalized_text": row[5],
                "created_at": row[6], "updated_at": row[7], "deleted_at": row[8],
            }
            record_ai_memory_event(user_id, "note", note_id, "updated", snapshot, commit=False)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return enhanced_note(user_id, note_id) or {}


def _clean_tags(values: object) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("Теги должны быть списком")
    result = []
    for value in values:
        tag = " ".join(str(value or "").split()).strip(" #,.;")[:40]
        if tag and tag.casefold() not in {item.casefold() for item in result}:
            result.append(tag)
        if len(result) >= MAX_TAGS:
            break
    return result


def _clean_checklist(values: object) -> list[dict]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("Чек-лист должен быть списком")
    result = []
    for item in values[:MAX_CHECKLIST_ITEMS]:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split()).strip()[:300]
        if not text:
            continue
        result.append({"text": text, "done": bool(item.get("done"))})
    return result


def update_note_metadata(
    user_id: int,
    note_id: int,
    *,
    pinned: object = False,
    tags: object = None,
    checklist: object = None,
) -> dict:
    if not get_note(user_id, note_id):
        raise ValueError("Заметка не найдена")
    if not isinstance(pinned, bool):
        raise ValueError("Закрепление должно быть true или false")
    clean_tags = _clean_tags(tags)
    clean_checklist = _clean_checklist(checklist)
    now = datetime.now(timezone.utc).isoformat()
    with db_lock:
        conn.execute(
            "INSERT INTO note_metadata(user_id,note_id,pinned,tags_json,checklist_json,updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(user_id,note_id) DO UPDATE SET pinned=excluded.pinned,tags_json=excluded.tags_json,"
            "checklist_json=excluded.checklist_json,updated_at=excluded.updated_at",
            (int(user_id), int(note_id), 1 if pinned else 0, json.dumps(clean_tags, ensure_ascii=False), json.dumps(clean_checklist, ensure_ascii=False), now),
        )
        conn.commit()
    return enhanced_note(user_id, note_id) or {}


init_note_enhancements()
