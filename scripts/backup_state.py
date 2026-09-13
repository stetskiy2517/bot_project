#!/usr/bin/env python3
"""Create a consistent local backup of persistent application state.

The SQLite database is copied with SQLite's online backup API, so a running web
process can keep using the source DB while the snapshot is made. The persistent
VAPID private key is copied into the same protected snapshot when it exists.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PREFIX = "snapshot-"
DEFAULT_RETENTION_DAYS = 14
MIN_SNAPSHOTS_TO_KEEP = 2


def _resolve_path(value: str | Path, *, base: Path = PROJECT_ROOT) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def database_path() -> Path:
    return _resolve_path(os.getenv("DB_PATH", "data/bot.db"))


def vapid_key_path(db_path: Path | None = None) -> Path:
    configured = str(os.getenv("WEB_PUSH_VAPID_PRIVATE_KEY") or "").strip()
    if configured:
        return _resolve_path(configured)
    source_db = db_path or database_path()
    return source_db.parent / "webpush_vapid_private.pem"


def backup_root() -> Path:
    configured = str(os.getenv("BACKUP_DIR") or "").strip()
    if configured:
        return _resolve_path(configured)
    return (Path.home() / ".local" / "share" / "personal-secretary" / "backups").resolve()


def retention_days() -> int:
    raw = str(os.getenv("BACKUP_RETENTION_DAYS") or DEFAULT_RETENTION_DAYS).strip()
    try:
        return max(1, int(raw))
    except ValueError as exc:
        raise ValueError("BACKUP_RETENTION_DAYS must be an integer") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_sqlite(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source}")

    source_uri = f"file:{source.as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=10) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        row = dst.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise RuntimeError(f"SQLite backup integrity_check failed: {row!r}")
    os.chmod(target, 0o600)


def _copy_vapid_key(source: Path, target: Path) -> bool:
    if not source.is_file():
        return False
    shutil.copyfile(source, target)
    os.chmod(target, 0o600)
    return True


def _snapshot_directories(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        (
            item
            for item in root.iterdir()
            if item.is_dir() and item.name.startswith(SNAPSHOT_PREFIX) and not item.name.startswith(".tmp-")
        ),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def prune_backups(root: Path, *, days: int) -> list[Path]:
    snapshots = _snapshot_directories(root)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    removed: list[Path] = []
    for index, snapshot in enumerate(snapshots):
        if index < MIN_SNAPSHOTS_TO_KEEP:
            continue
        modified = datetime.fromtimestamp(snapshot.stat().st_mtime, tz=timezone.utc)
        if modified >= cutoff:
            continue
        shutil.rmtree(snapshot)
        removed.append(snapshot)
    return removed


def create_backup(*, now: datetime | None = None) -> Path:
    source_db = database_path()
    source_vapid = vapid_key_path(source_db)
    root = backup_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_dir = root / f"{SNAPSHOT_PREFIX}{timestamp}"
    if final_dir.exists():
        final_dir = root / f"{SNAPSHOT_PREFIX}{timestamp}-{os.getpid()}"

    temp_dir = Path(tempfile.mkdtemp(prefix=".tmp-personal-secretary-", dir=root))
    try:
        os.chmod(temp_dir, 0o700)
        db_target = temp_dir / "bot.db"
        _backup_sqlite(source_db, db_target)

        vapid_target = temp_dir / "webpush_vapid_private.pem"
        vapid_present = _copy_vapid_key(source_vapid, vapid_target)

        manifest = {
            "created_at_utc": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
            "database": {
                "file": db_target.name,
                "bytes": db_target.stat().st_size,
                "sha256": _sha256(db_target),
                "integrity_check": "ok",
            },
            "vapid_key": {
                "present": vapid_present,
                "file": vapid_target.name if vapid_present else None,
                "sha256": _sha256(vapid_target) if vapid_present else None,
            },
        }
        manifest_path = temp_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(manifest_path, 0o600)

        os.replace(temp_dir, final_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    prune_backups(root, days=retention_days())
    return final_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up Personal Secretary persistent state")
    parser.add_argument("--print-path", action="store_true", help="print only the snapshot path on success")
    args = parser.parse_args()

    try:
        snapshot = create_backup()
    except Exception as exc:
        print(f"Backup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.print_path:
        print(snapshot)
    else:
        print(f"Backup created: {snapshot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
