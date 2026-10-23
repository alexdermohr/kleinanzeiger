"""Installed non-media A6 acceptance in a disposable Linux PID/net namespace.

The root supervisor owns only its temporary directory and child processes.
Actual product roles use different UIDs, no capabilities and no_new_privs.
No real platform, provider or Docker access. This is not old-inode host cutover
or complete backup/media release proof. Never run directly in a host namespace.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import ctypes
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import select
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
from threading import Event, Lock, Thread
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import mark_api
from mark_api.application import MarkService
from mark_api.completion_authority import CompletionAuthorityServer, create_owner_write_api_server
from mark_api.completion_witness import AuthorityWitnessClient, CompletionWitnessLedger, CompletionWitnessServer
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.fence_witness import FenceWitnessClient, _WitnessUnixHandler
from mark_api.results import ReadResult, ReadStatus
from mark_api.storage import SnapshotStore
from mark_api.write_api import WriteApiAccess, WriteCapability

UIDS = {"owner": 50111, "actor": 50112, "witness": 50113, "platform": 50114, "runtime": 50115}
PRINCIPAL = "synthetic-protected-customer"


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _emit(value):
    print(_json(value), flush=True)


def _drop(uid):
    assert os.geteuid() == 0 and uid in UIDS.values()
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.prctl(38, 1, 0, 0, 0) == 0  # PR_SET_NO_NEW_PRIVS
    libc.prctl(47, 4, 0, 0, 0)  # PR_CAP_AMBIENT_CLEAR_ALL
    for capability in range(64):
        result = libc.prctl(24, capability, 0, 0, 0)  # PR_CAPBSET_DROP
        assert result == 0 or ctypes.get_errno() == 22
    os.setgroups([])
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    assert libc.prctl(4, 0, 0, 0, 0) == 0  # PR_SET_DUMPABLE after changing UID
    os.umask(0o077)
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    assert all(int(status[key].strip(), 16) == 0 for key in ("CapEff", "CapPrm", "CapInh", "CapBnd", "CapAmb"))
    assert status["NoNewPrivs"].strip() == "1" and os.getgroups() == []
    assert os.getresuid() == (uid, uid, uid) and os.getresgid() == (uid, uid, uid)


def _http(port, method, path, *, token, payload=None, key=None):
    headers = {"Authorization": "Bearer " + token}
    data = None if payload is None else _json(payload).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    if key is not None:
        headers["Idempotency-Key"] = key
    request = Request("http://127.0.0.1:" + str(port) + path, data=data, headers=headers, method=method)
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=8)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read(1024 * 1024)), dict(response.headers)


def _wait_server(server, ready, *, close=None):
    stopped = Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    managed = hasattr(server, "start")
    thread = None
    if managed:
        server.start()
    else:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
    _emit({"ready": True, "uid": os.geteuid(), **ready})
    stopped.wait(150)
    if managed:
        server.close()
    else:
        server.shutdown()
        if hasattr(server, "wait_for_active_handlers"):
            assert server.wait_for_active_handlers(10)
        server.server_close()
        thread.join(3)
        assert not thread.is_alive()
    if close is not None:
        close()


def _platform(config):
    guard = Lock()
    ads = {"701": {"ad_id": "701", "title": "Existing table", "description": "Existing description", "state": "active"}}
    mutations = []
    modes = {"unknown_next": False, "unreadable": False, "block_next": False, "blocked_readers": 0}
    release = Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data):
            encoded = _json(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def dispatch(self):
            control = self.path.startswith("/control/")
            token = config["control_token"] if control else config["platform_token"]
            if self.headers.get("Authorization") != "Bearer " + token:
                self.reply(401, {"error": "unauthorized"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            assert 0 <= length <= 16384
            payload = json.loads(self.rfile.read(length)) if length else {}
            if control:
                with guard:
                    if self.path == "/control/unknown":
                        modes["unknown_next"] = True
                    elif self.path == "/control/block":
                        modes["block_next"] = True
                    elif self.path == "/control/release":
                        release.set()
                    response = {"mutations": list(mutations), "ads": list(ads.values()), "modes": dict(modes)}
                self.reply(200, response)
                return
            if self.command == "GET":
                with guard:
                    blocked = modes["block_next"] and bool(mutations)
                    if blocked:
                        modes["blocked_readers"] += 1
                if blocked and not release.wait(20):
                    self.reply(503, {"error": "synthetic_readback_not_released"})
                    return
                with guard:
                    status = 503 if modes["unreadable"] else 200
                    response = {"ads": list(ads.values())}
                self.reply(status, response)
                return
            with guard:
                segments = self.path.strip("/").split("/")
                if self.command == "POST" and segments == ["ads"]:
                    ad_id = str(900 + len(mutations))
                    ads[ad_id] = {"ad_id": ad_id, "title": payload["title"], "description": payload["description"], "state": "active"}
                else:
                    assert len(segments) in (2, 3) and segments[0] == "ads"
                    ad_id = segments[1]
                    assert ad_id in ads
                    if self.command == "DELETE":
                        del ads[ad_id]
                    elif self.command == "PATCH":
                        assert set(payload) <= {"title", "description"}
                        ads[ad_id].update(payload)
                    elif self.command == "POST" and segments[2] in ("pause", "activate"):
                        ads[ad_id]["state"] = "paused" if segments[2] == "pause" else "active"
                    else:
                        raise AssertionError("unexpected synthetic mutation")
                mutations.append({"method": self.command, "path": self.path, "ad_id": ad_id})
                if modes["unknown_next"]:
                    modes["unreadable"] = True
            self.reply(200, {"accepted": True})

        do_GET = dispatch
        do_POST = dispatch
        do_PATCH = dispatch
        do_DELETE = dispatch

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    _wait_server(server, {"port": server.server_port})


class PlatformAdapter:
    def __init__(self, port, token):
        self.port, self.token = port, token

    def read_ads(self):
        try:
            status, data, _ = _http(self.port, "GET", "/ads", token=self.token)
            if status != 200:
                return ReadResult.failure(ReadStatus.PARSE_ERROR, error="synthetic_postread_unavailable")
            rows = tuple(AdSnapshot(ad_id=row["ad_id"], title=row["title"], description=row["description"],
                                   lifecycle_state=LifecycleState(row["state"]), observed_at=datetime.now(timezone.utc),
                                   source="synthetic-independent-platform") for row in data["ads"])
            return ReadResult.success_nonempty(rows) if rows else ReadResult.success_empty(())
        except (OSError, URLError, ValueError):
            return ReadResult.failure(ReadStatus.PARSE_ERROR, error="synthetic_postread_unavailable")

    def write(self, method, path, payload=None):
        status, _, _ = _http(self.port, method, path, token=self.token, payload=payload)
        assert status == 200

    def create_ad(self, request):
        self.write("POST", "/ads", asdict(request))

    def set_state(self, ad_id, state):
        self.write("POST", "/ads/" + ad_id + ("/pause" if state is LifecycleState.PAUSED else "/activate"))

    def delete_ad(self, ad_id):
        self.write("DELETE", "/ads/" + ad_id)

    def update_content(self, ad_id, *, title=None, description=None):
        self.write("PATCH", "/ads/" + ad_id, {k: v for k, v in (("title", title), ("description", description)) if v is not None})


def _child(role, config):
    _drop(UIDS[role] if role in UIDS else UIDS["runtime"])
    if role == "platform":
        _platform(config)
    elif role == "witness":
        ledger = CompletionWitnessLedger(Path(config["db"]), principal=PRINCIPAL, initialize=not Path(config["db"]).exists())
        server = CompletionWitnessServer(ledger=ledger, owner_socket=Path(config["owner_socket"]),
                                        authority_socket=Path(config["actor_socket"]), owner_uid=UIDS["owner"], owner_gid=UIDS["owner"],
                                        authority_uid=UIDS["actor"], authority_gid=UIDS["actor"], runtime_uid=UIDS["runtime"])
        if config.get("drop_completion_key"):
            class DropCompletionAck(_WitnessUnixHandler):
                def _dispatch(self, request, target, session):
                    answer = super()._dispatch(request, target, session)
                    self._drop_once = request.get("op") == "completed" and request.get("key") == config["drop_completion_key"]
                    return answer

                def _send(self, value):
                    if getattr(self, "_drop_once", False):
                        self._drop_once = False
                        self.request.shutdown(socket.SHUT_RDWR)
                        raise ConnectionResetError("synthetic lost completion ACK")
                    super()._send(value)
            server._owner.RequestHandlerClass = DropCompletionAck
        _wait_server(server, {}, close=ledger.close)
    elif role == "actor":
        adapter = PlatformAdapter(config["platform_port"], config["platform_token"])
        service = MarkService(owner_reader=adapter, management_reader=adapter, delete_confirmation_reader=adapter,
                              reaction_reader=adapter, state_writer=adapter, delete_writer=adapter, content_writer=adapter,
                              create_writer=adapter,
                              content_reader_factory=lambda _: PlatformAdapter(config["platform_port"], config["platform_token"]),
                              store=None, writes_enabled=True)
        witness = AuthorityWitnessClient(Path(config["witness_socket"]), expected_owner_uid=UIDS["witness"])
        server = CompletionAuthorityServer(socket_path=Path(config["socket"]), owner_uid=UIDS["owner"], owner_gid=UIDS["owner"],
                                           runtime_uid=UIDS["runtime"], principal=PRINCIPAL, service=service, witness_client=witness)
        _wait_server(server, {})
    elif role == "owner":
        store = SnapshotStore(Path(config["db"]))
        witness = FenceWitnessClient(Path(config["witness_socket"]), expected_owner_uid=UIDS["witness"])
        access = WriteApiAccess(principal=PRINCIPAL, bearer_token=config["api_token"], writes_enabled=True,
                                capabilities=frozenset({WriteCapability.CREATE, WriteCapability.UPDATE_CONTENT, WriteCapability.SET_STATE, WriteCapability.DELETE}))
        server = create_owner_write_api_server(store=store, access=access, authority_socket=Path(config["actor_socket"]),
                                               authority_uid=UIDS["actor"], runtime_uid=UIDS["runtime"], fence_witness=witness)
        _wait_server(server, {"port": server.server_port}, close=witness.close)
    elif role == "request":
        result = _http(config["port"], config["method"], config["path"], token=config["api_token"],
                       payload=config.get("payload"), key=config["key"])
        _emit({"status": result[0], "body": result[1], "replayed": result[2].get("Idempotency-Replayed")})
    elif role == "probe":
        for path in config["files"]:
            try:
                descriptor = os.open(path, os.O_RDWR)
            except PermissionError:
                continue
            else:
                os.close(descriptor)
                raise AssertionError("untrusted UID opened protected resource")
        for path in config["sockets"]:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.settimeout(2)
                try:
                    peer.connect(path)
                    peer.sendall(b'{"version":1,"op":"operator_clear","key":"forged","sha":"0000000000000000000000000000000000000000000000000000000000000000","evidence":"forged"}\n')
                    response = peer.recv(1024)
                except (PermissionError, ConnectionResetError, BrokenPipeError):
                    continue
                assert not response or json.loads(response).get("ok") is False
        _emit({"denied": True, "uid": os.geteuid(), "file_count": len(config["files"]), "socket_count": len(config["sockets"])})
    else:
        raise AssertionError("unknown child role")


class Scenario:
    def __init__(self, root, *, lost_ack=False):
        self.root = root
        self.children = []
        self.configs = {}
        self.procs = {}
        self.api_token = secrets.token_hex(24)
        self.platform_token = secrets.token_hex(24)
        self.control_token = secrets.token_hex(24)
        root.mkdir(mode=0o755)
        self.owner_db = root / "owner-data" / "snapshot.sqlite"
        self.witness_db = root / "witness-data" / "witness.sqlite"
        self.owner_witness_socket = root / "w-owner" / "w.sock"
        self.actor_witness_socket = root / "w-actor" / "w.sock"
        self.actor_socket = root / "actor-rpc" / "a.sock"
        for path, uid, gid, mode in (
            (self.owner_db.parent, UIDS["owner"], UIDS["owner"], 0o700),
            (self.witness_db.parent, UIDS["witness"], UIDS["witness"], 0o700),
            (self.owner_witness_socket.parent, UIDS["witness"], UIDS["owner"], 0o2710),
            (self.actor_witness_socket.parent, UIDS["witness"], UIDS["actor"], 0o2710),
            (self.actor_socket.parent, UIDS["actor"], UIDS["owner"], 0o2710),
        ):
            path.mkdir()
            os.chown(path, uid, gid)
            os.chmod(path, mode)
        try:
            self.platform_port = self.start("platform", {"platform_token": self.platform_token, "control_token": self.control_token})["port"]
            self.start("witness", {"db": str(self.witness_db), "owner_socket": str(self.owner_witness_socket),
                                   "actor_socket": str(self.actor_witness_socket), "drop_completion_key": "lost:pause" if lost_ack else None})
            self.start("actor", {"socket": str(self.actor_socket), "witness_socket": str(self.actor_witness_socket),
                                 "platform_port": self.platform_port, "platform_token": self.platform_token})
            self.api_port = self.start("owner", {"db": str(self.owner_db), "witness_socket": str(self.owner_witness_socket),
                                                 "actor_socket": str(self.actor_socket), "api_token": self.api_token})["port"]
        except BaseException:
            self.close()
            raise

    def spawn(self, role, config):
        # Private pipe, never argv/environ. exec prevents an untrusted child
        # from inheriting supervisor memory containing other role credentials.
        child = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "--child", role],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, close_fds=True,
                                 env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        self.children.append(child)
        child.stdin.write(_json(config) + "\n")
        child.stdin.close()
        return child

    def receive(self, child, timeout=25):
        if not select.select([child.stdout], [], [], timeout)[0]:
            raise AssertionError("bounded synthetic child response missing")
        line = child.stdout.readline()
        if not line:
            error = child.stderr.read(8192) if child.poll() is not None else "child exited before readiness"
            raise AssertionError(error)
        return json.loads(line)

    def start(self, role, config):
        self.configs[role] = config
        child = self.spawn(role, config)
        self.procs[role] = child
        ready = self.receive(child)
        assert ready["ready"] is True and ready["uid"] == UIDS[role]
        return ready

    def stop(self, role, *, kill=False):
        child = self.procs.pop(role)
        if child.poll() is None:
            child.send_signal(signal.SIGKILL if kill else signal.SIGTERM)
        try:
            child.wait(15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(5)
            raise AssertionError("child shutdown did not drain")
        if not kill:
            assert child.returncode == 0, child.stderr.read(8192)

    def restart_owner(self):
        if "owner" in self.procs:
            self.stop("owner")
        deadline = time.monotonic() + 5
        while True:
            with closing(sqlite3.connect("file:" + str(self.witness_db) + "?mode=ro", uri=True)) as conn:
                active = conn.execute("SELECT count(*) FROM sessions WHERE state='active'").fetchone()[0]
            if active == 0:
                break
            assert time.monotonic() < deadline
            time.sleep(0.02)
        self.api_port = self.start("owner", self.configs["owner"])["port"]

    def request(self, method, path, key, payload=None):
        child = self.spawn("request", {"port": self.api_port, "api_token": self.api_token,
                                       "method": method, "path": path, "key": key, "payload": payload})
        result = self.receive(child)
        child.wait(5)
        assert child.returncode == 0, child.stderr.read(8192)
        return result

    def state(self, mode="state"):
        status, data, _ = _http(self.platform_port, "GET", "/control/" + mode, token=self.control_token)
        assert status == 200
        return data

    def probe(self, *, peer_only=False):
        paths = [self.actor_socket, self.actor_witness_socket, self.owner_witness_socket]
        old = []
        if peer_only:
            # Deliberately expose ONLY disposable test sockets: rejection must
            # come from SO_PEERCRED, not merely from pathname access controls.
            for path in paths:
                old.append((path.parent, stat.S_IMODE(path.parent.stat().st_mode)))
                old.append((path, stat.S_IMODE(path.stat().st_mode)))
                os.chmod(path.parent, 0o755)
                os.chmod(path, 0o666)
        try:
            files = [str(self.owner_db) + suffix for suffix in ("", "-wal", "-shm")] + [str(self.witness_db)]
            child = self.spawn("probe", {"files": files, "sockets": list(map(str, paths))})
            result = self.receive(child)
            child.wait(5)
            assert child.returncode == 0 and result["denied"] is True
        finally:
            for path, mode in reversed(old):
                os.chmod(path, mode)

    def crash_after_submit(self, role, *, graceful=False):
        self.state("block")
        key = "crash:" + role + (":term" if graceful else ":kill")
        child = self.spawn("request", {"port": self.api_port, "api_token": self.api_token,
                                       "method": "POST", "path": "/api/write/ads/701/pause", "key": key})
        deadline = time.monotonic() + 8
        while True:
            evidence = self.state()
            if len(evidence["mutations"]) == 1 and evidence["modes"]["blocked_readers"] > 0:
                break
            assert time.monotonic() < deadline, "no exact post-submit crash window"
            time.sleep(0.02)
        # The synthetic platform has durably changed state; its independent
        # readback is still held. Signal ONLY the exact child created above.
        if graceful:
            self.procs[role].send_signal(signal.SIGTERM)
            self.state("release")
            result = self.receive(child)
            child.wait(5)
            assert child.returncode == 0 and result["status"] in (200, 202, 503), result
            self.stop(role)
        else:
            socket_identity = self.actor_socket.stat() if role == "actor" else None
            self.stop(role, kill=True)
            self.state("release")
            child.wait(10)
            if role == "actor":
                result = self.receive(child)
                assert result["status"] in (500, 503), result
                # A killed server cannot unlink its private stale socket. The
                # isolated supervisor retires only that exact observed inode,
                # after waitpid proved its creator is dead. No host path touched.
                current = self.actor_socket.lstat()
                assert stat.S_ISSOCK(current.st_mode) and current.st_uid == UIDS["actor"]
                assert (current.st_dev, current.st_ino) == (socket_identity.st_dev, socket_identity.st_ino)
                self.actor_socket.unlink()
                self.start("actor", self.configs["actor"])
            else:
                assert child.returncode != 0, "killed owner unexpectedly returned success"
        self.restart_owner()
        replay = self.request("POST", "/api/write/ads/701/pause", key)
        assert replay["body"]["platform_retry_authorized"] is False, replay
        if graceful and result["status"] == 200:
            assert replay["status"] == 200 and replay["replayed"] == "true", replay
        else:
            assert replay["status"] in (409, 500, 503), replay
            another = self.request("POST", "/api/write/ads/701/pause", key + ":new")
            assert another["status"] in (409, 500, 503), another
        assert len(self.state()["mutations"]) == 1
        self.probe(peer_only=True)

    def close(self):
        errors = []
        try:
            for role in ("owner", "actor", "witness", "platform"):
                if role in self.procs:
                    try:
                        self.stop(role)
                    except BaseException as exc:
                        errors.append(type(exc).__name__)
        finally:
            for child in self.children:
                if child.poll() is None:
                    child.kill()
                    child.wait(5)
                child.stdout.close()
                child.stderr.close()
        if errors:
            raise AssertionError("synthetic role shutdown failed: " + ",".join(errors))


def _acceptance(host_net, host_pid):
    assert sys.platform == "linux" and os.geteuid() == 0 and os.getpid() == 1
    assert os.readlink("/proc/self/ns/net") != host_net and os.readlink("/proc/self/ns/pid") != host_pid
    interfaces = [line.split(":")[0].strip() for line in Path("/proc/net/dev").read_text().splitlines()[2:]]
    assert interfaces == ["lo"], interfaces
    package = Path(mark_api.__file__).resolve()
    assert "site-packages" in package.parts and package.stat().st_uid == 0, "requires root-owned installed wheel, never checkout imports"
    with tempfile.TemporaryDirectory(prefix="a6-uid-acceptance-") as temp:
        root = Path(temp)
        os.chmod(root, 0o755)
        scenario = None
        try:
            scenario = Scenario(root / "crud")
            scenario.probe()
            scenario.probe(peer_only=True)
            payload = {"category_path": ["Haus & Garten", "Möbel & Wohnen"], "title": "Synthetic table", "description": "Synthetic description", "price_eur": 25}
            created = scenario.request("POST", "/api/write/ads", "crud:create", payload)
            assert created["status"] == 200, created
            ad_id = created["body"]["operation_receipt"]["created_ad_id"]
            operations = [("POST", "/api/write/ads", "crud:create", payload),
                          ("PATCH", "/api/write/ads/" + ad_id, "crud:update", {"title": "Updated table", "description": "Updated description"}),
                          ("POST", "/api/write/ads/" + ad_id + "/pause", "crud:pause", None),
                          ("POST", "/api/write/ads/" + ad_id + "/activate", "crud:activate", None),
                          ("DELETE", "/api/write/ads/" + ad_id, "crud:delete", None)]
            for expected, operation in enumerate(operations, 1):
                result = scenario.request(*operation)
                assert result["status"] == 200 and result["body"]["platform_retry_authorized"] is False, result
                replay = scenario.request(*operation)
                assert replay["status"] == 200 and replay["replayed"] == "true", replay
                assert len(scenario.state()["mutations"]) == expected
            scenario.restart_owner()
            assert scenario.request(*operations[0])["status"] == 200
            assert len(scenario.state()["mutations"]) == 5
            scenario.close()
            scenario = Scenario(root / "lost-ack", lost_ack=True)
            lost = scenario.request("POST", "/api/write/ads/701/pause", "lost:pause")
            assert lost["status"] == 503 and lost["body"]["platform_retry_authorized"] is False, lost
            assert len(scenario.state()["mutations"]) == 1
            scenario.restart_owner()
            replay = scenario.request("POST", "/api/write/ads/701/pause", "lost:pause")
            assert replay["status"] == 200 and replay["replayed"] == "true", replay
            assert len(scenario.state()["mutations"]) == 1
            scenario.close()
            scenario = Scenario(root / "unknown")
            scenario.state("unknown")
            unknown = scenario.request("POST", "/api/write/ads/701/pause", "unknown:pause")
            assert unknown["status"] == 202 and unknown["body"]["platform_retry_authorized"] is False, unknown
            scenario.restart_owner()
            for key in ("unknown:pause", "unknown:new-key"):
                blocked = scenario.request("POST", "/api/write/ads/701/pause", key)
                assert blocked["status"] in (409, 500, 503) and blocked["body"]["platform_retry_authorized"] is False, blocked
            assert len(scenario.state()["mutations"]) == 1
            scenario.probe(peer_only=True)
            scenario.close()
            scenario = None
            for role, graceful in (("actor", False), ("owner", False), ("owner", True)):
                scenario = Scenario(root / ("crash-" + role + ("-term" if graceful else "-kill")))
                scenario.crash_after_submit(role, graceful=graceful)
                scenario.close()
                scenario = None
        finally:
            if scenario is not None:
                scenario.close()
    hashes = {name: hashlib.sha256((package.parent / name).read_bytes()).hexdigest()
              for name in ("completion_authority.py", "completion_witness.py", "fence_witness.py")}
    _emit({"result": "PASS", "scope": "installed-a6-independent-nonmedia-uid-composition", "roles": UIDS,
           "crud": True, "restart_replay": True, "lost_completion_ack": True, "unknown_fenced": True,
           "runtime_raw_db_wal_shm_denied": True, "forged_completion_clearance_denied": True,
           "kernel_peer_credentials": True, "loopback_only": True, "platform_access": False,
           "sigkill_actor_after_submit": True, "sigkill_owner_after_submit": True,
           "sigterm_owner_inflight_drained": True,
           "host_cutover_attested": False, "media_release_attested": False, "source_sha256": hashes})


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--child":
            config = json.loads(sys.stdin.readline(32768))
            sys.stdin.close()
            _child(sys.argv[2], config)
        else:
            parser = argparse.ArgumentParser()
            parser.add_argument("--host-net", required=True)
            parser.add_argument("--host-pid", required=True)
            arguments = parser.parse_args()
            _acceptance(arguments.host_net, arguments.host_pid)
    except BaseException:
        traceback.print_exc()
        raise SystemExit(1)
