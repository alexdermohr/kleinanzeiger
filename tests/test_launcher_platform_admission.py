"""Kleinanzeiger launcher admission: technical invariants, not blanket approval."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mark_api.launcher import _parser, build_product_launcher, main
from mark_api.private_web_runtime import PrivateWebRuntimeDependencyError


class ProductStartupAdmissionTests(unittest.TestCase):
    def test_write_ui_is_part_of_the_default_product_and_read_only_is_explicit(self):
        base = ["--db", "/tmp/unused-kleinanzeiger.sqlite", "--cdp-port", "9222"]
        self.assertTrue(_parser().parse_args(base).enable_writes)
        self.assertTrue(_parser().parse_args(base + ["--enable-writes"]).enable_writes)
        self.assertFalse(_parser().parse_args(base + ["--read-only"]).enable_writes)

    def test_bad_cdp_port_rejected_before_database_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "must-not-create.sqlite"
            with self.assertRaisesRegex(ValueError, "cdp_port"):
                build_product_launcher(db_path=db, cdp_port=0, initialize_db=True)
            self.assertFalse(db.exists())

    def test_new_store_requires_browser_dependencies_before_any_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "state.sqlite"
            with patch(
                "mark_api.launcher.require_private_web_runtime_dependency",
                side_effect=PrivateWebRuntimeDependencyError("missing private-web extra"),
            ):
                with self.assertRaises(PrivateWebRuntimeDependencyError):
                    build_product_launcher(
                        db_path=db, cdp_port=9222, initialize_db=True,
                    )
            self.assertFalse(db.parent.exists())

    def test_missing_store_fails_before_browser_access_or_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing" / "store.sqlite"
            with patch("sys.stderr", io.StringIO()) as error:
                code = main(["--db", str(db), "--cdp-port", "9222"])
            self.assertEqual(code, 2)
            self.assertIn("local snapshot store startup failed", error.getvalue())
            self.assertFalse(db.parent.exists())

    def test_environment_flags_do_not_create_missing_store_or_bypass_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "does-not-exist.sqlite"
            for flag in (
                {"MARK_PLATFORM_WRITES_ENABLED": "true"},
                {"MARK_PRIVATE_WEB_APPROVED": "true"},
                {"MARK_PROSELLERS_API_ENTITLEMENT_CONFIRMED": "true"},
            ):
                with self.subTest(flag=flag):
                    with patch.dict(os.environ, flag):
                        self.assertEqual(main(["--db", str(db), "--cdp-port", "9222"]), 2)
                    self.assertFalse(db.exists())

    def test_help_documents_full_product_and_explicit_read_only(self):
        with patch("sys.stdout", io.StringIO()) as out:
            with self.assertRaises(SystemExit) as exc:
                main(["--help"])
        self.assertEqual(exc.exception.code, 0)
        output = out.getvalue()
        self.assertIn("Kleinanzeiger", output)
        self.assertIn("--read-only", output)
        self.assertIn("--enable-generation", output)

    def test_provider_generation_requires_key_before_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "uncreated.sqlite"
            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": ""}),
                patch("mark_api.launcher.build_product_launcher",
                      side_effect=AssertionError("builder must not run")) as builder,
                patch("sys.stderr", io.StringIO()) as error,
            ):
                code = main([
                    "--db", str(db), "--cdp-port", "9222", "--enable-generation",
                ])
            self.assertEqual(code, 2)
            self.assertIn("OPENAI_API_KEY", error.getvalue())
            builder.assert_not_called()
            self.assertFalse(db.exists())

    def test_real_module_invocation_does_not_create_missing_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "not-created.sqlite"
            result = subprocess.run(
                [sys.executable, "-B", "-m", "mark_api.launcher",
                 "--db", str(db), "--cdp-port", "9222"],
                cwd=Path(__file__).resolve().parents[1],
                env={
                    **os.environ,
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                },
                capture_output=True, text=True, check=False, timeout=10,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("local snapshot store startup failed", result.stderr)
            self.assertFalse(db.exists())