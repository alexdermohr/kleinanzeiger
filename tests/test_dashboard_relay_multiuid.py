"""Opt-in REAL Linux/Docker peer-UID and network-namespace regression.

Set MARK_RELAY_TEST_IMAGE to an already present immutable sha256 image ID.
Only creates uniquely named, temporary Docker volumes/network/containers, then
destroys them. No Kleinanzeigen or production DB/browser/account is contacted.
Without this explicit image this test is skipped, never called a release proof.
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
IMAGE = os.environ.get("MARK_RELAY_TEST_IMAGE", "")
IMAGE_OK = bool(re.fullmatch(r"sha256:[0-9a-f]{64}", IMAGE))
PYTHON = "/opt/mark-api/venv/bin/python"


def _docker(*args: str, timeout: float = 45) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["/usr/bin/docker", *args], capture_output=True, text=True,
        timeout=timeout, check=False,
    )
    if proc.returncode:
        raise AssertionError(
            "isolated Docker invocation failed (no retry); "
            + proc.stderr[:600]
        )
    return proc


_BACKEND = r"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading, time
from mark_api.dashboard_relay import UnixDashboardRelay
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = b'{"ready":true}' if self.path == '/readyz' else b'{"error":"not_found"}'
        self.send_response(200 if self.path == '/readyz' else 404)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *_): pass
http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=http.serve_forever, daemon=True).start()
relay = UnixDashboardRelay(
    socket_path=Path('/bridge/dashboard.sock'), frontend_uid=50042,
    frontend_gid=50042, dashboard_host='127.0.0.1',
    dashboard_port=http.server_port,
)
relay.start()
print('READY', http.server_port, flush=True)
while True: time.sleep(2)
"""

_FRONTEND = r"""
from http.client import HTTPConnection, HTTPException
from pathlib import Path
import os, socket
from mark_api.dashboard_relay import FrontendDashboardRelay
relay = FrontendDashboardRelay(
    socket_path=Path('/bridge/dashboard.sock'),
    expected_owner_uid=50044, listen_host='127.0.0.1', listen_port=0,
)
relay.start()
try:
    conn = HTTPConnection('127.0.0.1', relay.server_port, timeout=5)
    conn.request('GET', '/readyz')
    res = conn.getresponse()
    assert (res.status, res.read()) == (200, b'{"ready":true}')
    conn.close()
    conn = HTTPConnection('127.0.0.1', relay.server_port, timeout=5)
    conn.request('GET', '/json/list')
    res = conn.getresponse()
    assert res.status == 404 and res.read() == b'{"error":"not_found"}'
    conn.close()
    # Host-only backend TCP is not present in the frontend namespace.
    port = int(os.environ['MARK_TEST_OWNER_PORT'])
    probe = socket.socket()
    probe.settimeout(.6)
    assert probe.connect_ex(('127.0.0.1', port)) != 0
    probe.close()
    # Fake owner UID cannot attest even if the socket path is unchanged.
    fake = FrontendDashboardRelay(
        socket_path=Path('/bridge/dashboard.sock'),
        expected_owner_uid=50045, listen_host='127.0.0.1', listen_port=0,
    )
    fake.start()
    try:
        bad = HTTPConnection('127.0.0.1', fake.server_port, timeout=5)
        try:
            bad.request('GET', '/readyz')
            bad.getresponse()
        except (OSError, HTTPException):
            pass
        else:
            raise AssertionError('UID mismatch was not rejected')
        finally:
            bad.close()
    finally:
        assert fake.stop()
    assert not Path('/var/lib/mark-api/mark.sqlite').exists()
    print('MULTI_UID_GREEN', flush=True)
finally:
    assert relay.stop()
"""

_FRONTEND_PUBLISHED = r"""
from pathlib import Path
import time
from mark_api.dashboard_relay import FrontendDashboardRelay
relay = FrontendDashboardRelay(
    socket_path=Path('/bridge/dashboard.sock'),
    expected_owner_uid=50044, listen_host='0.0.0.0', listen_port=8875,
)
relay.start()
print('FRONTEND_READY', flush=True)
while True: time.sleep(2)
"""

_UNAUTHORIZED = r"""
from pathlib import Path
import socket
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    sock.connect('/bridge/dashboard.sock')
except PermissionError:
    print('FOREIGN_UID_DENIED', flush=True)
else:
    raise AssertionError('foreign UID reached private socket')
finally:
    sock.close()
"""


@unittest.skipUnless(IMAGE_OK, "No explicitly attested synthetic immutable Docker image")
class RealMultiUidDashboardRelayTests(unittest.TestCase):
    def test_foreign_uid_cannot_open_or_map_owner_sqlite_wal_shm(self) -> None:
        """Actual Linux-Docker file DAC under distinct numeric UIDs.

        This does NOT attest old pre-cutover writable FDs or mmap references;
        those require an offline cutover with live host process inventory.
        """
        volume = "mark-relay-store-" + uuid4().hex[:14]
        created = False
        files = ("mark.sqlite", "mark.sqlite-wal", "mark.sqlite-shm")
        seed = """
import os
from pathlib import Path
directory = Path('/isolated-store')
os.chown(directory, 50044, 50044)
os.chmod(directory, 0o700)
for name in ('mark.sqlite', 'mark.sqlite-wal', 'mark.sqlite-shm'):
    path = directory / name
    path.write_bytes(b'0' * 4096)
    os.chown(path, 50044, 50044)
    path.chmod(0o600)
"""
        owner = """
import mmap, os
from pathlib import Path
for name in ('mark.sqlite', 'mark.sqlite-wal', 'mark.sqlite-shm'):
    handle = os.open(Path('/isolated-store') / name, os.O_RDWR)
    try:
        with mmap.mmap(handle, 4096, flags=mmap.MAP_SHARED,
                       prot=mmap.PROT_READ | mmap.PROT_WRITE) as view:
            view[0:1] = b'1'
    finally:
        os.close(handle)
print('OWNER_RW_MAPPING_OK', flush=True)
"""
        foreign = """
import os
from pathlib import Path
for name in ('mark.sqlite', 'mark.sqlite-wal', 'mark.sqlite-shm'):
    for flags in (os.O_RDONLY, os.O_RDWR):
        try:
            fd = os.open(Path('/isolated-store') / name, flags)
        except PermissionError:
            continue
        else:
            os.close(fd)
            raise AssertionError('untrusted runtime opened owner file: ' + name)
print('FOREIGN_UID_SQLITE_WAL_SHM_DENIED', flush=True)
"""
        try:
            _docker("volume", "create", volume)
            created = True
            common = (
                "run", "--rm", "--network", "none",
                "--mount", f"type=volume,source={volume},target=/isolated-store",
                "--entrypoint", PYTHON, IMAGE, "-B", "-c",
            )
            _docker(*common[:6], "--user", "0:0", *common[6:], seed)
            owner_result = _docker(
                *common[:6], "--user", "50044:50044", *common[6:], owner,
            )
            self.assertIn("OWNER_RW_MAPPING_OK", owner_result.stdout)
            denied = _docker(
                *common[:6], "--user", "50042:50042", *common[6:], foreign,
            )
            self.assertIn("FOREIGN_UID_SQLITE_WAL_SHM_DENIED", denied.stdout)
        finally:
            if created:
                cleanup = subprocess.run(
                    ["/usr/bin/docker", "volume", "rm", "-f", volume],
                    capture_output=True, text=True, timeout=25, check=False,
                )
                self.assertEqual(
                    cleanup.returncode, 0,
                    "temporary synthetic owner volume cleanup failed",
                )

    def test_owner_socket_permissions_and_frontend_net_namespace(self) -> None:
        suffix = uuid4().hex[:14]
        owner_name = "mark-relay-owner-" + suffix
        frontend_name = "mark-relay-frontend-" + suffix
        socket_volume = "mark-relay-socket-" + suffix
        isolated_net = "mark-relay-internal-" + suffix
        created_volume = False
        created_network = False
        with tempfile.TemporaryDirectory(prefix="mark-relay-multiuid-") as temp:
            source = Path(temp) / "src"
            shutil.copytree(
                REPO / "src" / "mark_api", source / "mark_api",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            for path in source.rglob("*"):
                if path.is_dir():
                    path.chmod(0o755)
                elif path.is_file():
                    path.chmod(0o644)
            source.chmod(0o755)
            try:
                _docker("volume", "create", socket_volume)
                created_volume = True
                # Docker internal bridges suppress host port publication on
                # this daemon. Keep a distinct bridge namespace so Chrome
                # 127.0.0.1 is still unreachable, but explicitly test the
                # published localhost listener instead of assuming it works.
                _docker(
                    "network", "create",
                    "--opt", "com.docker.network.bridge.enable_ip_masquerade=false",
                    "--opt", "com.docker.network.bridge.enable_icc=false",
                    isolated_net,
                )
                created_network = True
                _docker(
                    "run", "--rm", "--network", "none", "--user", "0:0",
                    "--mount", f"type=volume,source={socket_volume},target=/bridge",
                    "--entrypoint", "/bin/sh", IMAGE, "-ec",
                    "chown 50044:50042 /bridge; chmod 2710 /bridge",
                )
                _docker(
                    "run", "-d", "--name", owner_name, "--network", "host",
                    "--user", "50044:50044", "--read-only",
                    "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true",
                    "--mount", f"type=volume,source={socket_volume},target=/bridge",
                    "--mount", f"type=bind,source={source},target=/src,readonly",
                    "-e", "PYTHONPATH=/src", "-e", "PYTHONDONTWRITEBYTECODE=1",
                    "--workdir", "/", "--entrypoint", PYTHON,
                    IMAGE, "-B", "-c", _BACKEND,
                )
                port = None
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    logs = _docker("logs", owner_name).stdout
                    found = re.search(r"READY (\d+)", logs)
                    if found:
                        port = int(found.group(1))
                        break
                    time.sleep(.25)
                self.assertIsNotNone(port, "trusted owner failed to bind its UDS/HTTP")
                common = [
                    "run", "--rm", "--network", isolated_net,
                    "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true",
                    "--mount", f"type=volume,source={socket_volume},target=/bridge,readonly",
                    "--mount", f"type=bind,source={source},target=/src,readonly",
                    "-e", "PYTHONPATH=/src", "-e", "PYTHONDONTWRITEBYTECODE=1",
                    "--workdir", "/", "--entrypoint", PYTHON,
                ]
                front = _docker(
                    *common, "--user", "50042:50042",
                    "-e", f"MARK_TEST_OWNER_PORT={port}",
                    IMAGE, "-B", "-c", _FRONTEND,
                )
                self.assertIn("MULTI_UID_GREEN", front.stdout)
                foreign = _docker(
                    *common, "--user", "50045:50045",
                    IMAGE, "-B", "-c", _UNAUTHORIZED,
                )
                self.assertIn("FOREIGN_UID_DENIED", foreign.stdout)
                # Unlike a network:none smoke, the real internal network must
                # still publish only the host's 127.0.0.1 dashboard port.
                _docker(
                    "run", "-d", "--name", frontend_name,
                    "--network", isolated_net,
                    "-p", "127.0.0.1::8875",
                    "--user", "50042:50042", "--read-only",
                    "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true",
                    "--mount", f"type=volume,source={socket_volume},target=/bridge,readonly",
                    "--mount", f"type=bind,source={source},target=/src,readonly",
                    "-e", "PYTHONPATH=/src", "-e", "PYTHONDONTWRITEBYTECODE=1",
                    "--workdir", "/", "--entrypoint", PYTHON,
                    IMAGE, "-B", "-c", _FRONTEND_PUBLISHED,
                )
                binding = _docker("port", frontend_name, "8875/tcp").stdout.strip()
                self.assertRegex(binding, r"^127\.0\.0\.1:\d+$")
                public_port = int(binding.rsplit(":", 1)[1])
                from http.client import HTTPConnection
                reached = False
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    connection = HTTPConnection("127.0.0.1", public_port, timeout=2)
                    try:
                        connection.request("GET", "/readyz")
                        response = connection.getresponse()
                        reached = (
                            response.status == 200
                            and response.read() == b'{"ready":true}'
                        )
                    except OSError:
                        time.sleep(.25)
                    finally:
                        connection.close()
                    if reached:
                        break
                self.assertTrue(reached, "localhost-published isolated frontend unreachable")
            finally:
                subprocess.run(
                    ["/usr/bin/docker", "rm", "-f", frontend_name],
                    capture_output=True, timeout=25, check=False,
                )
                subprocess.run(
                    ["/usr/bin/docker", "rm", "-f", owner_name],
                    capture_output=True, timeout=25, check=False,
                )
                if created_network:
                    subprocess.run(
                        ["/usr/bin/docker", "network", "rm", isolated_net],
                        capture_output=True, timeout=25, check=False,
                    )
                if created_volume:
                    subprocess.run(
                        ["/usr/bin/docker", "volume", "rm", "-f", socket_volume],
                        capture_output=True, timeout=25, check=False,
                    )


if __name__ == "__main__":
    unittest.main()