"""Draft-only, independent SQLite ownership authority (Issue #76).

This is a deliberately *incomplete* storage-authority slice, NOT a product
runtime switch. The browser UID must have neither SQLite mounts nor inherited
writable descriptors before this can protect real Write flows. Only a bounded
typed request-claim/start/read interface is exposed here. There is NO SQL,
completion, clearance, file, migration, backup or general method-dispatch RPC.

A lost acknowledgement of claim/start is UNKNOWN: inspect the persisted key
and independent witness before deciding what happened, never blindly retry.
"""
from __future__ import annotations

import argparse
from contextlib import closing
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
from threading import BoundedSemaphore, Event, Thread
from typing import Any
from urllib.parse import quote

from .container_draft import container_draft_denied
from .fence_witness import (
    _canonical_json, _file_identity, _private_directory,
    _strict_json, FenceWitnessError,
)
from .storage import SnapshotStore, WriteApiRequestClaim, WriteApiRequestRecord


class StorageOwnerError(RuntimeError):
    """Outcome/authority not proven. Never infer that a write was rejected."""


_VERSION = 1
_MAX_FRAME = 32 * 1024
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_OWNER = re.compile(r"[A-Za-z0-9._:@-]{1,128}\Z", re.ASCII)
_MAX_PEERS = 8


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
            if len(raw) != 12 or struct.unpack("3i", raw)[1] != service.runtime_uid:
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
    daemon_threads = True
    block_on_close = False
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
                    len(credentials) != 12
                    or struct.unpack("3i", credentials)[1] != self.expected_owner_uid
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