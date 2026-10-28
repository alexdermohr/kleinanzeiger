"""Local customer work after a settled browser read failure; no platform writer."""
from __future__ import annotations

from threading import Event, Lock

from .periodic_sync import PeriodicOwnerSync
from .storage import SnapshotStore
from .write_api import acquire_write_api_store_lock


class LocalWorkspaceSession:
    """Keep one DB owner and serialize explicit, read-only reconnection attempts."""

    def __init__(self, *, store, cdp_port, timeout_seconds, runtime_factory, clock):
        self._store = store
        self._operation_lock = Lock()
        self._stop = Event()
        self._closed = False
        self._store_lock = acquire_write_api_store_lock(store)
        try:
            if not store.is_ready() or store.sync_recovery_status()["unresolved_count"]:
                raise RuntimeError("local workspace requires a healthy, settled store")
            self._sync = PeriodicOwnerSync(
                store=store, cdp_port=cdp_port,
                timeout_seconds=min(float(timeout_seconds), 30.0),
                runtime_factory=runtime_factory, clock=clock, stop_event=self._stop,
            )
        except BaseException:
            self._store_lock.close()
            raise

    def sync_once(self) -> int:
        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("local inventory read already running")
        try:
            if self._stop.is_set() or self._closed:
                raise RuntimeError("local workspace closing")
            if self._store.sync_recovery_status()["unresolved_count"]:
                raise RuntimeError("inventory recovery must be resolved first")
            # One explicit read. Failed reads append no owner observations.
            result = self._sync.run_once()
            if not result.is_success:
                raise RuntimeError("owner inventory unavailable")
            # Success refreshes data only. There is still no platform writer.
            return len(result.value or ())
        finally:
            self._operation_lock.release()

    def close(self) -> None:
        self._stop.set()
        # Keep the exclusive DB lease until the last reader is drained. The
        # product's bounded shutdown retains this worker on a timeout.
        with self._operation_lock:
            if not self._closed:
                self._store_lock.close()
                self._closed = True
