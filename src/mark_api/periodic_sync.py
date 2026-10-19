"""Bounded, read-only owner inventory polling; no platform writer or retry authority.

This is deliberately separate from the product launcher and its platform
writes. Invoke it only as the sole CDP inventory owner, with no concurrent
browser writer sharing that session. Its scheduler never authorizes writes.
"""
from __future__ import annotations

import argparse
import math
import signal
import sys
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from typing import Protocol

from .domain import AdSnapshot, LifecycleState
from .private_web_runtime import build_private_web_inventory_runtime
from .results import ReadResult, ReadStatus
from .storage import SnapshotStore


_SOURCE = "private-web-periodic-readonly"
_MIN_INTERVAL_SECONDS = 60.0
_MAX_INTERVAL_SECONDS = 24.0 * 60.0 * 60.0
_SIGNAL_STOP_POLL_SECONDS = 0.25


class PeriodicSyncError(RuntimeError):
    """Bounded public error; no credentials, browser output, or raw provider data."""


class PeriodicSyncStopped(PeriodicSyncError):
    """A requested stop was observed before a new read began."""


class InventoryRuntime(Protocol):
    def read_inventory(self) -> ReadResult[tuple[AdSnapshot, ...]]: ...
    def close(self) -> None: ...


class PeriodicOwnerSync:
    """Durable per-cycle journal with no owned writer or platform mutation path.

    A cycle begins with an in_progress SQLite record BEFORE the CDP read.
    Its terminal success is committed with the observed inventory and ABSENT
    transitions in one transaction. Errors never erase old observations.
    There is only one in-flight read for this instance; intervals start after
    the previous cycle ends, avoiding bursts after a slow response.
    """

    def __init__(
        self,
        *,
        store: SnapshotStore,
        cdp_port: int,
        interval_seconds: float = 300.0,
        timeout_seconds: float = 5.0,
        runtime_factory: Callable[..., InventoryRuntime] = build_private_web_inventory_runtime,
        clock: Callable[[], datetime] | None = None,
        stop_event: Event | None = None,
    ) -> None:
        if not isinstance(store, SnapshotStore):
            raise TypeError("store must be SnapshotStore")
        if type(cdp_port) is not int or not 1 <= cdp_port <= 65535:
            raise ValueError("cdp_port must be a valid local CDP port")
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or not math.isfinite(float(interval_seconds))
            or not _MIN_INTERVAL_SECONDS <= float(interval_seconds) <= _MAX_INTERVAL_SECONDS
        ):
            raise ValueError("poll interval must be between 60 and 86400 seconds")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or not 0 < float(timeout_seconds) <= 30
        ):
            raise ValueError("timeout_seconds must be finite and in (0, 30]")
        if not callable(runtime_factory) or (clock is not None and not callable(clock)):
            raise TypeError("runtime_factory and clock must be callable")
        if stop_event is not None and not isinstance(stop_event, Event):
            raise TypeError("stop_event must be Event")
        self._store = store
        self._cdp_port = cdp_port
        self._interval = float(interval_seconds)
        self._timeout = float(timeout_seconds)
        self._runtime_factory = runtime_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._stop = stop_event or Event()
        # A Python signal may run while Event.wait() holds the condition lock.
        # Signal handlers must not call Event.set(), which takes that lock.
        self._signal_stop_requested = False
        self._cycle_lock = Lock()

    def request_stop_from_signal(self) -> None:
        """Signal-handler-safe flag store; no Event/SQLite/browser locks."""
        self._signal_stop_requested = True

    def _stopping(self) -> bool:
        return self._signal_stop_requested or self._stop.is_set()

    def _wait_for_interval_or_stop(self) -> bool:
        # Bounded Event.wait permits prompt signal-only cancellation without
        # calling Event.set from the handler. The deadline preserves the full
        # configured interval and avoids a catch-up poll after early wakes.
        deadline = monotonic() + self._interval
        while not self._stopping():
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            self._stop.wait(min(remaining, _SIGNAL_STOP_POLL_SECONDS))
        return self._stopping()

    @property
    def stop_event(self) -> Event:
        return self._stop

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise PeriodicSyncError("invalid sync clock")
        if value.utcoffset() is None:
            raise PeriodicSyncError("invalid sync clock")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _valid_result(result: object) -> bool:
        if not isinstance(result, ReadResult):
            return False
        if not result.is_success:
            return True
        # Contract is an eagerly materialized immutable owner snapshot tuple.
        # Do not consume lazy generators once here and a second time in the
        # persistence layer: that could incorrectly assert success_empty.
        if type(result.value) is not tuple:
            return False
        entries = result.value
        if result.status is ReadStatus.SUCCESS_EMPTY and entries:
            return False
        if result.status is ReadStatus.SUCCESS_NONEMPTY and not entries:
            return False
        return (
            all(isinstance(item, AdSnapshot) for item in entries)
            and len({item.ad_id for item in entries}) == len(entries)
        )

    def _mark_failed(self, attempt: int, *, error_kind: str) -> bool:
        # CAS fails on an already committed attempt, rather than rewriting a
        # successful outcome after an ambiguous SQLite transaction. If SQLite
        # itself is unavailable, retain the original bounded operational
        # error; the attempt remains in_progress/unknown, NEVER success.
        try:
            self._store.fail_sync_attempt(
                attempt, source=_SOURCE, completed_at=self._now(),
                error_kind=error_kind,
            )
        except Exception:
            return False
        return True

    def _tracked_present_ids(self) -> tuple[str, ...]:
        # Store permits timezone-aware timestamps with different offsets.
        # SQLite's lexical ordering of their original ISO strings is NOT a
        # chronological ordering; SQLite julianday/strftime also lose
        # microsecond precision. Stream all observations through ONE
        # non-creating, read-only SQLite connection, retaining just one
        # (UTC instant, row ID, state) per ad. Exact datetime comparisons
        # match SnapshotStore.ad_history's timeline ordering, without one
        # separate connection/history sort per ad. Memory is O(unique ads).
        newest: dict[str, tuple[datetime, int, str]] = {}
        with closing(self._store._connect()) as connection:
            connection.execute("PRAGMA query_only=ON")
            cursor = connection.execute(
                "SELECT id, ad_id, observed_at, lifecycle_state FROM ad_snapshots"
            )
            for row in cursor:
                observed = datetime.fromisoformat(str(row["observed_at"]))
                if observed.tzinfo is None or observed.utcoffset() is None:
                    raise ValueError("invalid stored observation timezone")
                instant = observed.astimezone(timezone.utc)
                ad_id = str(row["ad_id"])
                ordinal = int(row["id"])
                former = newest.get(ad_id)
                if former is None or (instant, ordinal) > (former[0], former[1]):
                    newest[ad_id] = (instant, ordinal, str(row["lifecycle_state"]))
        return tuple(
            sorted(
                ad_id for ad_id, (_, _, state) in newest.items()
                if state != LifecycleState.ABSENT.value
            )
        )

    def run_once(self) -> ReadResult[tuple[AdSnapshot, ...]]:
        if self._stopping():
            raise PeriodicSyncStopped("sync is stopped")
        if not self._cycle_lock.acquire(blocking=False):
            raise PeriodicSyncError("inventory read already in progress")
        try:
            # Snapshot existing owner IDs before any network access.
            try:
                # Track only ads whose LATEST state is not ABSENT. Historical
                # IDs remain in storage for analytics without duplicate
                # ABSENT rows on every subsequent successful poll.
                tracked = self._tracked_present_ids()
                if self._stopping():
                    raise PeriodicSyncStopped("sync is stopped before journal")
                attempt = self._store.begin_sync_attempt(
                    source=_SOURCE, started_at=self._now(),
                )
            except PeriodicSyncStopped:
                raise
            except Exception:
                raise PeriodicSyncError("local sync journal unavailable") from None

            reader: InventoryRuntime | None = None
            try:
                if self._stopping():
                    self._mark_failed(attempt, error_kind="stopped_before_runtime")
                    raise PeriodicSyncStopped("sync is stopped before browser")
                try:
                    reader = self._runtime_factory(
                        cdp_port=self._cdp_port, timeout_seconds=self._timeout,
                    )
                except Exception:
                    self._mark_failed(attempt, error_kind="runtime_unavailable")
                    raise PeriodicSyncError("browser inventory runtime unavailable") from None
                if self._stopping():
                    self._mark_failed(attempt, error_kind="stopped_before_read")
                    raise PeriodicSyncStopped("sync is stopped before read")
                try:
                    result = reader.read_inventory()
                except Exception:
                    self._mark_failed(attempt, error_kind="reader_exception")
                    raise PeriodicSyncError("browser inventory read failed") from None
                if not self._valid_result(result):
                    self._mark_failed(attempt, error_kind="invalid_inventory")
                    raise PeriodicSyncError("browser inventory result invalid")
                try:
                    self._store.append_inventory_result(
                        result,
                        tracked_ad_ids=tracked,
                        observed_at=self._now(),
                        source=_SOURCE,
                        attempt_id=attempt,
                        completed_at=self._now(),
                    )
                except Exception:
                    # An unknown SQLite commit stays unresolved if the
                    # fail-sync CAS cannot prove it is still in_progress.
                    self._mark_failed(attempt, error_kind="persistence_error")
                    raise PeriodicSyncError("local sync persistence failed") from None
                return result
            finally:
                if reader is not None:
                    try:
                        reader.close()
                    except Exception:
                        # Already persisted evidence remains. The caller must
                        # stop rather than claim a clean runtime shutdown.
                        raise PeriodicSyncError("browser inventory runtime close failed") from None
        finally:
            self._cycle_lock.release()

    def run_loop(self, *, immediate: bool = False) -> int:
        """Return successful cycle count; ANY failed read halts polling.

        A login redirect currently may be classified as TRANSPORT_ERROR rather
        than UNAUTHENTICATED by the lower-level CDP adapter. Never retry an
        ambiguous login/transport state periodically; require an explicit new
        process start after operator investigation. No catch-up storm.
        """
        if not isinstance(immediate, bool):
            raise TypeError("immediate must be bool")
        success_count = 0
        due = immediate
        while not self._stopping():
            if not due and self._wait_for_interval_or_stop():
                break
            if self._stopping():
                break
            try:
                result = self.run_once()
            except PeriodicSyncStopped:
                # SIGTERM may win the race between the scheduler's stop
                # check and run_once's own check. It isn't a failed read.
                break
            if result.is_success:
                success_count += 1
            else:
                # Unexpected login redirects can surface as transport errors;
                # don't continuously re-open the protected management page.
                # Nonzero CLI exit also ensures process monitors don't record
                # a clean polling shutdown after failed observations.
                raise PeriodicSyncError("inventory read unavailable; polling stopped")
            due = False
        return success_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Explicit read-only periodic owner inventory from the already-"
            "authenticated loopback Chrome CDP. Run only with no concurrent "
            "writer using the same browser session."
        ),
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--cdp-port", type=int, required=True)
    parser.add_argument(
        "--interval-seconds", type=float, default=300.0,
        help="Read-only interval after previous cycle completes (60..86400).",
    )
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--once", action="store_true", help="One explicit read now.")
    parser.add_argument(
        "--immediate", action="store_true",
        help="Begin with a read, then repeat after the chosen interval.",
    )
    args = parser.parse_args(argv)
    if args.once and args.immediate:
        parser.error("--once and --immediate are mutually exclusive")
    try:
        # Never create a new SQLite database in a syncing process.
        store = SnapshotStore(args.db, create_if_missing=False)
        store.tracked_ad_ids()
        synchronizer = PeriodicOwnerSync(
            store=store,
            cdp_port=args.cdp_port,
            interval_seconds=args.interval_seconds,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception:
        print("mark-api-sync: invalid or unavailable local configuration", file=sys.stderr)
        return 2
    if args.once:
        try:
            result = synchronizer.run_once()
        except PeriodicSyncError:
            print("mark-api-sync: inventory cycle failed", file=sys.stderr)
            return 2
        if not result.is_success:
            print("mark-api-sync: inventory unavailable; old data preserved", file=sys.stderr)
            return 2
        print("mark-api-sync: owner inventory recorded")
        return 0
    old_handlers: dict[int, object] = {}
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda _signal, _frame: synchronizer.request_stop_from_signal())
        # SIGTERM cancels future cycles; any active read completes or leaves
        # durable in_progress evidence if the process is forcibly killed.
        synchronizer.run_loop(immediate=args.immediate)
    except Exception:
        print("mark-api-sync: periodic read halted; inspect SQLite sync journal", file=sys.stderr)
        return 2
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())