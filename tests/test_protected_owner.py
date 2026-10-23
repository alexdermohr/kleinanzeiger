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


if __name__ == "__main__":
    unittest.main()
