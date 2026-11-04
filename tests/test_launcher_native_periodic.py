"""Native customer inventory scheduler: reuse owner CDP lock, pause and resume."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from time import sleep
from types import SimpleNamespace
from unittest.mock import patch

from mark_api.launcher import (
    ProductLauncherRuntime, _validated_owner_poll_interval, build_product_launcher,
)
from mark_api.private_web_runtime import OwnerInventorySyncFailure
from mark_api.results import ReadResult
from mark_api.storage import SnapshotStore


class _Server:
    server_address = ("127.0.0.1", 18990)

    def __init__(self):
        self.started = threading.Event()
        self.stopped = threading.Event()
        self.close_calls = 0

    def serve_forever(self):
        self.started.set()
        self.stopped.wait(3)

    def shutdown(self):
        self.stopped.set()

    def server_close(self):
        self.close_calls += 1


class _Inventory:
    def __init__(self):
        self.close_calls = 0

    def read_inventory(self):
        return ReadResult.success_empty(())

    def close(self):
        self.close_calls += 1


class _Writer:
    server_address = ("127.0.0.1", 18991)

    def __init__(self):
        self.close_calls = 0
        self.start_calls = 0
        self.sync_calls = 0
        self.sync_failure = None

    def start(self):
        self.start_calls += 1

    def sync_owner_inventory(self):
        self.sync_calls += 1
        if self.sync_failure is not None:
            raise self.sync_failure
        return 0

    def close(self):
        self.close_calls += 1


def _runtime(sync, *, enabled=None, decision_lock=None):
    server = _Server()
    inventory = _Inventory()
    writer = _Writer()
    runtime = ProductLauncherRuntime(
        inventory_runtime=inventory, write_runtime=writer,
        write_access=SimpleNamespace(bearer_token="synthetic"),
        server=server, dashboard_write_token="synthetic",
        startup_inventory_count=0, startup_persisted_count=0,
        startup_email_import_report=SimpleNamespace(), writes_enabled=True,
        periodic_sync=sync, periodic_interval_seconds=60,
        periodic_enabled=enabled, periodic_decision_lock=decision_lock,
    )
    # Fast test clock; public builder rejects intervals below one minute.
    runtime._periodic_interval = 0.025
    return runtime, server, inventory, writer


class NativePeriodicInventoryTests(unittest.TestCase):
    def test_interval_contract_rejects_invalid_before_browser_or_store(self):
        for value in (0, 60, 300, 86400, 300.0):
            self.assertEqual(_validated_owner_poll_interval(value), float(value))
        for value in (-1, 59, 86401, True, False, float("nan"), float("inf"), "300"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _validated_owner_poll_interval(value)

    def test_failed_browser_read_pauses_until_confirmed_manual_rearm(self):
        first = threading.Event()
        resumed = threading.Event()
        enabled = threading.Event()
        enabled.set()
        calls = []

        def sync():
            calls.append("read")
            if len(calls) == 1:
                first.set()
                raise OwnerInventorySyncFailure("connection_unavailable")
            resumed.set()
            return 0

        runtime, server, inventory, writer = _runtime(sync, enabled=enabled)
        owner = threading.Thread(target=runtime.serve_forever)
        owner.start()
        try:
            self.assertTrue(server.started.wait(1))
            self.assertTrue(first.wait(1))
            self.assertFalse(enabled.wait(0.14))
            self.assertEqual(len(calls), 1, "never blind-retry ambiguous login/transport errors")
            enabled.set()  # Only the real dashboard's successful manual sync does this.
            self.assertTrue(resumed.wait(1), "a successful explicit sync re-arms polling")
            self.assertEqual(len(calls), 2)
        finally:
            runtime.close()
            owner.join(1)
        self.assertFalse(owner.is_alive())
        self.assertEqual((server.close_calls, inventory.close_calls, writer.close_calls), (1, 1, 1))

    def test_manual_success_after_failed_poll_cannot_be_lost_to_late_pause(self):
        enabled = threading.Event()
        enabled.set()
        decision_lock = threading.Lock()
        inside_clear = threading.Event()
        continue_clear = threading.Event()
        manual_finished = threading.Event()
        original_clear = enabled.clear

        def delayed_clear():
            inside_clear.set()
            self.assertTrue(continue_clear.wait(2))
            original_clear()

        enabled.clear = delayed_clear

        attempts = []

        def failing_sync():
            attempts.append("read")
            if len(attempts) == 1:
                raise OwnerInventorySyncFailure("login_required")
            return 0

        runtime, server, inventory, writer = _runtime(
            failing_sync, enabled=enabled, decision_lock=decision_lock,
        )
        owner = threading.Thread(target=runtime.serve_forever)
        owner.start()
        try:
            self.assertTrue(inside_clear.wait(1), "poll reached failure pause")

            def manual_success():
                # Real dashboard callback acquires after successful owner read.
                with decision_lock:
                    enabled.set()
                manual_finished.set()

            manual = threading.Thread(target=manual_success)
            manual.start()
            self.assertFalse(manual_finished.wait(0.05),
                             "manual rearm must wait for older poll decision")
            continue_clear.set()
            self.assertTrue(manual_finished.wait(1))
            manual.join(1)
            self.assertTrue(enabled.is_set(), "newer manual success remains armed")
        finally:
            continue_clear.set()
            runtime.close()
            owner.join(1)

    def test_busy_cycle_skips_without_disabling_next_read(self):
        second = threading.Event()
        calls = []

        def sync():
            calls.append("read")
            if len(calls) == 1:
                raise OwnerInventorySyncFailure("busy")
            second.set()
            return 0

        runtime, _, _, _ = _runtime(sync)
        owner = threading.Thread(target=runtime.serve_forever)
        owner.start()
        try:
            self.assertTrue(second.wait(1), "busy writer cannot suppress all future inventory reads")
            self.assertTrue(runtime._periodic_enabled.is_set())
            self.assertGreaterEqual(len(calls), 2)
        finally:
            runtime.close()
            owner.join(1)

    def test_shutdown_waits_for_inflight_read_before_closing_owner(self):
        started = threading.Event()
        release = threading.Event()

        def sync():
            started.set()
            self.assertTrue(release.wait(2), "test releases an in-flight read")
            return 0

        runtime, server, inventory, writer = _runtime(sync)
        owner = threading.Thread(target=runtime.serve_forever)
        owner.start()
        self.assertTrue(started.wait(1))
        done = threading.Event()

        def shutdown():
            try:
                runtime.close()
            finally:
                done.set()

        closer = threading.Thread(target=shutdown)
        closer.start()
        self.assertFalse(done.wait(0.06))
        self.assertEqual(writer.close_calls, 0, "do not release the shared CDP before read settles")
        release.set()
        self.assertTrue(done.wait(2))
        owner.join(1)
        closer.join(1)
        self.assertFalse(owner.is_alive())
        self.assertEqual((server.close_calls, inventory.close_calls, writer.close_calls), (1, 1, 1))

    def test_real_dashboard_manual_success_rearms_but_failure_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "owner.sqlite"
            SnapshotStore(database)
            captured = {}
            writer = _Writer()
            reader = _Inventory()

            def dashboard_factory(_store, **options):
                captured.update(options)
                return _Server()

            with patch("mark_api.launcher._require_private_web_cdp_authority"), \
                    patch("mark_api.launcher.build_private_web_write_api_runtime", return_value=writer):
                launcher = build_product_launcher(
                    db_path=database, cdp_port=9222, dashboard_port=0,
                    write_port=0, runtime_factory=lambda **kwargs: reader,
                    dashboard_factory=dashboard_factory,
                    sync_interval_seconds=60,
                )
            try:
                self.assertIsNotNone(launcher._periodic_sync)
                self.assertEqual(launcher._periodic_interval, 60)
                self.assertTrue(callable(captured.get("sync_once")))
                self.assertTrue(launcher._periodic_enabled.is_set())
                for kind in ("login_required", "connection_unavailable",
                             "read_failed", "storage_unavailable"):
                    writer.sync_failure = OwnerInventorySyncFailure(kind)
                    with self.subTest(kind=kind), self.assertRaises(OwnerInventorySyncFailure):
                        captured["sync_once"]()
                    self.assertFalse(launcher._periodic_enabled.is_set(),
                                     "failed manual read must pause automatic reads")
                    writer.sync_failure = None
                    self.assertEqual(captured["sync_once"](), 0)
                    self.assertTrue(launcher._periodic_enabled.is_set())
                writer.sync_failure = OwnerInventorySyncFailure("busy")
                with self.assertRaises(OwnerInventorySyncFailure):
                    captured["sync_once"]()
                self.assertTrue(launcher._periodic_enabled.is_set(),
                                "another reader holding the CDP lock is not a failed observation")
                writer.sync_failure = RuntimeError("synthetic unknown failure")
                with self.assertRaises(RuntimeError):
                    captured["sync_once"]()
                self.assertFalse(launcher._periodic_enabled.is_set())
                before = writer.sync_calls
                launcher._periodic_interval = 0.025
                thread = threading.Thread(target=launcher.serve_forever)
                thread.start()
                try:
                    self.assertTrue(launcher._server.started.wait(1))
                    sleep(0.11)
                    self.assertEqual(writer.sync_calls, before,
                                     "scheduled reads must not resume after manual failure")
                finally:
                    launcher.close()
                    thread.join(1)
                    self.assertFalse(thread.is_alive())
            finally:
                launcher.close()
            self.assertEqual((writer.close_calls, reader.close_calls), (1, 1))

    def test_no_polling_for_zero_interval_or_offline_owner(self):
        runtime, server, reader, writer = _runtime(lambda: self.fail("unexpected owner read"))
        runtime._periodic_sync = None
        runtime._periodic_interval = 0
        owner = threading.Thread(target=runtime.serve_forever)
        owner.start()
        try:
            self.assertTrue(server.started.wait(1))
            sleep(0.08)
        finally:
            runtime.close()
            owner.join(1)
        self.assertEqual((reader.close_calls, writer.close_calls), (1, 1))


if __name__ == "__main__":
    unittest.main()
