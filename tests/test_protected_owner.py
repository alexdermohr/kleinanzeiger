"""Fail-closed protected owner admission, before privileged services exist."""
from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from mark_api.completion_authority import CompletionAuthorityError
from mark_api.protected_owner import ProtectedOwnerRuntime, create_protected_owner_runtime


def params():
    uid = os.geteuid()
    return {
        "database": Path("/tmp/synthetic-owner-main.sqlite"),
        "dashboard_socket": Path("/tmp/synthetic-owner-dashboard.sock"),
        "witness_socket": Path("/tmp/synthetic-owner-witness.sock"),
        "actor_socket": Path("/tmp/synthetic-owner-actor.sock"),
        "witness_uid": uid + 101, "actor_uid": uid + 102,
        "runtime_uid": uid + 103, "runtime_gid": uid + 103,
        "principal": "synthetic-customer",
        "bearer_token": "synthetic-private-owner-bearer",
        "media_enabled": True, "write_port": 0,
    }


class ProtectedOwnerAdmissionTests(unittest.TestCase):
    def test_relative_non_path_or_overlapping_resources_deny_before_witness(self):
        defaults = params()
        cases = [
            {"database": Path("relative.sqlite")},
            {"dashboard_socket": "/tmp/owner.sock"},
            {"actor_socket": defaults["witness_socket"]},
            {"write_port": True},
            {"write_port": -1},
            {"media_enabled": 1},
            {"runtime_gid": 0},
        ]
        for altered in cases:
            with self.subTest(altered=altered), patch(
                "mark_api.protected_owner.FenceWitnessClient",
            ) as witness:
                with self.assertRaises(ValueError):
                    create_protected_owner_runtime(**{**defaults, **altered})
                witness.assert_not_called()

    def test_equal_runtime_actor_witness_or_root_identity_denied(self):
        cfg = params()
        for changed in (
            {"actor_uid": cfg["runtime_uid"]},
            {"witness_uid": cfg["actor_uid"]},
            {"runtime_uid": os.geteuid()},
        ):
            with self.subTest(changed=changed), patch(
                "mark_api.protected_owner.FenceWitnessClient",
            ) as witness:
                with self.assertRaises(CompletionAuthorityError):
                    create_protected_owner_runtime(**{**cfg, **changed})
                witness.assert_not_called()

    @unittest.skipIf(os.geteuid() == 0, "nonroot UID required")
    def test_missing_independent_witness_never_creates_sqlite_or_owner(self):
        cfg = params()
        with patch(
            "mark_api.protected_owner.FenceWitnessClient",
            side_effect=OSError("synthetic witness absent"),
        ) as witness, patch(
            "mark_api.protected_owner.CompletionDashboardOwnerServer",
        ) as owner:
            with self.assertRaises(OSError):
                create_protected_owner_runtime(**cfg)
            witness.assert_called_once_with(
                cfg["witness_socket"], expected_owner_uid=cfg["witness_uid"],
            )
            owner.assert_not_called()

    def test_owner_close_always_closes_witness_after_owner_stop_failure(self):
        dashboard = MagicMock()
        dashboard.stop.side_effect = RuntimeError("synthetic quiescence error")
        witness = MagicMock()
        runtime = ProtectedOwnerRuntime(object(), dashboard, witness)
        with self.assertRaises(RuntimeError):
            runtime.close()
        witness.close.assert_called_once_with()

    @unittest.skipIf(os.geteuid() == 0, "nonroot UID required")
    def test_factory_reuses_exact_attested_owner_store_for_write_api(self):
        cfg = params()
        independent_owner = MagicMock()
        independent_owner.store = object()
        witness = MagicMock()
        server = MagicMock()
        with patch(
            "mark_api.protected_owner.FenceWitnessClient", return_value=witness,
        ), patch(
            "mark_api.protected_owner.CompletionDashboardOwnerServer",
            return_value=independent_owner,
        ), patch(
            "mark_api.protected_owner.create_owner_write_api_server",
            return_value=server,
        ) as write_builder:
            runtime = create_protected_owner_runtime(**cfg)
            self.assertIs(runtime.write_server, server)
            independent_owner.start.assert_called_once_with()
            bound = write_builder.call_args.kwargs
            self.assertIs(bound["store"], independent_owner.store)
            self.assertIs(bound["fence_witness"], witness)
            self.assertEqual(bound["authority_uid"], cfg["actor_uid"])
            self.assertEqual(bound["runtime_uid"], cfg["runtime_uid"])
            self.assertEqual(bound["access"].principal, cfg["principal"])
            self.assertTrue(bound["access"].writes_enabled)
            runtime.close()
            independent_owner.stop.assert_called_once_with()
            witness.close.assert_called_once_with()


class ProtectedOwnerCliTests(unittest.TestCase):
    def pipe(self, content: bytes) -> int:
        fd, writer = os.pipe()
        try:
            os.write(writer, content)
        finally:
            os.close(writer)
        self.addCleanup(os.close, fd)
        return fd

    def test_private_owner_config_is_exact_and_bounded(self):
        import json
        from mark_api.protected_owner import _read_private_owner_config
        cfg = {**params(), **{
            key: str(params()[key])
            for key in ("database", "dashboard_socket", "witness_socket", "actor_socket")
        }}
        data = (json.dumps(cfg) + "\n").encode()
        self.assertEqual(_read_private_owner_config(self.pipe(data)), cfg)

    def test_malformed_foreign_or_config_escape_rejected(self):
        import json
        from mark_api.protected_owner import _read_private_owner_config
        cfg = {**params(), **{
            key: str(params()[key])
            for key in ("database", "dashboard_socket", "witness_socket", "actor_socket")
        }}
        cases = (
            b'{"actor_uid":1,"actor_uid":2}\n',
            b'{"actor_uid":NaN}\n',
            b"{}\n" + b"x" * 8200,
            (json.dumps({**cfg, "sql": "DROP TABLE"}) + "\n").encode(),
            (json.dumps({**cfg, "media_enabled": 1}) + "\n").encode(),
            (json.dumps({**cfg, "database": "../owner.sqlite"}) + "\n").encode(),
            (json.dumps({k: v for k, v in cfg.items() if k != "bearer_token"}) + "\n").encode(),
        )
        for data in cases:
            with self.subTest(data=data[:25]), self.assertRaises(ValueError):
                _read_private_owner_config(self.pipe(data))

    def test_missing_witness_and_bootstrap_never_open_untrusted_sqlite(self):
        import io
        import json
        from contextlib import redirect_stderr
        from mark_api.protected_owner import main
        cfg = {**params(), **{
            key: str(params()[key])
            for key in ("database", "dashboard_socket", "witness_socket", "actor_socket")
        }}
        buf = io.StringIO()
        with redirect_stderr(buf):
            status = main(["--config-fd", str(self.pipe((json.dumps(cfg) + "\n").encode()))])
        self.assertEqual(status, 2)
        self.assertIn("unavailable", buf.getvalue())
        self.assertNotIn(cfg["bearer_token"], buf.getvalue())
        self.assertNotIn(cfg["database"], buf.getvalue())


    def test_shutdown_exception_is_fail_closed_and_never_logs_private_path(self):
        import io
        import signal
        from contextlib import redirect_stderr
        from mark_api.protected_owner import main

        cfg = {**params(), **{
            name: str(params()[name])
            for name in ("database", "dashboard_socket", "witness_socket", "actor_socket")
        }}
        fake_runtime = MagicMock()
        fake_runtime.write_server.server_port = 17721
        fake_runtime.write_server.shutdown.side_effect = RuntimeError(
            "synthetic database path " + cfg["database"] + " token " + cfg["bearer_token"]
        )
        stderr = io.StringIO()
        with patch(
            "mark_api.protected_owner._read_private_owner_config",
            return_value=cfg,
        ), patch(
            "mark_api.protected_owner.create_protected_owner_runtime",
            return_value=fake_runtime,
        ), patch(
            "threading.Event"
        ), patch(
            "threading.Thread"
        ), patch(
            "signal.signal", return_value=signal.SIG_DFL
        ), redirect_stderr(stderr):
            result = main(["--config-fd", "0"])
        self.assertEqual(result, 2)
        self.assertNotIn(cfg["database"], stderr.getvalue())
        self.assertNotIn(cfg["bearer_token"], stderr.getvalue())
        fake_runtime.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()