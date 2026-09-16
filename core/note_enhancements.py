"""Extended note editing, categories, tags and checklists."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from core.ai_memory_store import record_ai_memory_event
from core.db import conn, db_lock
from core.note_store import (
    MAX_NOTE_LENGTH,
    MAX_NOTE_TITLE_LENGTH,
    get_note,
    list_notes,
    normalize_note_text,
)

MAX_TAGS = 20
MAX_CHECKLIST_ITEMS = 100
NOTE_CATEGORIES = {"work", "health", "rest", "travel", "family", "personal", "other"}
DEFAULT_NOTE_CATEGORY = "other"


def init_note_enhancements() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS note_metadata (
                user_id INTEGER NOT NULL,
                note_id INTEGER NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                tags_json TEXT NOT NULL DEFAULT '[]',
                checklist_json TEXT NOT NULL DEFAULT '[]',
                category TEXT NOT NULL DEFAULT 'other',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id,note_id)
            )"""
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(note_metadata)").fetchall()}
        if "category" not in columns:
            conn.execute(
                "ALTER TABLE note_metadata ADD COLUMN category TEXT NOT NULL DEFAULT 'other'"
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_note_metadata_user_pinned ON note_metadata(user_id,pinned)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_note_metadata_user_category ON note_metadata(user_id,category)")
        conn.commit()


def _decode_metadata_values(
    pinned: object,
    tags_json: object,
    checklist_json: object,
    category: object,
    updated_at: object,
) -> dict:
    try:
        tags = json.loads(tags_json) if tags_json else []
    except (TypeError, json.JSONDecodeError):
        tags = []
    try:
        checklist = json.loads(checklist_json) if checklist_json else []
    except (TypeError, json.JSONDecodeError):
        checklist = []
    resolved_category = str(category or DEFAULT_NOTE_CATEGORY).strip().lower()
    if resolved_category not in NOTE_CATEGORIES:
        resolved_category = DEFAULT_NOTE_CATEGORY
    return {
        "pinned": bool(pinned),
        "tags": tags if isinstance(tags, list) else [],
        "checklist": checklist if isinstance(checklist, list) else [],
        "category": resolved_category,
        "metadata_updated_at": updated_at,
    }


def _metadata_row(user_id: int, note_id: int) -> dict:
    init_note_enhancements()
    with db_lock:
        row = conn.execute(
            "SELECT pinned,tags_json,checklist_json,category,updated_at FROM note_metadata WHERE user_id=? AND note_id=?",
            (int(user_id), int(note_id)),
        ).fetchone()
    if not row:
        return {
            "pinned": False,
            "tags": [],
            "checklist": [],
            "category": DEFAULT_NOTE_CATEGORY,
            "metadata_updated_at": None,
        }
    return _decode_metadata_values(*row)


def enhanced_note(user_id: int, note_id: int) -> dict | None:
    note = get_note(user_id, note_id)
    return {**note, **_metadata_row(user_id, note_id)} if note else None


def list_enhanced_notes(user_id: int, *, limit: int = 100) -> list[dict]:
    """Return active notes with metadata, with pinned notes first and stable recency order."""
    notes = list_notes(user_id, limit=limit)
    if not notes:
        return []
    init_note_enhancements()
    note_ids = [int(item["note_id"]) for item in notes]
    placeholders = ",".join("?" for _ in note_ids)
    with db_lock:
        rows = conn.execute(
            f"SELECT note_id,pinned,tags_json,checklist_json,category,updated_at "
            f"FROM note_metadata WHERE user_id=? AND note_id IN ({placeholders})",
            (int(user_id), *note_ids),
        ).fetchall()
    metadata = {
        int(row[0]): _decode_metadata_values(row[1], row[2], row[3], row[4], row[5])
        for row in rows
    }
    fallback = {
        "pinned": False,
        "tags": [],
        "checklist": [],
        "category": DEFAULT_NOTE_CATEGORY,
        "metadata_updated_at": None,
    }
    enriched = [
        {**note, **metadata.get(int(note["note_id"]), fallback)}
        for note in notes
    ]
    enriched.sort(key=lambda item: 0 if item.get("pinned") else 1)
    return enriched


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
    if not isinstance(values, list):
        raise ValueError("Теги должны быть списком")
    result = []
    seen = set()
    for value in values:
        tag = " ".join(str(value or "").split()).strip(" #,.;")[:40]
        key = tag.casefold()
        if tag and key not in seen:
            result.append(tag)
            seen.add(key)
        if len(result) >= MAX_TAGS:
            break
    return result


def _clean_checklist(values: object) -> list[dict]:
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


def _clean_category(value: object) -> str:
    category = str(value or DEFAULT_NOTE_CATEGORY).strip().lower()
    if category not in NOTE_CATEGORIES:
        raise ValueError("Неизвестная категория заметки")
    return category


def update_note_metadata(
    user_id: int,
    note_id: int,
    *,
    pinned: object = None,
    tags: object = None,
    checklist: object = None,
    category: object = None,
) -> dict:
    current = get_note(user_id, note_id)
    if not current:
        raise ValueError("Заметка не найдена")
    current_metadata = _metadata_row(user_id, note_id)

    if pinned is None:
        clean_pinned = bool(current_metadata.get("pinned"))
    elif isinstance(pinned, bool):
        clean_pinned = pinned
    else:
        raise ValueError("Закрепление должно быть true или false")

    clean_tags = (
        list(current_metadata.get("tags") or [])
        if tags is None
        else _clean_tags(tags)
    )
    clean_checklist = (
        list(current_metadata.get("checklist") or [])
        if checklist is None
        else _clean_checklist(checklist)
    )
    clean_category = _clean_category(
        current_metadata.get("category") if category is None else category
    )
    now = datetime.now(timezone.utc).isoformat()
    snapshot = {
        **current,
        "pinned": clean_pinned,
        "tags": clean_tags,
        "checklist": clean_checklist,
        "category": clean_category,
        "metadata_updated_at": now,
    }
    with db_lock:
        try:
            conn.execute(
                "INSERT INTO note_metadata(user_id,note_id,pinned,tags_json,checklist_json,category,updated_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(user_id,note_id) DO UPDATE SET pinned=excluded.pinned,tags_json=excluded.tags_json,"
                "checklist_json=excluded.checklist_json,category=excluded.category,updated_at=excluded.updated_at",
                (
                    int(user_id),
                    int(note_id),
                    1 if clean_pinned else 0,
                    json.dumps(clean_tags, ensure_ascii=False),
                    json.dumps(clean_checklist, ensure_ascii=False),
                    clean_category,
                    now,
                ),
            )
            record_ai_memory_event(user_id, "note", note_id, "updated", snapshot, commit=False)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return enhanced_note(user_id, note_id) or {}


init_note_enhancements()
