"""Persistent storage for recognized voice text only.

Raw audio is intentionally not stored here. The application keeps only the
recognized text so it can be used for history and future assistant features.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.db import conn, db_lock

MAX_TRANSCRIPT_LENGTH = 10_000


def init_voice_transcript_store() -> None:
    with db_lock:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS voice_transcripts (
                transcript_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'web_voice',
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_voice_transcripts_user_created "
            "ON voice_transcripts(user_id, created_at)"
        )
        conn.commit()


def save_voice_transcript(user_id: int, text: str, *, source: str = "web_voice") -> int | None:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned or len(cleaned) > MAX_TRANSCRIPT_LENGTH:
        return None
    created_at = datetime.now(timezone.utc).isoformat()
    with db_lock:
        cursor = conn.execute(
            "INSERT INTO voice_transcripts(user_id,text,source,created_at) VALUES (?,?,?,?)",
            (int(user_id), cleaned, str(source or "web_voice"), created_at),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_voice_transcripts(user_id: int, *, limit: int = 500) -> list[dict]:
    limit = max(1, min(int(limit), 10_000))
    with db_lock:
        rows = conn.execute(
            "SELECT transcript_id,text,source,created_at FROM voice_transcripts "
            "WHERE user_id=? ORDER BY transcript_id DESC LIMIT ?",
            (int(user_id), limit),
        ).fetchall()
    return [
        {
            "transcript_id": int(row[0]),
            "text": row[1],
            "source": row[2],
            "created_at": row[3],
        }
        for row in rows
    ]


init_voice_transcript_store()
