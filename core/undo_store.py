"""A short-lived, transactional undo journal for local note edits."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import json
import time

from core.ai_memory_store import record_ai_memory_event
from core.command_store import current_request_id
from core.db import conn, db_lock

_suppressed = ContextVar("undo_suppressed", default=False)
NOTE_COLUMNS = ("note_id", "user_id", "title", "normalized_title", "text", "normalized_text",
                "created_at", "updated_at", "deleted_at")
TTL_SECONDS = 600


def _coalesce_existing_actions() -> None:
    groups = conn.execute(
        "SELECT user_id,request_id,note_id,MIN(action_id),MAX(action_id) "
        "FROM undo_actions WHERE consumed=0 GROUP BY user_id,request_id,note_id HAVING COUNT(*)>1"
    ).fetchall()
    for user_id, request_id, note_id, first_id, last_id in groups:
        latest = conn.execute(
            "SELECT after_json,expires_at FROM undo_actions WHERE action_id=?",
            (last_id,),
        ).fetchone()
        if latest:
            conn.execute(
                "UPDATE undo_actions SET after_json=?,expires_at=? WHERE action_id=?",
                (latest[0], latest[1], first_id),
            )
        conn.execute(
            "DELETE FROM undo_actions WHERE user_id=? AND request_id=? AND note_id=? "
            "AND consumed=0 AND action_id<>?",
            (user_id, request_id, note_id, first_id),
        )


def init_undo_store():
    with db_lock:
        conn.execute("""CREATE TABLE IF NOT EXISTS undo_actions (
            action_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            request_id TEXT NOT NULL, note_id INTEGER NOT NULL,
            before_json TEXT, after_json TEXT NOT NULL,
            expires_at REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0
        )""")
        _coalesce_existing_actions()
        conn.execute("CREATE INDEX IF NOT EXISTS idx_undo_user ON undo_actions(user_id,action_id DESC)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_undo_request_note "
            "ON undo_actions(user_id,request_id,note_id,consumed)"
        )
        conn.create_function("undo_request_id", 0, lambda: None if _suppressed.get() else current_request_id())
        conn.create_function(
            "undo_snapshot", len(NOTE_COLUMNS),
            lambda *values: json.dumps(dict(zip(NOTE_COLUMNS, values)), ensure_ascii=False),
        )
        for operation in ("INSERT", "UPDATE"):
            old = "NULL" if operation == "INSERT" else "undo_snapshot(" + ",".join(f"OLD.{c}" for c in NOTE_COLUMNS) + ")"
            new = "undo_snapshot(" + ",".join(f"NEW.{c}" for c in NOTE_COLUMNS) + ")"
            conn.execute(f"""CREATE TEMP TRIGGER IF NOT EXISTS capture_note_{operation.lower()}
                AFTER {operation} ON main.notes
                WHEN undo_request_id() IS NOT NULL
                BEGIN
                  DELETE FROM undo_actions WHERE expires_at<CAST(strftime('%s','now') AS INTEGER);
                  DELETE FROM undo_actions WHERE user_id=NEW.user_id AND action_id NOT IN
                    (SELECT action_id FROM undo_actions WHERE user_id=NEW.user_id ORDER BY action_id DESC LIMIT 49);
                  INSERT INTO undo_actions(user_id,request_id,note_id,before_json,after_json,expires_at)
                  SELECT NEW.user_id,undo_request_id(),NEW.note_id,{old},{new},CAST(strftime('%s','now') AS INTEGER)+{TTL_SECONDS}
                  WHERE NOT EXISTS (
                    SELECT 1 FROM undo_actions
                    WHERE user_id=NEW.user_id AND request_id=undo_request_id()
                      AND note_id=NEW.note_id AND consumed=0
                  );
                  UPDATE undo_actions SET after_json={new},expires_at=CAST(strftime('%s','now') AS INTEGER)+{TTL_SECONDS}
                  WHERE user_id=NEW.user_id AND request_id=undo_request_id()
                    AND note_id=NEW.note_id AND consumed=0;
                END""")
        conn.commit()


def last_note_action(user_id: int) -> dict | None:
    with db_lock:
        row = conn.execute(
            "SELECT action_id,note_id,expires_at FROM undo_actions WHERE user_id=? AND consumed=0 AND expires_at>? ORDER BY action_id DESC LIMIT 1",
            (user_id, time.time()),
        ).fetchone()
    return {"id": row[0], "note_id": row[1], "expires_at": row[2], "label": "Отменить изменение заметки"} if row else None


def undo_note_action(user_id: int, action_id: int) -> dict:
    token = _suppressed.set(True)
    try:
        with db_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                action = conn.execute(
                    "SELECT note_id,before_json,after_json,expires_at,consumed FROM undo_actions WHERE user_id=? AND action_id=?",
                    (user_id, action_id),
                ).fetchone()
                if not action:
                    raise ValueError("Действие не найдено")
                if action[4]:
                    conn.commit()
                    return {"ok": True, "already_undone": True}
                if action[3] <= time.time():
                    raise ValueError("Время отмены истекло")
                row = conn.execute("SELECT " + ",".join(NOTE_COLUMNS) + " FROM notes WHERE user_id=? AND note_id=?",
                                   (user_id, action[0])).fetchone()
                current = dict(zip(NOTE_COLUMNS, row)) if row else None
                if current != json.loads(action[2]):
                    raise ValueError("Заметка уже изменилась. Более новые данные не перезаписываю.")
                before = json.loads(action[1]) if action[1] else dict(current, deleted_at=datetime.now(timezone.utc).isoformat())
                before["updated_at"] = datetime.now(timezone.utc).isoformat()
                fields = NOTE_COLUMNS[2:]
                conn.execute("UPDATE notes SET " + ",".join(f"{key}=?" for key in fields) + " WHERE user_id=? AND note_id=?",
                             [*(before[key] for key in fields), user_id, action[0]])
                conn.execute("UPDATE undo_actions SET consumed=1 WHERE action_id=? AND user_id=?", (action_id, user_id))
                record_ai_memory_event(user_id, "note", action[0], "updated", {"action": "undo", "note": before}, commit=False)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"ok": True}
    finally:
        _suppressed.reset(token)
