"""Native Windows customer backup/restore: preserve SQLite writes, never overwrite."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from mark_api.backup_cli import BackupError, backup_store
from mark_api.storage import SnapshotStore
from mark_api.windows_backup import WindowsBackupError, restore_native_windows


@unittest.skipUnless(os.name == "nt", "native Windows only")
class WindowsCustomerBackupTests(unittest.TestCase):
    def test_backup_and_create_only_restore(self):
        with tempfile.TemporaryDirectory(
            prefix="ka-native-restore-", dir=os.environ["LOCALAPPDATA"]
        ) as root:
            directory = Path(root)
            src = directory / "anzeigen.sqlite"
            # Keep an old reader active to force committed SQLite-WAL frames
            # to remain uncheckpointed while the customer creates a backup.
            store = SnapshotStore(src, create_if_missing=True)
            reader = sqlite3.connect(src)
            self.assertEqual(
                reader.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal"
            )
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM sync_attempts").fetchone()
            try:
                store.begin_sync_attempt(
                    source="windows-synthetic-open-fence",
                    started_at=datetime.now(timezone.utc),
                    require_no_unresolved=True,
                )
                self.assertTrue(src.with_name(src.name + "-wal").exists())
                backup_dir = directory / "Backups"
                backup_dir.mkdir()
                backup = backup_dir / "sicherung.sqlite"
                receipt = backup_store(src, backup_db=backup)
                self.assertEqual(receipt.open_sync_attempts, 1)
                self.assertTrue(backup.is_file())
                self.assertEqual(
                    hashlib.sha256(backup.read_bytes()).hexdigest(),
                    receipt.backup_sha256,
                )
                self.assertTrue(
                    backup.with_name(backup.name + ".manifest.json").is_file()
                )
                restored = directory / "wiederhergestellt.sqlite"
                self.assertEqual(
                    restore_native_windows(backup, restored),
                    receipt.backup_sha256,
                )
                reopened = SnapshotStore(restored, create_if_missing=False)
                self.assertEqual(reopened.tracked_ad_ids(), ())
                self.assertEqual(
                    reopened.latest_sync_attempt().outcome, "in_progress"
                )
                with self.assertRaises(WindowsBackupError):
                    restore_native_windows(backup, restored)
                with self.assertRaises(BackupError):
                    backup_store(src, backup_db=backup)
            finally:
                reader.rollback()
                reader.close()

    def test_bad_manifest_cannot_create_any_database(self):
        with tempfile.TemporaryDirectory(
            prefix="ka-native-restore-", dir=os.environ["LOCALAPPDATA"]
        ) as root:
            directory = Path(root)
            src = directory / "anzeigen.sqlite"
            SnapshotStore(src, create_if_missing=True)
            backup_dir = directory / "Backups"
            backup_dir.mkdir()
            backup = backup_dir / "sicherung.sqlite"
            backup_store(src, backup_db=backup)
            companion = backup.with_name(backup.name + ".manifest.json")
            companion.write_text("{}", encoding="utf-8")
            restored = directory / "neue-anzeigen.sqlite"
            with self.assertRaises(WindowsBackupError):
                restore_native_windows(backup, restored)
            self.assertFalse(restored.exists())

    def test_restore_rejects_undocumented_backup_sidecar(self):
        with tempfile.TemporaryDirectory(
            prefix="ka-native-restore-", dir=os.environ["LOCALAPPDATA"]
        ) as root:
            directory = Path(root)
            src = directory / "anzeigen.sqlite"
            SnapshotStore(src, create_if_missing=True)
            backups = directory / "Backups"
            backups.mkdir()
            backup = backups / "sicherung.sqlite"
            backup_store(src, backup_db=backup)
            target = directory / "restored.sqlite"
            for suffix in ("-wal", "-shm", "-journal"):
                hidden = backup.with_name(backup.name + suffix)
                hidden.write_bytes(b"unverified SQLite sidecar")
                try:
                    with self.assertRaises(WindowsBackupError):
                        restore_native_windows(backup, target)
                    self.assertFalse(target.exists())
                finally:
                    hidden.unlink()

    def test_restore_rejects_sidecar_leftovers(self):
        with tempfile.TemporaryDirectory(
            prefix="ka-native-restore-", dir=os.environ["LOCALAPPDATA"]
        ) as root:
            directory = Path(root)
            src = directory / "anzeigen.sqlite"
            SnapshotStore(src, create_if_missing=True)
            backup_dir = directory / "Backups"
            backup_dir.mkdir()
            backup = backup_dir / "sicherung.sqlite"
            backup_store(src, backup_db=backup)
            restored = directory / "neue-anzeigen.sqlite"
            restored.with_name(restored.name + "-wal").write_bytes(b"old")
            with self.assertRaises(WindowsBackupError):
                restore_native_windows(backup, restored)
            self.assertFalse(restored.exists())


if __name__ == "__main__":
    unittest.main()