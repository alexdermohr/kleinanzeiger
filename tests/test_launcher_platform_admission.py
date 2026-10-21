"""Synthetic product startup tests. Never contact a real platform or CDP."""
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


class FakeLauncher:
    write_server_address = ("127.0.0.1", 18766)
    dashboard_url = "http://127.0.0.1:18765/#write_token=synthetic"
    write_bearer_token = "synthetic-test-token"
    startup_inventory_count = 0
    startup_persisted_count = 0

    def __init__(self):
        self.served = 0
        self.closed = 0

    def serve_forever(self):
        self.served += 1

    def close(self):
        self.closed += 1


class ProductStartupTests(unittest.TestCase):
    def test_cli_reaches_synthetic_builder_without_blanket_denial(self):
        launcher = FakeLauncher()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "existing.sqlite"
            with (
                patch("mark_api.launcher.build_product_launcher", return_value=launcher)
                as builder,
                patch("sys.stdout", io.StringIO()) as output,
                patch.dict(os.environ, {"MARK_PRIVATE_WEB_APPROVED": "false"}),
            ):
                code = main(["--db", str(db), "--cdp-port", "9222"])
        self.assertEqual(code, 0)
        builder.assert_called_once()
        self.assertEqual(builder.call_args.kwargs["db_path"], db)
        self.assertEqual(builder.call_args.kwargs["cdp_port"], 9222)
        self.assertFalse(builder.call_args.kwargs["initialize_db"])
        self.assertFalse(builder.call_args.kwargs["enable_writes"])
        self.assertEqual((launcher.served, launcher.closed), (1, 1))
        self.assertIn("Startup sync: 0 current ad(s)", output.getvalue())
        self.assertIn("Mark platform writes: disabled", output.getvalue())
        self.assertNotIn("Mark write API:", output.getvalue())
        self.assertNotIn(launcher.write_bearer_token, output.getvalue())

    def test_cli_write_activation_requires_explicit_flag_and_never_prints_backend_bearer(self):
        launcher = FakeLauncher()
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("mark_api.launcher.build_product_launcher", return_value=launcher)
                as builder,
                patch("sys.stdout", io.StringIO()) as output,
            ):
                code = main(["--db", str(Path(tmp) / "state.sqlite"),
                             "--cdp-port", "9222", "--enable-writes"])
        self.assertEqual(code, 0)
        self.assertTrue(builder.call_args.kwargs["enable_writes"])
        self.assertIn("Mark write API:", output.getvalue())
        self.assertNotIn(launcher.write_bearer_token, output.getvalue())
        self.assertNotIn("Mark write bearer token", output.getvalue())

    def test_programmatic_default_disables_capabilities_and_dashboard_proxy(self):
        from mark_api.results import ReadResult
        from mark_api.storage import SnapshotStore

        class Owner:
            def read_inventory(self):
                return ReadResult.success_empty(())
            def close(self):
                return None

        class WriteRuntime:
            server_address = ("127.0.0.1", 18766)
            def start(self):
                return self.server_address
            def close(self):
                return None

        class Server:
            server_address = ("127.0.0.1", 18765)
            def server_close(self):
                return None

        observed = {}
        def make_write(**kwargs):
            observed["write"] = kwargs
            return WriteRuntime()
        def make_dashboard(_store, **kwargs):
            observed["dashboard"] = kwargs
            return Server()

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite"
            with (
                patch("mark_api.launcher._require_private_web_cdp_authority"),
                patch("mark_api.launcher.build_private_web_write_api_runtime",
                      side_effect=AssertionError("use injected write only")),
            ):
                launcher = build_product_launcher(
                    db_path=db, cdp_port=9222, initialize_db=True,
                    runtime_factory=lambda **_kwargs: Owner(),
                    write_runtime_factory=make_write,
                    dashboard_factory=make_dashboard,
                )
            try:
                self.assertFalse(launcher.writes_enabled)
                self.assertEqual(launcher.dashboard_url, "http://127.0.0.1:18765/")
                self.assertIsNone(observed["dashboard"]["write_proxy"])
                self.assertFalse(observed["write"]["core_writes_enabled"])
                self.assertFalse(observed["write"]["media_writes_enabled"])
                self.assertFalse(observed["write"]["access"].writes_enabled)
                self.assertEqual(observed["write"]["access"].capabilities, frozenset())
            finally:
                launcher.close()
            self.assertTrue(SnapshotStore(db).is_ready())

    def test_cli_preserves_bounded_builder_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing.sqlite"
            with (
                patch("mark_api.launcher.build_product_launcher",
                      side_effect=ProductLauncherError("local snapshot store startup failed"))
                as builder,
                patch("sys.stderr", io.StringIO()) as error,
            ):
                code = main(["--db", str(db), "--cdp-port", "9222"])
            self.assertEqual(code, 2)
            builder.assert_called_once()
            self.assertIn("local snapshot store startup failed", error.getvalue())
            self.assertFalse(db.exists())

    def test_real_builder_missing_database_never_starts_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "typo" / "state.sqlite"
            def unexpected_runtime(**kwargs):
                raise AssertionError("unexpected CDP")
            with (
                patch("mark_api.launcher._require_private_web_cdp_authority"),
                self.assertRaisesRegex(
                    ProductLauncherError, "local snapshot store startup failed"
                ),
            ):
                build_product_launcher(db_path=db, cdp_port=9222,
                                       runtime_factory=unexpected_runtime)
            self.assertFalse(db.parent.exists())

    def test_invalid_cdp_port_rejected_before_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite"
            with patch("mark_api.launcher.SnapshotStore",
                       side_effect=AssertionError("unexpected store")) as store:
                with self.assertRaisesRegex(ValueError, "cdp_port"):
                    build_product_launcher(db_path=db, cdp_port=0)
                store.assert_not_called()
            self.assertFalse(db.exists())

    def test_help_describes_functioning_product_launcher(self):
        with patch("sys.stdout", io.StringIO()) as output:
            with self.assertRaises(SystemExit) as exit_status:
                main(["--help"])
        self.assertEqual(exit_status.exception.code, 0)
        help_text = output.getvalue().replace("\n", " ")
        self.assertIn("already-authenticated local Chrome/CDP", help_text)
        self.assertIn("--cdp-port", help_text)
        self.assertIn("--enable-writes", help_text)
        self.assertNotIn("currently unavailable", help_text)

    def test_real_module_denies_unentitled_before_missing_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "typo" / "state.sqlite"
            repo = Path(__file__).resolve().parents[1]
            result = subprocess.run(
                [sys.executable, "-B", "-m", "mark_api.launcher",
                 "--db", str(db), "--cdp-port", "9222"],
                cwd=repo,
                env={**os.environ, "PYTHONPATH": str(repo / "src")},
                capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("automatic Kleinanzeigen/CDP access is unavailable", result.stderr)
            self.assertFalse(db.parent.exists())


    def test_public_builder_default_private_web_denies_before_sqlite(self):
        # A browser login, a user-controlled flag, and old environment
        # variables never prove the account's external platform permission.
        for enable_writes in (False, True):
            with self.subTest(enable_writes=enable_writes):
                with tempfile.TemporaryDirectory() as tmp:
                    db = Path(tmp) / "never-created.sqlite"
                    with (
                        patch("mark_api.launcher.SnapshotStore") as storage,
                        patch("mark_api.launcher.build_private_web_write_api_runtime") as writer,
                        patch.dict(os.environ, {"MARK_PRIVATE_WEB_APPROVED": "true"}),
                    ):
                        with self.assertRaisesRegex(
                            ProductLauncherError, "verified official account entitlement"
                        ):
                            build_product_launcher(
                                db_path=db, cdp_port=9222,
                                initialize_db=True, enable_writes=enable_writes,
                            )
                        storage.assert_not_called()
                        writer.assert_not_called()
                    self.assertFalse(db.exists())

    def test_public_builder_injected_runtime_never_bypasses_entitlement(self):
        # This public API accepts a factory for fakes, not platform authority.
        # In production even an injected factory must be denied before DB.
        for enabled in (False, True):
            with self.subTest(enable_writes=enabled):
                with tempfile.TemporaryDirectory() as tmp:
                    db = Path(tmp) / "must-not-exist.sqlite"
                    calls = []
                    def fake_browser(**_kw):
                        calls.append("unentitled_factory_invoked")
                        raise RuntimeError("synthetic browser should not run")
                    with self.assertRaisesRegex(
                        ProductLauncherError, "verified official account entitlement"
                    ):
                        build_product_launcher(
                            db_path=db, cdp_port=19610, initialize_db=True,
                            enable_writes=enabled, runtime_factory=fake_browser,
                        )
                    self.assertFalse(db.exists(), "unentitled programmatic call created SQLite")
                    self.assertEqual(calls, [], "unentitled custom factory was invoked")

    def test_public_cli_denies_before_sqlite_and_cdp_even_with_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "never-created.sqlite"
            for options in (
                (),
                ("--init-db",),
                ("--enable-writes",),
                ("--init-db", "--enable-writes"),
            ):
                with self.subTest(options=options):
                    with (
                        patch("mark_api.launcher.SnapshotStore") as storage,
                        patch("mark_api.launcher.build_private_web_inventory_runtime") as cdp,
                        patch("sys.stderr", io.StringIO()) as errors,
                    ):
                        code = main([
                            "--db", str(db), "--cdp-port", "9222", *options,
                        ])
                        self.assertEqual(code, 2)
                        self.assertIn("verified official account entitlement", errors.getvalue())
                        storage.assert_not_called()
                        cdp.assert_not_called()
                    self.assertFalse(db.exists())

    def test_unentitled_transport_cannot_be_unlocked_with_boolean_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite"
            with patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    main([
                        "--db", str(db), "--cdp-port", "9222",
                        "--platform-authorized", "--enable-writes",
                    ])
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()