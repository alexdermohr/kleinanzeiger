"""Protected SQLite/Write owner composition for an already trusted A6 host.

This is the real packaged role factory used by the installed multi-UID test.
It never launches a root helper, grants an untrusted UID raw SQLite or certifies
an existing host FD/MAP_SHARED/WAL/restore cutover.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from http.server import ThreadingHTTPServer

from .completion_authority import _roles, create_owner_write_api_server
from .completion_dashboard import CompletionDashboardOwnerServer
from .fence_witness import FenceWitnessClient
from .write_api import WriteApiAccess, WriteCapability


@dataclass(slots=True)
class ProtectedOwnerRuntime:
    """Owner-UID-only resources; caller must drain/close write_server first."""

    write_server: ThreadingHTTPServer
    dashboard_owner: CompletionDashboardOwnerServer
    witness: FenceWitnessClient

    def close(self) -> None:
        # Owner socket is retired only after the public API is quiescent. Do
        # not mark a still-running browser mutation as safely abandoned.
        try:
            self.dashboard_owner.stop()
        finally:
            self.witness.close()


def create_protected_owner_runtime(
    *, database: Path, dashboard_socket: Path, witness_socket: Path,
    actor_socket: Path, witness_uid: int, actor_uid: int,
    runtime_uid: int, runtime_gid: int, principal: str,
    bearer_token: str, media_enabled: bool = True, write_port: int = 0,
) -> ProtectedOwnerRuntime:
    """Compose only the existing verified A6 owner, actor and witness paths.

    Every SQLite open is through the independent owner UID. The dashboard
    uses the SAME already-attested SnapshotStore as the Write API, never a
    second weaker database identity or a generic untrusted completion RPC.
    """
    paths = (database, dashboard_socket, witness_socket, actor_socket)
    if any(not isinstance(path, Path) or not path.is_absolute()
           or path != Path(os.path.normpath(path)) for path in paths):
        raise ValueError("absolute canonical protected owner paths required")
    if len(set(paths)) != len(paths):
        raise ValueError("protected owner resources must be distinct")
    if (type(runtime_gid) is not int or runtime_gid <= 0
            or type(media_enabled) is not bool
            or type(write_port) is not int or not 0 <= write_port <= 65535):
        raise ValueError("invalid protected owner runtime parameters")
    # No root or shared runtime/actor/witness UID may become an owner.
    _roles(os.geteuid(), witness_uid, actor_uid, runtime_uid)
    witness = FenceWitnessClient(
        witness_socket, expected_owner_uid=witness_uid,
    )
    dashboard_owner = None
    try:
        capabilities = {
            WriteCapability.CREATE, WriteCapability.UPDATE_CONTENT,
            WriteCapability.SET_STATE, WriteCapability.DELETE,
        }
        if media_enabled:
            capabilities.add(WriteCapability.CREATE_MEDIA)
        access = WriteApiAccess(
            principal=principal, bearer_token=bearer_token, writes_enabled=True,
            capabilities=frozenset(capabilities),
        )
        dashboard_owner = CompletionDashboardOwnerServer(
            database=database, socket_path=dashboard_socket,
            runtime_uid=runtime_uid, runtime_gid=runtime_gid,
            access=access, fence_witness=witness,
        )
        dashboard_owner.start()
        write_server = create_owner_write_api_server(
            store=dashboard_owner.store, access=access,
            authority_socket=actor_socket, authority_uid=actor_uid,
            runtime_uid=runtime_uid, fence_witness=witness,
            port=write_port,
        )
        return ProtectedOwnerRuntime(write_server, dashboard_owner, witness)
    except BaseException:
        # Startup must never leave an orphaned protected owner listener after
        # the actor / witness / media contract refused its admission.
        try:
            if dashboard_owner is not None:
                dashboard_owner.stop()
        finally:
            witness.close()
        raise
