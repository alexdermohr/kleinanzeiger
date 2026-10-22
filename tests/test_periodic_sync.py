"""Synthetic read-only polling tests; never contact a browser or marketplace."""
from __future__ import annotations

import contextlib
from dataclasses import replace
import io
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.periodic_sync import (
    PeriodicOwnerSync, PeriodicSyncError, PeriodicSyncStopped, main,
)
from mark_api.private_web_runtime import (
    PrivateWebInventoryReadCancelled, PrivateWebInventoryRuntime,
)
from mark_api.results import ReadResult, ReadStatus
from mark_api.storage import SnapshotStore


_NOW = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)


def snapshot(ad_id: str = "1234567890") -> AdSnapshot:
    return AdSnapshot(
        ad_id=ad_id,
        observed_at=_NOW,
        source="synthetic-owner",
        title="Eigene Vase",
        lifecycle_state=LifecycleState.ACTIVE,
        views=4,
    )


class Runtime:
    def __init__(self, response, *, failure=None, close_failure=False):
        self.response = response
        self.failure = failure
        self.close_failure = close_failure
        self.read_count = 0
        self.closed = False

    def read_inventory(self, *, stop_requested=None):
        if stop_requested is not None and stop_requested():
            raise PrivateWebInventoryReadCancelled("inventory read cancelled")
        self.read_count += 1
        if self.failure is not None:
            raise self.failure
        return self.response

    def close(self):
        self.closed = True
        if self.close_failure:
            raise RuntimeError("private browser close failure")


class PeriodicReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "snapshots.sqlite"
        self.store = SnapshotStore(self.path, create_if_missing=True)

    def controller(self, factory, **kwargs):
        parameters = {
            "store": self.store,
            "cdp_port": 19610,
            "interval_seconds": 60,
            "clock": lambda: _NOW,
            "runtime_factory": factory,
        }
        parameters.update(kwargs)
        return PeriodicOwnerSync(**parameters)

    def test_successful_refresh_persists_snapshot_and_journal_atomically(self):
        owner = Runtime(ReadResult.success_nonempty((snapshot(),)))
        observed = []
        sync = self.controller(
            lambda **kwargs: observed.append(kwargs) or owner,
        )
        result = sync.run_once()
        self.assertIs(result.status, ReadStatus.SUCCESS_NONEMPTY)
        self.assertEqual(owner.read_count, 1)
        self.assertTrue(owner.closed)
        self.assertEqual(observed, [{"cdp_port": 19610, "timeout_seconds": 5.0}])
        self.assertEqual(self.store.tracked_ad_ids(), ("1234567890",))
        attempt = self.store.latest_sync_attempt()
        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.source, "private-web-periodic-readonly")
        self.assertEqual(attempt.outcome, "success_nonempty")
        self.assertEqual(attempt.snapshot_count, 1)

    def test_successful_empty_inventory_marks_previously_owned_ads_absent(self):
        self.controller(lambda **_: Runtime(
            ReadResult.success_nonempty((snapshot(),))
        )).run_once()
        empty = Runtime(ReadResult.success_empty(()))
        self.controller(lambda **_: empty).run_once()
        latest = self.store.latest_sync_attempt()
        self.assertEqual(latest.outcome, "success_empty")
        self.assertEqual(latest.snapshot_count, 0)
        history = self.store.ad_history("1234567890")
        self.assertEqual(history[-1].lifecycle_state, LifecycleState.ABSENT)
        self.assertTrue(empty.closed)

    def test_absent_transition_is_not_appended_on_each_empty_poll(self):
        self.controller(lambda **_: Runtime(
            ReadResult.success_nonempty((snapshot(),))
        )).run_once()
        empty = self.controller(lambda **_: Runtime(ReadResult.success_empty(())))
        empty.run_once()
        recorded = self.store.ad_history("1234567890")
        self.assertEqual(len(recorded), 2)
        self.assertIs(recorded[-1].lifecycle_state, LifecycleState.ABSENT)
        for _ in range(4):
            empty.run_once()
        self.assertEqual(self.store.ad_history("1234567890"), recorded)
        self.assertEqual(self.store.latest_sync_attempt().outcome, "success_empty")
        self.assertEqual(self.store.tracked_ad_ids(), ("1234567890",))
        # A returned ad is present again and its next disappearance creates
        # exactly one new ABSENT transition.
        self.controller(lambda **_: Runtime(
            ReadResult.success_nonempty((snapshot(),))
        )).run_once()
        empty.run_once()
        self.assertEqual(len(self.store.ad_history("1234567890")), 4)
        self.assertIs(
            self.store.ad_history("1234567890")[-1].lifecycle_state,
            LifecycleState.ABSENT,
        )

    def test_current_state_scanned_once_not_one_history_query_per_ad(self):
        # Historical rows grow with every cycle; the scheduler should never
        # call the per-ad history/sort API for every tracked ID.
        for i in range(40):
            self.store.append_ad_snapshot(snapshot(str(1000000000 + i)))
        with patch.object(
            self.store, "latest_ad_snapshot",
            side_effect=AssertionError("per-ad history API used"),
        ):
            controller = self.controller(
                lambda **_: Runtime(ReadResult.success_empty(()))
            )
            result = controller.run_once()
        self.assertIs(result.status, ReadStatus.SUCCESS_EMPTY)
        self.assertEqual(self.store.latest_sync_attempt().snapshot_count, 0)
        self.assertEqual(len(self.store.tracked_ad_ids()), 40)

    def test_latest_state_uses_instant_not_lexical_iso_for_offset_timestamps(self):
        # The lexical '14:30+05:00' predates 10:00Z in real time, even
        # though its wall clock hour and ISO string sort later.
        self.store.append_ad_snapshot(replace(
            snapshot(),
            observed_at=datetime(
                2026, 10, 19, 14, 30, tzinfo=timezone(timedelta(hours=5))
            ),
            lifecycle_state=LifecycleState.ABSENT,
        ))
        self.store.append_ad_snapshot(replace(
            snapshot(),
            observed_at=datetime(2026, 10, 19, 10, tzinfo=timezone.utc),
            lifecycle_state=LifecycleState.ACTIVE,
        ))
        sync = self.controller(lambda **_: Runtime(ReadResult.success_empty(())))
        sync.run_once()
        history = self.store.ad_history("1234567890")
        self.assertEqual(len(history), 3)
        self.assertEqual(history[-1].lifecycle_state, LifecycleState.ABSENT)
        sync.run_once()
        self.assertEqual(len(self.store.ad_history("1234567890")), 3)

    def test_later_absent_instant_avoids_duplicate_despite_earlier_local_hour(self):
        # Real time says 09:30Z ABSENT is later than 14:00+05 (09:00Z)
        # ACTIVE; lexical ISO sorting claims the opposite.
        self.store.append_ad_snapshot(replace(
            snapshot(),
            observed_at=datetime(
                2026, 10, 19, 14, tzinfo=timezone(timedelta(hours=5))
            ),
            lifecycle_state=LifecycleState.ACTIVE,
        ))
        self.store.append_ad_snapshot(replace(
            snapshot(),
            observed_at=datetime(2026, 10, 19, 9, 30, tzinfo=timezone.utc),
            lifecycle_state=LifecycleState.ABSENT,
        ))
        self.controller(lambda **_: Runtime(ReadResult.success_empty(()))).run_once()
        self.assertEqual(len(self.store.ad_history("1234567890")), 2)
        self.assertEqual(self.store.latest_sync_attempt().outcome, "success_empty")

    def test_same_second_microseconds_respected_for_latest_owner_state(self):
        self.store.append_ad_snapshot(replace(
            snapshot(),
            observed_at=datetime(2026, 10, 19, 10, 0, 0, 201, timezone.utc),
            lifecycle_state=LifecycleState.ABSENT,
        ))
        self.store.append_ad_snapshot(replace(
            snapshot(),
            observed_at=datetime(2026, 10, 19, 10, 0, 0, 202, timezone.utc),
            lifecycle_state=LifecycleState.ACTIVE,
        ))
        self.controller(lambda **_: Runtime(ReadResult.success_empty(()))).run_once()
        self.assertEqual(len(self.store.ad_history("1234567890")), 3)


    def test_corrupt_sqlite_owner_history_never_journals_or_opens_browser(self):
        # Native TEXT affinity still permits BLOB values. Coercing bytes
        # to str invents a fake owner identity and a false successful
        # ABSENT observation. Each corrupt case uses its own temporary
        # source to keep actual RED writes from masking later failures.
        cases = (
            ("ad_id", sqlite3.Binary(b"forged-native-blob")),
            ("ad_id", ""),
            ("ad_id", " \t\u2003 "),
            ("observed_at", sqlite3.Binary(b"2026-10-19T10:00:00+00:00")),
            ("observed_at", "not-an-iso-timestamp"),
            ("observed_at", "2026-10-19T10:00:00"),
            ("lifecycle_state", sqlite3.Binary(b"active")),
            ("lifecycle_state", "unrecognized-state"),
        )
        for column, invalid in cases:
            with (
                self.subTest(column=column, native=type(invalid).__name__, value=repr(invalid)),
                tempfile.TemporaryDirectory(prefix="mark-sync-corrupt-") as temp,
            ):
                path = Path(temp) / "synthetic.sqlite"
                store = SnapshotStore(path, create_if_missing=True)
                store.append_ad_snapshot(snapshot())
                with contextlib.closing(sqlite3.connect(path)) as db, db:
                    db.execute(
                        f"UPDATE ad_snapshots SET {column} = ? WHERE id = 1",
                        (invalid,),
                    )
                calls = []
                controller = PeriodicOwnerSync(
                    store=store,
                    cdp_port=19610,
                    interval_seconds=60,
                    clock=lambda: _NOW,
                    runtime_factory=lambda **kw: calls.append(kw) or Runtime(
                        ReadResult.success_empty(())
                    ),
                )
                with self.assertRaisesRegex(
                    PeriodicSyncError, "local sync journal unavailable"
                ) as failed:
                    controller.run_once()
                self.assertNotIn("forged-native-blob", str(failed.exception))
                self.assertEqual(calls, [], "browser factory ran on corrupt source")
                self.assertIsNone(
                    store.latest_sync_attempt(),
                    "corrupt owner source created misleading sync journal",
                )
                with contextlib.closing(sqlite3.connect(path)) as db:
                    self.assertEqual(
                        db.execute("SELECT COUNT(*) FROM ad_snapshots").fetchone(),
                        (1,),
                    )

    def test_native_known_owner_states_keep_existing_semantics(self):
        # Type/enum corruption checks must not collapse legitimately
        # recorded UNKNOWN, PAUSED or ABSENT states into a failure.
        for state in LifecycleState:
            with (
                self.subTest(state=state.value),
                tempfile.TemporaryDirectory(prefix="mark-sync-valid-") as temp,
            ):
                store = SnapshotStore(Path(temp) / "synthetic.sqlite")
                store.append_ad_snapshot(replace(
                    snapshot(), lifecycle_state=state,
                ))
                sync = PeriodicOwnerSync(
                    store=store, cdp_port=19610,
                    runtime_factory=lambda **_: Runtime(ReadResult.success_empty(())),
                )
                self.assertEqual(
                    sync._tracked_present_ids(),
                    () if state is LifecycleState.ABSENT else ("1234567890",),
                )
                self.assertIsNone(store.latest_sync_attempt())

    def test_failure_settlement_sqlite_loss_preserves_bounded_error_and_unknown_outcome(self):
        # A storage failure after the attempt starts can prevent its failure
        # CAS too. A raw sqlite exception must never replace the safe error,
        # and an in_progress attempt must not be reclassified as success.
        sync = self.controller(lambda **_: Runtime(ReadResult.success_empty(())))
        with (
            patch.object(
                self.store, "append_inventory_result",
                side_effect=sqlite3.OperationalError("SECRET_DATABASE_PATH"),
            ),
            patch.object(
                self.store, "fail_sync_attempt",
                side_effect=sqlite3.OperationalError("SECRET_DATABASE_PATH"),
            ),
        ):
            with self.assertRaisesRegex(PeriodicSyncError, "persistence failed") as cm:
                sync.run_once()
        self.assertNotIn("SECRET_DATABASE_PATH", str(cm.exception))
        self.assertEqual(self.store.latest_sync_attempt().outcome, "in_progress")

    def test_http_auth_and_network_failures_keep_old_observations(self):
        self.controller(lambda **_: Runtime(
            ReadResult.success_nonempty((snapshot(),))
        )).run_once()
        prior = self.store.ad_history("1234567890")
        for status in (ReadStatus.UNAUTHENTICATED, ReadStatus.TRANSPORT_ERROR):
            with self.subTest(status=status):
                failure = Runtime(ReadResult.failure(status, error="synthetic"))
                result = self.controller(lambda **_: failure).run_once()
                self.assertIs(result.status, status)
                self.assertEqual(self.store.ad_history("1234567890"), prior)
                self.assertEqual(self.store.latest_sync_attempt().outcome, "failed")
                self.assertTrue(failure.closed)
        self.assertEqual(
            self.store.last_successful_sync_attempt().outcome,
            "success_nonempty",
        )

    def test_runtime_failure_and_reader_exception_are_durably_journaled(self):
        def fail_start(**_kwargs):
            raise RuntimeError("SECRET_BROWSER_HEADER")

        with self.assertRaisesRegex(PeriodicSyncError, "runtime unavailable"):
            self.controller(fail_start).run_once()
        self.assertEqual(self.store.latest_sync_attempt().error_kind, "runtime_unavailable")
        failure = Runtime(None, failure=RuntimeError("PRIVATE_COOKIE_VALUE"))
        with self.assertRaisesRegex(PeriodicSyncError, "read failed") as caught:
            self.controller(lambda **_: failure).run_once()
        self.assertNotIn("PRIVATE_COOKIE_VALUE", str(caught.exception))
        self.assertTrue(failure.closed)
        self.assertEqual(self.store.latest_sync_attempt().error_kind, "reader_exception")

    def test_unexpected_owner_result_is_rejected_before_snapshot_change(self):
        runtime = Runtime({"not": "ReadResult"})
        with self.assertRaisesRegex(PeriodicSyncError, "result invalid"):
            self.controller(lambda **_: runtime).run_once()
        self.assertEqual(self.store.tracked_ad_ids(), ())
        self.assertEqual(self.store.latest_sync_attempt().error_kind, "invalid_inventory")
        self.assertTrue(runtime.closed)

    def test_lazy_or_mutable_inventory_is_rejected_without_consumption(self):
        consumed = []
        def lazy():
            consumed.append("ran")
            yield snapshot()
        for items in ([snapshot()], lazy()):
            with self.subTest(kind=type(items).__name__):
                reader = Runtime(ReadResult.success_nonempty(items))
                with self.assertRaisesRegex(PeriodicSyncError, "result invalid"):
                    self.controller(lambda **_: reader).run_once()
                self.assertEqual(self.store.latest_sync_attempt().error_kind, "invalid_inventory")
                self.assertEqual(self.store.tracked_ad_ids(), ())
                self.assertTrue(reader.closed)
        self.assertEqual(consumed, [])

    def test_close_error_does_not_downgrade_already_committed_success(self):
        runtime = Runtime(ReadResult.success_nonempty((snapshot(),)), close_failure=True)
        with self.assertRaisesRegex(PeriodicSyncError, "close failed"):
            self.controller(lambda **_: runtime).run_once()
        self.assertEqual(self.store.latest_sync_attempt().outcome, "success_nonempty")
        self.assertEqual(self.store.tracked_ad_ids(), ("1234567890",))

    def test_concurrent_reads_are_denied_and_never_start_second_cycle(self):
        entered = threading.Event()
        release = threading.Event()

        class PausedRuntime(Runtime):
            def read_inventory(self, *, stop_requested=None):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("synthetic blocked read")
                return super().read_inventory(stop_requested=stop_requested)

        runtime = PausedRuntime(ReadResult.success_empty(()))
        calls = []
        sync = self.controller(lambda **kw: calls.append(kw) or runtime)
        outcomes = []
        worker = threading.Thread(target=lambda: outcomes.append(sync.run_once()))
        worker.start()
        self.assertTrue(entered.wait(2))
        try:
            with self.assertRaisesRegex(PeriodicSyncError, "already in progress"):
                sync.run_once()
        finally:
            release.set()
            worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(self.store.latest_sync_attempt().outcome, "success_empty")

    def test_stop_signal_prevents_new_cycle(self):
        event = threading.Event()
        event.set()
        runtime = Runtime(ReadResult.success_empty(()))
        sync = self.controller(lambda **_: runtime, stop_event=event)
        with self.assertRaisesRegex(PeriodicSyncError, "stopped"):
            sync.run_once()
        self.assertEqual(runtime.read_count, 0)
        self.assertIsNone(self.store.latest_sync_attempt())
        self.assertEqual(sync.run_loop(), 0)

    def test_signal_racing_the_next_cycle_is_a_normal_shutdown(self):
        calls = []
        sync = self.controller(lambda **_: calls.append(1) or Runtime(
            ReadResult.success_empty(())
        ))
        original = sync.run_once
        def stop_at_cycle_boundary():
            sync.stop_event.set()
            return original()
        sync.run_once = stop_at_cycle_boundary
        self.assertEqual(sync.run_loop(immediate=True), 0)
        self.assertEqual(calls, [])
        self.assertIsNone(self.store.latest_sync_attempt())

    def test_any_failed_poll_halts_without_login_redirect_retries(self):
        # The real CDP reader can classify a login redirect as transport error.
        # Neither classification may cause automated login-page probes.
        for status in (ReadStatus.UNAUTHENTICATED, ReadStatus.TRANSPORT_ERROR):
            with self.subTest(status=status):
                calls = []
                sync = self.controller(
                    lambda **kw: calls.append(kw) or Runtime(
                        ReadResult.failure(status, http_status=403)
                    )
                )
                with self.assertRaisesRegex(PeriodicSyncError, "polling stopped"):
                    sync.run_loop(immediate=True)
                self.assertEqual(len(calls), 1)
                self.assertEqual(self.store.latest_sync_attempt().error_kind, status.value)

    def test_default_library_runtime_denies_private_cdp(self):
        # A caller cannot grant permission simply by leaving the runtime
        # factory unspecified or possessing an authenticated CDP browser.
        with self.assertRaisesRegex(
            PeriodicSyncError, "verified official platform entitlement"
        ):
            PeriodicOwnerSync(store=self.store, cdp_port=19610)
        self.assertIsNone(self.store.latest_sync_attempt())

    def test_public_cli_denies_before_database_or_browser_access(self):
        for args in (
            ["--once"],
            ["--immediate"],
            ["--interval-seconds", "60"],
        ):
            with (
                self.subTest(args=args),
                patch("mark_api.periodic_sync.SnapshotStore") as store,
                patch("mark_api.periodic_sync.PeriodicOwnerSync") as factory,
                patch("mark_api.periodic_sync.build_private_web_inventory_runtime") as cdp,
                contextlib.redirect_stderr(io.StringIO()) as errors,
            ):
                self.assertEqual(main([
                    "--db", str(self.path), "--cdp-port", "19610", *args
                ]), 2)
                self.assertIn("automatic Kleinanzeigen/CDP reads are disabled", errors.getvalue())
                self.assertIn("verified official platform entitlement", errors.getvalue())
                store.assert_not_called()
                factory.assert_not_called()
                cdp.assert_not_called()

    def test_invalid_period_and_port_fail_before_network_or_journal(self):
        runtime = Runtime(ReadResult.success_empty(()))
        for interval in (0, 59.9, float("nan"), float("inf"), 86401, True):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                self.controller(lambda **_: runtime, interval_seconds=interval)
        for port in (0, 65536, -1, True, "9222"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                PeriodicOwnerSync(store=self.store, cdp_port=port)
        self.assertEqual(runtime.read_count, 0)
        self.assertIsNone(self.store.latest_sync_attempt())

    def test_cli_missing_database_is_not_created_when_denied(self):
        missing = Path(self.temp.name) / "absent.sqlite"
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main([
                "--db", str(missing), "--cdp-port", "19610", "--once"
            ]), 2)
        self.assertFalse(missing.exists())
        self.assertIn("reads are disabled", errors.getvalue())

    def test_cli_does_not_accept_forgeable_entitlement_flag(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main([
                    "--db", str(self.path), "--cdp-port", "19610",
                    "--allow-platform-reads", "--once",
                ])
        self.assertEqual(caught.exception.code, 2)
        self.assertIsNone(self.store.latest_sync_attempt())

    def test_signal_stop_during_history_scan_prevents_journal_and_browser(self):
        calls = []
        sync = self.controller(lambda **_: calls.append(1) or Runtime(
            ReadResult.success_empty(())
        ))
        def scan_and_stop():
            sync.request_stop_from_signal()
            return ()
        with patch.object(sync, "_tracked_present_ids", side_effect=scan_and_stop):
            with self.assertRaises(PeriodicSyncStopped):
                sync.run_once()
        self.assertEqual(calls, [])
        self.assertIsNone(self.store.latest_sync_attempt())

    def test_signal_stop_during_sqlite_begin_records_failed_no_browser_attempt(self):
        calls = []
        sync = self.controller(lambda **_: calls.append(1) or Runtime(
            ReadResult.success_empty(())
        ))
        original = self.store.begin_sync_attempt

        def begin_and_stop(**kwargs):
            attempt = original(**kwargs)
            sync.request_stop_from_signal()
            return attempt

        with patch.object(self.store, "begin_sync_attempt", side_effect=begin_and_stop):
            with self.assertRaises(PeriodicSyncStopped):
                sync.run_once()
        self.assertEqual(calls, [])
        last = self.store.latest_sync_attempt()
        self.assertEqual(last.outcome, "failed")
        self.assertEqual(last.error_kind, "stopped_before_runtime")

    def test_signal_stop_after_runtime_creation_skips_read_and_closes_runtime(self):
        runtime = Runtime(ReadResult.success_empty(()))
        def construct(**_):
            sync.request_stop_from_signal()
            return runtime
        sync = self.controller(construct)
        with self.assertRaises(PeriodicSyncStopped):
            sync.run_once()
        self.assertEqual(runtime.read_count, 0)
        self.assertTrue(runtime.closed)
        attempt = self.store.latest_sync_attempt()
        self.assertEqual(attempt.outcome, "failed")
        self.assertEqual(attempt.error_kind, "stopped_before_read")

    def test_failed_cancel_settlement_is_not_clean_shutdown(self):
        for stop_point in ("before_runtime", "before_read"):
            with self.subTest(stop_point=stop_point):
                runtime = Runtime(ReadResult.success_empty(()))
                if stop_point == "before_runtime":
                    original = self.store.begin_sync_attempt
                    def begin_and_stop(**kwargs):
                        attempt = original(**kwargs)
                        sync.request_stop_from_signal()
                        return attempt
                    sync = self.controller(lambda **_: runtime)
                    setup = patch.object(self.store, "begin_sync_attempt", side_effect=begin_and_stop)
                else:
                    def build_and_stop(**_):
                        sync.request_stop_from_signal()
                        return runtime
                    sync = self.controller(build_and_stop)
                    setup = contextlib.nullcontext()
                with (
                    setup,
                    patch.object(self.store, "fail_sync_attempt", side_effect=sqlite3.OperationalError("PRIVATE_PATH")),
                ):
                    with self.assertRaisesRegex(PeriodicSyncError, "settlement failed") as caught:
                        sync.run_loop(immediate=True)
                self.assertNotIn("PRIVATE_PATH", str(caught.exception))
                self.assertEqual(runtime.read_count, 0)
                self.assertEqual(self.store.latest_sync_attempt().outcome, "in_progress")

    def test_real_sigterm_during_cdp_call_aborts_without_next_navigation(self):
        if not hasattr(signal, "pthread_sigmask"):
            self.skipTest("Linux/POSIX signal mask required")
        from mark_api.private_web_runtime import build_private_web_inventory_runtime
        calls = []
        class FakeCdp:
            def __init__(self):
                self.closed = False
                self.send_guard = None
            def bind_cancellable_reads(self, *, stop_requested, send_guard):
                self.send_guard = send_guard
            def call(self, method, params=None):
                if self.send_guard is None:
                    raise AssertionError("signal-safe send guard was not installed")
                with self.send_guard():
                    calls.append(method)
                    if len(calls) == 1:
                        signal.raise_signal(signal.SIGTERM)
                        return {"result": {"value": True}}
                    raise AssertionError("no CDP call allowed after SIGTERM")
            def close(self):
                self.closed = True
        cdp = FakeCdp()
        sync = self.controller(
            lambda **kw: build_private_web_inventory_runtime(**kw),
        )
        original = signal.signal(
            signal.SIGTERM,
            lambda *_: sync.request_stop_from_signal(),
        )
        try:
            with (
                patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"),
                patch("mark_api.private_web_inventory_cdp._LoopbackCdpClient", return_value=cdp),
                patch("mark_api.private_web_inventory_cdp._require_private_web_cdp_authority"),
            ):
                with self.assertRaises(PeriodicSyncStopped):
                    sync.run_once()
        finally:
            signal.signal(signal.SIGTERM, original)
        self.assertEqual(calls, ["Runtime.evaluate"])
        self.assertTrue(cdp.closed)
        self.assertTrue(sync._stopping())
        attempt = self.store.latest_sync_attempt()
        self.assertEqual(attempt.outcome, "failed")
        self.assertEqual(attempt.error_kind, "stopped_before_read")

    def test_stop_after_outer_check_before_runtime_entry_prevents_browser_read(self):
        # A signal after PeriodicOwnerSync's last check but immediately
        # before the real runtime read must not start a browser operation.
        owner_reader = Mock()
        owner_reader.read_ads.return_value = ReadResult.success_empty(())
        closed = []
        class SignalAtEntry(PrivateWebInventoryRuntime):
            def read_inventory(self, *, stop_requested=None):
                sync.request_stop_from_signal()
                return super().read_inventory(stop_requested=stop_requested)
        runtime = SignalAtEntry(
            owner_reader=owner_reader, close_runtime=lambda: closed.append(True),
        )
        sync = self.controller(lambda **_: runtime)
        with self.assertRaises(PeriodicSyncStopped):
            sync.run_once()
        owner_reader.read_ads.assert_not_called()
        self.assertEqual(closed, [True])
        attempt = self.store.latest_sync_attempt()
        self.assertEqual(attempt.outcome, "failed")
        self.assertEqual(attempt.error_kind, "stopped_before_read")

    def test_cancelled_runtime_settlement_failure_is_not_clean_shutdown(self):
        owner_reader = Mock()
        class SignalAtEntry(PrivateWebInventoryRuntime):
            def read_inventory(self, *, stop_requested=None):
                sync.request_stop_from_signal()
                return super().read_inventory(stop_requested=stop_requested)
        runtime = SignalAtEntry(owner_reader=owner_reader, close_runtime=lambda: None)
        sync = self.controller(lambda **_: runtime)
        with patch.object(
            self.store, "fail_sync_attempt",
            side_effect=sqlite3.OperationalError("PRIVATE_PATH"),
        ):
            with self.assertRaisesRegex(
                PeriodicSyncError, "cancellation settlement failed",
            ) as caught:
                sync.run_loop(immediate=True)
        owner_reader.read_ads.assert_not_called()
        self.assertNotIn("PRIVATE_PATH", str(caught.exception))
        self.assertEqual(self.store.latest_sync_attempt().outcome, "in_progress")

    def test_cancellation_setup_failure_is_not_a_clean_shutdown(self):
        # A platform/thread lacking the required atomic CDP admission may
        # not look like a SIGTERM-requested graceful stop with exit status 0.
        from mark_api.private_web_inventory_cdp import PrivateWebInventoryReadSetupError
        class UnavailableRuntime:
            def __init__(self):
                self.closed = False
            def read_inventory(self, *, stop_requested=None):
                raise PrivateWebInventoryReadSetupError("signal-safe transport unavailable")
            def close(self):
                self.closed = True
        runtime = UnavailableRuntime()
        sync = self.controller(lambda **_: runtime)
        with self.assertRaisesRegex(PeriodicSyncError, "browser inventory read failed"):
            sync.run_loop(immediate=True)
        self.assertTrue(runtime.closed)
        self.assertEqual(self.store.latest_sync_attempt().outcome, "failed")
        self.assertEqual(self.store.latest_sync_attempt().error_kind, "reader_exception")

    def test_runtime_without_cancellation_entry_fails_closed(self):
        class LegacyRuntime:
            def __init__(self):
                self.read_count = 0
                self.closed = False
            def read_inventory(self):
                self.read_count += 1
                return ReadResult.success_empty(())
            def close(self):
                self.closed = True
        runtime = LegacyRuntime()
        with self.assertRaisesRegex(PeriodicSyncError, "browser inventory read failed"):
            self.controller(lambda **_: runtime).run_once()
        self.assertEqual(runtime.read_count, 0)
        self.assertTrue(runtime.closed)
        self.assertEqual(self.store.latest_sync_attempt().error_kind, "reader_exception")

    def test_signal_request_does_not_take_event_condition_lock(self):
        # Run in a subprocess because a regression to Event.set() inside the
        # lock would deadlock. Timeout protects the test suite itself.
        script = """
import signal, tempfile
from pathlib import Path
from mark_api.periodic_sync import PeriodicOwnerSync
from mark_api.storage import SnapshotStore
with tempfile.TemporaryDirectory() as tmp:
    store = SnapshotStore(Path(tmp) / "db.sqlite", create_if_missing=True)
    sync = PeriodicOwnerSync(
        store=store, cdp_port=9222, interval_seconds=60,
        runtime_factory=lambda **_: (_ for _ in ()).throw(AssertionError("no browser allowed")),
    )
    old = signal.signal(signal.SIGTERM,
                        lambda _signal, _frame: sync.request_stop_from_signal())
    try:
        with sync.stop_event._cond:
            signal.raise_signal(signal.SIGTERM)
        assert sync.run_loop() == 0
        assert store.latest_sync_attempt() is None
    finally:
        signal.signal(signal.SIGTERM, old)
print("SIGTERM under Event condition lock returned safely")
"""
        repo = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo / "src")
        child = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo, env=env, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertIn("returned safely", child.stdout)

    def test_signal_stop_wakes_scheduler_with_bounded_wait(self):
        sync = self.controller(lambda **_: (_ for _ in ()).throw(
            AssertionError("no browser permitted")
        ))
        observed = []
        def stop_on_first_wait(timeout):
            observed.append(timeout)
            sync.request_stop_from_signal()
            return False
        with patch.object(sync.stop_event, "wait", side_effect=stop_on_first_wait):
            self.assertEqual(sync.run_loop(), 0)
        self.assertEqual(len(observed), 1)
        self.assertGreater(observed[0], 0)
        self.assertLessEqual(observed[0], 0.25)
        self.assertIsNone(self.store.latest_sync_attempt())

    def test_denied_public_cli_does_not_install_signal_handlers(self):
        # Signal handling remains covered by synthetic run_once / run_loop
        # tests, but no public CDP CLI may initialize a session or handler.
        with (
            patch("signal.signal") as install,
            patch("mark_api.periodic_sync.PeriodicOwnerSync") as builder,
            patch("mark_api.periodic_sync.SnapshotStore") as storage,
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            self.assertEqual(main([
                "--db", str(self.path), "--cdp-port", "19610",
            ]), 2)
            self.assertIn("reads are disabled", errors.getvalue())
            install.assert_not_called()
            builder.assert_not_called()
            storage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
