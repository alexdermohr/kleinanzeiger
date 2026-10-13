"""Offline-only paired SQLite/Witness restore preflight; never a release approval.

A trusted operator must pin the manifest digest outside both restored volumes.
No test invokes a real Kleinanzeigen adapter, clears a fence or restores files.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from mark_api.fence_witness import FenceLedger
from mark_api.paired_restore_preflight import (
    PairPreflightError, inspect_offline_pair, verify_offline_pair, main,
)
from mark_api.storage import SnapshotStore


GENERATION = "7c26d1ed1bed48c6a5a2f4d3c3cb5208"
SHA = "a" * 64
KEY = "pair:write:one"
AT = datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


class PairedRestorePreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="mark-pair-restore-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / "trusted-offline-copies"
        self.source.mkdir(mode=0o700)
        self.sqlite = self.source / "mark.sqlite"
        self.ledger = self.source / "witness.sqlite"
        self.manifest = self.source / "manifest.json"
        SnapshotStore(self.sqlite)
        with FenceLedger(self.ledger, initialize=True):
            pass

    def _start(self, *, key: str = KEY, sha: str = SHA, witness: bool = True) -> None:
        store = SnapshotStore(self.sqlite, create_if_missing=False)
        result = store.claim_write_api_request(
            idempotency_key=key, request_sha256=sha,
            claim_owner="pair-owner", requested_at=AT,
        )
        self.assertTrue(result.created)
        store.begin_write_api_request(
            idempotency_key=key, request_sha256=sha,
            claim_owner="pair-owner", execution_started_at=AT,
            strict_owner_fence=True,
        )
        if witness:
            with FenceLedger(self.ledger) as owner:
                session = owner.open_session()
                owner.execution_started(
                    session, key=key, sha=sha, action="create",
                )
                owner.seal_session(session)

    def _pin(self, *, generation: str = GENERATION) -> str:
        inspected = inspect_offline_pair(self.sqlite, self.ledger)
        manifest = {"schema_version": 1, "generation": generation, **inspected}
        raw = _canonical(manifest)
        self.manifest.write_bytes(raw)
        os.chmod(self.manifest, 0o600)
        return hashlib.sha256(raw).hexdigest()

    def test_empty_pair_with_explicit_external_anchor_is_read_only(self) -> None:
        expected = self._pin()
        files = {
            path: (path.stat().st_ino, path.stat().st_size)
            for path in (self.sqlite, self.ledger, self.manifest)
        }
        receipt = verify_offline_pair(
            self.sqlite, self.ledger, self.manifest,
            expected_manifest_sha256=expected,
        )
        self.assertEqual(receipt["generation"], GENERATION)
        self.assertEqual(receipt["sqlite_started_count"], 0)
        self.assertEqual(receipt["witness_unresolved_count"], 0)
        self.assertTrue(receipt["offline_only"])
        self.assertEqual(files, {
            path: (path.stat().st_ino, path.stat().st_size)
            for path in files
        })

    def test_matching_started_operation_survives_and_never_becomes_cleared(self) -> None:
        self._start()
        expected = self._pin()
        report = verify_offline_pair(
            self.sqlite, self.ledger, self.manifest,
            expected_manifest_sha256=expected,
        )
        self.assertEqual(report["sqlite_started_count"], 1)
        self.assertEqual(report["witness_unresolved_count"], 1)
        self.assertEqual(report["dashboard_pending_count"], 0)
        with FenceLedger(self.ledger) as owner:
            self.assertTrue(owner.fence_pending())
        persisted = SnapshotStore(self.sqlite, create_if_missing=False)
        self.assertTrue(persisted.create_recovery_pending(
            exclude_idempotency_key="other", claim_owner="pair-owner",
        ))

    def test_mismatched_started_key_between_independent_stores_fails(self) -> None:
        self._start(witness=False)
        with FenceLedger(self.ledger) as owner:
            session = owner.open_session()
            owner.execution_started(
                session, key="pair:wrong:key", sha=SHA, action="create",
            )
            owner.seal_session(session)
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_sqlite_fence_removed_after_attestation_is_not_a_valid_pair(self) -> None:
        self._start()
        expected = self._pin()
        with sqlite3.connect(self.sqlite) as connection:
            connection.execute("DELETE FROM write_api_requests")
        with self.assertRaises(PairPreflightError):
            verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=expected,
            )

    def test_witness_rollback_to_older_valid_ledger_is_rejected(self) -> None:
        old_ledger = self.source / "witness-old.sqlite"
        shutil.copyfile(self.ledger, old_ledger)
        os.chmod(old_ledger, 0o600)
        self._start()
        expected = self._pin()
        shutil.copyfile(old_ledger, self.ledger)
        with self.assertRaises(PairPreflightError):
            verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=expected,
            )

    def test_manifest_swap_even_to_consistent_older_generation_fails(self) -> None:
        old = self._pin()
        self._start()
        self._pin(generation="abd4cdd67d41412f82feb2b099830817")
        with self.assertRaises(PairPreflightError):
            verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=old,
            )


    def test_tampered_but_sqlite_valid_witness_hash_chain_fails_closed(self) -> None:
        self._start()
        with sqlite3.connect(self.ledger) as connection:
            connection.execute("DROP TRIGGER events_no_update")
            connection.execute(
                "UPDATE events SET row_sha256=? WHERE kind='opened'",
                ("f" * 64,),
            )
            connection.execute(
                "CREATE TRIGGER events_no_update BEFORE UPDATE ON events "
                "BEGIN SELECT RAISE(ABORT, 'append-only events'); END;"
            )
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_missing_or_invalid_external_anchor_never_passes(self) -> None:
        self._pin()
        for invalid in ("", "a" * 63, "g" * 64):
            with self.subTest(invalid=invalid):
                with self.assertRaises((ValueError, PairPreflightError)):
                    verify_offline_pair(
                        self.sqlite, self.ledger, self.manifest,
                        expected_manifest_sha256=invalid,
                    )

    def test_sidecars_rejected_even_for_zero_byte_wal(self) -> None:
        expected = self._pin()
        wal = self.source / "mark.sqlite-wal"
        wal.write_bytes(b"")
        with self.assertRaises(PairPreflightError):
            verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=expected,
            )

    def test_active_witness_owner_blocks_offline_preflight(self) -> None:
        with FenceLedger(self.ledger) as owner:
            with self.assertRaises(PairPreflightError):
                inspect_offline_pair(self.sqlite, self.ledger)

    def test_symlink_and_world_writable_source_rejected(self) -> None:
        alias = self.source / "linked.sqlite"
        alias.symlink_to(self.sqlite)
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(alias, self.ledger)
        os.chmod(self.sqlite, 0o666)
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_malformed_duplicate_key_manifest_fails_closed(self) -> None:
        expected = self._pin()
        raw = self.manifest.read_bytes()
        poisoned = raw.replace(
            b',"generation":', b',"generation":"forged","generation":',
            1,
        )
        self.manifest.write_bytes(poisoned)
        # Even if the caller mistakenly pins the attacker-supplied bytes,
        # duplicate keys must not pass canonical JSON validation.
        with self.assertRaises(PairPreflightError):
            verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=hashlib.sha256(poisoned).hexdigest(),
            )
        self.assertNotEqual(hashlib.sha256(poisoned).hexdigest(), expected)

    def test_cli_requires_explicit_expected_manifest_digest(self) -> None:
        with self.assertRaises(SystemExit) as exit_code:
            main(["--sqlite", str(self.sqlite), "--witness", str(self.ledger),
                  "--manifest", str(self.manifest)])
        self.assertEqual(exit_code.exception.code, 2)


    def test_dashboard_tombstone_and_sync_open_are_preserved_in_pinned_pair(self) -> None:
        store = SnapshotStore(self.sqlite, create_if_missing=False)
        attempt = store.begin_sync_attempt(
            source="offline-qa", started_at=AT, require_no_unresolved=True,
        )
        store.claim_dashboard_pending_write(
            scope="ad:777", resource_key="ad:777",
            idempotency_key="pending:one", method="DELETE",
            path="/api/write/ads/777", payload_json=None, ad_id="777",
        )
        expected = self._pin()
        result = verify_offline_pair(
            self.sqlite, self.ledger, self.manifest,
            expected_manifest_sha256=expected,
        )
        self.assertEqual(result["dashboard_pending_count"], 1)
        self.assertEqual(result["sync_open_count"], 1)
        self.assertEqual(
            SnapshotStore(self.sqlite, create_if_missing=False)
            .sync_recovery_status()["oldest_unresolved_attempt"]["id"],
            attempt,
        )
        manifest = json.loads(self.manifest.read_bytes())
        # An attacker may manufacture a new SHA, but not the independently
        # pinned floor supplied to the legitimate operator.
        manifest["fences"]["sqlite_sync_open"] = []
        tampered = _canonical(manifest)
        self.manifest.write_bytes(tampered)
        with self.assertRaises(PairPreflightError):
            verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=hashlib.sha256(tampered).hexdigest(),
            )

    def test_witness_sidecars_are_not_accepted_as_valid_snapshot(self) -> None:
        expected = self._pin()
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                sidecar = self.source / ("witness.sqlite" + suffix)
                sidecar.write_bytes(b"")
                with self.assertRaises(PairPreflightError):
                    verify_offline_pair(
                        self.sqlite, self.ledger, self.manifest,
                        expected_manifest_sha256=expected,
                    )
                sidecar.unlink()

    def test_missing_database_fails_without_creating_empty_replacement(self) -> None:
        expected = self._pin()
        self.sqlite.unlink()
        with self.assertRaises(PairPreflightError):
            verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=expected,
            )
        self.assertFalse(self.sqlite.exists())

    def test_unattested_draft_container_denies_new_cli_before_io(self) -> None:
        from unittest.mock import patch

        with patch(
            "mark_api.paired_restore_preflight.container_draft_denied",
            return_value=True,
        ):
            self.assertEqual(main(["--sqlite", "/nonexistent"]), 2)



if __name__ == "__main__":
    unittest.main()