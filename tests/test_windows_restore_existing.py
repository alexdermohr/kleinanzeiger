"""Isolated real Windows tests for restoring over an existing customer database."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mark_api.backup_cli import backup_store
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.storage import SnapshotStore

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "windows-restore-existing.py"


@unittest.skipUnless(os.name == "nt", "native Windows only")
class WindowsRestoreExistingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix="kleinanzeiger-restore-existing-",
            dir=os.environ["LOCALAPPDATA"],
        )
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.db = self.state / "anzeigen.sqlite"
        self.backups = self.state / "Backups"
        self.backups.mkdir()
        self.selected = self.backups / "Kleinanzeiger-20261108-110000-001.sqlite"
        self.store = SnapshotStore(self.db, create_if_missing=True)
        self._append("synthetic-original")
        backup_store(self.db, backup_db=self.selected)

    def _append(self, ad_id: str) -> None:
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id=ad_id,
            title="Synthetic " + ad_id,
            observed_at=datetime.now(timezone.utc),
            source="synthetic-windows-restore-acceptance",
            lifecycle_state=LifecycleState.ACTIVE,
        ))

    def _run(self) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        env.pop("OPENAI_API_KEY", None)
        return subprocess.run(
            [sys.executable, "-u", str(HELPER),
             "--db", str(self.db), "--backup", str(self.selected)],
            capture_output=True, text=True, check=False,
            env=env, timeout=50,
        )

    def test_selected_snapshot_replaces_current_only_after_current_backup(self):
        self._append("synthetic-newer")
        before = {file.name for file in self.backups.glob("*.sqlite")}
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RESTORE_PREVIOUS_SAVED", result.stdout)
        restored = SnapshotStore(self.db, create_if_missing=False)
        self.assertIn("synthetic-original", restored.tracked_ad_ids())
        self.assertNotIn("synthetic-newer", restored.tracked_ad_ids())
        newly_created = [file for file in self.backups.glob("*.sqlite")
                         if file.name not in before]
        self.assertEqual(len(newly_created), 1)
        self.assertTrue(Path(str(newly_created[0]) + ".manifest.json").is_file())
        saved_previous = SnapshotStore(newly_created[0], create_if_missing=False)
        self.assertIn("synthetic-newer", saved_previous.tracked_ad_ids())
        # The original selected backup must not have been rewritten or renamed.
        self.assertTrue(self.selected.is_file())

    def test_tampered_manifest_cannot_change_or_archive_current_database(self):
        self._append("synthetic-newer")
        self.selected.with_name(self.selected.name + ".manifest.json").write_text(
            "{}", encoding="utf-8",
        )
        before_sha = hashlib.sha256(self.db.read_bytes()).hexdigest()
        backup_count = len(list(self.backups.glob("*.sqlite")))
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RESTORE_EXISTING_FAILED", result.stderr)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before_sha)
        self.assertEqual(len(list(self.backups.glob("*.sqlite"))), backup_count)
        self.assertIn("synthetic-newer",
                      SnapshotStore(self.db, create_if_missing=False).tracked_ad_ids())
        self.assertEqual(list(self.state.glob(".kleinanzeiger-restore-*.sqlite")), [])


if __name__ == "__main__":
    unittest.main()
