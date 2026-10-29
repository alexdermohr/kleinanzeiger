"""Local customer work after a settled browser read failure; no platform writer."""
from __future__ import annotations

from threading import Event, Lock

from .periodic_sync import PeriodicOwnerSync, PeriodicSyncError, PeriodicSyncStopped
from .results import ReadStatus
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
        from .private_web_runtime import OwnerInventorySyncFailure

        if not self._operation_lock.acquire(blocking=False):
            raise OwnerInventorySyncFailure("busy")
        try:
            if self._stop.is_set() or self._closed:
                raise OwnerInventorySyncFailure("browser_unavailable")
            try:
                if self._store.sync_recovery_status()["unresolved_count"]:
                    raise OwnerInventorySyncFailure("recovery_required")
            except OwnerInventorySyncFailure:
                raise
            except Exception:
                raise OwnerInventorySyncFailure("storage_unavailable") from None
            # One explicit read. Failed reads append no owner observations.
            try:
                result = self._sync.run_once()
            except PeriodicSyncStopped:
                raise OwnerInventorySyncFailure("browser_unavailable") from None
            except PeriodicSyncError as exc:
                kind = {
                    "inventory read already in progress": "busy",
                    "browser inventory runtime unavailable": "browser_unavailable",
                    "local sync journal unavailable": "storage_unavailable",
                    "local sync persistence failed": "storage_unavailable",
                    "local sync cancellation settlement failed": "storage_unavailable",
                    "browser inventory read failed": "read_failed",
                    "browser inventory result invalid": "read_failed",
                    "browser inventory runtime close failed": "read_failed",
                }.get(str(exc), "read_failed")
                raise OwnerInventorySyncFailure(kind) from None
            except Exception:
                raise OwnerInventorySyncFailure("read_failed") from None
            if not result.is_success:
                kind = {
                    ReadStatus.UNAUTHENTICATED: "login_required",
                    ReadStatus.TRANSPORT_ERROR: "connection_unavailable",
                }.get(result.status, "read_failed")
                raise OwnerInventorySyncFailure(kind)
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
