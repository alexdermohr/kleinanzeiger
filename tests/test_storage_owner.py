"""Synthetic protocol tests for an unfinished independent storage-owner slice.

These same-UID tests exercise the protocol and state transitions only.
The real OS trust boundary requires test_storage_owner_multiuid.py.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import socket
import sqlite3
import struct
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch

from mark_api.storage import SnapshotStore
from mark_api import storage_owner as owner_module
from mark_api.results import ReadResult
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

    def test_stop_does_not_release_sqlite_owner_while_rpc_is_in_flight(self) -> None:
        """A stopped authority must never publish a new generation early.

        A real accepted RPC is held just before persistent BEGIN. Its Unix
        connection must finish and the SQLite request must be committed
        before stop() can return or remove the socket name.
        """
        service = self.make_service()
        service.runtime_uid = os.geteuid()  # Framing-only in-process seam.
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        client.claim(key="ui:shutdown:1", sha=SHA, owner="runtime:one", at=NOW)
        entered, release, stop_done = Event(), Event(), Event()
        writes: list[object] = []
        exceptions: list[BaseException] = []
        begin = service.store.begin_write_api_request

        def blocked_begin(**kw: object) -> object:
            entered.set()
            if not release.wait(timeout=6):
                raise AssertionError("test blocked storage request was never released")
            return begin(**kw)

        def writer() -> None:
            try:
                writes.append(client.begin(
                    key="ui:shutdown:1", sha=SHA, owner="runtime:one", at=NOW,
                ))
            except BaseException as exc:
                exceptions.append(exc)

        def shutdown() -> None:
            try:
                service.stop()
            except BaseException as exc:
                exceptions.append(exc)
            finally:
                stop_done.set()

        with patch.object(
            service.store, "begin_write_api_request", side_effect=blocked_begin,
        ):
            request = Thread(target=writer, daemon=True)
            request.start()
            self.assertTrue(entered.wait(timeout=3))
            closing_thread = Thread(target=shutdown, daemon=True)
            closing_thread.start()
            try:
                self.assertFalse(
                    stop_done.wait(timeout=0.3),
                    "storage owner stopped before an outstanding RPC completed",
                )
            finally:
                release.set()
                request.join(timeout=7)
                closing_thread.join(timeout=7)
        self.assertFalse(request.is_alive())
        self.assertFalse(closing_thread.is_alive())
        self.assertFalse(exceptions, exceptions)
        self.assertTrue(stop_done.is_set())
        self.assertEqual(len(writes), 1)
        self.assertFalse(self.sock.exists())
        record = SnapshotStore(self.database, create_if_missing=False).write_api_request(
            "ui:shutdown:1",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.state, "in_progress")
        self.assertIsNotNone(record.execution_started_at)

    def test_out_of_order_sync_completions_are_not_spoofed_or_rejected(self) -> None:
        """Latest started and latest completed-success are distinct journal facts."""
        service = self.make_service()
        store = SnapshotStore(self.database, create_if_missing=False)
        t0, t1, t2, t3 = (
            NOW, NOW + timedelta(minutes=1),
            NOW + timedelta(minutes=2), NOW + timedelta(minutes=3),
        )
        older = store.begin_sync_attempt(source="overlap", started_at=t0)
        newer = store.begin_sync_attempt(source="overlap", started_at=t1)
        store.append_inventory_result(
            ReadResult.success_empty(()), source="overlap", observed_at=t2,
            attempt_id=newer, completed_at=t2,
        )
        store.append_inventory_result(
            ReadResult.success_empty(()), source="overlap", observed_at=t3,
            attempt_id=older, completed_at=t3,
        )
        actual = store.sync_status()
        self.assertEqual(actual["latest_attempt"]["id"], newer)
        self.assertEqual(actual["last_successful_attempt"]["id"], older)
        service.runtime_uid = os.geteuid()  # Framing only.
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        self.assertEqual(client.sync_status(), actual)
        # An older success cannot win the store's completion-time ordering
        # unless it was actually completed strictly later than the new one.
        forged = dict(actual)
        forged["last_successful_attempt"] = dict(
            actual["last_successful_attempt"], completed_at=t2.isoformat(),
        )
        with patch.object(client, "_call", return_value={"sync_status": forged}):
            with self.assertRaises(StorageOwnerError):
                client.sync_status()

    def test_linux_ucred_uid_is_unsigned_32_bit(self) -> None:
        """Even though deployed UIDs are 50044/50046, Linux uid_t is unsigned."""
        for uid in (50044, 50046, 2**31, 2**32 - 2):
            with self.subTest(uid=uid):
                raw = struct.pack("=iII", os.getpid(), uid, 50044)
                self.assertEqual(owner_module._linux_ucred_uid(raw), uid)
        for raw in (b"", bytes(11), bytes(13)):
            with self.subTest(length=len(raw)), self.assertRaises(StorageOwnerError):
                owner_module._linux_ucred_uid(raw)

    def test_shutdown_does_not_unlink_after_four_second_inflight_rpc(self) -> None:
        """The 4s serve_forever join is not a 4s handler drain deadline."""
        service = self.make_service()
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        client.claim(key="ui:slow:1", sha=SHA, owner="runtime:one", at=NOW)
        entered, release, done = Event(), Event(), Event()
        errors: list[BaseException] = []
        orig = service.store.begin_write_api_request
        def delayed(**kw: object) -> object:
            entered.set()
            if not release.wait(timeout=7):
                raise AssertionError("slow RPC was not released")
            return orig(**kw)
        def request() -> None:
            try:
                client.begin(key="ui:slow:1", sha=SHA, owner="runtime:one", at=NOW)
            except BaseException as exc:
                errors.append(exc)
        def close() -> None:
            try:
                service.stop()
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()
        with patch.object(service.store, "begin_write_api_request", side_effect=delayed):
            writer = Thread(target=request, daemon=True)
            writer.start()
            self.assertTrue(entered.wait(3))
            stopper = Thread(target=close, daemon=True)
            stopper.start()
            try:
                self.assertFalse(done.wait(timeout=4.3),
                                 "socket unlinked before in-flight RPC ended")
                self.assertTrue(self.sock.exists())
            finally:
                release.set()
                writer.join(9)
                stopper.join(9)
        self.assertFalse(writer.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertFalse(errors, errors)
        self.assertTrue(done.is_set())
        self.assertFalse(self.sock.exists())
        persisted = SnapshotStore(self.database, create_if_missing=False).write_api_request(
            "ui:slow:1",
        )
        self.assertIsNotNone(persisted.execution_started_at)

    def test_history_readback_is_bounded_and_orders_real_instants(self) -> None:
        from mark_api.domain import AdSnapshot, LifecycleState
        service = self.make_service()
        store = SnapshotStore(self.database, create_if_missing=False)
        timeline = (
            NOW + timedelta(hours=1),
            NOW + timedelta(minutes=2),
            NOW + timedelta(hours=1),
            NOW + timedelta(minutes=3),
        )
        entries = tuple(AdSnapshot(
            ad_id="123", observed_at=when,
            source='mark:source-v1:{"source":"opaque origin"}',
            lifecycle_state=LifecycleState.ACTIVE,
            title="Record " + str(index),
            description="Zeile Eins\n" + "x" * 7800,
            metric_source="management+stats",
            views=index,
        ) for index, when in enumerate(timeline))
        for row in entries:
            store.append_ad_snapshot(row)
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        self.assertEqual(client.ad_history("123"), store.ad_history("123"))
        self.assertEqual(client.ad_history("unknown"), ())

    def test_history_watermark_disallows_late_insert_but_next_read_includes(self) -> None:
        from mark_api.domain import AdSnapshot
        service = self.make_service()
        store = SnapshotStore(self.database, create_if_missing=False)
        for n in range(20):
            store.append_ad_snapshot(AdSnapshot(
                ad_id="123", observed_at=NOW, source="first", views=n,
            ))
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        late = AdSnapshot(ad_id="123", observed_at=NOW - timedelta(minutes=1),
                          source="late", views=99)
        old = client._call
        fired = False
        def insert_between_pages(op: str, **fields: object) -> dict[str, object]:
            nonlocal fired
            result = old(op, **fields)
            if op == "ad_history_page" and not fired:
                fired = True
                store.append_ad_snapshot(late)
            return result
        with patch.object(client, "_call", side_effect=insert_between_pages):
            before = client.ad_history("123")
        self.assertTrue(fired)
        self.assertNotIn(late, before)
        self.assertEqual(len(before), 20)
        self.assertIn(late, client.ad_history("123"))

    def test_history_terminal_page_cannot_overrun_reply_frame(self) -> None:
        from mark_api.domain import AdSnapshot
        service = self.make_service()
        store = SnapshotStore(self.database, create_if_missing=False)
        snapshot = AdSnapshot(ad_id="frame", source="owner", observed_at=NOW,
                              title="single row")
        store.append_ad_snapshot(snapshot)
        raw_record = owner_module._snapshot_as_json(snapshot)
        optimistic = {
            "ok": True,
            "result": {
                "watermark": 1, "more": True,
                "rows": [{"id": 1, "snapshot": raw_record}],
            },
        }
        # false is one byte larger than true. A final-page response must be
        # accounted for with its actual/worst-case byte length.
        tiny_limit = len(owner_module._canonical_json(optimistic).encode("utf-8"))
        with patch.object(owner_module, "_MAX_FRAME", tiny_limit):
            with self.assertRaises(StorageOwnerError):
                service.dispatch({
                    "version": 1, "op": "ad_history_page", "ad_id": "frame",
                    "after_id": 0, "watermark": None,
                })

    def test_history_readback_preserves_valid_empty_optional_text(self) -> None:
        from mark_api.domain import AdSnapshot
        service = self.make_service()
        stored = AdSnapshot(ad_id="empty", source="owner", observed_at=NOW,
                            title="", description="")
        SnapshotStore(self.database, create_if_missing=False).append_ad_snapshot(stored)
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        self.assertEqual(client.ad_history("empty"), (stored,))

    def test_history_pagination_rejects_forged_sql_and_cursor_envelope(self) -> None:
        service = self.make_service()
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        for fields in (
            {"ad_id": "123", "after_id": 0, "watermark": None, "sql": "DELETE FROM write_api_requests"},
            {"ad_id": "123", "after_id": True, "watermark": None},
            {"ad_id": "123", "after_id": -1, "watermark": None},
        ):
            with self.subTest(fields=fields), self.assertRaises(StorageOwnerError):
                service.dispatch({"version": 1, "op": "ad_history_page", **fields})
        with patch.object(client, "_call", return_value={
            "watermark": 10, "more": True, "rows": [],
        }), self.assertRaises(StorageOwnerError):
            client.ad_history("123")

    def test_email_only_query_and_analytics_use_typed_owner_not_raw_sqlite(self) -> None:
        from mark_api.domain import InboundMessageEvent, AdClassification
        from mark_api.analytics import AnalyticsService
        from mark_api.query import MarkQueryService
        service = self.make_service()
        store = SnapshotStore(self.database, create_if_missing=False)
        events = tuple(InboundMessageEvent(
            ad_id="email-only", conversation_id=f"buyer-{i % 3}",
            provider_message_id=f"provider:{i:02}",
            observed_at=NOW + timedelta(minutes=(20 - i)),
            source="email:sample",
        ) for i in range(19))
        self.assertEqual(store.append_inbound_message_events(events), 19)
        expected_classification = AdClassification(
            ad_id="email-only", observed_at=NOW,
            source="manual", city="Magdeburg",
        )
        store.append_classification(expected_classification)
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        self.assertEqual(client.inbound_message_history("email-only"),
                         store.inbound_message_history("email-only"))
        self.assertEqual(client.inbound_message_counts("email-only"), (3, 19))
        self.assertEqual(client.inbound_message_ad_ids(), ("email-only",))
        self.assertEqual(client.latest_classification("email-only"),
                         expected_classification)
        self.assertIsNone(client.latest_classification("missing"))
        query_expected, query_rpc = MarkQueryService(store), MarkQueryService(client)
        self.assertEqual(query_rpc.email_reactions(), query_expected.email_reactions())
        self.assertEqual(query_rpc.email_reaction("email-only"),
                         query_expected.email_reaction("email-only"))
        analytics_expected, analytics_rpc = AnalyticsService(store), AnalyticsService(client)
        self.assertEqual(analytics_rpc.rank_ads("email_inbound_message_count"),
                         analytics_expected.rank_ads("email_inbound_message_count"))
        self.assertEqual(analytics_rpc.group_rankings("city", "email_inbound_message_count"),
                         analytics_expected.group_rankings("city", "email_inbound_message_count"))

    def test_inbox_multibyte_full_reply_frames_do_not_truncate_events(self) -> None:
        from mark_api.domain import InboundMessageEvent
        service = self.make_service()
        store = SnapshotStore(self.database, create_if_missing=False)
        events = tuple(InboundMessageEvent(
            ad_id="long-valid",
            conversation_id="buyer:" + str(i),
            provider_message_id=f"provider:{i:02}:" + "x" * 1800,
            observed_at=NOW + timedelta(minutes=30 - i),
            source="email:" + "ä" * 800,
        ) for i in range(22))
        self.assertEqual(store.append_inbound_message_events(events), len(events))
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        self.assertEqual(
            client.inbound_message_history("long-valid"),
            store.inbound_message_history("long-valid"),
        )
        self.assertEqual(client.inbound_message_counts("long-valid"), (22, 22))
        self.assertEqual(client.inbound_message_history("missing"), ())

    def test_typed_inbox_pages_refuse_sql_and_untrusted_cursors(self) -> None:
        service = self.make_service()
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        for fields in (
            {"ad_id": "email-only", "after_id": True, "watermark": None},
            {"ad_id": "email-only", "after_id": 0, "watermark": None,
             "sql": "DELETE FROM write_api_requests"},
        ):
            with self.subTest(fields=fields), self.assertRaises(StorageOwnerError):
                service.dispatch({"version": 1, "op": "inbound_message_page", **fields})
        with self.assertRaises(StorageOwnerError):
            service.dispatch({
                "version": 1, "op": "inbound_ad_ids_page",
                "after": "", "watermark": None,
                "sql": "SELECT * FROM write_recovery_clearances",
            })
        with patch.object(client, "_call", return_value={
            "watermark": 2, "more": True, "rows": [],
        }), self.assertRaises(StorageOwnerError):
            client.inbound_message_history("email-only")
        with patch.object(client, "_call", return_value={
            "watermark": 2, "more": True, "ids": [],
        }), self.assertRaises(StorageOwnerError):
            client.inbound_message_ad_ids()

    def test_reaction_history_rpc_preserves_original_provenance_and_counters(self) -> None:
        from mark_api.domain import ReactionSnapshot
        service = self.make_service()
        store = SnapshotStore(self.database, create_if_missing=False)
        originals = tuple(ReactionSnapshot(
            ad_id="rx", observed_at=NOW + timedelta(minutes=(20 - i)),
            source="kleinanzeigen:" + "ä" * 800,
            conversation_count=i, unique_buyer_count=i + 2,
            inbound_message_count=i + 7,
        ) for i in range(19))
        for row in originals:
            store.append_reaction_snapshot(row)
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        self.assertEqual(client.reaction_history("rx"), originals)
        self.assertEqual(client.reaction_history("no-match"), ())

    def test_reaction_history_rpc_pins_watermark_and_rejects_sql(self) -> None:
        from mark_api.domain import ReactionSnapshot
        service = self.make_service()
        store = SnapshotStore(self.database, create_if_missing=False)
        for i in range(19):
            store.append_reaction_snapshot(ReactionSnapshot(
                ad_id="rx", observed_at=NOW, source="first",
                conversation_count=i, unique_buyer_count=i,
                inbound_message_count=i,
            ))
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        late = ReactionSnapshot(
            ad_id="rx", observed_at=NOW, source="late",
            conversation_count=99, unique_buyer_count=99,
            inbound_message_count=99,
        )
        original_call = client._call
        fired = False
        def append_late(op: str, **fields: object) -> dict[str, object]:
            nonlocal fired
            result = original_call(op, **fields)
            if op == "reaction_history_page" and not fired:
                fired = True
                store.append_reaction_snapshot(late)
            return result
        with patch.object(client, "_call", side_effect=append_late):
            previous = client.reaction_history("rx")
        self.assertTrue(fired)
        self.assertEqual(len(previous), 19)
        self.assertNotIn(late, previous)
        self.assertIn(late, client.reaction_history("rx"))
        for fields in (
            {"ad_id": "rx", "after_id": True, "watermark": None},
            {"ad_id": "rx", "after_id": 0, "watermark": None,
             "sql": "DELETE FROM write_recovery_clearances"},
        ):
            with self.subTest(fields=fields), self.assertRaises(StorageOwnerError):
                service.dispatch({
                    "version": 1, "op": "reaction_history_page", **fields,
                })
        with patch.object(client, "_call", return_value={
            "watermark": 1, "more": True, "rows": [],
        }), self.assertRaises(StorageOwnerError):
            client.reaction_history("rx")

    def test_typed_inventory_journal_preserves_started_recovery_fences(self) -> None:
        from mark_api.domain import AdSnapshot, LifecycleState
        service = self.make_service()
        previous = SnapshotStore(self.database, create_if_missing=False)
        previous.append_ad_snapshot(AdSnapshot(
            ad_id="100", observed_at=NOW, source="synthetic", title="Old",
            lifecycle_state=LifecycleState.ACTIVE,
        ))
        service.runtime_uid = os.geteuid()  # Protocol only; real UID in Docker.
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        client.claim(key="uid:fenced:sync", sha=SHA, owner="runtime:one", at=NOW)
        client.begin(key="uid:fenced:sync", sha=SHA, owner="runtime:one", at=NOW)
        self.assertEqual(client.tracked_ad_ids(), ("100",))
        attempt = client.begin_sync_attempt(source="synthetic", started_at=NOW)
        self.assertEqual(client.sync_status()["state"], "in_progress")
        self.assertEqual(previous.ad_history("100")[-1].lifecycle_state,
                         LifecycleState.ACTIVE)
        snapshot = AdSnapshot(
            ad_id="200", source="synthetic",
            observed_at=NOW + timedelta(minutes=1), title="New",
            lifecycle_state=LifecycleState.ACTIVE, views=4,
        )
        count = client.append_inventory_result(
            ReadResult.success_nonempty((snapshot,)),
            tracked_ad_ids=("100",), source="synthetic", attempt_id=attempt,
            observed_at=NOW + timedelta(minutes=1),
            completed_at=NOW + timedelta(minutes=2),
        )
        self.assertEqual(count, 2)
        self.assertEqual(previous.latest_ad_snapshot("200"), snapshot)
        self.assertEqual(previous.latest_ad_snapshot("100").lifecycle_state,
                         LifecycleState.ABSENT)
        self.assertEqual(client.sync_status()["state"], "success_nonempty")
        self.assertTrue(client.create_pending(
            exclude_key="uid:next:sync", owner="runtime:one"))
        self.assertFalse(client.has_write_recovery_clearance(
            idempotency_key="uid:fenced:sync", request_sha256=SHA))
        # No silent replay of a potentially committed operation.
        with self.assertRaises(StorageOwnerError):
            client.append_inventory_result(
                ReadResult.success_nonempty((snapshot,)),
                tracked_ad_ids=("100",), source="synthetic", attempt_id=attempt,
                observed_at=NOW + timedelta(minutes=1),
                completed_at=NOW + timedelta(minutes=2),
            )
        self.assertEqual(len(previous.ad_history("200")), 1)

    def test_multi_batch_inventory_preserves_multiline_descriptions(self) -> None:
        from mark_api.domain import AdSnapshot, LifecycleState
        service = self.make_service()
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        attempt = client.begin_sync_attempt(source="synthetic", started_at=NOW)
        snapshots = tuple(
            AdSnapshot(ad_id=str(800 + index), observed_at=NOW,
                       source="synthetic", lifecycle_state=LifecycleState.ACTIVE,
                       title="Artikel", description="Zeile eins\\n" + "x" * 7800,
                       views=index)
            for index in range(9)
        )
        self.assertEqual(client.append_inventory_result(
            ReadResult.success_nonempty(snapshots), tracked_ad_ids=(),
            observed_at=NOW, source="synthetic", attempt_id=attempt,
            completed_at=NOW + timedelta(minutes=1),
        ), 9)
        store = SnapshotStore(self.database, create_if_missing=False)
        self.assertEqual(store.latest_ad_snapshot("808"), snapshots[-1])
        self.assertEqual(client.sync_status()["state"], "success_nonempty")

    def test_lost_stage_ack_never_retries_or_commits_a_success(self) -> None:
        from mark_api.domain import AdSnapshot
        service = self.make_service()
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        attempt = client.begin_sync_attempt(source="synthetic", started_at=NOW)
        original = client._call
        calls = []

        def lose_ack(op: str, **fields: object) -> dict[str, object]:
            calls.append(op)
            value = original(op, **fields)
            if op == "sync_stage":
                raise StorageOwnerError("synthetic ACK loss AFTER append")
            return value

        with patch.object(client, "_call", side_effect=lose_ack):
            with self.assertRaisesRegex(StorageOwnerError, "ACK loss"):
                client.append_inventory_result(
                    ReadResult.success_nonempty((owner_module._snapshot_from_json({
                        "ad_id": "700", "observed_at": NOW.isoformat(),
                        "source": "synthetic", "lifecycle_state": "active",
                        "title": "One", "description": None,
                        "views": None, "watch_count": None, "reply_count": None,
                        "metric_source": None,
                    }),)), tracked_ad_ids=(), source="synthetic",
                    attempt_id=attempt, observed_at=NOW,
                    completed_at=NOW + timedelta(minutes=1),
                )
        self.assertEqual(calls.count("sync_stage"), 1)
        self.assertNotIn("sync_finish", calls)
        self.assertEqual(client.sync_stage_status(attempt)["accepted"], 1)
        self.assertEqual(client.sync_status()["state"], "in_progress")
        self.assertEqual(SnapshotStore(self.database, create_if_missing=False)
                         .ad_history("700"), ())

    def test_incomplete_inventory_stage_never_commits_or_clears_fences(self) -> None:
        from mark_api.domain import AdSnapshot, LifecycleState
        service = self.make_service()
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        attempt = client.begin_sync_attempt(source="synthetic", started_at=NOW)
        snapshot = AdSnapshot(
            ad_id="300", source="synthetic", observed_at=NOW,
            lifecycle_state=LifecycleState.ACTIVE,
        )
        owner_module._snapshot_as_json(snapshot)
        reply = service.dispatch({
            "version": 1, "op": "sync_stage", "attempt": attempt, "offset": 0,
            "snapshots": [owner_module._snapshot_as_json(snapshot)],
        })
        self.assertEqual(reply["accepted"], 1)
        self.assertEqual(client.sync_status()["state"], "in_progress")
        self.assertEqual(client.sync_stage_status(attempt)["accepted"], 1)
        self.assertEqual(SnapshotStore(self.database, create_if_missing=False)
                         .ad_history("300"), ())
        # Untrusted executor cannot attach arbitrary SQL/clearance to the
        # operation-bound envelope, or claim success using a wrong digest.
        with self.assertRaises(StorageOwnerError):
            service.dispatch({
                "version": 1, "op": "sync_stage", "attempt": attempt,
                "offset": 1, "snapshots": [], "sql": "DELETE FROM write_api_requests",
            })
        with self.assertRaises(StorageOwnerError):
            service.dispatch({
                "version": 1, "op": "sync_finish", "attempt": attempt,
                "source": "synthetic", "digest": "0" * 64,
                "observed_at": NOW.isoformat(), "completed_at": NOW.isoformat(),
            })
        self.assertEqual(client.sync_status()["state"], "in_progress")
        service.stop()
        self.assertFalse(self.sock.exists())
        restarted = self.make_service()
        restarted.runtime_uid = os.geteuid()
        restarted.start()
        fresh = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        self.assertEqual(fresh.sync_status()["state"], "in_progress")
        self.assertEqual(SnapshotStore(self.database, create_if_missing=False)
                         .ad_history("300"), ())
        with self.assertRaises(StorageOwnerError):
            fresh.sync_stage_status(attempt)

    def test_bounded_inventory_batch_rejects_forged_record_and_unknown_attempt(self) -> None:
        from mark_api.domain import AdSnapshot
        service = self.make_service()
        service.runtime_uid = os.geteuid()
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        attempt = client.begin_sync_attempt(source="synthetic", started_at=NOW)
        record = owner_module._snapshot_as_json(AdSnapshot(
            ad_id="400", source="synthetic", observed_at=NOW,
        ))
        for forged in (
            dict(record, views=True),
            dict(record, metric_source=42),
            dict(record, sql="DROP TABLE write_recovery_clearances"),
            dict(record, source="other"),
        ):
            with self.subTest(forged=forged), self.assertRaises(StorageOwnerError):
                service.dispatch({"version": 1, "op": "sync_stage",
                                  "attempt": attempt, "offset": 0,
                                  "snapshots": [forged]})
        with self.assertRaises(StorageOwnerError):
            service.dispatch({"version": 1, "op": "sync_stage",
                              "attempt": attempt + 1, "offset": 0,
                              "snapshots": [record]})
        self.assertEqual(client.sync_stage_status(attempt)["accepted"], 0)
        client.fail_sync_attempt(attempt, source="synthetic",
                                 completed_at=NOW + timedelta(minutes=1),
                                 error_kind="reader_exception")
        self.assertEqual(client.sync_status()["state"], "failed")
        self.assertEqual(SnapshotStore(self.database, create_if_missing=False)
                         .ad_history("400"), ())

    def test_clearance_lookup_is_exact_and_cannot_mint_recovery_authority(self) -> None:
        service = self.make_service()
        claim = {"version": 1, "op": "claim", "key": "ui:clearance:1",
                 "sha": SHA, "owner": "runtime:one", "at": NOW.isoformat()}
        service.dispatch(claim)
        service.dispatch(dict(claim, op="begin"))
        query = {"version": 1, "op": "has_clearance", "key": "ui:clearance:1",
                 "sha": SHA}
        self.assertEqual(service.dispatch(query), {"cleared": False})
        # Synthetic operator-side setup, NOT independent platform evidence.
        trusted = SnapshotStore(self.database, create_if_missing=False)
        trusted.record_write_recovery_clearance(
            idempotency_key="ui:clearance:1", request_sha256=SHA,
            verification_kind="operator_verified_postread",
            evidence_reference="synthetic-test-only", observed_at=NOW,
        )
        service.runtime_uid = os.geteuid()  # Framing-only, no OS UID proof.
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        self.assertTrue(client.has_write_recovery_clearance(
            idempotency_key="ui:clearance:1", request_sha256=SHA,
        ))
        self.assertFalse(client.has_write_recovery_clearance(
            idempotency_key="ui:clearance:1", request_sha256="b" * 64,
        ))
        with self.assertRaises(StorageOwnerError):
            service.dispatch(dict(query, verification_kind="operator_verified_postread"))
        with self.assertRaises(StorageOwnerError):
            service.dispatch(dict(query, op="record_write_recovery_clearance"))

    def test_clearance_lookup_rejects_forged_response_without_retry(self) -> None:
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        for result in ({"cleared": 1}, {"cleared": None},
                       {"cleared": True, "evidence": "forged"}, {}):
            with self.subTest(result=result), patch.object(
                client, "_call", return_value=result,
            ), self.assertRaises(StorageOwnerError):
                client.has_write_recovery_clearance(
                    idempotency_key="ui:clearance:1", request_sha256=SHA,
                )

    def test_sync_status_exact_readback_without_recovery_mutation(self) -> None:
        # The broker must expose the real sync journal, not infer success
        # from last-known ad snapshots or synthesize a successful attempt.
        service = self.make_service()
        empty = service.dispatch({"version": 1, "op": "sync_status"})
        self.assertEqual(empty, {"sync_status": {
            "state": "never_attempted", "latest_attempt": None,
            "last_successful_attempt": None,
        }})
        store = SnapshotStore(self.database, create_if_missing=False)
        attempt = store.begin_sync_attempt(source="private_web_inventory", started_at=NOW)
        failed = service.dispatch({"version": 1, "op": "sync_status"})
        self.assertEqual(failed["sync_status"], store.sync_status())
        self.assertEqual(failed["sync_status"]["state"], "in_progress")
        store.fail_sync_attempt(
            attempt, source="private_web_inventory", completed_at=NOW,
            error_kind="transport_error",
        )
        service.runtime_uid = os.geteuid()  # Same-UID framing only.
        service.start()
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        self.assertEqual(client.sync_status(), store.sync_status())
        self.assertEqual(client.sync_status()["state"], "failed")
        self.assertIsNone(client.sync_status()["last_successful_attempt"])
        success_id = store.begin_sync_attempt(
            source="private_web_inventory", started_at=NOW,
        )
        self.assertEqual(store.append_inventory_result(
            ReadResult.success_empty(()), observed_at=NOW,
            source="private_web_inventory", attempt_id=success_id,
            completed_at=NOW,
        ), 0)
        self.assertEqual(client.sync_status()["state"], "success_empty")
        self.assertEqual(
            client.sync_status()["last_successful_attempt"]["id"], success_id,
        )
        failure_id = store.begin_sync_attempt(
            source="private_web_inventory", started_at=NOW,
        )
        store.fail_sync_attempt(
            failure_id, source="private_web_inventory", completed_at=NOW,
            error_kind="transport_error",
        )
        status = client.sync_status()
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["last_successful_attempt"]["id"], success_id)
        for fields in ({"sql": "DELETE FROM write_recovery_clearances"},
                       {"filename": str(self.database)}, {"success": True}):
            with self.subTest(fields=fields), self.assertRaises(StorageOwnerError):
                service.dispatch({"version": 1, "op": "sync_status", **fields})

    def test_sync_status_client_rejects_forged_or_inconsistent_status(self) -> None:
        client = StorageOwnerClient(self.sock, expected_owner_uid=os.geteuid())
        malformed = [
            {"state": "success_empty", "latest_attempt": None,
             "last_successful_attempt": None},
            {"state": "never_attempted", "latest_attempt": None,
             "last_successful_attempt": {"id": 1}},
            {"state": "success_empty", "latest_attempt": {
                "id": 1, "source": "private_web_inventory",
                "started_at": NOW.isoformat(), "completed_at": NOW.isoformat(),
                "outcome": "success_empty", "error_kind": None,
                "snapshot_count": 0,
            }, "last_successful_attempt": None},
            {"state": "failed", "latest_attempt": {
                "id": 1, "source": "private_web_inventory",
                "started_at": NOW.isoformat(), "completed_at": NOW.isoformat(),
                "outcome": "success_empty", "error_kind": None,
                "snapshot_count": 0,
            }, "last_successful_attempt": None},
            {"state": "never_attempted", "latest_attempt": None,
             "last_successful_attempt": None, "sql": "SELECT *"},
        ]
        for result in malformed:
            with self.subTest(result=result), patch.object(
                client, "_call", return_value={"sync_status": result},
            ), self.assertRaises(StorageOwnerError):
                client.sync_status()

    def test_container_draft_denies_direct_python_main_before_argparse(self) -> None:
        with patch("mark_api.storage_owner.container_draft_denied", return_value=True):
            self.assertEqual(main(["--db", str(self.database)]), 2)


if __name__ == "__main__":
    unittest.main()
