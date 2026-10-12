"""Draft-only, independent SQLite ownership authority (Issue #76).

This is a deliberately *incomplete* storage-authority slice, NOT a product
runtime switch. The browser UID must have neither SQLite mounts nor inherited
writable descriptors before this can protect real Write flows. Only bounded
typed request-claim/start/read, read-only status/clearance lookups and
transactional, in-memory-staged owner-inventory/sync journaling are exposed.
Executor-supplied inventory is observation data, NOT independent platform
attestation, and cannot manufacture write-recovery evidence or clear fences.
There is NO arbitrary SQL, completion, clearance issuance, file, migration,
backup or general method-dispatch RPC.

A lost acknowledgement of claim/start is UNKNOWN: inspect the persisted key
and independent witness before deciding what happened, never blindly retry.
"""
from __future__ import annotations

import argparse
import hashlib
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import re
import signal
import socket
import socketserver
import sqlite3
import stat
import struct
import sys
from threading import BoundedSemaphore, Event, RLock, Thread
from typing import Any
from urllib.parse import quote

from .container_draft import container_draft_denied
from .fence_witness import (
    _canonical_json, _file_identity, _private_directory,
    _strict_json, FenceWitnessError,
)
from .storage import (SnapshotStore, WriteApiRequestClaim, WriteApiRequestRecord,
                      _SYNC_ERROR_KINDS)
from .domain import AdSnapshot, LifecycleState
from .results import ReadResult, ReadStatus


class StorageOwnerError(RuntimeError):
    """Outcome/authority not proven. Never infer that a write was rejected."""


_VERSION = 1
_MAX_FRAME = 32 * 1024
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_OWNER = re.compile(r"[A-Za-z0-9._:@-]{1,128}\Z", re.ASCII)
_MAX_PEERS = 8
_MAX_SYNC_BATCH = 4
_MAX_SYNC_ITEMS = 2048
_TRACKED_PAGE = 64
_SYNC_DIGEST_SEED = hashlib.sha256(b"mark-storage-owner-sync-v1").hexdigest()
_UCRED = struct.Struct("=iII")  # Linux pid_t (signed), uid_t/gid_t (unsigned).


def _linux_ucred_uid(raw: bytes) -> int:
    """Decode a complete Linux SO_PEERCRED payload without signed-UID aliasing."""
    if type(raw) is not bytes or len(raw) != _UCRED.size:
        raise StorageOwnerError("malformed Linux peer credential")
    return _UCRED.unpack(raw)[1]


def _validated(value: object, pattern: re.Pattern[str]) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise StorageOwnerError("invalid operation-bound request value")
    return value


def _timestamp(value: object) -> datetime:
    if type(value) is not str or not 1 <= len(value) <= 80:
        raise StorageOwnerError("invalid request timestamp")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise StorageOwnerError("invalid request timestamp") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise StorageOwnerError("request timestamp must be timezone-aware")
    return timestamp


def _record_as_json(record: WriteApiRequestRecord) -> dict[str, object]:
    return {
        "idempotency_key": record.idempotency_key,
        "request_sha256": record.request_sha256,
        "state": record.state,
        "requested_at": record.requested_at.isoformat(),
        "claim_owner": record.claim_owner,
        "execution_started_at": (
            record.execution_started_at.isoformat()
            if record.execution_started_at is not None else None
        ),
        "completed_at": (
            record.completed_at.isoformat()
            if record.completed_at is not None else None
        ),
        "response_status": record.response_status,
        "response_json": record.response_json,
    }


def _record_from_json(value: object) -> WriteApiRequestRecord:
    if not isinstance(value, dict) or set(value) != {
        "idempotency_key", "request_sha256", "state", "requested_at",
        "claim_owner", "execution_started_at", "completed_at",
        "response_status", "response_json",
    }:
        raise StorageOwnerError("storage reply has invalid record schema")
    key = _validated(value["idempotency_key"], _KEY)
    sha = _validated(value["request_sha256"], _SHA)
    state = value["state"]
    if type(state) is not str or state not in {"in_progress", "completed"}:
        raise StorageOwnerError("storage reply has invalid record state")
    claimed = value["claim_owner"]
    if claimed is not None:
        claimed = _validated(claimed, _OWNER)
    started = value["execution_started_at"]
    completed = value["completed_at"]
    if started is not None:
        started = _timestamp(started)
    if completed is not None:
        completed = _timestamp(completed)
    status = value["response_status"]
    response_json = value["response_json"]
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        raise StorageOwnerError("storage reply has invalid HTTP status")
    if response_json is not None and type(response_json) is not str:
        raise StorageOwnerError("storage reply has invalid response")
    if (
        (state == "in_progress" and (
            completed is not None or status is not None or response_json is not None
        ))
        or (state == "completed" and (
            completed is None or status is None or response_json is None
        ))
    ):
        raise StorageOwnerError("storage reply has invalid recovery transition")
    return WriteApiRequestRecord(
        idempotency_key=key, request_sha256=sha, state=state,
        requested_at=_timestamp(value["requested_at"]),
        claim_owner=claimed, execution_started_at=started, completed_at=completed,
        response_status=status, response_json=response_json,
    )


def _sync_status_from_json(value: object) -> dict[str, object]:
    """Validate the exact persisted journal projection, never invent freshness.

    The independent owner is trusted for storage integrity only after UID and
    inode attestation. Its replies must still be schema- and state-consistent.
    In particular, no successful sync may be inferred from absent attempts.
    """
    if type(value) is not dict or set(value) != {
        "state", "latest_attempt", "last_successful_attempt",
    }:
        raise StorageOwnerError("storage sync status reply schema invalid")

    def checked_attempt(row: object) -> dict[str, object] | None:
        if row is None:
            return None
        if type(row) is not dict or set(row) != {
            "id", "source", "started_at", "completed_at",
            "outcome", "error_kind", "snapshot_count",
        }:
            raise StorageOwnerError("storage sync attempt reply schema invalid")
        if (
            type(row["id"]) is not int or row["id"] < 1
            or type(row["source"]) is not str
            or not row["source"].strip() or len(row["source"]) > 1024
        ):
            raise StorageOwnerError("storage sync attempt identity invalid")
        started = _timestamp(row["started_at"])
        completed = row["completed_at"]
        if completed is not None:
            completed = _timestamp(completed)
            if completed < started:
                raise StorageOwnerError("storage sync completion predates start")
        outcome, error, count = (row["outcome"], row["error_kind"],
                                 row["snapshot_count"])
        if outcome == "in_progress":
            valid = completed is None and error is None and count is None
        elif outcome == "failed":
            valid = completed is not None and type(error) is str \
                and error in _SYNC_ERROR_KINDS and count is None
        elif outcome == "success_empty":
            valid = completed is not None and error is None \
                and type(count) is int and count == 0
        elif outcome == "success_nonempty":
            valid = completed is not None and error is None \
                and type(count) is int and count > 0
        else:
            valid = False
        if not valid:
            raise StorageOwnerError("storage sync attempt transition invalid")
        return row

    latest = checked_attempt(value["latest_attempt"])
    success = checked_attempt(value["last_successful_attempt"])
    if latest is None:
        if value["state"] != "never_attempted" or success is not None:
            raise StorageOwnerError("storage sync never-attempted state invalid")
    else:
        if value["state"] != latest["outcome"]:
            raise StorageOwnerError("storage sync latest state mismatch")
        if latest["outcome"] in {"success_empty", "success_nonempty"}:
            # Latest *started* is ordered by id, but last successful is
            # ordered by completion time. An older attempt can finish later.
            # Reject a missing success or a supposedly newer result that
            # could not win the store's (completed_at DESC, id DESC) order.
            if success is None or (
                success["id"] != latest["id"]
                and _timestamp(success["completed_at"])
                    <= _timestamp(latest["completed_at"])
            ):
                raise StorageOwnerError("storage latest success ordering invalid")
        if success is not None:
            if (
                success["outcome"] not in {"success_empty", "success_nonempty"}
                or success["id"] > latest["id"]
                or (success["id"] == latest["id"] and success != latest)
            ):
                raise StorageOwnerError("storage last-success binding invalid")
    return value



def _limited_text(
    value: object, *, bound: int, optional: bool = False,
    allow_linebreaks: bool = False,
) -> str | None:
    if value is None and optional:
        return None
    if (
        type(value) is not str or not value.strip()
        or len(value) > bound or any(
            ord(ch) == 0 or (not allow_linebreaks and ord(ch) in (10, 13))
            for ch in value
        )
        or any(0xD800 <= ord(ch) <= 0xDFFF for ch in value)
    ):
        raise StorageOwnerError("invalid bounded inventory field")
    return value


def _snapshot_as_json(snapshot: AdSnapshot) -> dict[str, object]:
    """Canonical, fixed-schema wire representation; no SQL/path dispatch."""
    if not isinstance(snapshot, AdSnapshot):
        raise StorageOwnerError("inventory entry is not an AdSnapshot")
    return {
        "ad_id": snapshot.ad_id, "observed_at": snapshot.observed_at.isoformat(),
        "source": snapshot.source, "lifecycle_state": str(snapshot.lifecycle_state),
        "title": snapshot.title, "description": snapshot.description,
        "views": snapshot.views, "watch_count": snapshot.watch_count,
        "reply_count": snapshot.reply_count, "metric_source": snapshot.metric_source,
    }


def _snapshot_from_json(raw: object) -> AdSnapshot:
    required = {
        "ad_id", "observed_at", "source", "lifecycle_state",
        "title", "description", "views", "watch_count",
        "reply_count", "metric_source",
    }
    if type(raw) is not dict or set(raw) != required:
        raise StorageOwnerError("inventory entry has invalid schema")
    counters = {}
    for name in ("views", "watch_count", "reply_count"):
        value = raw[name]
        if value is not None and (type(value) is not int or value < 0 or value > 2**63 - 1):
            raise StorageOwnerError("inventory entry has invalid counter")
        counters[name] = value
    state = raw["lifecycle_state"]
    if type(state) is not str:
        raise StorageOwnerError("inventory entry has invalid lifecycle state")
    try:
        lifecycle = LifecycleState(state)
    except ValueError as exc:
        raise StorageOwnerError("inventory entry has invalid lifecycle state") from exc
    return AdSnapshot(
        ad_id=_limited_text(raw["ad_id"], bound=160),
        observed_at=_timestamp(raw["observed_at"]),
        source=_validated(raw["source"], _OWNER),
        lifecycle_state=lifecycle,
        title=_limited_text(raw["title"], bound=512, optional=True),
        description=_limited_text(
            raw["description"], bound=8192, optional=True,
            allow_linebreaks=True,
        ),
        metric_source=_limited_text(raw["metric_source"], bound=160, optional=True),
        **counters,
    )


def _sync_digest(previous: str, snapshots: list[dict[str, object]]) -> str:
    """Append-only chunk commitment, independent of JSON key ordering."""
    return hashlib.sha256(
        bytes.fromhex(previous) + _canonical_json(snapshots).encode("utf-8")
    ).hexdigest()


@dataclass(slots=True)
class _SyncStage:
    attempt: int
    source: str
    snapshots: list[AdSnapshot] = field(default_factory=list)
    digest: str = _SYNC_DIGEST_SEED


def _attested_database(path: Path, uid: int) -> tuple[int, int, int, int]:
    """Existing, owner-private schema-v2 store; never create or repair files."""
    if (
        not isinstance(path, Path) or not path.is_absolute()
        or path != Path(os.path.normpath(path))
        or path.name in {"", ".", ".."}
    ):
        raise StorageOwnerError("database path is not absolute and normalized")
    try:
        _private_directory(path.parent, uid, mode=0o700)
        info = _file_identity(path, uid)
        # Existing SQLite sidecars can be created by its own DB writer; all
        # present sidecars must be equally private. No symlink or hardlink.
        for ending in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(path) + ending)
            try:
                sidecar.lstat()
            except FileNotFoundError:
                continue
            _file_identity(sidecar, uid)
        parent = path.parent.lstat()
    except (OSError, FenceWitnessError) as exc:
        raise StorageOwnerError("untrusted database inode or sidecar") from exc
    return info.st_dev, info.st_ino, parent.st_dev, parent.st_ino


class _StorageHandler(socketserver.StreamRequestHandler):
    """One bounded request per authenticated connection; no implicit retry."""

    def handle(self) -> None:
        self.request.settimeout(5.0)
        service: StorageOwnerServer = self.server  # type: ignore[assignment]
        try:
            if not hasattr(socket, "SO_PEERCRED"):
                raise StorageOwnerError("SO_PEERCRED unavailable")
            raw = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            if _linux_ucred_uid(raw) != service.runtime_uid:
                raise StorageOwnerError("runtime UID not authorized")
            frame = self.rfile.readline(_MAX_FRAME + 2)
            if (
                not frame.endswith(b"\n") or len(frame) > _MAX_FRAME + 1
                or len(frame) <= 1
            ):
                raise StorageOwnerError("malformed storage request frame")
            request = _strict_json(frame[:-1])
            if type(request.get("version")) is not int or request["version"] != _VERSION:
                raise StorageOwnerError("storage protocol version mismatch")
            answer = service.dispatch(request)
            self._send({"ok": True, "result": answer})
        except (StorageOwnerError, FenceWitnessError, ValueError,
                sqlite3.Error, OSError, TimeoutError):
            # A late commit, connection failure or reply failure is UNKNOWN.
            # No response here is a promise of a pre-commit rejection.
            try:
                self._send({"ok": False, "error": "request_denied"})
            except OSError:
                pass

    def _send(self, value: dict[str, object]) -> None:
        data = (_canonical_json(value) + "\n").encode("utf-8")
        if len(data) > _MAX_FRAME + 1:
            raise StorageOwnerError("storage reply exceeds frame bound")
        self.wfile.write(data)
        self.wfile.flush()


class StorageOwnerServer(
    socketserver.ThreadingMixIn, socketserver.UnixStreamServer,
):
    """Private UID-owned SQLite; only an exact distinct runtime UID can RPC."""

    allow_reuse_address = False
    # An accepted RPC may already have entered SQLite when SIGTERM arrives.
    # Never unlink the public socket / report stop complete until all such
    # operations have finished; else an old generation can commit *after*
    # the operator believes its authority has been revoked.
    daemon_threads = False
    block_on_close = True
    request_queue_size = _MAX_PEERS

    def __init__(
        self, *, database: Path, socket_path: Path,
        runtime_uid: int, runtime_gid: int,
    ) -> None:
        uid = os.geteuid()
        if (
            uid == 0 or type(runtime_uid) is not int or runtime_uid <= 0
            or type(runtime_gid) is not int or runtime_gid <= 0
            or runtime_uid == uid or not hasattr(socket, "SO_PEERCRED")
            or not isinstance(socket_path, Path) or not socket_path.is_absolute()
            or socket_path != Path(os.path.normpath(socket_path))
            or socket_path == database
        ):
            raise StorageOwnerError("distinct unprivileged storage/runtime UIDs required")
        identity = _attested_database(database, uid)
        # Validate the full existing schema BEFORE SnapshotStore can add
        # historical columns. A missing recovery table is never auto-created.
        with closing(sqlite3.connect(
            "file:" + quote(str(database), safe="/") + "?mode=rw", uri=True, timeout=3,
        )) as connection:
            connection.row_factory = sqlite3.Row
            marker = connection.execute("PRAGMA user_version").fetchone()
            if marker is None or marker[0] != 2:
                raise StorageOwnerError("storage schema-v2 cutover not attested")
            SnapshotStore._validate_store_schema(connection)
        self.store = SnapshotStore(database, create_if_missing=False,
                                   require_existing_schema=True)
        if _attested_database(database, uid) != identity:
            raise StorageOwnerError("storage inode changed during startup")
        try:
            _private_directory(socket_path.parent, uid, mode=0o2710,
                               gid=runtime_gid)
        except FenceWitnessError as exc:
            raise StorageOwnerError("unsafe storage RPC directory") from exc
        if socket_path.exists() or socket_path.is_symlink():
            raise StorageOwnerError("refusing to replace storage RPC socket")
        self.database = database
        self.socket_path = socket_path
        self._identity = identity
        self.runtime_uid = runtime_uid
        self._runtime_gid = runtime_gid
        self._inode: tuple[int, int] | None = None
        self._slots = BoundedSemaphore(_MAX_PEERS)
        # Staged inventory is memory-only. A crash retains the committed
        # in-progress journal but NEVER publishes an incomplete success.
        self._sync_lock = RLock()
        self._active_sync: _SyncStage | None = None
        self._serving: Thread | None = None
        super().__init__(str(socket_path), _StorageHandler)
        try:
            os.chmod(socket_path, 0o660)
            file = socket_path.lstat()
            self._inode = file.st_dev, file.st_ino
            if (
                not stat.S_ISSOCK(file.st_mode) or file.st_uid != uid
                or file.st_gid != runtime_gid or file.st_nlink != 1
                or stat.S_IMODE(file.st_mode) != 0o660
            ):
                raise StorageOwnerError("storage RPC socket ownership invalid")
        except BaseException:
            self.server_close()
            raise

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def _attest(self) -> None:
        if _attested_database(self.database, os.geteuid()) != self._identity:
            raise StorageOwnerError("storage source was replaced")

    def dispatch(self, request: dict[str, object]) -> dict[str, object]:
        """Closed operation table: no arbitrary method names, SQL or clearance."""
        self._attest()
        op = request.get("op")
        if op == "ready" and set(request) == {"version", "op"}:
            return {"ready": self.store.is_ready()}
        if op == "sync_status" and set(request) == {"version", "op"}:
            return {"sync_status": self.store.sync_status()}

        if op == "tracked_ids" and set(request) == {"version", "op", "after"}:
            after = request["after"]
            if after != "":
                after = _limited_text(after, bound=160)
            if type(after) is not str:
                raise StorageOwnerError("invalid tracked-ID cursor")
            # Strictly ordered and bounded reply. Never grant a query/SQL API.
            ids = [key for key in self.store.tracked_ad_ids() if key > after]
            batch = ids[:_TRACKED_PAGE]
            return {"ids": batch, "more": len(ids) > _TRACKED_PAGE}
        if op == "sync_begin" and set(request) == {"version", "op", "source", "at"}:
            source = _validated(request["source"], _OWNER)
            started_at = _timestamp(request["at"])
            with self._sync_lock:
                if self._active_sync is not None:
                    raise StorageOwnerError("another inventory sync is unresolved")
                attempt = self.store.begin_sync_attempt(
                    source=source, started_at=started_at,
                )
                self._active_sync = _SyncStage(attempt=attempt, source=source)
                return {"attempt": attempt}
        if op == "sync_stage_status" and set(request) == {
            "version", "op", "attempt",
        }:
            with self._sync_lock:
                stage = self._required_stage(request["attempt"])
                return {"accepted": len(stage.snapshots), "digest": stage.digest}
        if op == "sync_stage" and set(request) == {
            "version", "op", "attempt", "offset", "snapshots",
        }:
            raw_snapshots = request["snapshots"]
            offset = request["offset"]
            if (
                type(raw_snapshots) is not list
                or not 1 <= len(raw_snapshots) <= _MAX_SYNC_BATCH
                or type(offset) is not int or offset < 0
            ):
                raise StorageOwnerError("invalid inventory stage batch")
            with self._sync_lock:
                stage = self._required_stage(request["attempt"])
                if offset != len(stage.snapshots) or (
                    len(stage.snapshots) + len(raw_snapshots) > _MAX_SYNC_ITEMS
                ):
                    raise StorageOwnerError("inventory stage cursor or capacity mismatch")
                snapshots = [_snapshot_from_json(row) for row in raw_snapshots]
                if any(item.source != stage.source for item in snapshots):
                    raise StorageOwnerError("inventory source does not match attempt")
                existing = {item.ad_id for item in stage.snapshots}
                added = [item.ad_id for item in snapshots]
                if len(set(added)) != len(added) or any(key in existing for key in added):
                    raise StorageOwnerError("inventory duplicate ad identity")
                serialized = [_snapshot_as_json(item) for item in snapshots]
                digest = _sync_digest(stage.digest, serialized)
                stage.snapshots.extend(snapshots)
                stage.digest = digest
                return {"accepted": len(stage.snapshots), "digest": stage.digest}
        if op == "sync_fail" and set(request) == {
            "version", "op", "attempt", "source", "at", "error_kind",
        }:
            source = _validated(request["source"], _OWNER)
            timestamp = _timestamp(request["at"])
            error = request["error_kind"]
            if type(error) is not str or error not in _SYNC_ERROR_KINDS:
                raise StorageOwnerError("invalid sync failure classification")
            with self._sync_lock:
                stage = self._required_stage(request["attempt"])
                if stage.source != source:
                    raise StorageOwnerError("sync failure source mismatch")
                self.store.fail_sync_attempt(
                    stage.attempt, source=source, completed_at=timestamp,
                    error_kind=error,
                )
                self._active_sync = None
                return {"failed": True, "attempt": stage.attempt}
        if op == "sync_finish" and set(request) == {
            "version", "op", "attempt", "source",
            "digest", "observed_at", "completed_at",
        }:
            source = _validated(request["source"], _OWNER)
            digest = _validated(request["digest"], _SHA)
            observed = _timestamp(request["observed_at"])
            completed = _timestamp(request["completed_at"])
            with self._sync_lock:
                stage = self._required_stage(request["attempt"])
                if stage.source != source or stage.digest != digest:
                    raise StorageOwnerError("sync completion manifest mismatch")
                snapshots = tuple(stage.snapshots)
                result = (ReadResult.success_nonempty(snapshots)
                          if snapshots else ReadResult.success_empty(()))
                # Existing SnapshotStore commits missing-AD observations and
                # the exact terminal sync journal in one SQLite transaction.
                persisted = self.store.append_inventory_result(
                    result, tracked_ad_ids=self.store.tracked_ad_ids(),
                    observed_at=observed, source=source,
                    attempt_id=stage.attempt, completed_at=completed,
                )
                self._active_sync = None
                return {"persisted": persisted, "attempt": stage.attempt,
                        "digest": digest}
        if op == "has_clearance" and set(request) == {
            "version", "op", "key", "sha",
        }:
            key = _validated(request["key"], _KEY)
            sha = _validated(request["sha"], _SHA)
            return {"cleared": self.store.has_write_recovery_clearance(
                idempotency_key=key, request_sha256=sha,
            )}
        if op == "read_request" and set(request) == {"version", "op", "key"}:
            key = _validated(request["key"], _KEY)
            rec = self.store.write_api_request(key)
            return {"record": _record_as_json(rec) if rec is not None else None}
        if op == "create_pending" and set(request) == {
            "version", "op", "exclude_key", "owner",
        }:
            key = _validated(request["exclude_key"], _KEY)
            owner = _validated(request["owner"], _OWNER)
            return {"pending": self.store.create_recovery_pending(
                exclude_idempotency_key=key, claim_owner=owner,
            )}
        if op == "claim" and set(request) == {
            "version", "op", "key", "sha", "owner", "at",
        }:
            claim = self.store.claim_write_api_request(
                idempotency_key=_validated(request["key"], _KEY),
                request_sha256=_validated(request["sha"], _SHA),
                requested_at=_timestamp(request["at"]),
                claim_owner=_validated(request["owner"], _OWNER),
                allow_abandoned_takeover=False,
            )
            return {"created": claim.created, "record": _record_as_json(claim.record)}
        if op == "begin" and set(request) == {
            "version", "op", "key", "sha", "owner", "at",
        }:
            rec = self.store.begin_write_api_request(
                idempotency_key=_validated(request["key"], _KEY),
                request_sha256=_validated(request["sha"], _SHA),
                claim_owner=_validated(request["owner"], _OWNER),
                execution_started_at=_timestamp(request["at"]),
            )
            return {"record": _record_as_json(rec)}
        raise StorageOwnerError("storage operation is not exposed")


    def _required_stage(self, attempt: object) -> _SyncStage:
        if type(attempt) is not int or attempt < 1:
            raise StorageOwnerError("invalid inventory sync attempt identity")
        stage = self._active_sync
        if stage is None or stage.attempt != attempt:
            raise StorageOwnerError("inventory sync not active in this generation")
        return stage

    def start(self) -> None:
        if self._serving is not None:
            raise StorageOwnerError("storage owner already started")
        self._serving = Thread(target=self.serve_forever, kwargs={
            "poll_interval": 0.1,
        }, daemon=True, name="mark-independent-storage-owner")
        self._serving.start()

    def stop(self) -> None:
        if self._serving is not None:
            if self._serving.is_alive():
                self.shutdown()
            self._serving.join(timeout=4)
        self.server_close()

    def server_close(self) -> None:
        super().server_close()
        try:
            info = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self._inode:
            self.socket_path.unlink()


class StorageOwnerClient:
    """Typed RPC client: transport ambiguity poisons this operation, no retry."""

    def __init__(self, socket_path: Path, *, expected_owner_uid: int) -> None:
        if (
            not isinstance(socket_path, Path) or not socket_path.is_absolute()
            or type(expected_owner_uid) is not int or expected_owner_uid <= 0
            or not hasattr(socket, "SO_PEERCRED")
        ):
            raise StorageOwnerError("independent storage UID/socket required")
        self.socket_path = socket_path
        self.expected_owner_uid = expected_owner_uid

    def _call(self, op: str, **fields: object) -> dict[str, object]:
        message = (_canonical_json({"version": _VERSION, "op": op, **fields}) +
                   "\n").encode("utf-8")
        if len(message) > _MAX_FRAME + 1:
            raise StorageOwnerError("storage request exceeds protocol bound")
        try:
            with closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as peer:
                peer.settimeout(8)
                peer.connect(str(self.socket_path))
                credentials = peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                if (
                    _linux_ucred_uid(credentials) != self.expected_owner_uid
                ):
                    raise StorageOwnerError("independent storage peer UID is wrong")
                peer.sendall(message)
                with peer.makefile("rb", buffering=0) as stream:
                    frame = stream.readline(_MAX_FRAME + 2)
                if (
                    not frame.endswith(b"\n") or len(frame) > _MAX_FRAME + 1
                    or len(frame) <= 1
                ):
                    raise StorageOwnerError("storage reply missing or truncated")
                answer = _strict_json(frame[:-1])
                if set(answer) != {"ok", "result"} or answer["ok"] is not True:
                    raise StorageOwnerError("storage request not acknowledged")
                result = answer["result"]
                if not isinstance(result, dict):
                    raise StorageOwnerError("storage reply is not typed")
                return result
        except (StorageOwnerError, FenceWitnessError, OSError,
                TimeoutError, ValueError) as exc:
            raise StorageOwnerError(
                "storage outcome uncertain; read exact key and witness before retry"
            ) from exc

    def ready(self) -> bool:
        answer = self._call("ready")
        if set(answer) != {"ready"} or type(answer["ready"]) is not bool:
            raise StorageOwnerError("storage readiness reply invalid")
        return answer["ready"]

    def sync_status(self) -> dict[str, object]:
        answer = self._call("sync_status")
        if set(answer) != {"sync_status"}:
            raise StorageOwnerError("storage sync status reply invalid")
        return _sync_status_from_json(answer["sync_status"])


    def tracked_ad_ids(self) -> tuple[str, ...]:
        """Read bounded ascending pages, never a raw SQLite table or cursor."""
        keys: list[str] = []
        after = ""
        while True:
            response = self._call("tracked_ids", after=after)
            if (
                set(response) != {"ids", "more"}
                or type(response["ids"]) is not list
                or type(response["more"]) is not bool
                or len(response["ids"]) > _TRACKED_PAGE
            ):
                raise StorageOwnerError("invalid tracked-ID page")
            page = response["ids"]
            for value in page:
                item = _limited_text(value, bound=160)
                if item <= after:
                    raise StorageOwnerError("tracked-ID page is not strictly sorted")
                keys.append(item)
                after = item
            if not response["more"]:
                return tuple(keys)
            if not page or len(page) != _TRACKED_PAGE:
                raise StorageOwnerError("tracked-ID page falsely claims more")

    def begin_sync_attempt(self, *, source: str, started_at: datetime) -> int:
        answer = self._call("sync_begin", source=_validated(source, _OWNER),
                            at=started_at.isoformat())
        if set(answer) != {"attempt"} or type(answer["attempt"]) is not int or answer["attempt"] < 1:
            raise StorageOwnerError("invalid sync begin receipt")
        return answer["attempt"]

    def sync_stage_status(self, attempt_id: int) -> dict[str, object]:
        """Observation-only readback after an uncertain append ACK."""
        answer = self._call("sync_stage_status", attempt=attempt_id)
        if (
            set(answer) != {"accepted", "digest"}
            or type(answer["accepted"]) is not int
            or not 0 <= answer["accepted"] <= _MAX_SYNC_ITEMS
        ):
            raise StorageOwnerError("invalid inventory stage status")
        _validated(answer["digest"], _SHA)
        return answer

    def fail_sync_attempt(
        self, attempt_id: int, *, source: str, completed_at: datetime,
        error_kind: str,
    ) -> None:
        if error_kind not in _SYNC_ERROR_KINDS:
            raise StorageOwnerError("invalid sync failure status")
        answer = self._call(
            "sync_fail", attempt=attempt_id, source=_validated(source, _OWNER),
            at=completed_at.isoformat(), error_kind=error_kind,
        )
        if set(answer) != {"failed", "attempt"} or answer != {
            "failed": True, "attempt": attempt_id,
        }:
            raise StorageOwnerError("invalid sync failure receipt")

    def append_inventory_result(
        self, result: ReadResult[tuple[AdSnapshot, ...]], *,
        tracked_ad_ids: Any = (), observed_at: datetime, source: str,
        attempt_id: int | None = None, completed_at: datetime | None = None,
    ) -> int:
        """Commit an exact owner-inventory attempt atomically, never on lost ACK.

        The independent owner computes the already-tracked IDs from SQLite:
        caller-provided tracked IDs cannot omit historical source records.
        A lost stage/finish reply is UNKNOWN, never an automatic RPC retry.
        """
        if not isinstance(result, ReadResult):
            raise StorageOwnerError("invalid owner inventory read result")
        if type(attempt_id) is not int or attempt_id < 1 or completed_at is None:
            raise StorageOwnerError("explicit durable sync attempt is required")
        source = _validated(source, _OWNER)
        if not result.is_success:
            self.fail_sync_attempt(
                attempt_id, source=source, completed_at=completed_at,
                error_kind=result.status.value,
            )
            return 0
        snapshots = tuple(result.value or ())
        if (
            (result.status is ReadStatus.SUCCESS_EMPTY and snapshots)
            or (result.status is ReadStatus.SUCCESS_NONEMPTY and not snapshots)
            or any(not isinstance(item, AdSnapshot) for item in snapshots)
            or len(snapshots) > _MAX_SYNC_ITEMS
        ):
            raise StorageOwnerError("invalid inventory read result")
        digest = _SYNC_DIGEST_SEED
        offset = 0
        while offset < len(snapshots):
            batch: list[dict[str, object]] = []
            for item in snapshots[offset:offset + _MAX_SYNC_BATCH]:
                candidate = [*batch, _snapshot_as_json(item)]
                frame = _canonical_json({
                    "version": _VERSION, "op": "sync_stage",
                    "attempt": attempt_id, "offset": offset,
                    "snapshots": candidate,
                }).encode("utf-8")
                if len(frame) + 1 > _MAX_FRAME + 1:
                    break
                batch = candidate
            if not batch:
                raise StorageOwnerError("inventory item exceeds protocol frame limit")
            digest = _sync_digest(digest, batch)
            answer = self._call(
                "sync_stage", attempt=attempt_id, offset=offset, snapshots=batch,
            )
            if answer != {"accepted": offset + len(batch), "digest": digest}:
                raise StorageOwnerError("inventory chunk ACK does not match manifest")
            offset += len(batch)
        answer = self._call(
            "sync_finish", attempt=attempt_id, source=source, digest=digest,
            observed_at=observed_at.isoformat(), completed_at=completed_at.isoformat(),
        )
        if (
            set(answer) != {"persisted", "attempt", "digest"}
            or answer["attempt"] != attempt_id
            or answer["digest"] != digest
            or type(answer["persisted"]) is not int
            or answer["persisted"] < len(snapshots)
        ):
            raise StorageOwnerError("inventory commit receipt is unverified")
        return answer["persisted"]

    def has_write_recovery_clearance(
        self, *, idempotency_key: str, request_sha256: str,
    ) -> bool:
        """Read one exact persisted clearance; never issue or imply one."""
        answer = self._call(
            "has_clearance", key=_validated(idempotency_key, _KEY),
            sha=_validated(request_sha256, _SHA),
        )
        if set(answer) != {"cleared"} or type(answer["cleared"]) is not bool:
            raise StorageOwnerError("storage clearance lookup reply invalid")
        return answer["cleared"]

    def read_request(self, key: str) -> WriteApiRequestRecord | None:
        answer = self._call("read_request", key=_validated(key, _KEY))
        if set(answer) != {"record"}:
            raise StorageOwnerError("storage request lookup reply invalid")
        if answer["record"] is None:
            return None
        record = _record_from_json(answer["record"])
        if record.idempotency_key != key:
            raise StorageOwnerError("storage readback key binding mismatch")
        return record

    def create_pending(self, *, exclude_key: str, owner: str) -> bool:
        answer = self._call("create_pending", exclude_key=_validated(exclude_key, _KEY),
                            owner=_validated(owner, _OWNER))
        if set(answer) != {"pending"} or type(answer["pending"]) is not bool:
            raise StorageOwnerError("storage pending reply invalid")
        return answer["pending"]

    def claim(self, *, key: str, sha: str, owner: str,
              at: datetime) -> WriteApiRequestClaim:
        answer = self._call("claim", key=_validated(key, _KEY),
                            sha=_validated(sha, _SHA),
                            owner=_validated(owner, _OWNER),
                            at=at.isoformat())
        if set(answer) != {"created", "record"} or type(answer["created"]) is not bool:
            raise StorageOwnerError("storage claim reply invalid")
        record = _record_from_json(answer["record"])
        if (
            (record.idempotency_key, record.request_sha256) != (key, sha)
            or record.claim_owner != owner
        ):
            raise StorageOwnerError("storage claim operation binding mismatch")
        return WriteApiRequestClaim(created=answer["created"], record=record)

    def begin(self, *, key: str, sha: str, owner: str,
              at: datetime) -> WriteApiRequestRecord:
        answer = self._call("begin", key=_validated(key, _KEY),
                            sha=_validated(sha, _SHA),
                            owner=_validated(owner, _OWNER), at=at.isoformat())
        if set(answer) != {"record"}:
            raise StorageOwnerError("storage start reply invalid")
        record = _record_from_json(answer["record"])
        if (
            (record.idempotency_key, record.request_sha256) != (key, sha)
            or record.claim_owner != owner
        ):
            raise StorageOwnerError("storage start operation binding mismatch")
        if record.execution_started_at is None:
            raise StorageOwnerError("storage start not persisted")
        return record


def main(argv: list[str] | None = None) -> int:
    if container_draft_denied():
        print("mark-api: unattested container draft cannot run Python entrypoints",
              file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description="Draft independent SQLite owner")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--runtime-uid", type=int, required=True)
    parser.add_argument("--runtime-gid", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        service = StorageOwnerServer(
            database=args.db, socket_path=args.socket,
            runtime_uid=args.runtime_uid, runtime_gid=args.runtime_gid,
        )
    except (StorageOwnerError, sqlite3.Error, OSError, ValueError):
        print("mark storage owner: unavailable existing private store or IPC",
              file=sys.stderr)
        return 2
    stopping = Event()
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda _sig, _frame: stopping.set())
    try:
        service.start()
        while not stopping.wait(0.2):
            if service._serving is None or not service._serving.is_alive():
                return 2
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())