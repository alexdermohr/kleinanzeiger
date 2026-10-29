"""Bounded A6 owner-inventory capacity and protocol regression tests."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import tempfile
import time
import tracemalloc
import unittest
from unittest.mock import patch

from mark_api.domain import AdSnapshot
from mark_api.results import ReadResult
from mark_api.storage import SnapshotStore
from mark_api.storage_owner import (
    StorageOwnerClient, StorageOwnerError, StorageOwnerServer,
    _MAX_FRAME, _MAX_SYNC_BATCH, _MAX_SYNC_ITEMS, _VERSION,
    _canonical_json, _snapshot_as_json,
)


NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
SOURCE = "capacity-test"


class StorageOwnerCapacityTests(unittest.TestCase):
    @contextmanager
    def owner(self, *, socket_transport: bool):
        with tempfile.TemporaryDirectory(prefix="mark-owner-capacity-") as raw:
            base = Path(raw)
            database_dir = base / "database"
            database_dir.mkdir(mode=0o700)
            database = database_dir / "mark.sqlite"
            SnapshotStore(database)
            rpc_dir = base / "rpc"
            rpc_dir.mkdir(mode=0o700)
            rpc_dir.chmod(0o2710)
            endpoint = rpc_dir / "storage.sock"
            server = StorageOwnerServer(
                database=database, socket_path=endpoint,
                runtime_uid=os.geteuid() + 10001, runtime_gid=os.getegid(),
            )
            try:
                client = StorageOwnerClient(endpoint, expected_owner_uid=os.geteuid())
                if socket_transport:
                    # Existing test-only framing fence; production construction
                    # above still rejects equal storage/runtime UIDs.
                    server.runtime_uid = os.geteuid()
                    server.start()
                    yield server, client, database, None
                else:
                    timings: list[float] = []

                    def dispatch(op: str, **fields: object) -> dict[str, object]:
                        request = {"version": _VERSION, "op": op, **fields}
                        encoded = (_canonical_json(request) + "\n").encode("utf-8")
                        self.assertLessEqual(len(encoded), _MAX_FRAME + 1)
                        started = time.perf_counter()
                        answer = server.dispatch(request)
                        stage = server._active_sync
                        if stage is not None:
                            self.assertEqual(len(stage.snapshots), len(stage.ad_ids))
                            self.assertLessEqual(len(stage.snapshots), _MAX_SYNC_ITEMS)
                            self.assertLessEqual(len(stage.ad_ids), _MAX_SYNC_ITEMS)
                        timings.append(time.perf_counter() - started)
                        return answer

                    with patch.object(client, "_call", side_effect=dispatch):
                        yield server, client, database, timings
            finally:
                server.stop()

    @staticmethod
    def snapshots(count: int, *, prefix: str = "ad") -> tuple[AdSnapshot, ...]:
        return tuple(
            AdSnapshot(ad_id=f"{prefix}-{index:05d}", observed_at=NOW, source=SOURCE)
            for index in range(count)
        )

    def commit(self, client: StorageOwnerClient, count: int) -> int:
        attempt = client.begin_sync_attempt(source=SOURCE, started_at=NOW)
        return client.append_inventory_result(
            ReadResult.success_nonempty(self.snapshots(count)),
            source=SOURCE, attempt_id=attempt, observed_at=NOW,
            completed_at=NOW + timedelta(minutes=1),
        )

    def test_bounded_direct_generations_accept_100_2048_5000_and_12000(self) -> None:
        self.assertEqual(_MAX_SYNC_BATCH, 4)
        self.assertEqual(_MAX_SYNC_ITEMS, 12_000)
        for count in (100, 2_048, 5_000, 12_000):
            with self.subTest(count=count), self.owner(socket_transport=False) as (
                server, client, database, timings,
            ):
                if count == 12_000:
                    tracemalloc.start()
                started = time.perf_counter()
                self.assertEqual(self.commit(client, count), count)
                elapsed = time.perf_counter() - started
                peak = tracemalloc.get_traced_memory()[1] if count == 12_000 else None
                if count == 12_000:
                    tracemalloc.stop()
                self.assertEqual(len(server._active_sync.ad_ids) if server._active_sync else 0, 0)
                self.assertEqual(
                    SnapshotStore(database, create_if_missing=False)
                    .latest_sync_attempt().snapshot_count,
                    count,
                )
                ordered = sorted(timings or ())
                p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
                print(
                    f"capacity={count} elapsed_s={elapsed:.3f} "
                    f"rpc_dispatch_p95_ms={p95 * 1000:.3f} "
                    f"tracemalloc_peak_bytes={peak if peak is not None else 'not-measured'}"
                )

    def test_real_unix_socket_generation_accepts_10000_with_32k_frames(self) -> None:
        with self.owner(socket_transport=True) as (_server, client, database, _timings):
            started = time.perf_counter()
            self.assertEqual(self.commit(client, 10_000), 10_000)
            elapsed = time.perf_counter() - started
            self.assertEqual(
                SnapshotStore(database, create_if_missing=False)
                .latest_sync_attempt().snapshot_count,
                10_000,
            )
            print(f"unix_socket_capacity=10000 elapsed_s={elapsed:.3f}")

    def test_12001_is_rejected_before_any_owner_rpc(self) -> None:
        client = StorageOwnerClient(Path("/not-connected.sock"), expected_owner_uid=os.geteuid())
        with patch.object(client, "_call") as rpc, self.assertRaisesRegex(
            StorageOwnerError, "invalid inventory read result",
        ):
            client.append_inventory_result(
                ReadResult.success_nonempty(self.snapshots(12_001)),
                source=SOURCE, attempt_id=1, observed_at=NOW,
                completed_at=NOW + timedelta(minutes=1),
            )
        rpc.assert_not_called()

    def test_duplicate_across_batches_and_out_of_order_offset_do_not_mutate_stage(self) -> None:
        with self.owner(socket_transport=False) as (server, client, _database, _timings):
            attempt = client.begin_sync_attempt(source=SOURCE, started_at=NOW)
            first = self.snapshots(4, prefix="duplicate")
            accepted = server.dispatch({
                "version": _VERSION, "op": "sync_stage", "attempt": attempt,
                "offset": 0, "snapshots": [_snapshot_as_json(item) for item in first],
            })
            self.assertEqual(accepted["accepted"], 4)
            digest = accepted["digest"]
            duplicate = AdSnapshot(
                ad_id=first[0].ad_id, observed_at=NOW, source=SOURCE,
            )
            with self.assertRaisesRegex(StorageOwnerError, "duplicate"):
                server.dispatch({
                    "version": _VERSION, "op": "sync_stage", "attempt": attempt,
                    "offset": 4, "snapshots": [_snapshot_as_json(duplicate)],
                })
            with self.assertRaisesRegex(StorageOwnerError, "cursor"):
                server.dispatch({
                    "version": _VERSION, "op": "sync_stage", "attempt": attempt,
                    "offset": 3,
                    "snapshots": [_snapshot_as_json(self.snapshots(1, prefix="late")[0])],
                })
            self.assertEqual(client.sync_stage_status(attempt), {
                "accepted": 4, "digest": digest,
            })
            self.assertEqual(len(server._active_sync.snapshots), 4)
            self.assertEqual(len(server._active_sync.ad_ids), 4)


if __name__ == "__main__":
    unittest.main()
