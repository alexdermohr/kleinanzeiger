"""Fail-closed legal admission for the real private-web/CDP inventory smoke.

No test opens real CDP, Kleinanzeigen, SQLite, HTTP or an actual account.
Injected synthetic runtime factories remain available to existing tests.
"""
from __future__ import annotations

import io
import os
import socket
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import mark_api.private_web_runtime_smoke as smoke


class PrivateWebSmokeAdmissionTests(unittest.TestCase):
    def _assert_default_denied(self) -> None:
        # Even a correctly configured local browser and a self-declared
        # environment flag cannot establish *independent* platform authority.
        with (
            patch.object(smoke, "SnapshotStore", side_effect=AssertionError("no SQLite")) as store,
            patch.object(smoke, "create_server", side_effect=AssertionError("no HTTP")) as server,
            patch.object(smoke, "build_opener", side_effect=AssertionError("no HTTP client")) as opener,
            patch("socket.socket", side_effect=AssertionError("no socket")),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "independent platform permission is not established"
            ):
                smoke.run_private_web_runtime_smoke(19610, timeout_seconds=0.1)
            store.assert_not_called()
            server.assert_not_called()
            opener.assert_not_called()

    def test_programmatic_default_fails_before_any_network_or_storage(self) -> None:
        self._assert_default_denied()

    def test_unattested_environment_booleans_cannot_enable_platform_reads(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MARK_PLATFORM_WRITES_ENABLED": "true",
                "MARK_ALLOW_PRIVATE_WEB_READS": "true",
                "KLEINANZEIGEN_API_ENTITLEMENT_CONFIRMED": "true",
            },
        ):
            self._assert_default_denied()

    def test_cli_main_rejects_real_default_without_platform_side_effects(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(smoke, "SnapshotStore", side_effect=AssertionError("no SQLite")) as store,
            patch.object(smoke, "create_server", side_effect=AssertionError("no server")) as server,
            patch("socket.socket", side_effect=AssertionError("no socket")),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            with self.assertRaises(SystemExit) as exc:
                smoke.main(["--cdp-port", "19610", "--timeout-seconds", "0.1"])
            self.assertEqual(exc.exception.code, 2)
            store.assert_not_called()
            server.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("independent platform permission", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_synthetic_dependency_injection_remains_available(self) -> None:
        calls: list[tuple[int, float]] = []

        def synthetic_factory(*, cdp_port: int, timeout_seconds: float):
            calls.append((cdp_port, timeout_seconds))
            raise RuntimeError("synthetic adapter only")

        with self.assertRaisesRegex(RuntimeError, "synthetic adapter only"):
            smoke.run_private_web_runtime_smoke(
                19610, timeout_seconds=0.2, runtime_factory=synthetic_factory
            )
        self.assertEqual(calls, [(19610, 0.2)])


if __name__ == "__main__":
    unittest.main()
