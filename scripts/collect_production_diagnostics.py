#!/usr/bin/env python3
"""Collect a redacted production diagnostic bundle for support.

This script intentionally reads only:
- recent user_activity_log rows,
- application service journal,
- deployment/runtime metadata.

It never reads or prints application secrets, OAuth tokens, cookies, or .env values
other than DB_PATH.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import subprocess


SECRET_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer|basic)\s+[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(access[_-]?token|refresh[_-]?token|client[_-]?secret|api[_-]?key|credentials|cookie|session)(\s*[:=]\s*)[^\s,;]+"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)(X-CSRF-Token|csrf_token)(\s*[:=]\s*)[^\s,;]+"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"), "[EMAIL_REDACTED]"),
)

SENSITIVE_DETAIL_KEYS = {
    "message", "text", "transcript", "title", "body", "content", "query",
    "email", "name", "description", "location", "address",
}


def redact(value: object) -> object:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            clean_key = str(key)
            if clean_key.casefold() in SENSITIVE_DETAIL_KEYS:
                result[clean_key] = "[REDACTED]"
            else:
                result[clean_key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    text = value
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def env_db_path(project_dir: Path) -> Path:
    default = project_dir / "data" / "bot.db"
    env_path = project_dir / ".env"
    if not env_path.is_file():
        return default
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() != "DB_PATH":
                continue
            configured = value.strip().strip("'\"")
            if not configured:
                return default
            path = Path(configured).expanduser()
            return path if path.is_absolute() else project_dir / path
    except OSError:
        pass
    return default


def recent_user_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT user_id FROM user_activity_log ORDER BY activity_id DESC LIMIT 1"
    ).fetchone()
    return int(row[0]) if row else None


def activity_rows(conn: sqlite3.Connection, user_id: int, limit: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT activity_id, request_id, method, path, status, phase, error_code,
               duration_ms, details_json, created_at
        FROM user_activity_log
        WHERE user_id=?
        ORDER BY activity_id DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    result = []
    for row in rows:
        try:
            details = json.loads(row[8]) if row[8] else {}
        except (TypeError, json.JSONDecodeError):
            details = {"invalid_json": True}
        result.append(
            redact(
                {
                    "id": row[0],
                    "request_id": row[1],
                    "method": row[2],
                    "path": row[3],
                    "status": row[4],
                    "phase": row[5],
                    "error_code": row[6],
                    "duration_ms": row[7],
                    "details": details,
                    "created_at": row[9],
                }
            )
        )
    return result


def run_text(command: list[str], *, cwd: Path | None = None, timeout: int = 15) -> str:
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return f"[unavailable: {type(exc).__name__}]"
    return str(redact(proc.stdout[-50000:]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--user-id", type=int)
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument("--journal-lines", type=int, default=350)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    project_dir = Path(args.project_dir).expanduser().resolve()
    output = Path(args.output)
    limit = max(1, min(args.limit, 500))
    journal_lines = max(20, min(args.journal_lines, 1000))
    db_path = env_db_path(project_dir)

    payload: dict = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "project_dir": str(project_dir),
        "db_exists": db_path.is_file(),
        "git_head": run_text(["git", "rev-parse", "HEAD"], cwd=project_dir).strip(),
        "service_status": run_text(
            ["systemctl", "--no-pager", "--full", "status", "personal-secretary"],
            timeout=10,
        ),
        "journal": run_text(
            [
                "sudo", "-n", "journalctl", "-u", "personal-secretary",
                "-n", str(journal_lines), "--no-pager", "--output=short-iso",
            ],
            timeout=15,
        ),
    }

    if db_path.is_file():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            user_id = args.user_id or recent_user_id(conn)
            payload["selected_user_id"] = user_id
            payload["selection"] = "explicit" if args.user_id else "most_recent_activity"
            payload["activity"] = activity_rows(conn, user_id, limit) if user_id else []
        except sqlite3.Error as exc:
            payload["activity_error"] = f"{type(exc).__name__}: {exc}"
            payload["activity"] = []
        finally:
            conn.close()
    else:
        payload["activity"] = []
        payload["activity_error"] = "database_not_found"

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(redact(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
