"""Preserve create-only backups and require verified, atomic replacement in Windows UI."""
from __future__ import annotations
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CustomerRestoreContractTests(unittest.TestCase):
    def test_restore_existing_is_staged_backed_up_and_single_atomic_swap(self):
        source = (ROOT / "scripts/windows-restore-existing.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn("restore_native_windows(selected, stage)", source)
        self.assertLess(source.index("restore_native_windows(selected, stage)"),
                        source.index("backup_store(db, backup_db=recovery)"))
        self.assertLess(source.index("backup_store(db, backup_db=recovery)"),
                        source.index("os.replace(stage, db)"))
        self.assertIn("receipt.backup_sha256", source)
        self.assertIn("PRAGMA wal_checkpoint(TRUNCATE)", source)
        self.assertIn("with closing(sqlite3.connect", source)
        self.assertIn("database sidecar still active", source)
        self.assertEqual(source.count("os.replace(stage, db)"), 1)
        self.assertNotIn("os.remove(db)", source)
        self.assertNotIn("db.unlink(", source)
        self.assertTrue(any(isinstance(n,ast.FunctionDef) and n.name=="restore_existing" for n in tree.body))

    def test_customer_dialog_handles_existing_and_empty_databases(self):
        hub = (ROOT / "scripts/windows-center.ps1").read_text(encoding="utf-8-sig")
        worker = (ROOT / "scripts/windows-worker.ps1").read_text(encoding="utf-8")
        dialog = hub.split("$restore.Add_Click({", 1)[1].split("$form.Add_FormClosed", 1)[0]
        self.assertIn("$hasExistingData = Test-Path -LiteralPath $db", dialog)
        self.assertIn("als eigene Sicherung aufbewahrt", dialog)
        self.assertIn("[Windows.Forms.MessageBoxButtons]::YesNo", dialog)
        self.assertIn("Start-Worker 'Restore' $false $selected", dialog)
        self.assertNotIn("Die Datenbank ist bereits vorhanden. Wiederherstellung gesperrt.", dialog)
        self.assertIn("& $venvPython -u $helper --backup $source --db $db", worker)
        self.assertIn("& $venvPython -m mark_api.windows_backup --backup $source --db $db", worker)

    def test_helper_is_always_in_windows_zip_and_installed_app(self):
        workflow = (ROOT / ".github/workflows/windows.yml").read_text()
        worker = (ROOT / "scripts/windows-worker.ps1").read_text()
        self.assertIn(r"scripts\windows-restore-existing.py", workflow)
        self.assertIn("'windows-restore-existing.py'", worker)
        self.assertIn("'.html','.py'", worker)


if __name__ == "__main__":
    unittest.main()
