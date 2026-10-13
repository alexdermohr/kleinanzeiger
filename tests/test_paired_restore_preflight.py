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
from unittest.mock import patch

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
        manifest = {"schema_version": 4, "generation": generation, **inspected}
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
        self.assertEqual(loaded["schema_version"], 4)
        loaded["schema_version"] = 1
        older = _canonical(loaded)
        self.manifest.write_bytes(older)
        with self.assertRaises(PairPreflightError):
            verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=hashlib.sha256(older).hexdigest(),
            )
        self.assertNotEqual(hashlib.sha256(older).hexdigest(), expected)

    def _persist_media_http_response(
        self, *, key: str, status: int, envelope: dict[str, object],
    ) -> None:
        self._start(key=key)
        SnapshotStore(self.sqlite, create_if_missing=False).complete_write_api_request(
            idempotency_key=key, request_sha256=SHA, claim_owner="pair-owner",
            response_status=status,
            response_json=json.dumps(
                envelope, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
            ),
            completed_at=AT,
        )

    def test_http500_missing_media_receipt_is_an_unresolved_restore_fence(self) -> None:
        key = "media:executor-failure"
        self._persist_media_http_response(
            key=key, status=500,
            envelope={"media_persistence_confirmed": False, "operation_receipt": None},
        )
        store = SnapshotStore(self.sqlite, create_if_missing=False)
        self.assertTrue(store.create_recovery_pending(
            exclude_idempotency_key="different-key", claim_owner="separate-owner",
        ))
        inspected = inspect_offline_pair(self.sqlite, self.ledger)
        self.assertEqual(inspected["fences"]["sqlite_quarantined"], [])
        self.assertEqual(inspected["fences"]["ambiguous_final_creates"], [])
        self.assertEqual(inspected["fences"]["sqlite_media_unresolved"], [[key, SHA]])
        manifest_sha = self._pin()
        report = verify_offline_pair(
            self.sqlite, self.ledger, self.manifest,
            expected_manifest_sha256=manifest_sha,
        )
        self.assertEqual(report["sqlite_media_unresolved_count"], 1)
        self.assertEqual(report["ambiguous_create_count"], 1)
        self.assertFalse(report["restore_authorized"])

    def test_ambiguous_media_receipt_in_http500_response_is_fenced(self) -> None:
        key = "media:unknown-500"
        self._persist_media_http_response(
            key=key, status=500,
            envelope={
                "media_persistence_confirmed": False,
                "operation_receipt": {"outcome": "ambiguous", "writer_invoked": True},
            },
        )
        self.assertEqual(
            inspect_offline_pair(self.sqlite, self.ledger)["fences"]["sqlite_media_unresolved"],
            [[key, SHA]],
        )

    def test_valid_confirmed_media_http_response_is_not_a_new_media_fence(self) -> None:
        key = "media:final-confirmed"
        self._persist_media_http_response(
            key=key, status=200,
            envelope={
                "media_persistence_confirmed": True,
                "operation_receipt": {"outcome": "confirmed", "writer_invoked": True},
            },
        )
        self.assertFalse(SnapshotStore(
            self.sqlite, create_if_missing=False,
        ).create_recovery_pending(
            exclude_idempotency_key="different-key", claim_owner="separate-owner",
        ))
        self.assertEqual(
            inspect_offline_pair(self.sqlite, self.ledger)["fences"]["sqlite_media_unresolved"],
            [],
        )

    def test_malformed_completed_media_result_cannot_yield_empty_fence_inventory(self) -> None:
        self._persist_media_http_response(
            key="media:bad500", status=500,
            envelope={
                "media_persistence_confirmed": "false",
                "operation_receipt": None,
            },
        )
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_only_exact_clearance_removes_failed_media_http_response_fence(self) -> None:
        key = "media:failed-clear"
        self._persist_media_http_response(
            key=key, status=500,
            envelope={"media_persistence_confirmed": False, "operation_receipt": None},
        )
        store = SnapshotStore(self.sqlite, create_if_missing=False)
        store.record_write_recovery_clearance(
            idempotency_key=key, request_sha256=SHA,
            verification_kind="operator_verified_postread",
            evidence_reference="synthetic-test-only:media-operator-postread",
            observed_at=AT,
        )
        self.assertFalse(store.create_recovery_pending(
            exclude_idempotency_key="different-key", claim_owner="separate-owner",
        ))
        self.assertEqual(
            inspect_offline_pair(self.sqlite, self.ledger)["fences"]["sqlite_media_unresolved"],
            [],
        )

    def test_v3_manifest_rejected_after_media_recovery_fence_upgrade(self) -> None:
        expected = self._pin()
        manifest = json.loads(self.manifest.read_bytes())
        self.assertEqual(manifest["schema_version"], 4)
        manifest["schema_version"] = 3
        downgraded = _canonical(manifest)
        self.manifest.write_bytes(downgraded)
        with self.assertRaises(PairPreflightError):
            verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=hashlib.sha256(downgraded).hexdigest(),
            )
        self.assertNotEqual(expected, hashlib.sha256(downgraded).hexdigest())

    def test_owner_quarantined_write_is_a_distinct_unresolved_restore_fence(self) -> None:
        self._start(key="quarantine:no-create-receipt")
        store = SnapshotStore(self.sqlite, create_if_missing=False)
        store.quarantine_started_write_api_request(
            idempotency_key="quarantine:no-create-receipt",
            request_sha256=SHA,
            claim_owner="pair-owner",
            quarantined_at=AT,
        )
        self.assertTrue(store.create_recovery_pending(
            exclude_idempotency_key="another-key", claim_owner="separate-owner",
        ))
        observed = inspect_offline_pair(self.sqlite, self.ledger)
        self.assertEqual(observed["fences"]["ambiguous_creates"], [])
        self.assertEqual(observed["fences"]["ambiguous_final_creates"], [])
        self.assertEqual(
            observed["fences"]["sqlite_quarantined"],
            [["quarantine:no-create-receipt", SHA]],
        )
        digest = self._pin()
        report = verify_offline_pair(
            self.sqlite, self.ledger, self.manifest,
            expected_manifest_sha256=digest,
        )
        self.assertEqual(report["sqlite_quarantined_count"], 1)
        self.assertEqual(report["ambiguous_create_count"], 1)
        self.assertFalse(report["restore_authorized"])

    def test_independent_operation_bound_clearance_excludes_quarantine_fence(self) -> None:
        self._start(key="quarantine:cleared")
        store = SnapshotStore(self.sqlite, create_if_missing=False)
        store.quarantine_started_write_api_request(
            idempotency_key="quarantine:cleared", request_sha256=SHA,
            claim_owner="pair-owner", quarantined_at=AT,
        )
        store.record_write_recovery_clearance(
            idempotency_key="quarantine:cleared", request_sha256=SHA,
            verification_kind="operator_verified_postread",
            evidence_reference="synthetic-test-only:operator-observation",
            observed_at=AT,
        )
        self.assertFalse(store.create_recovery_pending(
            exclude_idempotency_key="unrelated", claim_owner="separate-owner",
        ))
        result = inspect_offline_pair(self.sqlite, self.ledger)
        self.assertEqual(result["fences"]["sqlite_quarantined"], [])
        self.assertEqual(result["fences"]["sqlite_started"], [["quarantine:cleared", SHA]])

    def test_quarantined_response_with_unsupported_envelope_fails_closed(self) -> None:
        self._start(key="quarantine:tamper")
        store = SnapshotStore(self.sqlite, create_if_missing=False)
        store.quarantine_started_write_api_request(
            idempotency_key="quarantine:tamper", request_sha256=SHA,
            claim_owner="pair-owner", quarantined_at=AT,
        )
        with sqlite3.connect(self.sqlite) as connection:
            connection.execute(
                "UPDATE write_api_requests SET response_json=? WHERE idempotency_key=?",
                ('{"error":"write_outcome_unverified","idempotency_key":"other-key"}',
                 "quarantine:tamper"),
            )
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_v2_manifest_rejected_when_quarantine_fence_schema_is_upgraded(self) -> None:
        old_sha = self._pin()
        raw = json.loads(self.manifest.read_bytes())
        self.assertEqual(raw["schema_version"], 4)
        raw["schema_version"] = 2
        old_bytes = _canonical(raw)
        self.manifest.write_bytes(old_bytes)
        with self.assertRaises(PairPreflightError):
            verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=hashlib.sha256(old_bytes).hexdigest(),
            )
        self.assertNotEqual(old_sha, hashlib.sha256(old_bytes).hexdigest())

    def test_offline_candidate_sqlite_must_use_exact_protocol_mode_0600(self) -> None:
        os.chmod(self.sqlite, 0o400)
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_offline_candidate_witness_must_use_exact_protocol_mode_0600(self) -> None:
        os.chmod(self.ledger, 0o700)
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_world_writable_nonsticky_staging_ancestor_is_rejected(self) -> None:
        ancestor = self.source.parent
        old_mode = ancestor.stat().st_mode & 0o7777
        try:
            os.chmod(ancestor, 0o777)
            with self.assertRaises(PairPreflightError):
                inspect_offline_pair(self.sqlite, self.ledger)
        finally:
            os.chmod(ancestor, old_mode)

    def test_uid_owned_sticky_writable_ancestor_is_still_rejected(self) -> None:
        # Root-owned /tmp 1777 is allowed, but caller-owned 1777 is not.
        ancestor = self.source.parent
        old_mode = ancestor.stat().st_mode & 0o7777
        try:
            os.chmod(ancestor, 0o1777)
            with self.assertRaises(PairPreflightError):
                inspect_offline_pair(self.sqlite, self.ledger)
        finally:
            os.chmod(ancestor, old_mode)

    def test_offline_private_parent_requires_protocol_mode_0700(self) -> None:
        try:
            os.chmod(self.source, 0o500)
            with self.assertRaises(PairPreflightError):
                inspect_offline_pair(self.sqlite, self.ledger)
        finally:
            os.chmod(self.source, 0o700)

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

    def test_witness_partial_start_index_uppercase_literal_does_not_attest(self) -> None:
        with sqlite3.connect(self.ledger) as connection:
            connection.execute("DROP INDEX one_start_per_key")
            connection.execute(
                "CREATE UNIQUE INDEX one_start_per_key ON events(request_key) "
                "WHERE kind='STARTED'"
            )
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_witness_partial_start_index_literal_with_space_does_not_attest(self) -> None:
        with sqlite3.connect(self.ledger) as connection:
            connection.execute("DROP INDEX one_start_per_key")
            connection.execute(
                "CREATE UNIQUE INDEX one_start_per_key ON events(request_key) "
                "WHERE kind='start ed'"
            )
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_witness_seq_integer_primarykey_token_is_not_rowid_primary_key(self) -> None:
        # Change the table SQL in the staged untrusted candidate, keeping the
        # same normalized characters: integer primarykey != integer primary key.
        with sqlite3.connect(self.ledger) as connection:
            connection.execute("PRAGMA writable_schema=ON")
            cursor = connection.execute(
                "UPDATE sqlite_master SET sql=replace("
                "sql,'seq INTEGER PRIMARY KEY','seq INTEGER PRIMARYKEY') "
                "WHERE name='events' AND type='table'"
            )
            self.assertEqual(cursor.rowcount, 1)
            connection.execute("PRAGMA schema_version=71")
            connection.execute("PRAGMA writable_schema=OFF")
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_witness_started_operation_without_originating_session_fails(self) -> None:
        # After the original owner sealed, a candidate image is corrupted by
        # removing only its originating sessions row. Events and chain remain
        # cryptographically self-consistent, but operator_clearance cannot run.
        self._start(key="orphan:start")
        with sqlite3.connect(self.ledger) as connection:
            originating = connection.execute(
                "SELECT session_id FROM events "
                "WHERE kind='started' AND request_key='orphan:start'"
            ).fetchone()
            self.assertIsNotNone(originating)
            connection.execute(
                "DELETE FROM sessions WHERE id=?", (originating[0],),
            )
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_witness_orphan_open_event_cannot_pass_empty_fence_inventory(self) -> None:
        with FenceLedger(self.ledger) as owner:
            session = owner.open_session()
            owner.seal_session(session)
        with sqlite3.connect(self.ledger) as connection:
            connection.execute("DELETE FROM sessions WHERE id=?", (session,))
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_witness_noop_delete_trigger_cannot_imitate_secure_schema(self) -> None:
        with sqlite3.connect(self.ledger) as connection:
            connection.execute("DROP TRIGGER events_no_delete")
            connection.execute(
                "CREATE TRIGGER events_no_delete BEFORE DELETE ON events "
                "BEGIN SELECT 1; END"
            )
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_witness_noop_update_trigger_cannot_imitate_secure_schema(self) -> None:
        with sqlite3.connect(self.ledger) as connection:
            connection.execute("DROP TRIGGER events_no_update")
            connection.execute(
                "CREATE TRIGGER events_no_update BEFORE UPDATE ON events "
                "BEGIN SELECT 1; END"
            )
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_witness_extra_veto_trigger_cannot_bypass_append_only_guards(self) -> None:
        with sqlite3.connect(self.ledger) as connection:
            connection.execute(
                "CREATE TRIGGER extra_veto BEFORE DELETE ON events "
                "BEGIN SELECT RAISE(IGNORE); END"
            )
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_unexpected_sqlite_store_insert_veto_trigger_is_rejected(self) -> None:
        with sqlite3.connect(self.sqlite) as connection:
            connection.execute(
                "CREATE TRIGGER deny_write BEFORE INSERT ON write_api_requests "
                "BEGIN SELECT RAISE(ABORT, 'recovery write sabotaged'); END"
            )
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_sqlite_store_weakened_recovery_clearance_constraint_is_rejected(self) -> None:
        # The old structure validator checked columns, not whether the
        # requirement for exact 64-character request SHA still exists.
        with sqlite3.connect(self.sqlite) as connection:
            connection.execute("PRAGMA writable_schema=ON")
            row = connection.execute(
                "UPDATE sqlite_master SET sql=replace(sql,"
                "'CHECK(length(request_sha256) = 64)',"
                "'CHECK(length(request_sha256) >= 0)') "
                "WHERE name='write_recovery_clearances' AND type='table'"
            )
            self.assertEqual(row.rowcount, 1)
            connection.execute("PRAGMA schema_version=92")
            connection.execute("PRAGMA writable_schema=OFF")
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_reserved_sqlite_prefix_trigger_in_store_is_rejected(self) -> None:
        with sqlite3.connect(self.sqlite) as connection:
            connection.execute("PRAGMA writable_schema=ON")
            connection.execute(
                "INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql) "
                "VALUES('trigger','sqlite_write_veto','write_api_requests',0,?)",
                (
                    "CREATE TRIGGER sqlite_write_veto BEFORE INSERT "
                    "ON write_api_requests BEGIN SELECT RAISE(IGNORE); END",
                ),
            )
            connection.execute("PRAGMA schema_version=93")
            connection.execute("PRAGMA writable_schema=OFF")
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_witness_uppercase_schema_type_cannot_hide_active_trigger(self) -> None:
        # SQLite can load a writable_schema-injected uppercase type whose SQL
        # still installs a real event veto. The verifier must not filter by
        # case-sensitive sqlite_master.type before inspecting every row.
        with sqlite3.connect(self.ledger) as connection:
            connection.execute("PRAGMA writable_schema=ON")
            connection.execute(
                "INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql) "
                "VALUES('TRIGGER','uppercase_veto','events',0,?)",
                (
                    "CREATE TRIGGER uppercase_veto BEFORE INSERT ON events "
                    "WHEN NEW.kind='started' BEGIN SELECT RAISE(IGNORE); END",
                ),
            )
            connection.execute("PRAGMA schema_version=115")
            connection.execute("PRAGMA writable_schema=OFF")
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_witness_forged_trigger_parent_table_cannot_be_ignored(self) -> None:
        with sqlite3.connect(self.ledger) as connection:
            connection.execute("PRAGMA writable_schema=ON")
            connection.execute(
                "INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql) "
                "VALUES('TRIGGER','cross_table_veto','other',0,?)",
                (
                    "CREATE TRIGGER cross_table_veto BEFORE INSERT ON events "
                    "BEGIN SELECT RAISE(IGNORE); END",
                ),
            )
            connection.execute("PRAGMA schema_version=116")
            connection.execute("PRAGMA writable_schema=OFF")
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_witness_reserved_sqlite_prefix_trigger_must_never_be_hidden(self) -> None:
        # A hostile writable_schema snapshot can contain a reserved sqlite_
        # name even though normal CREATE would reject that name. This trigger
        # can silently ignore future Witness started events after restore.
        with sqlite3.connect(self.ledger) as connection:
            connection.execute("PRAGMA writable_schema=ON")
            connection.execute(
                "INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql) "
                "VALUES('trigger','sqlite_veto','events',0,?)",
                (
                    "CREATE TRIGGER sqlite_veto BEFORE INSERT ON events "
                    "WHEN NEW.kind='started' BEGIN SELECT RAISE(IGNORE); END",
                ),
            )
            connection.execute("PRAGMA schema_version=81")
            connection.execute("PRAGMA writable_schema=OFF")
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_sqlite_wal_header_rejected_before_image_parser(self) -> None:
        with sqlite3.connect(self.sqlite) as connection:
            self.assertEqual(connection.execute(
                "PRAGMA journal_mode=WAL",
            ).fetchone()[0].lower(), "wal")
        with patch(
            "mark_api.paired_restore_preflight._readonly",
            side_effect=AssertionError("untrusted WAL source reached SQLite parser"),
        ):
            with self.assertRaises(PairPreflightError):
                inspect_offline_pair(self.sqlite, self.ledger)

    def test_sqlite_wal_header_without_sidecar_is_not_safe_restore_source(self) -> None:
        with sqlite3.connect(self.sqlite) as connection:
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            self.assertEqual(mode[0].lower(), "wal")
        self.assertEqual(self.sqlite.read_bytes()[18:20], bytes((2, 2)))
        self.assertFalse(self.sqlite.with_name(self.sqlite.name + "-wal").exists())
        self.assertFalse(self.sqlite.with_name(self.sqlite.name + "-shm").exists())
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_witness_sqlite_prefix_lookalike_trigger_is_not_skipped(self) -> None:
        # In SQL LIKE, '_' matches any single character. User objects named
        # sqliteXveto must not be confused with the reserved sqlite_ prefix.
        with sqlite3.connect(self.ledger) as connection:
            connection.execute(
                "CREATE TRIGGER sqliteXveto BEFORE DELETE ON events "
                "BEGIN SELECT RAISE(IGNORE); END"
            )
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_witness_nonunique_start_index_cannot_imitate_secure_schema(self) -> None:
        with sqlite3.connect(self.ledger) as connection:
            connection.execute("DROP INDEX one_start_per_key")
            connection.execute(
                "CREATE INDEX one_start_per_key ON events(request_key) "
                "WHERE kind='started'"
            )
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_witness_unconstrained_sessions_cannot_imitate_secure_schema(self) -> None:
        with sqlite3.connect(self.ledger) as connection:
            connection.execute("DROP TABLE sessions")
            connection.execute(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, state TEXT NOT NULL, "
                "opened_at TEXT NOT NULL, sealed_at TEXT)"
            )
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

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

    def test_bad_anchored_sqlite_digest_does_not_enter_sqlite_parser(self) -> None:
        pinned = self._pin()
        # Modify an otherwise valid source header after external attestation.
        # A parser trap proves that no SQLite connection may precede its digest.
        with sqlite3.connect(self.sqlite) as connection:
            connection.execute("PRAGMA application_id = 12345")
        from unittest.mock import patch
        with patch(
            "mark_api.paired_restore_preflight._readonly",
            side_effect=AssertionError("untrusted SQLite parser reached"),
        ):
            with self.assertRaises(PairPreflightError):
                verify_offline_pair(
                    self.sqlite, self.ledger, self.manifest,
                    expected_manifest_sha256=pinned,
                )

    def test_bad_anchored_witness_digest_does_not_enter_sqlite_parser(self) -> None:
        pinned = self._pin()
        with sqlite3.connect(self.ledger) as connection:
            connection.execute("PRAGMA application_id = 12345")
        from unittest.mock import patch
        with patch(
            "mark_api.paired_restore_preflight._readonly",
            side_effect=AssertionError("untrusted Witness parser reached"),
        ):
            with self.assertRaises(PairPreflightError):
                verify_offline_pair(
                    self.sqlite, self.ledger, self.manifest,
                    expected_manifest_sha256=pinned,
                )

    def test_readonly_sqlite_connections_disable_trusted_schema_before_reads(self) -> None:
        from mark_api.paired_restore_preflight import _readonly
        connection = _readonly(self.sqlite)
        try:
            result = connection.execute("PRAGMA trusted_schema").fetchone()
            self.assertIsNotNone(result)
            self.assertEqual(result[0], 0)
            self.assertEqual(
                connection.execute("PRAGMA query_only").fetchone()[0], 1,
            )
        finally:
            connection.close()

    def test_symlinked_directory_ancestor_must_be_rejected(self) -> None:
        alias = self.source.parent / "alias-ancestor"
        alias.symlink_to(self.source.parent, target_is_directory=True)
        # Immediate parent resolves as a private real directory, but a
        # grandparent is the attacker-controlled link.
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(alias / self.source.name / self.sqlite.name, self.ledger)

    def test_oversized_sparse_image_is_rejected_before_hashing(self) -> None:
        from mark_api.paired_restore_preflight import _MAX_IMAGE_BYTES, _sha256_fd
        oversized = self.source / "oversized.sqlite"
        with oversized.open("wb") as target:
            target.truncate(_MAX_IMAGE_BYTES + 1)
        with oversized.open("rb") as source:
            with self.assertRaisesRegex(PairPreflightError, "bounded"):
                _sha256_fd(source.fileno())

    def test_witness_wal_header_not_accepted_after_deserialization(self) -> None:
        with sqlite3.connect(self.ledger) as source:
            mode = source.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            self.assertEqual(str(mode).lower(), "wal")
        with self.ledger.open("rb") as raw:
            raw.seek(18)
            self.assertEqual(raw.read(2), bytes((2, 2)))
        with self.assertRaises(PairPreflightError):
            inspect_offline_pair(self.sqlite, self.ledger)

    def test_parser_must_consume_pinned_fd_bytes_during_directory_swap(self) -> None:
        pinned = self._pin()
        attacker = self.source.parent / "attacker-copy"
        attacker.mkdir(mode=0o700)
        other = SnapshotStore(attacker / self.sqlite.name)
        other.claim_write_api_request(
            idempotency_key="attacker-unmatched-start", request_sha256=SHA,
            requested_at=AT, claim_owner="attacker",
        )
        other.begin_write_api_request(
            idempotency_key="attacker-unmatched-start", request_sha256=SHA,
            claim_owner="attacker", execution_started_at=AT,
        )
        parked = self.source.parent / "temporarily-parked-origin"
        from mark_api import paired_restore_preflight as pair
        from unittest.mock import patch
        original = pair._readonly
        intervened = False

        def swap_exactly_before_parse(path, *args, **kwargs):
            nonlocal intervened
            if path == self.sqlite and not intervened:
                intervened = True
                self.source.rename(parked)
                attacker.rename(self.source)
                try:
                    return original(path, *args, **kwargs)
                finally:
                    self.source.rename(attacker)
                    parked.rename(self.source)
            return original(path, *args, **kwargs)

        with patch.object(pair, "_readonly", side_effect=swap_exactly_before_parse):
            verified = verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=pinned,
            )
        self.assertTrue(intervened)
        self.assertEqual(verified["sqlite_started_count"], 0)
        self.assertEqual(verified["witness_unresolved_count"], 0)

    def test_installed_parser_never_reopens_a_filepath_after_digest(self) -> None:
        pinned = self._pin()
        from unittest.mock import patch
        original_connect = sqlite3.connect

        def reject_named_db_reopen(database, *args, **kwargs):
            if str(database).startswith("file:"):
                raise AssertionError("SQLite reopened mutable source path")
            return original_connect(database, *args, **kwargs)

        with patch(
            "mark_api.paired_restore_preflight.sqlite3.connect",
            side_effect=reject_named_db_reopen,
        ):
            report = verify_offline_pair(
                self.sqlite, self.ledger, self.manifest,
                expected_manifest_sha256=pinned,
            )
        self.assertEqual(report["manifest_sha256"], pinned)

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