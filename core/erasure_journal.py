"""Durable deletion receipts kept outside the restored application database.

Receipts contain keyed identity hashes and deletion times, never email, tokens,
messages or numeric user IDs. Old incarnations are erased; later signups survive.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3


def journal_path(db_path):
    configured = os.getenv("ERASURE_JOURNAL_PATH")
    return Path(configured).expanduser().resolve() if configured else Path(db_path).resolve().parent / ".privacy" / (Path(db_path).name + "-erasures.sqlite3")


def runtime_lock(db_path, *, exclusive=False):
    path = Path(str(Path(db_path).resolve()) + ".runtime.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, (fcntl.LOCK_EX | fcntl.LOCK_NB) if exclusive else fcntl.LOCK_SH)
    except Exception:
        os.close(descriptor)
        raise RuntimeError("Stop application processes before restoring the database")
    return descriptor


@contextmanager
def open_journal(path, *, create=False):
    path = Path(path)
    if not path.exists() and not create:
        raise RuntimeError("Required erasure journal is missing; startup/restore refused")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(sqlite3.connect(path, timeout=15)) as ledger:
        ledger.execute("PRAGMA synchronous=FULL")
        if create:
            ledger.execute("CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY CHECK(id=1), journal_id TEXT NOT NULL, salt TEXT NOT NULL)")
            ledger.execute("CREATE TABLE IF NOT EXISTS erasures (identity_hash TEXT PRIMARY KEY, erased_at TEXT NOT NULL)")
            ledger.execute("INSERT OR IGNORE INTO metadata VALUES (1,?,?)", (secrets.token_hex(32), secrets.token_hex(32)))
            ledger.commit()
            os.chmod(path, 0o600)
        meta = ledger.execute("SELECT journal_id,salt FROM metadata WHERE id=1").fetchone()
        if not meta:
            raise RuntimeError("Erasure journal metadata is invalid")
        yield ledger, meta


def _digest(salt, provider, subject):
    return hmac.new(bytes.fromhex(salt), f"{provider}:{subject}".encode(), hashlib.sha256).hexdigest()


def _identities(connection):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rows = []
    if "google_accounts" in tables:
        rows.extend((int(uid), "profile", str(subject), created) for uid, subject, created in connection.execute("SELECT user_id,google_sub,created_at FROM google_accounts"))
    if "identity_accounts" in tables:
        rows.extend((int(uid), provider, str(subject), created) for uid, provider, subject, created in connection.execute("SELECT user_id,provider,subject,created_at FROM identity_accounts"))
    return rows


def _guard_id(connection):
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='privacy_restore_guard'").fetchone()
    if not exists:
        return None
    row = connection.execute("SELECT journal_id FROM privacy_restore_guard WHERE id=1").fetchone()
    if not row:
        raise RuntimeError("Database erasure guard is invalid")
    return row[0]


def _erase_old_incarnations(connection, ledger, salt):
    deleted = set()
    for user_id, provider, subject, created in _identities(connection):
        row = ledger.execute("SELECT erased_at FROM erasures WHERE identity_hash=?", (_digest(salt, provider, subject),)).fetchone()
        if row:
            try:
                creation = datetime.fromisoformat(created)
                erasure = datetime.fromisoformat(row[0])
                if creation.tzinfo is None or erasure.tzinfo is None:
                    raise ValueError("Missing account timestamp timezone")
                creation = creation.astimezone(timezone.utc)
                erasure = erasure.astimezone(timezone.utc)
            except (TypeError, ValueError):
                raise RuntimeError("Cannot determine account incarnation during restore")
            if creation <= erasure:
                deleted.add(user_id)
    tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        if table.startswith("sqlite_") or not re.fullmatch(r"[a-z_]+", table):
            continue
        if "user_id" in {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}:
            connection.executemany(f"DELETE FROM {table} WHERE user_id=?", [(uid,) for uid in deleted])
    return len(deleted)


def enforce_erasure_journal(connection, db_path, *, require_existing=False, path=None):
    """Run before any authentication, application worker or restored DB publication."""
    path = Path(path) if path else journal_path(db_path)
    expected = _guard_id(connection)
    with open_journal(path, create=not expected and not require_existing) as (ledger, meta):
        if expected and not secrets.compare_digest(expected, meta[0]):
            raise RuntimeError("Erasure journal does not belong to this database")
        connection.execute("PRAGMA secure_delete=ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            count = _erase_old_incarnations(connection, ledger, meta[1])
            connection.execute("CREATE TABLE IF NOT EXISTS privacy_restore_guard (id INTEGER PRIMARY KEY CHECK(id=1), journal_id TEXT NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO privacy_restore_guard VALUES (1,?)", (meta[0],))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return count


def record_erasure(connection, db_path, user_id):
    expected = _guard_id(connection)
    if not expected:
        raise RuntimeError("Erasure guard must be initialized before account deletion")
    with open_journal(journal_path(db_path)) as (ledger, meta):
        if not secrets.compare_digest(expected, meta[0]):
            raise RuntimeError("Erasure journal identity mismatch")
        identities = [(provider, subject) for uid, provider, subject, _ in _identities(connection) if uid == user_id]
        if not identities:
            raise RuntimeError("Cannot record account identity for deletion")
        stamp = datetime.now(timezone.utc).isoformat()
        ledger.executemany("INSERT INTO erasures VALUES (?,?) ON CONFLICT(identity_hash) DO UPDATE SET erased_at=excluded.erased_at", [(_digest(meta[1], provider, subject), stamp) for provider, subject in identities])
        # Commit the receipt BEFORE deleting the account. A crash between commits
        # is repaired on startup by re-applying the confirmed deletion.
        ledger.commit()
