"""Opt-in REAL Linux/Docker SQLite-owner recovery-fence security exercise.

The untrusted executor UID 50044 has no data mount in the intended topology.
This deliberately mounts a *read-only test-only* volume under UID 50044 to
prove that DAC independently denies old main/WAL/SHM raw access. No reused
installed Mark DB/volume, no Chrome, no platform Write and no host UID changes.

This is a partial owner-authority proof, NOT a migration / shared backup /
historical preopened-fd revocation or production recovery release gate.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from uuid import uuid4


REPO = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("MARK_STORAGE_OWNER_TEST_IMAGE", "")
IMAGE_OK = bool(re.fullmatch(r"sha256:[a-f0-9]{64}", IMAGE))
PYTHON = "/usr/local/bin/python"


def _docker(*args: str, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    value = subprocess.run(
        ["/usr/bin/docker", *args], capture_output=True, text=True,
        timeout=45, check=False,
    )
    if value.returncode and not allow_failure:
        raise AssertionError("isolated Docker action failed: " + value.stderr[-900:])
    return value


_SEED = r"""
import os
from pathlib import Path
from mark_api.storage import SnapshotStore
database=Path('/data')
rpc=Path('/rpc')
os.chown(database,50046,50046)
os.chmod(database,0o700)
os.chown(rpc,50046,50044)
os.chmod(rpc,0o2710)
os.setgroups([])
os.setgid(50046)
os.setuid(50046)
SnapshotStore(database/'mark.sqlite')
print('PRIVATE_EXISTING_SCHEMA_V2',flush=True)
"""

_SERVICE = r"""
from pathlib import Path
from mark_api.storage_owner import StorageOwnerServer
import time
server=StorageOwnerServer(
    database=Path('/data/mark.sqlite'),
    socket_path=Path('/rpc/owner.sock'),
    runtime_uid=50044,runtime_gid=50044,
)
server.start()
print('PRIVATE_STORAGE_OWNER_READY',flush=True)
while True:time.sleep(2)
"""

_RUNTIME = r"""
from datetime import datetime, timezone
from pathlib import Path
from mark_api.storage_owner import StorageOwnerClient, StorageOwnerError
from mark_api.storage import WriteApiRequestRecord
import json, socket, time
client=StorageOwnerClient(Path('/rpc/owner.sock'),expected_owner_uid=50046)
assert client.ready()
assert client.read_request('uid:test:1') is None
now=datetime(2026,10,11,12,0,tzinfo=timezone.utc)
request=client.claim(key='uid:test:1',sha='a'*64,owner='runtime:one',at=now)
assert request.created and isinstance(request.record,WriteApiRequestRecord)
started=client.begin(key='uid:test:1',sha='a'*64,owner='runtime:one',at=now)
assert started.state=='in_progress' and started.execution_started_at is not None
assert client.create_pending(exclude_key='uid:test:next',owner='runtime:one') is True
# Typed RPC must not be a SQL, arbitrary method, completion or clearance broker.
for operation in ('clearance','complete','SQL','execute','restore','delete'):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(4)
        sock.connect('/rpc/owner.sock')
        payload={'version':1,'op':operation,'key':'uid:test:1',
                 'sql':'DELETE FROM write_api_requests'}
        sock.sendall((json.dumps(payload)+'\n').encode())
        received=b''
        while not received.endswith(b'\n'):
            part=sock.recv(4096)
            assert part
            received+=part
        reply=json.loads(received)
        assert reply=={'ok':False,'error':'request_denied'},reply
assert client.create_pending(exclude_key='uid:test:next',owner='runtime:one')
# Old successful completion cannot be forged by the compromised runtime.
assert client.read_request('uid:test:1').state=='in_progress'
# A lost ACK is UNKNOWN: only perform observation-only readback, not replay.
with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
    sock.settimeout(4)
    sock.connect('/rpc/owner.sock')
    request={'version':1,'op':'claim','key':'uid:lost-ack','sha':'b'*64,
             'owner':'runtime:one','at':now.isoformat()}
    sock.sendall((json.dumps(request)+'\n').encode())
    sock.shutdown(socket.SHUT_RDWR)
for _ in range(100):
    record=client.read_request('uid:lost-ack')
    if record is not None:break
    time.sleep(0.02)
assert record is not None and record.request_sha256=='b'*64
print('STORAGE_TYPED_FENCE_AND_LOST_ACK_GREEN',flush=True)
"""

_FOREIGN_DAC = r"""
import os, mmap
from pathlib import Path
db=Path('/data')
denied=0
for name in ('mark.sqlite','mark.sqlite-wal','mark.sqlite-shm'):
    path=db/name
    for flags in (os.O_RDONLY,os.O_RDWR):
        try:
            fd=os.open(path,flags)
        except PermissionError:
            denied+=1
            continue
        except FileNotFoundError:
            # A temporary WAL/SHM inode may not exist between transactions.
            assert name != 'mark.sqlite', 'main database unexpectedly missing'
            continue
        else:
            os.close(fd)
            raise AssertionError('UID50044 opened private SQLite inode '+name)
assert denied>=2, 'private existing main database was not DAC-denied'
print('EXECUTOR_RAW_SQLITE_FD_MAPPING_DENIED',flush=True)
"""

_PERSISTED_AFTER_KILL = r"""
import sqlite3
from pathlib import Path
database=Path('/data/mark.sqlite')
db=sqlite3.connect('file:/data/mark.sqlite?mode=ro',uri=True)
try:
    persisted=db.execute(
        'SELECT state,request_sha256,execution_started_at FROM write_api_requests '
        'WHERE idempotency_key=?',('uid:test:1',)
    ).fetchone()
    assert persisted is not None
    assert persisted[0]=='in_progress' and persisted[1]=='a'*64
    assert persisted[2] is not None
    late=db.execute('SELECT request_sha256 FROM write_api_requests '
                    'WHERE idempotency_key=?',('uid:lost-ack',)).fetchone()
    assert late is not None and late[0]=='b'*64
    cleared=db.execute('SELECT COUNT(*) FROM write_recovery_clearances').fetchone()
    assert cleared[0]==0
finally:
    db.close()
print('SIGKILL_PERSISTED_OPEN_FENCES_AND_NO_CLEARANCE',flush=True)
"""


_WRONG_UID = r"""
from pathlib import Path
from mark_api.storage_owner import StorageOwnerClient,StorageOwnerError
try:
    StorageOwnerClient(Path('/rpc/owner.sock'),expected_owner_uid=50047).ready()
except StorageOwnerError:
    print('FORGED_OWNER_UID_REJECTED',flush=True)
else:
    raise AssertionError('forged storage owner uid attestation')
"""


@unittest.skipUnless(IMAGE_OK, "Explicit immutable preexisting Docker test image needed")
class RealStorageOwnerTests(unittest.TestCase):
    def test_foreign_uid_cannot_rewrite_or_clear_private_sqlite_fences(self) -> None:
        suffix = uuid4().hex[:12]
        store_volume = "mark-store-owner-db-" + suffix
        socket_volume = "mark-store-owner-rpc-" + suffix
        service = "mark-store-owner-" + suffix
        volumes: list[str] = []
        container_started = False
        with tempfile.TemporaryDirectory(prefix="mark-storage-owner-real-") as temp:
            root = Path(temp)
            source = root / "src"
            shutil.copytree(
                REPO / "src" / "mark_api", source / "mark_api",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            for item in (root, source, *source.rglob("*")):
                if item.is_dir():
                    item.chmod(0o755)
                elif item.is_file():
                    item.chmod(0o644)
            code = [
                "--mount", f"type=bind,source={source},target=/src,readonly",
                "-e", "PYTHONPATH=/src", "-e", "PYTHONDONTWRITEBYTECODE=1",
                "--workdir", "/", "--entrypoint", PYTHON,
            ]
            store = ["--mount", f"type=volume,source={store_volume},target=/data"]
            rpc = ["--mount", f"type=volume,source={socket_volume},target=/rpc"]
            try:
                for name in (store_volume, socket_volume):
                    _docker("volume", "create", name)
                    volumes.append(name)
                seed = _docker("run", "--rm", "--network", "none",
                               "--user", "0:0", *code, *store, *rpc,
                               IMAGE, "-B", "-c", _SEED)
                self.assertIn("PRIVATE_EXISTING_SCHEMA_V2", seed.stdout)
                _docker(
                    "run", "-d", "--name", service, "--network", "none",
                    "--user", "50046:50046", "--read-only",
                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                    *code, *store, *rpc, IMAGE, "-B", "-c", _SERVICE,
                )
                container_started = True
                self._ready(service, "PRIVATE_STORAGE_OWNER_READY")
                runtime = _docker(
                    "run", "--rm", "--network", "none", "--read-only",
                    "--user", "50044:50044", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true",
                    *code, *rpc, IMAGE, "-B", "-c", _RUNTIME,
                )
                self.assertIn("STORAGE_TYPED_FENCE_AND_LOST_ACK_GREEN", runtime.stdout)
                # This additional mount exists ONLY for the hostile DAC test.
                hostile = _docker(
                    "run", "--rm", "--network", "none", "--read-only",
                    "--user", "50044:50044", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true",
                    *code,
                    "--mount", f"type=volume,source={store_volume},target=/data,readonly",
                    IMAGE, "-B", "-c", _FOREIGN_DAC,
                )
                self.assertIn("EXECUTOR_RAW_SQLITE_FD_MAPPING_DENIED", hostile.stdout)
                wrong = _docker(
                    "run", "--rm", "--network", "none", "--read-only",
                    "--user", "50044:50044", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true",
                    *code, *rpc, IMAGE, "-B", "-c", _WRONG_UID,
                )
                self.assertIn("FORGED_OWNER_UID_REJECTED", wrong.stdout)
                # SIGKILL: an old socket must not be silently deleted on restart.
                _docker("rm", "-f", service)
                container_started = False
                replay = _docker(
                    "run", "--rm", "--network", "none", "--read-only",
                    "--user", "50046:50046", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true",
                    *code,
                    "--mount", f"type=volume,source={store_volume},target=/data,readonly",
                    IMAGE, "-B", "-c", _PERSISTED_AFTER_KILL,
                )
                self.assertIn("SIGKILL_PERSISTED_OPEN_FENCES_AND_NO_CLEARANCE",
                              replay.stdout)
                bad = _docker(
                    "run", "--rm", "--network", "none", "--read-only",
                    "--user", "50046:50046", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true",
                    *code, *store, *rpc, IMAGE, "-B", "-c", _SERVICE,
                    allow_failure=True,
                )
                self.assertNotEqual(bad.returncode, 0)
                self.assertIn("refusing to replace storage RPC socket", bad.stderr)
            finally:
                if container_started:
                    result = _docker("rm", "-f", service, allow_failure=True)
                    self.assertEqual(result.returncode, 0, result.stderr[-300:])
                for volume in reversed(volumes):
                    result = _docker("volume", "rm", volume, allow_failure=True)
                    self.assertEqual(result.returncode, 0, result.stderr[-300:])

    def _ready(self, container: str, marker: str) -> None:
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            found = _docker("logs", container)
            if marker in found.stdout:
                return
            if "Traceback" in found.stderr + found.stdout:
                self.fail("synthetic storage service failed: " + found.stdout[-600:])
            time.sleep(0.2)
        self.fail("synthetic storage service did not start")