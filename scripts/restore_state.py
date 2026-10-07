"""Offline, checksum-verified SQLite restore with mandatory erasure receipts."""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from core.erasure_journal import enforce_erasure_journal, journal_path, runtime_lock, open_journal, _guard_id
from scripts.backup_state import database_path


def restore_snapshot(snapshot, target, *, journal=None):
    snapshot, target = Path(snapshot).resolve(), Path(target).resolve()
    receipt_path = Path(journal).resolve() if journal else journal_path(target)
    # A restore is never allowed to initialize an empty receipt journal.
    if not receipt_path.is_file():
        raise RuntimeError("Erasure journal is required before restoring")
    manifest = json.loads((snapshot / "manifest.json").read_text())
    source = snapshot / "bot.db"
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != manifest["database"]["sha256"]:
        raise RuntimeError("Backup checksum mismatch")
    descriptor = runtime_lock(target, exclusive=True)
    temporary = None
    try:
        if any(Path(str(target)+suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise RuntimeError("SQLite sidecars exist; complete offline checkpoint before restore")
        if target.is_file():
            with closing(sqlite3.connect(target.as_uri()+"?mode=ro", uri=True)) as current:
                expected = _guard_id(current)
            with open_journal(receipt_path) as (_, meta):
                if expected and expected != meta[0]:
                    raise RuntimeError("Erasure journal does not belong to the current database")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".restore-", dir=target.parent)
        os.close(fd)
        temporary = Path(name)
        with closing(sqlite3.connect(source.as_uri()+"?mode=ro", uri=True)) as src, closing(sqlite3.connect(temporary)) as candidate:
            src.backup(candidate)
            if candidate.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
            erased = enforce_erasure_journal(candidate, target, require_existing=True, path=receipt_path)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {"restored": True, "erased_accounts": erased}
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--target", type=Path, default=None)
    parser.add_argument("--journal", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(restore_snapshot(args.snapshot, args.target or database_path(), journal=args.journal)))
        return 0
    except Exception as exc:
        print(f"Restore refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
