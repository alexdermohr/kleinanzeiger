"""Synthetic read-only polling tests; never contact a browser or marketplace."""
from __future__ import annotations

import contextlib
from dataclasses import replace
import io
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.periodic_sync import PeriodicOwnerSync, PeriodicSyncError, main
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

    def read_inventory(self):
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
            def read_inventory(self):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("synthetic blocked read")
                return super().read_inventory()

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

    def test_default_library_runtime_refuses_private_cdp(self):
        # A direct PeriodicOwnerSync consumer must not bypass the public CLI
        # restriction merely by leaving the runtime_factory unspecified.
        with self.assertRaisesRegex(
            PeriodicSyncError, "verified official platform entitlement"
        ):
            PeriodicOwnerSync(store=self.store, cdp_port=19610)
        self.assertIsNone(self.store.latest_sync_attempt())

    def test_public_cli_always_refuses_private_cdp_before_any_io(self):
        # A browser login or CLI flag is not an official platform entitlement.
        # Neither SQLite nor browser/runtime may even be constructed.
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
                contextlib.redirect_stderr(io.StringIO()) as err,
            ):
                self.assertEqual(main([
                    "--db", str(self.path), "--cdp-port", "19610", *args,
                ]), 2)
                self.assertIn("automatic Kleinanzeigen/CDP reads are disabled", err.getvalue())
                self.assertIn("verified official platform entitlement", err.getvalue())
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

    def test_cli_missing_database_is_not_created_while_platform_denied(self):
        missing = Path(self.temp.name) / "absent.sqlite"
        with contextlib.redirect_stderr(io.StringIO()) as output:
            code = main(["--db", str(missing), "--cdp-port", "19610", "--once"])
        self.assertEqual(code, 2)
        self.assertFalse(missing.exists())
        self.assertIn("reads are disabled", output.getvalue())

    def test_cli_refuses_user_supplied_entitlement_bypass(self):
        # No locally forgeable flag or mere --once acknowledgement can
        # establish the third-party account and official transport rights.
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main([
                    "--db", str(self.path), "--cdp-port", "19610",
                    "--allow-platform-reads", "--once",
                ])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()