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
from dataclasses import replace
from pathlib import Path

from mark_api.domain import (
    AdSnapshot, CreateOperationReceipt, LifecycleState,
    MediaPostReadStatus, OperationOutcome,
)
from mark_api.fence_witness import FenceLedger, FenceWitnessError
from mark_api.paired_restore_preflight import (
    PairPreflightError, inspect_offline_pair, verify_offline_pair, main,
    _witness_owner_absent,
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
        manifest = {"schema_version": 2, "generation": generation, **inspected}
        raw = _canonical(manifest)
        self.manifest.write_bytes(raw)
        os.chmod(self.manifest, 0o600)
        return hashlib.sha256(raw).hexdigest()

    def _create_checkpoint(self, *, reference: str = "write-api:paired-create") -> tuple[CreateOperationReceipt, int]:
        store = SnapshotStore(self.sqlite, create_if_missing=False)
        observed = AdSnapshot(
            ad_id="201", observed_at=AT, source="management",
            lifecycle_state=LifecycleState.ACTIVE,
            title="Restore test vase", description="Synthetic item",
        )
        receipt = CreateOperationReceipt(
            operation="create", started_at=AT, completed_at=AT,
            outcome=OperationOutcome.CONFIRMED,
            pre_read_status="success_empty",
            confirmation_pre_read_status="success_empty",
            post_read_status="success_nonempty",
            confirmation_post_read_status="success_nonempty",
            content_post_read_status="success_nonempty",
            writer_invoked=True, created_ad_id="201",
            authorization_by="offline-test-owner",
            authorization_reference=reference,
            post_snapshot=observed,
            confirmation_post_snapshot=observed,
            content_post_snapshot=observed,
        )
        checkpoint_id = store.append_create_operation_checkpoint(receipt)
        return receipt, checkpoint_id

    def _ambiguous_final_create(
        self, *, reference: str = "write-api:media-unverified",
        media_status: MediaPostReadStatus | None = MediaPostReadStatus.UNKNOWN,
    ) -> int:
        """Browser wrote an ambiguous final media receipt without any checkpoint."""
        receipt = CreateOperationReceipt(
            operation="create", started_at=AT, completed_at=AT,
            outcome=OperationOutcome.AMBIGUOUS,
            pre_read_status="success_empty",
            confirmation_pre_read_status="success_empty",
            post_read_status="unknown",
            confirmation_post_read_status="unknown",
            content_post_read_status="unknown",
            writer_invoked=True, created_ad_id=None,
            authorization_by="offline-test-owner",
            authorization_reference=reference,
            media_post_read_status=media_status,
            media_persistence_confirmed=False,
        )
        SnapshotStore(self.sqlite, create_if_missing=False).append_create_operation_receipt(receipt)
        with sqlite3.connect(self.sqlite) as connection:
            return int(connection.execute(
                "SELECT MAX(id) FROM create_operation_receipts",
            ).fetchone()[0])

    def test_ambiguous_final_media_receipt_without_checkpoint_is_manifested(self) -> None:
        receipt_id = self._ambiguous_final_create()
        store = SnapshotStore(self.sqlite, create_if_missing=False)
        self.assertTrue(store.create_recovery_pending(
            exclude_idempotency_key="new", claim_owner="another-runtime",
        ))
        inspected = inspect_offline_pair(self.sqlite, self.ledger)
        self.assertEqual(inspected["fences"]["ambiguous_creates"], [])
        self.assertEqual(inspected["fences"]["ambiguous_final_creates"], [receipt_id])
        pinned = self._pin()
        receipt = verify_offline_pair(
            self.sqlite, self.ledger, self.manifest,
            expected_manifest_sha256=pinned,
        )
        self.assertEqual(receipt["ambiguous_create_count"], 1)
        self.assertEqual(receipt["ambiguous_final_create_count"], 1)

    def test_nonmedia_ambiguous_final_create_is_not_media_recovery_fence(self) -> None:
        self._ambiguous_final_create(media_status=None)
        self.assertFalse(SnapshotStore(
            self.sqlite, create_if_missing=False,
        ).create_recovery_pending(
            exclude_idempotency_key="new", claim_owner="another-runtime",
        ))
        self.assertEqual(
            inspect_offline_pair(self.sqlite, self.ledger)["fences"]["ambiguous_final_creates"], [],
        )

    def test_unrelated_operator_clearance_cannot_hide_ambiguous_final_receipt(self) -> None:
        receipt_id = self._ambiguous_final_create(reference="write-api:unrelated")
        self._start(key="separate-write")
        store = SnapshotStore(self.sqlite, create_if_missing=False)
        store.record_write_recovery_clearance(
            idempotency_key="separate-write", request_sha256=SHA,
            verification_kind="operator_verified_postread",
            evidence_reference="synthetic-only:operator-outcome", observed_at=AT,
        )
        self.assertTrue(store.create_recovery_pending(
            exclude_idempotency_key="new", claim_owner="another-runtime",
        ))
        self.assertEqual(
            inspect_offline_pair(self.sqlite, self.ledger)["fences"]["ambiguous_final_creates"],
            [receipt_id],
        )

    def test_exact_cleared_final_receipt_no_longer_adds_unresolved_media_fence(self) -> None:
        self._start(key="media-exact")
        self._ambiguous_final_create(reference="write-api:media-exact")
        store = SnapshotStore(self.sqlite, create_if_missing=False)
        store.record_write_recovery_clearance(
            idempotency_key="media-exact", request_sha256=SHA,
            verification_kind="operator_verified_postread",
            evidence_reference="synthetic-only:independent-review", observed_at=AT,
        )
        self.assertFalse(store.create_recovery_pending(
            exclude_idempotency_key="new", claim_owner="another-runtime",
        ))
        inspected = inspect_offline_pair(self.sqlite, self.ledger)
        self.assertEqual(inspected["fences"]["ambiguous_final_creates"], [])
        self.assertEqual(inspected["fences"]["witness_unresolved"], [["media-exact", SHA]])

    def test_v1_manifest_not_accepted_after_fence_inventory_schema_upgrade(self) -> None:
        expected = self._pin()
        loaded = json.loads(self.manifest.read_bytes())
        self.assertEqual(loaded["schema_version"], 2)
        loaded["schema_version"] = 1
        older = _canonical(loaded)
        self.manifest.write_bytes(older)
        with self.assertRaises(PairPreflightError):
            verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=hashlib.sha256(older).hexdigest(),
            )
        self.assertNotEqual(hashlib.sha256(older).hexdigest(), expected)

    def test_missing_witness_lock_fails_closed_without_recreating_lock(self) -> None:
        lock = self.ledger.with_name(self.ledger.name + ".lock")
        lock.unlink()
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)
        self.assertFalse(lock.exists(), "read-only verifier must not create a lock")

    def test_held_offline_lock_rejects_new_witness_owner_after_scan_starts(self) -> None:
        with _witness_owner_absent(self.ledger):
            with self.assertRaises(FenceWitnessError):
                FenceLedger(self.ledger)

    def test_witness_lock_mode_must_match_owner_protocol(self) -> None:
        lock = self.ledger.with_name(self.ledger.name + ".lock")
        os.chmod(lock, 0o400)
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_confirmed_create_checkpoint_without_final_receipt_is_unresolved(self) -> None:
        receipt, checkpoint = self._create_checkpoint()
        inspected = inspect_offline_pair(self.sqlite, self.ledger)
        self.assertEqual(inspected["fences"]["ambiguous_creates"], [checkpoint])
        externally_anchored = self._pin()
        observed = verify_offline_pair(
            self.sqlite, self.ledger, self.manifest,
            expected_manifest_sha256=externally_anchored,
        )
        self.assertEqual(observed["ambiguous_create_count"], 1)
        final = replace(
            receipt,
            media_post_read_status=MediaPostReadStatus.CONFIRMED,
            media_persistence_confirmed=True,
        )
        SnapshotStore(self.sqlite, create_if_missing=False).append_create_operation_receipt(final)
        after = inspect_offline_pair(self.sqlite, self.ledger)
        self.assertEqual(after["fences"]["ambiguous_creates"], [])
        # The old externally pinned manifest never silently accepts a newer
        # different source generation even if both are locally consistent.
        with self.assertRaises(PairPreflightError):
            verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=externally_anchored,
            )

    def test_other_receipt_cannot_settle_a_crashed_checkpoint(self) -> None:
        receipt, checkpoint = self._create_checkpoint()
        foreign_final = replace(
            receipt,
            authorization_reference="write-api:unrelated",
            media_post_read_status=MediaPostReadStatus.CONFIRMED,
            media_persistence_confirmed=True,
        )
        SnapshotStore(self.sqlite, create_if_missing=False).append_create_operation_receipt(foreign_final)
        self.assertEqual(
            inspect_offline_pair(self.sqlite, self.ledger)["fences"]["ambiguous_creates"],
            [checkpoint],
        )

    def test_unconfirmed_media_receipt_does_not_settle_checkpoint(self) -> None:
        receipt, checkpoint = self._create_checkpoint()
        final = replace(receipt, media_persistence_confirmed=False)
        SnapshotStore(self.sqlite, create_if_missing=False).append_create_operation_receipt(final)
        self.assertEqual(
            inspect_offline_pair(self.sqlite, self.ledger)["fences"]["ambiguous_creates"],
            [checkpoint],
        )

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