from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.backup_state import create_backup, prune_backups


class StateBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "data" / "bot.db"
        self.db_path.parent.mkdir(parents=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO sample(value) VALUES (?)", ("working-state",))
            connection.commit()

        self.vapid_path = self.db_path.parent / "webpush_vapid_private.pem"
        self.vapid_path.write_text("test-private-key\n", encoding="utf-8")
        os.chmod(self.vapid_path, 0o600)
        self.backup_dir = self.root / "backups"
        self.env = {
            "DB_PATH": str(self.db_path),
            "WEB_PUSH_VAPID_PRIVATE_KEY": str(self.vapid_path),
            "BACKUP_DIR": str(self.backup_dir),
            "BACKUP_RETENTION_DAYS": "7",
            "BACKUP_MAX_SNAPSHOTS": "10",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_backup_contains_consistent_database_vapid_key_and_manifest(self):
        with patch.dict(os.environ, self.env, clear=False):
            snapshot = create_backup(now=datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc))

        self.assertTrue(snapshot.is_dir())
        backup_db = snapshot / "bot.db"
        with sqlite3.connect(backup_db) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "working-state")

        self.assertEqual((snapshot / "webpush_vapid_private.pem").read_text(encoding="utf-8"), "test-private-key\n")
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["database"]["integrity_check"], "ok")
        self.assertTrue(manifest["database"]["sha256"])
        self.assertTrue(manifest["vapid_key"]["present"])
        self.assertTrue(manifest["vapid_key"]["sha256"])

    def test_backup_still_succeeds_before_vapid_key_exists(self):
        self.vapid_path.unlink()
        with patch.dict(os.environ, self.env, clear=False):
            snapshot = create_backup(now=datetime(2026, 9, 13, 8, 1, tzinfo=timezone.utc))
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["vapid_key"]["present"])
        self.assertFalse((snapshot / "webpush_vapid_private.pem").exists())

    def test_retention_keeps_three_newest_snapshots_even_when_old(self):
        self.backup_dir.mkdir(parents=True)
        now = datetime.now(timezone.utc)
        snapshots = []
        for index in range(4):
            snapshot = self.backup_dir / f"snapshot-2026010{index + 1}T000000Z"
            snapshot.mkdir()
            age_days = 40 - index
            timestamp = (now - timedelta(days=age_days)).timestamp()
            os.utime(snapshot, (timestamp, timestamp))
            snapshots.append(snapshot)

        removed = prune_backups(self.backup_dir, days=14)
        remaining = [item for item in snapshots if item.exists()]
        self.assertEqual(len(remaining), 3)
        self.assertEqual(len(removed), 1)

    def test_retention_caps_recent_snapshots_by_count(self):
        self.backup_dir.mkdir(parents=True)
        now = datetime.now(timezone.utc)
        snapshots = []
        for index in range(12):
            snapshot = self.backup_dir / f"snapshot-202609{index + 1:02d}T000000Z"
            snapshot.mkdir()
            timestamp = (now - timedelta(hours=index)).timestamp()
            os.utime(snapshot, (timestamp, timestamp))
            snapshots.append(snapshot)

        removed = prune_backups(self.backup_dir, days=7, max_count=10)
        remaining = [item for item in snapshots if item.exists()]
        self.assertEqual(len(remaining), 10)
        self.assertEqual(len(removed), 2)


if __name__ == "__main__":
    unittest.main()
