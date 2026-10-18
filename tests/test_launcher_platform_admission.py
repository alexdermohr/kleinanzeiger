"""Default-deny legal platform admission; synthetic sources only."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from mark_api.launcher import ProductLauncherError, build_product_launcher, main


class PlatformAdmissionTests(unittest.TestCase):
    def test_cli_denied_before_store_browser_or_injected_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "state.sqlite"
            with (
                patch("mark_api.launcher.SnapshotStore",
                      side_effect=AssertionError("store touched")) as store,
                patch("mark_api.launcher.build_product_launcher",
                      side_effect=AssertionError("builder touched")) as builder,
                patch("mark_api.launcher.build_private_web_inventory_runtime",
                      side_effect=AssertionError("CDP touched")) as runtime,
                patch("sys.stderr", io.StringIO()) as error,
            ):
                code = main(["--db", str(db), "--init-db",
                             "--cdp-port", "9222", "--email",
                             str(Path(tmp) / "unused.eml")])
            self.assertEqual(code, 2)
            self.assertIn("not authorized", error.getvalue())
            store.assert_not_called()
            builder.assert_not_called()
            runtime.assert_not_called()
            self.assertFalse(db.parent.exists())

    def test_programmatic_builder_denied_before_any_write_or_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "new.sqlite"
            with (
                patch("mark_api.launcher.SnapshotStore",
                      side_effect=AssertionError("store opened")) as store,
                patch("mark_api.launcher._default_write_token",
                      side_effect=AssertionError("token generated")) as token,
            ):
                with self.assertRaisesRegex(ProductLauncherError, "not authorized"):
                    build_product_launcher(
                        db_path=db, cdp_port=9222, initialize_db=True)
                store.assert_not_called()
                token.assert_not_called()
            self.assertFalse(db.exists())

    def test_arbitrary_environment_flags_do_not_unlock_cdp(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "unused.sqlite"
            for flag in (
                {"MARK_PLATFORM_WRITES_ENABLED": "true"},
                {"MARK_PRIVATE_WEB_APPROVED": "true"},
                {"MARK_PROSELLERS_API_ENTITLEMENT_CONFIRMED": "true"},
            ):
                with self.subTest(flag=flag):
                    with patch.dict(os.environ, flag), patch("sys.stderr", io.StringIO()) as err:
                        self.assertEqual(main(["--db", str(db), "--cdp-port", "9222"]), 2)
                        self.assertIn("not authorized", err.getvalue())
                    self.assertFalse(db.exists())

    def test_legacy_help_is_readable_and_warns(self):
        with patch("sys.stdout", io.StringIO()) as out:
            with self.assertRaises(SystemExit) as exc:
                main(["--help"])
        self.assertEqual(exc.exception.code, 0)
        self.assertIn("currently unavailable", out.getvalue())
        self.assertIn("mark-api-offline", out.getvalue().replace("\n", ""))

    def test_real_module_invocation_denies_before_creating_any_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "not-created.sqlite"
            result = subprocess.run(
                [sys.executable, "-B", "-m", "mark_api.launcher",
                 "--db", str(db), "--cdp-port", "9222", "--init-db"],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
                capture_output=True, text=True, check=False, timeout=10,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("not authorized", result.stderr)
            self.assertFalse(db.exists())
