"""Kleinanzeiger is the supported public command surface; Mark is legacy only."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "scripts"
EXPECTED = {
    "kleinanzeiger": "launcher",
    "kleinanzeiger-offline": "offline",
    "kleinanzeiger-dashboard": "dashboard",
    "kleinanzeiger-backup": "backup",
    "kleinanzeiger-windows-restore": "windows_restore",
    "kleinanzeiger-sync": "sync",
    "kleinanzeiger-import-mail": "import_mail",
    "kleinanzeiger-import-articles": "import_articles",
    "kleinanzeiger-generate": "generate",
    "kleinanzeiger-classify": "classify",
    "kleinanzeiger-ki-auftraege": "ki_auftraege",
    "kleinanzeiger-local-smoke": "local_smoke",
    "kleinanzeiger-migrate-legacy": "migrate_legacy",
    "kleinanzeiger-pair-restore-check": "pair_restore_check",
}


class KleinanzeigerBrandingTests(unittest.TestCase):
    def test_public_executables_use_product_namespace(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        entrypoints = metadata["project"]["scripts"]
        for name, function in EXPECTED.items():
            with self.subTest(name=name):
                self.assertEqual(entrypoints[name], "kleinanzeiger.cli:" + function)
        # Historical programmatic clients and existing Windows installations
        # continue to resolve the old import/CLI spelling during migration.
        self.assertEqual(entrypoints["mark-api-launch"], "mark_api.launcher:main")

    def test_windows_runtime_modules_remain_backward_compatible(self):
        # Existing and rolled-back customer wheels may predate the new public
        # namespace. Keep runtime calls on the stable import ABI while all
        # visible UI, error messages and new console scripts say Kleinanzeiger.
        # The manual-start script keeps the legacy offline module directly.
        # The GUI's stable compatibility wrapper instead calls that module's
        # main(), preserving private STOP/EOF control after old-wheel rollback.
        import re

        compatibility = {
            "windows-backup.ps1": ["-m mark_api.backup_cli"],
            "windows-restore.ps1": ["-m mark_api.windows_backup"],
            "windows-center.ps1": [
                "-m mark_api.portfolio_import_cli",
                "-m mark_api.windows_customer_choices",
                "-m mark_api.launcher",
            ],
            "windows-start.ps1": [
                "-m mark_api.launcher", "-m mark_api.offline_launcher",
            ],
            "windows-worker.ps1": [
                "-m mark_api.backup_cli",
                "-m mark_api.launcher",
                "-m mark_api.launcher",
                "-m mark_api.email_import_cli",
                "-m mark_api.windows_customer_choices",
                "-m mark_api.portfolio_import_cli",
                "-m mark_api.windows_backup",
            ],
        }
        for path in sorted(WINDOWS.glob("windows-*.ps1")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(script=path.name):
                actual = re.findall(r"-m mark_api\.[A-Za-z0-9_]+", text)
                self.assertCountEqual(actual, compatibility.get(path.name, []))
        center = (WINDOWS / "windows-center.ps1").read_text(encoding="utf-8-sig")
        wrapper = (WINDOWS / "windows-offline-compat.py").read_text(encoding="utf-8")
        self.assertIn("windows-offline-compat.py", center)
        self.assertIn("from mark_api import offline_launcher", wrapper)
        self.assertIn("offline_launcher.main()", wrapper)
        self.assertIn("server.shutdown()", wrapper)
        self.assertNotIn("-m mark_api.offline_launcher", center)

    def test_product_namespace_help_in_clean_subprocess(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        for argv in (
            ["-m", "kleinanzeiger", "launch", "--help"],
            ["-m", "kleinanzeiger.launcher", "--help"],
            ["-m", "kleinanzeiger", "offline", "--help"],
            ["-m", "kleinanzeiger.offline_launcher", "--help"],
            ["-m", "kleinanzeiger", "backup", "--help"],
            ["-m", "kleinanzeiger", "windows-restore", "--help"],
        ):
            with self.subTest(argv=argv):
                proc = subprocess.run([sys.executable, *argv],
                    env=env, cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", timeout=30, check=False)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("usage:", proc.stdout.lower())


if __name__ == "__main__":
    unittest.main()