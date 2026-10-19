"""The public Python module entry must never activate the legacy browser launcher."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DefaultEntryIsOfflineTests(unittest.TestCase):
    def _child(self, *args: str) -> subprocess.CompletedProcess[str]:
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root / "src")
        # Keep a real CDP port, credentials and platform flags unavailable.
        return subprocess.run(
            [sys.executable, "-B", "-m", "mark_api", *args],
            cwd=root, env=env, text=True, capture_output=True, timeout=12,
            check=False,
        )

    def test_default_module_help_explicitly_documents_local_files(self) -> None:
        process = self._child("--help")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("read-only loopback dashboard", process.stdout)
        self.assertIn("--email", process.stdout)
        self.assertIn("--init-db", process.stdout)
        self.assertNotIn("--cdp-port", process.stdout)
        self.assertNotIn("--enable-writes", process.stdout)

    def test_default_entry_missing_db_fails_without_creating_anything(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing" / "db.sqlite"
            process = self._child("--db", str(missing))
            self.assertEqual(process.returncode, 2)
            self.assertIn("SQLite database unavailable", process.stderr)
            self.assertFalse(missing.parent.exists())

    def test_importing_module_does_not_import_cdp_or_platform_clients(self) -> None:
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root / "src")
        result = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys,mark_api.__main__; "
             "loaded=sorted(x for x in sys.modules "
             "if x.startswith(('mark_api.private_web','mark_api.adapters','openai'))); "
             "print(loaded);raise SystemExit(bool(loaded))"],
            cwd=root, env=env, text=True, capture_output=True, timeout=12,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")

    def test_default_entry_invalid_local_mail_is_not_a_platform_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mail = root / "invalid.eml"
            mail.write_text("From: untrusted@example.invalid\n\nnot a Kleinanzeigen email", encoding="utf-8")
            db = root / "local.sqlite"
            process = self._child(
                "--db", str(db), "--init-db", "--email", str(mail),
                "--port", "0",
            )
            self.assertEqual(process.returncode, 2)
            self.assertIn("local email import failed", process.stderr)
            self.assertNotIn("CDP", process.stderr)
            self.assertNotIn("http://www.kleinanzeigen.de", process.stdout)
