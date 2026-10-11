"""Synthetic protocol tests for an unfinished independent storage-owner slice.

These same-UID tests exercise the protocol and state transitions only.
The real OS trust boundary requires test_storage_owner_multiuid.py.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import os
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mark_api.storage import SnapshotStore
from mark_api.storage_owner import (
    StorageOwnerClient, StorageOwnerError, StorageOwnerServer, main,
    _attested_database, _record_from_json, _VERSION,
)


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 64


class StorageOwnerUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="mark-storage-owner-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.db_directory = base / "database"
        self.db_directory.mkdir(mode=0o700)
        self.database = self.db_directory / "mark.sqlite"
        SnapshotStore(self.database)
        self.rpc_dir = base / "rpc"
        self.rpc_dir.mkdir(mode=0o700)
        self.rpc_dir.chmod(0o2710)
        self.sock = self.rpc_dir / "storage.sock"

    def make_service(self) -> StorageOwnerServer:
        instance = StorageOwnerServer(
            database=self.database, socket_path=self.sock,
            runtime_uid=os.geteuid() + 10001,
            runtime_gid=os.getegid(),
        )
        self.addCleanup(instance.stop)
        return instance

    def test_existing_schema_is_mandatory_and_no_missing_file_is_created(self) -> None:
        self.database.unlink()
        with self.assertRaises(StorageOwnerError):
            self.make_service()
        self.assertFalse(self.database.exists())

    def test_owner_start_does_not_silently_add_missing_index(self) -> None:
        # A full recovery schema can be intact while an optional index is
        # absent. A trusted storage owner must not perform *any* startup DDL.
        with sqlite3.connect(self.database) as conn:
            conn.execute("DROP INDEX idx_ad_snapshots_ad_id_observed")
        service = self.make_service()
        self.assertTrue(service.dispatch({"version": 1, "op": "ready"})["ready"])
        with sqlite3.connect(self.database) as conn:
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_ad_snapshots_ad_id_observed'"
            ).fetchall()
        self.assertEqual(indexes, [])

    def test_missing_recovery_table_cannot_be_auto_repaired(self) -> None:
        with sqlite3.connect(self.database) as conn:
            conn.execute("DROP TABLE write_recovery_clearances")
        with self.assertRaises(sqlite3.DatabaseError):
            self.make_service()
        with sqlite3.connect(self.database) as conn:
            self.assertEqual(
                conn.execute("SELECT name FROM sqlite_master "
                             "WHERE name='write_recovery_clearances'").fetchall(),
                [],
            )

    def test_wrong_source_uid_or_mode_refused_before_socket(self) -> None:
        self.database.chmod(0o644)
        with self.assertRaises(StorageOwnerError):
            self.make_service()
        self.assertFalse(self.sock.exists())

    def test_non_private_or_symlink_sidecar_is_rejected(self) -> None:
        sidecar = Path(str(self.database) + "-wal")
        sidecar.symlink_to(self.database)
        with self.assertRaises(StorageOwnerError):
            self.make_service()
        sidecar.unlink()
        sidecar.write_bytes(b"fake WAL")
        sidecar.chmod(0o644)
        with self.assertRaises(StorageOwnerError):
            self.make_service()
        self.assertFalse(self.sock.exists())

    def test_root_uid_runtime_and_equal_uid_cannot_create_broker(self) -> None:
        for peer_uid in (0, os.geteuid()):
            with self.subTest(peer_uid=peer_uid), self.assertRaises(StorageOwnerError):
                StorageOwnerServer(
                    database=self.database, socket_path=self.sock,
                    runtime_uid=peer_uid, runtime_gid=os.getegid(),
                )

    def test_stale_socket_and_symlink_are_not_removed(self) -> None:
        with closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as old:
            old.bind(str(self.sock))
        with self.assertRaisesRegex(StorageOwnerError, "refusing to replace"):
            self.make_service()
        self.assertTrue(self.sock.is_socket())
        self.sock.unlink()
        self.sock.symlink_to("/tmp/fake-storage.sock")
        with self.assertRaisesRegex(StorageOwnerError, "refusing to replace"):
            self.make_service()
        self.assertTrue(self.sock.is_symlink())

    def test_exact_write_claim_and_start_persist_without_clearance(self) -> None:
        service = self.make_service()
        def call(op: str, **fields: object) -> dict[str, object]:
            return service.dispatch({"version": _VERSION, "op": op, **fields})

        created = call("claim", key="ui:claim:1", sha=SHA,
                       owner="runtime:one", at=NOW.isoformat())
        self.assertTrue(created["created"])
        self.assertIsNone(created["record"]["execution_started_at"])
        self.assertEqual(
            call("claim", key="ui:claim:1", sha=SHA,
                 owner="runtime:one", at=NOW.isoformat())["created"], False,
        )
        self.assertEqual(call("create_pending", exclude_key="ui:next", owner="runtime:one"),
                         {"pending": False})
        begun = call("begin", key="ui:claim:1", sha=SHA,
                     owner="runtime:one", at=NOW.isoformat())
        self.assertEqual(begun["record"]["state"], "in_progress")
        self.assertEqual(begun["record"]["execution_started_at"], NOW.isoformat())
        self.assertEqual(call("create_pending", exclude_key="ui:next", owner="runtime:one"),
                         {"pending": True})
        self.assertEqual(call("read_request", key="ui:claim:1")["record"],
                         begun["record"])
        with self.assertRaises(ValueError):
            call("begin", key="ui:claim:1", sha=SHA,
                 owner="runtime:one", at=NOW.isoformat())
        self.assertFalse(SnapshotStore(self.database, create_if_missing=False)
                         .has_write_recovery_clearance(
                             idempotency_key="ui:claim:1", request_sha256=SHA,
                         ))
        self.assertEqual(
            SnapshotStore(self.database, create_if_missing=False)
            .write_api_request("ui:claim:1").state, "in_progress",
        )

    def test_closed_rpc_table_denies_sql_clearances_completion_and_paths(self) -> None:
        service = self.make_service()
        calls = [
            {"version": 1, "op": "execute", "sql": "DELETE FROM write_api_requests"},
            {"version": 1, "op": "record_write_recovery_clearance",
             "key": "ui:claim:1", "sha": SHA},
            {"version": 1, "op": "complete", "key": "ui:claim:1", "response": {}},
            {"version": 1, "op": "clearance", "key": "ui:claim:1"},
            {"version": 1, "op": "claim", "key": "ui:claim:1", "sha": SHA,
             "owner": "runtime:one", "at": NOW.isoformat(),
             "allow_abandoned_takeover": True},
            {"version": 1, "op": "read_request", "key": "ui:claim:1",
             "filename": str(self.database)},
            {"version": 1, "op": "claim", "key": "ui:claim:1", "sha": SHA,
             "owner": "runtime:one", "at": NOW.isoformat(), "sql": "VACUUM"},
            {"version": 1, "op": "ready", "other": True},
        ]
        with patch.object(service.store, "record_write_recovery_clearance",
                          side_effect=AssertionError("unsafe clearance reached")), \
             patch.object(service.store, "complete_write_api_request",
                          side_effect=AssertionError("unsafe completion reached")):
            for request in calls:
                with self.subTest(request=request), self.assertRaises(StorageOwnerError):
                    service.dispatch(request)
        self.assertIsNone(SnapshotStore(self.database, create_if_missing=False)
                          .write_api_request("ui:claim:1"))

    def test_concurrent_claim_and_begin_cannot_erase_or_double_start(self) -> None:
        service = self.make_service()
        claim = {
            "version": 1, "op": "claim", "key": "ui:parallel:1", "sha": SHA,
            "owner": "runtime:one", "at": NOW.isoformat(),
        }
        with ThreadPoolExecutor(max_workers=8) as executor:
            receipts = list(executor.map(service.dispatch, [claim] * 8))
        self.assertEqual(sum(bool(row["created"]) for row in receipts), 1)
        start = dict(claim, op="begin")
        def start_once(_: int) -> bool:
            try:
                service.dispatch(start)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=8) as executor:
            started = list(executor.map(start_once, range(8)))
        self.assertEqual(sum(started), 1)
        self.assertTrue(
            service.dispatch({"version": 1, "op": "create_pending",
                              "exclude_key": "ui:other", "owner": "runtime:one"})[
                                  "pending"
            ]
        )
        self.assertIsNone(
            SnapshotStore(self.database, create_if_missing=False)
            .write_api_request("ui:parallel:1").completed_at
        )

    def test_same_uid_test_bypass_only_models_framing_not_uid_isolation(self) -> None:
        service = self.make_service()
        service.runtime_uid = os.geteuid()  # Unit-only seam; real service never allows it.
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        self.assertTrue(client.ready())
        self.assertIsNone(client.read_request("ui:frame:1"))
        created = client.claim(key="ui:frame:1", sha=SHA,
                               owner="runner:one", at=NOW)
        self.assertTrue(created.created)
        self.assertEqual(created.record.idempotency_key, "ui:frame:1")
        begun = client.begin(key="ui:frame:1", sha=SHA,
                             owner="runner:one", at=NOW)
        self.assertEqual(client.read_request("ui:frame:1"), begun)
        self.assertTrue(client.create_pending(exclude_key="ui:other", owner="runner:one"))
        with self.assertRaises(StorageOwnerError):
            client._call("complete", key="ui:frame:1")
        with self.assertRaises(StorageOwnerError):
            client.begin(key="ui:frame:1", sha=SHA, owner="runner:one", at=NOW)

    def test_client_rejects_colliding_fingerprint_and_claim_owner(self) -> None:
        service = self.make_service()
        service.runtime_uid = os.geteuid()  # Only same-UID framing test.
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        claimed = client.claim(key="ui:collision", sha=SHA,
                               owner="runtime:one", at=NOW)
        self.assertTrue(claimed.created)
        with self.assertRaisesRegex(StorageOwnerError, "binding mismatch"):
            client.claim(key="ui:collision", sha="b" * 64,
                         owner="runtime:one", at=NOW)
        with self.assertRaisesRegex(StorageOwnerError, "binding mismatch"):
            client.claim(key="ui:collision", sha=SHA,
                         owner="runtime:other", at=NOW)
        with self.assertRaisesRegex(StorageOwnerError, "uncertain"):
            client.begin(key="ui:collision", sha=SHA,
                         owner="runtime:other", at=NOW)
        self.assertIsNone(client.read_request("ui:collision").execution_started_at)
        self.assertFalse(client.create_pending(
            exclude_key="ui:collision-next", owner="runtime:one",
        ))

    def test_client_rejects_mismatched_readback_key_even_from_owner_uid(self) -> None:
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        source = self.make_service()
        record = source.dispatch({
            "version": 1, "op": "claim", "key": "ui:elsewhere",
            "sha": SHA, "owner": "runtime:one", "at": NOW.isoformat(),
        })["record"]
        with patch.object(client, "_call", return_value={"record": record}):
            with self.assertRaisesRegex(StorageOwnerError, "binding mismatch"):
                client.read_request("ui:expected")

    def test_foreign_uid_rejected_before_any_rpc_mutation(self) -> None:
        service = self.make_service()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        with self.assertRaises(StorageOwnerError):
            client.claim(key="ui:forbidden", sha=SHA,
                         owner="runner:one", at=NOW)
        self.assertIsNone(SnapshotStore(self.database, create_if_missing=False)
                          .write_api_request("ui:forbidden"))

    def test_lost_or_untrusted_reply_never_triggers_hidden_retry(self) -> None:
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        with self.assertRaisesRegex(StorageOwnerError, "uncertain"):
            client.claim(key="ui:unavailable", sha=SHA,
                         owner="runner:one", at=NOW)
        self.assertIsNone(SnapshotStore(self.database, create_if_missing=False)
                          .write_api_request("ui:unavailable"))

    def test_wrong_response_type_rejected_and_no_auto_retry(self) -> None:
        with self.assertRaises(StorageOwnerError):
            _record_from_json({"state": "completed"})

    def test_literal_sqlite_filename_with_question_and_hash_is_not_uri_query(self) -> None:
        unusual = self.db_directory / "mark?#literal.sqlite"
        SnapshotStore(unusual)
        service = StorageOwnerServer(
            database=unusual, socket_path=self.sock,
            runtime_uid=os.geteuid() + 10001, runtime_gid=os.getegid(),
        )
        self.addCleanup(service.stop)
        self.assertTrue(service.dispatch({"version": 1, "op": "ready"})["ready"])
        self.assertTrue(unusual.exists())

    def test_existing_private_inode_not_replaced(self) -> None:
        identity = _attested_database(self.database, os.geteuid())
        self.assertEqual(identity[:2], (self.database.stat().st_dev,
                                        self.database.stat().st_ino))
        service = self.make_service()
        replacement = self.db_directory / "replacement.sqlite"
        self.database.rename(replacement)
        SnapshotStore(self.database)
        with self.assertRaisesRegex(StorageOwnerError, "replaced"):
            service.dispatch({"version": 1, "op": "ready"})

    def test_container_draft_denies_direct_python_main_before_argparse(self) -> None:
        with patch("mark_api.storage_owner.container_draft_denied", return_value=True):
            self.assertEqual(main(["--db", str(self.database)]), 2)


if __name__ == "__main__":
    unittest.main()