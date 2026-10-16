"""Synthetic tests only; they do not demonstrate host UID/mmap separation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mark_api.backup_intent_authority import (
    BackupIntentAuthority, BackupIntentError, _locked,
)
from mark_api.fence_witness import FenceLedger
from mark_api.paired_backup_operator import PairedBackupOperatorError, seal_offline_pair
from mark_api.storage import SnapshotStore


FIRST = "1" * 32
SECOND = "2" * 32


class BackupIntentAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="mark-independent-intent-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.source = base / "trusted-cold-source"
        self.output = base / "trusted-generations"
        self.anchor = base / "trusted-head-anchors"
        self.intents = base / "trusted-one-time-intents"
        for directory in (self.source, self.output, self.anchor, self.intents):
            directory.mkdir(mode=0o700)
        self.sqlite = self.source / "mark.sqlite"
        self.witness = self.source / "witness.sqlite"
        SnapshotStore(self.sqlite)
        with FenceLedger(self.witness, initialize=True):
            pass
        self.operator = BackupIntentAuthority(self.intents)

    def _hash(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _prepare(self, generation: str = FIRST, previous: str | None = None) -> dict[str, str]:
        return self.operator.prepare(
            generation, self.sqlite, self.witness,
            previous_head_sha256=previous,
        )

    def _seal(self, generation: str = FIRST, previous: str | None = None) -> dict[str, object]:
        self._prepare(generation, previous)
        return self.operator.seal_prepared(generation, self.output, self.anchor)

    def test_trusted_operator_prepares_and_acknowledges_exactly_once(self) -> None:
        prepared = self._prepare()
        self.assertEqual(prepared["sqlite_sha256"], self._hash(self.sqlite))
        self.assertEqual(prepared["witness_sha256"], self._hash(self.witness))
        self.assertEqual(prepared["previous_head_sha256"], "0" * 64)
        self.assertFalse((self.intents / (FIRST + ".committing.json")).exists())
        result = self.operator.seal_prepared(FIRST, self.output, self.anchor)
        self.assertTrue(result["external_operator_intent_acknowledged"])
        self.assertFalse(result["restore_authorized"])
        self.assertFalse((self.anchor / ".inflight").exists())
        self.assertEqual(result["head_sha256"], self._hash(self.anchor / "latest.json"))
        for phase in ("prepared", "committing", "acknowledged"):
            path = self.intents / f"{FIRST}.{phase}.json"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(BackupIntentError):
            self.operator.acknowledge(FIRST, self.output, self.anchor)
        with self.assertRaises(BackupIntentError):
            self.operator.begin_commit(FIRST)
        with self.assertRaises(BackupIntentError):
            self._prepare()

    def test_replayed_original_attestation_cannot_consume_forged_marker(self) -> None:
        first = self._seal()
        marker = self.anchor / ".inflight"
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, FIRST.encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        # The same generation, actual original input SHAs and original
        # predecessor are present. External durable acknowledged state wins.
        with self.assertRaisesRegex(BackupIntentError, "no unconsumed"):
            self.operator.acknowledge(FIRST, self.output, self.anchor)
        self.assertEqual(marker.read_bytes(), FIRST.encode("ascii"))
        self.assertEqual(first["head_sha256"], self._hash(self.anchor / "latest.json"))

    def test_second_generation_must_extend_independently_acknowledged_tip(self) -> None:
        first = self._seal()
        with self.assertRaisesRegex(BackupIntentError, "predecessor"):
            self._prepare(SECOND)
        prepared = self._prepare(SECOND, first["head_sha256"])
        self.assertEqual(prepared["previous_head_sha256"], first["head_sha256"])
        second = self.operator.seal_prepared(SECOND, self.output, self.anchor)
        self.assertNotEqual(first["head_sha256"], second["head_sha256"])
        self.assertEqual(
            json.loads((self.anchor / "latest.json").read_bytes())["predecessor_sha256"],
            first["head_sha256"],
        )
        with self.assertRaises(BackupIntentError):
            self.operator.acknowledge(FIRST, self.output, self.anchor)

    def test_prepared_or_committing_generation_blocks_other_preparations(self) -> None:
        self._prepare()
        with self.assertRaisesRegex(BackupIntentError, "pending"):
            self._prepare(SECOND)
        self.operator.begin_commit(FIRST)
        with self.assertRaisesRegex(BackupIntentError, "never retry"):
            self.operator.begin_commit(FIRST)
        with self.assertRaisesRegex(BackupIntentError, "pending"):
            self._prepare(SECOND)
        self.assertFalse((self.intents / (FIRST + ".acknowledged.json")).exists())

    def test_crash_before_head_keeps_external_intent_and_inflight(self) -> None:
        self._prepare()
        def crash_at_publish(src: Path, dst: Path) -> None:
            raise OSError("synthetic before HEAD publication")
        with patch("mark_api.paired_backup_operator.os.replace", side_effect=crash_at_publish):
            with self.assertRaises(PairedBackupOperatorError):
                self.operator.seal_prepared(FIRST, self.output, self.anchor)
        self.assertFalse((self.anchor / "latest.json").exists())
        self.assertTrue((self.anchor / ".inflight").exists())
        self.assertTrue((self.intents / (FIRST + ".committing.json")).exists())
        self.assertFalse((self.intents / (FIRST + ".acknowledged.json")).exists())
        with self.assertRaises(BackupIntentError):
            self.operator.acknowledge(FIRST, self.output, self.anchor)
        self.assertTrue((self.anchor / ".inflight").exists())

    def test_crash_after_head_with_inflight_reconciles_only_open_intent(self) -> None:
        self._prepare()
        real_unlink = os.unlink
        def crash_on_fence_release(path: Path, *args: object, **kwargs: object) -> None:
            if Path(path) == self.anchor / ".inflight":
                raise OSError("synthetic crash after HEAD durability")
            real_unlink(path, *args, **kwargs)
        with patch("mark_api.paired_backup_operator.os.unlink", side_effect=crash_on_fence_release):
            with self.assertRaises(PairedBackupOperatorError):
                self.operator.seal_prepared(FIRST, self.output, self.anchor)
        self.assertTrue((self.anchor / "latest.json").exists())
        self.assertTrue((self.anchor / ".inflight").exists())
        self.assertFalse((self.intents / (FIRST + ".acknowledged.json")).exists())
        recovered = self.operator.acknowledge(FIRST, self.output, self.anchor)
        self.assertTrue(recovered["reconciled"])
        self.assertFalse((self.anchor / ".inflight").exists())
        with self.assertRaisesRegex(BackupIntentError, "no unconsumed"):
            self.operator.acknowledge(FIRST, self.output, self.anchor)

    def test_crash_after_sealer_success_before_external_ack_is_recoverable(self) -> None:
        self._prepare()
        with patch.object(
            BackupIntentAuthority, "acknowledge",
            side_effect=BackupIntentError("simulated lost external ack"),
        ):
            with self.assertRaises(BackupIntentError):
                self.operator.seal_prepared(FIRST, self.output, self.anchor)
        self.assertTrue((self.anchor / "latest.json").exists())
        self.assertFalse((self.anchor / ".inflight").exists())
        self.assertFalse((self.intents / (FIRST + ".acknowledged.json")).exists())
        result = self.operator.acknowledge(FIRST, self.output, self.anchor)
        self.assertFalse(result["reconciled"])
        self.assertTrue(result["acknowledged_once"])

    def test_source_image_tamper_after_preparation_blocks_seal(self) -> None:
        self._prepare()
        raw = bytearray(self.sqlite.read_bytes())
        raw[-1] ^= 1
        self.sqlite.write_bytes(raw)
        with self.assertRaises(PairedBackupOperatorError):
            self.operator.seal_prepared(FIRST, self.output, self.anchor)
        self.assertTrue((self.intents / (FIRST + ".committing.json")).exists())
        self.assertFalse((self.intents / (FIRST + ".acknowledged.json")).exists())

    def test_published_image_tamper_does_not_consume_external_intent(self) -> None:
        prepared = self._prepare()
        self.operator.begin_commit(FIRST)
        seal_offline_pair(
            self.sqlite, self.witness, self.output, self.anchor,
            expected_sqlite_sha256=prepared["sqlite_sha256"],
            expected_witness_sha256=prepared["witness_sha256"],
            expected_previous_head_sha256=None,
            generation=FIRST,
        )
        target = self.output / FIRST / "mark.sqlite"
        data = bytearray(target.read_bytes())
        data[-1] ^= 1
        target.write_bytes(data)
        with self.assertRaises(BackupIntentError):
            self.operator.acknowledge(FIRST, self.output, self.anchor)
        self.assertFalse((self.intents / (FIRST + ".acknowledged.json")).exists())
        self.assertTrue((self.intents / (FIRST + ".committing.json")).exists())

    def test_invalid_external_history_and_unknown_artifacts_fail_closed(self) -> None:
        self._prepare()
        intent = self.intents / f"{FIRST}.prepared.json"
        altered = json.loads(intent.read_bytes())
        altered["sqlite_sha256"] = "b" * 64
        intent.write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaises(BackupIntentError):
            self.operator.begin_commit(FIRST)
        # Nothing is deleted or self-repaired.
        self.assertTrue(intent.exists())
        self.assertFalse((self.intents / (FIRST + ".committing.json")).exists())

    def test_unrecognized_operator_artifact_blocks_prepare(self) -> None:
        (self.intents / "unrelated").write_text("bogus", encoding="utf-8")
        with self.assertRaises(BackupIntentError):
            self._prepare()
        self.assertFalse((self.intents / (FIRST + ".prepared.json")).exists())

    def test_external_lock_blocks_competing_prepare(self) -> None:
        with _locked(self.intents):
            with self.assertRaisesRegex(BackupIntentError, "another independent"):
                self._prepare()
        self.assertFalse((self.intents / (FIRST + ".prepared.json")).exists())

    def test_missing_lock_is_not_recreated_under_old_flock_holder(self) -> None:
        lock = self.intents / ".intent.lock"
        second_entered = False
        try:
            with _locked(self.intents):
                lock.unlink()
                try:
                    with _locked(self.intents):
                        second_entered = True
                except BackupIntentError:
                    pass
        except BackupIntentError:
            # The outer pin must also fail closed after its inode was removed.
            pass
        self.assertFalse(second_entered, "new lock inode enabled a concurrent sealer")
        self.assertFalse(lock.exists(), "normal operations must never replace lost lock")

    def test_reinitializing_nonempty_history_must_not_restore_missing_lock(self) -> None:
        self._prepare()
        lock = self.intents / ".intent.lock"
        lock.unlink()
        with self.assertRaises(BackupIntentError):
            BackupIntentAuthority(self.intents).begin_commit(FIRST)
        self.assertFalse(lock.exists())
        self.assertFalse((self.intents / f"{FIRST}.committing.json").exists())

    def test_corrupted_acknowledged_ancestor_blocks_later_seal(self) -> None:
        first = self._seal()
        self._prepare(SECOND, first["head_sha256"])
        target = self.output / FIRST / "mark.sqlite"
        damaged = bytearray(target.read_bytes())
        damaged[-1] ^= 1
        target.write_bytes(damaged)
        with self.assertRaises(BackupIntentError):
            self.operator.seal_prepared(SECOND, self.output, self.anchor)
        self.assertEqual(self._hash(self.anchor / "latest.json"), first["head_sha256"])
        self.assertFalse((self.output / SECOND).exists())
        self.assertTrue((self.intents / f"{SECOND}.committing.json").is_file())
        self.assertFalse((self.intents / f"{SECOND}.acknowledged.json").exists())

    def test_late_corrupted_ancestor_blocks_external_acknowledgement(self) -> None:
        first = self._seal()
        self._prepare(SECOND, first["head_sha256"])
        self.operator.begin_commit(SECOND)
        prepared = json.loads((self.intents / f"{SECOND}.prepared.json").read_bytes())
        seal_offline_pair(
            self.sqlite, self.witness, self.output, self.anchor,
            expected_sqlite_sha256=prepared["sqlite_sha256"],
            expected_witness_sha256=prepared["witness_sha256"],
            expected_previous_head_sha256=first["head_sha256"],
            generation=SECOND,
        )
        ancestor = self.output / FIRST / "mark.sqlite"
        damaged = bytearray(ancestor.read_bytes())
        damaged[-1] ^= 1
        ancestor.write_bytes(damaged)
        with self.assertRaises(BackupIntentError):
            self.operator.acknowledge(SECOND, self.output, self.anchor)
        self.assertTrue((self.intents / f"{SECOND}.committing.json").exists())
        self.assertFalse((self.intents / f"{SECOND}.acknowledged.json").exists())

    def test_lock_replacement_with_same_bytes_is_not_trusted(self) -> None:
        lock = self.intents / ".intent.lock"
        replacement = self.intents / "replacement-lock"
        replacement.write_bytes(b"")
        replacement.chmod(0o600)
        os.replace(replacement, lock)
        with self.assertRaisesRegex(BackupIntentError, "lock inode"):
            self._prepare()
        self.assertFalse((self.intents / f"{FIRST}.prepared.json").exists())

    def test_operator_restart_reuses_original_pinned_lock(self) -> None:
        lock = self.intents / ".intent.lock"
        original_inode = lock.stat().st_ino
        restarted = BackupIntentAuthority(self.intents)
        result = restarted.prepare(FIRST, self.sqlite, self.witness, previous_head_sha256=None)
        self.assertEqual(result["generation"], FIRST)
        self.assertEqual(lock.stat().st_ino, original_inode)

    def test_private_operator_state_is_required(self) -> None:
        self.intents.chmod(0o755)
        with self.assertRaises((BackupIntentError, PairedBackupOperatorError)):
            BackupIntentAuthority(self.intents)


if __name__ == "__main__":
    unittest.main()
