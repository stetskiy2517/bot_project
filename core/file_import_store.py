"""Persistent drafts for file/screenshot import proposals."""

from __future__ import annotations

import json
import time
import uuid

from core.db import conn, db_lock

DRAFT_TTL_SECONDS = 7 * 24 * 60 * 60


def init_file_import_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS file_import_drafts (
                user_id INTEGER NOT NULL,
                draft_id TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY(user_id, draft_id)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_file_import_drafts_user_updated "
            "ON file_import_drafts(user_id, updated_at DESC)"
        )
        conn.commit()


def _cleanup() -> None:
    conn.execute("DELETE FROM file_import_drafts WHERE expires_at<=?", (time.time(),))


def create_file_import_draft(user_id: int, result: dict) -> str:
    draft_id = uuid.uuid4().hex
    now = time.time()
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    with db_lock:
        _cleanup()
        conn.execute(
            "INSERT INTO file_import_drafts(user_id,draft_id,result_json,created_at,updated_at,expires_at) "
            "VALUES (?,?,?,?,?,?)",
            (int(user_id), draft_id, payload, now, now, now + DRAFT_TTL_SECONDS),
        )
        conn.commit()
    return draft_id


def list_file_import_drafts(user_id: int, *, limit: int = 5) -> list[dict]:
    with db_lock:
        _cleanup()
        rows = conn.execute(
            "SELECT draft_id,result_json,created_at,updated_at FROM file_import_drafts "
            "WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
            (int(user_id), max(1, min(int(limit), 20))),
        ).fetchall()
        conn.commit()
    drafts = []
    for row in rows:
        try:
            result = json.loads(row[1])
        except (TypeError, ValueError):
            continue
        if not isinstance(result, dict):
            continue
        drafts.append({
            "draft_id": row[0],
            "result": result,
            "created_at": row[2],
            "updated_at": row[3],
        })
    return drafts


def mark_file_import_item(
    user_id: int,
    draft_id: str,
    *,
    kind: str,
    index: int,
    target: str,
) -> dict:
    if kind not in {"task", "event"}:
        raise ValueError("Некорректный тип элемента")
    if target not in {"task", "calendar"}:
        raise ValueError("Некорректное действие импорта")
    key = "tasks" if kind == "task" else "events"

    with db_lock:
        row = conn.execute(
            "SELECT result_json FROM file_import_drafts WHERE user_id=? AND draft_id=?",
            (int(user_id), str(draft_id)),
        ).fetchone()
        if not row:
            raise ValueError("Черновик импорта не найден")
        try:
            result = json.loads(row[0])
        except (TypeError, ValueError) as exc:
            raise ValueError("Черновик импорта повреждён") from exc
        items = result.get(key)
        if not isinstance(items, list) or not (0 <= int(index) < len(items)):
            raise ValueError("Элемент импорта не найден")
        item = items[int(index)]
        if not isinstance(item, dict):
            raise ValueError("Элемент импорта повреждён")
        imported = item.setdefault("imported", {})
        imported[target] = True
        now = time.time()
        conn.execute(
            "UPDATE file_import_drafts SET result_json=?,updated_at=?,expires_at=? "
            "WHERE user_id=? AND draft_id=?",
            (
                json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                now,
                now + DRAFT_TTL_SECONDS,
                int(user_id),
                str(draft_id),
            ),
        )
        conn.commit()
    return item


def delete_file_import_draft(user_id: int, draft_id: str) -> None:
    with db_lock:
        conn.execute(
            "DELETE FROM file_import_drafts WHERE user_id=? AND draft_id=?",
            (int(user_id), str(draft_id)),
        )
        conn.commit()


init_file_import_store()
