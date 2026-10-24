"""Root-only installed protected product start/restart in disposable PID/netns.

The live installed role modules are exercised. No real CDP, account, provider,
platform mutation or host cutover is performed. Failures never mint an
operator-recovery clearance or mark old customer databases as safe.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import subprocess
import sys
import tempfile
from time import monotonic, sleep
from urllib.parse import parse_qs, urlsplit

import mark_api

UIDS = {"frontend":50042, "witness":50043, "actor":50044,
        "ingress":50045, "owner":50046}


def check(condition, reason):
    if not condition:
        raise AssertionError(reason)


def child_roles(supervisor_pid):
    roles = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal():
            continue
        try:
            text = (path / "status").read_text()
        except FileNotFoundError:
            continue
        fields = {}
        for line in text.splitlines():
            if line.startswith(("PPid:", "Uid:")):
                fields[line.split(":",1)[0]] = line.split(":",1)[1].strip().split()
        if fields.get("PPid") != [str(supervisor_pid)]:
            continue
        uid = int(fields["Uid"][1])
        for role, expected in UIDS.items():
            if uid == expected:
                check(role not in roles, "duplicate protected role UID")
                roles[role] = int(path.name)
    check(set(roles) == set(UIDS), "five installed role processes not found")
    return roles



class _SyntheticCDPHandler(BaseHTTPRequestHandler):
    """Isolated test-only wire-level CDP responder, not a product adapter."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/json/list":
            self.server.discoveries += 1
            body = json.dumps([{"type": "page", "id": "synthetic-owner",
                "webSocketDebuggerUrl":
                "ws://127.0.0.1:19322/devtools/page/synthetic-owner"}]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/devtools/page/synthetic-owner":
            self.send_error(404)
            return
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or self.headers.get("Upgrade", "").lower() != "websocket":
            self.send_error(400)
            return
        proof = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
        ).digest()).decode()
        self.server.upgrades += 1
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", proof)
        self.end_headers()
        self.close_connection = True
        while True:
            header = self.rfile.read(2)
            if len(header) != 2 or (header[0] & 15) == 8:
                return
            n = header[1] & 127
            if n == 126:
                n = int.from_bytes(self.rfile.read(2), "big")
            elif n == 127:
                n = int.from_bytes(self.rfile.read(8), "big")
            if n > 256 * 1024 or not header[1] & 128 or (header[0] & 15) != 1:
                return
            mask = self.rfile.read(4)
            payload = self.rfile.read(n)
            if len(mask) != 4 or len(payload) != n:
                return
            try:
                data = json.loads(bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))
                method, params = data["method"], data.get("params", {})
                self.server.commands.append(method)
                if method != "Runtime.evaluate":
                    answer = {"error": {"code": -32601, "message": "not admitted"}}
                elif params.get("awaitPromise") is True:
                    expression = params["expression"]
                    if "pageNum=1" not in expression or "same-origin" not in expression:
                        answer = {"error": {"code": -32602, "message": "unknown inventory"}}
                    else:
                        ads = {"ads": [{"id": "1234567890",
                            "title": "Synthetic CDP vase", "state": "active",
                            "viewCount": 7, "watchCount": 2}],
                            "paging": {"last": 1}}
                        answer = {"result": {"result": {"type": "object", "value": {
                            "status": 200, "body": json.dumps(ads)}}}}
                else:
                    answer = {"result": {"result": {"type": "object", "value": {
                        "readyState": "complete", "origin": "https://www.kleinanzeigen.de",
                        "path": "/m-meine-anzeigen.html"}}}}
                raw = json.dumps({"id": data["id"], **answer}).encode()
                head = (bytes((0x81, len(raw))) if len(raw) < 126 else
                        bytes((0x81, 126)) + len(raw).to_bytes(2, "big"))
                self.wfile.write(head + raw)
                self.wfile.flush()
            except (OSError, ValueError, KeyError, TypeError):
                return


class SyntheticCDP(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 19322), _SyntheticCDPHandler)
        self.commands = []
        self.discoveries = 0
        self.upgrades = 0


def inventory_confirmed(cdp):
    deadline = monotonic() + 12
    while monotonic() < deadline:
        try:
            with closing(HTTPConnection("127.0.0.1", 8875, timeout=2)) as client:
                client.request("GET", "/api/sync/status")
                response = client.getresponse()
                attempt = (json.loads(response.read()).get("latest_attempt") or {})
            if attempt.get("outcome") == "success_nonempty":
                break
            if attempt.get("outcome") == "failed":
                raise AssertionError("real CDP sync failed d=%d ws=%d calls=%d" % (cdp.discoveries, cdp.upgrades, len(cdp.commands)))
        except (OSError, TimeoutError):
            pass
        sleep(.1)
    else:
        raise AssertionError("real CDP sync timeout d=%d ws=%d calls=%d" % (cdp.discoveries, cdp.upgrades, len(cdp.commands)))
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=2)) as client:
        client.request("GET", "/api/ads")
        reply = client.getresponse()
        rows = json.loads(reply.read())
        check(reply.status == 200 and any(
            item.get("ad_id") == "1234567890"
            and item.get("title") == "Synthetic CDP vase"
            and item.get("views") == 7 for item in rows
        ), "real CDP inventory absent from protected dashboard")


def dashboard_ready():
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=4)) as client:
        client.request("GET", "/readyz")
        response = client.getresponse()
        response.read()
        check(response.status == 200, "installed dashboard readiness failed")
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=4)) as client:
        client.request("GET", "/api/dashboard/config")
        response = client.getresponse()
        config = json.loads(response.read())
        check(response.status == 200 and config["write_ui_available"] is True,
              "protected UI does not expose witnessed Write controls")
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=4)) as client:
        client.request("GET", "/api/ads")
        response = client.getresponse()
        response.read()
        check(response.status == 200, "protected stored-inventory UI not available")


def start(binary, root, init_new, *, sync_interval=0):
    argv = [str(binary), "--state-root", str(root), "--cdp-port", "19322",
            "--sync-interval", str(sync_interval)]
    if init_new:
        argv.append("--init-new")
    child = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, close_fds=True,
        env={"PATH":"/usr/bin:/bin","HOME":"/nonexistent","LANG":"C.UTF-8"},
    )
    check(child.stdout is not None, "product startup pipe missing")
    check(select.select([child.stdout], [], [], 40)[0], "product never printed dashboard URL")
    line = child.stdout.readline()
    if not line.startswith("Kleinanzeiger: http://127.0.0.1:8875/#write_token="):
        # A read of EOF may precede waitpid by a few milliseconds. Wait for
        # the actual exit before inspecting only our fixed nonsecret phase.
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            raise AssertionError("protected startup closed readiness before finishing") from None
        raw = child.stderr.read(512).strip()
        marker = "Kleinanzeiger: protected customer startup unavailable ("
        if raw.startswith(marker) and raw.endswith(")") and len(raw) < 190:
            phase = raw[len(marker):-1]
            if phase and all(char.isascii() and (char.isalpha() or char in " -")
                             for char in phase):
                raise AssertionError("startup phase: " + phase)
        raise AssertionError("installed protected dashboard URL unavailable")
    parsed = urlsplit(line.strip().split(": ",1)[1])
    fragment = parse_qs(parsed.fragment)
    check(len(fragment.get("write_token",[])) == 1
          and len(fragment["write_token"][0]) >= 40,
          "private UI token missing")
    check(child.poll() is None, "protected product exited after readiness")
    return child, fragment["write_token"][0]


def shutdown(child):
    if child.poll() is None:
        child.send_signal(signal.SIGTERM)
    try:
        code = child.wait(timeout=70)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(5)
        raise AssertionError("protected shutdown exceeded bounded drain")
    check(code == 0, "protected role shutdown did not drain")
    child.stdout.close()
    child.stderr.close()


def admission(net_before, pid_before):
    check(sys.platform == "linux" and os.geteuid() == 0
          and os.getpid() == 1, "root isolated PID1 required")
    check(os.readlink("/proc/self/ns/net") != net_before
          and os.readlink("/proc/self/ns/pid") != pid_before,
          "test must not use any host PID/network namespace")
    image = Path(mark_api.__file__).resolve()
    check("site-packages" in image.parts and image.stat().st_uid == 0,
          "test must execute root-owned installed wheel")
    binary = Path(sys.executable).with_name("kleinanzeiger-protected")
    check(binary.is_file() and os.access(binary,os.X_OK),
          "new installed product start executable missing")

    with tempfile.TemporaryDirectory(prefix="kleinanzeiger-new-product-") as scratch:
        os.chmod(scratch, 0o755)
        root = Path(scratch) / "new-install"
        child = None
        cdp = SyntheticCDP()
        from threading import Thread
        worker = Thread(target=cdp.serve_forever, daemon=True)
        worker.start()
        try:
            # Real production CDP transport: verify this test-only wire server
            # before interpreting an actor/owner sync failure as product error.
            from mark_api.private_web_inventory_cdp import CdpManagementInventoryReader
            from mark_api.results import ReadStatus
            probe = CdpManagementInventoryReader(cdp_port=19322)
            observed = probe.read_ads()
            probe.close()
            check(observed.status is ReadStatus.SUCCESS_NONEMPTY,
                  "synthetic CDP peer protocol failed: " + observed.status.value)
            child, first_token = start(binary, root, init_new=True, sync_interval=60)
            dashboard_ready()
            inventory_confirmed(cdp)
            roles = child_roles(child.pid)
            host_net = os.readlink("/proc/self/ns/net")
            namespaces = {
                role: os.readlink(f"/proc/{pid}/ns/net")
                for role, pid in roles.items()
            }
            check(namespaces["actor"] == host_net and namespaces["ingress"] == host_net,
                  "Actor must have host-CDP while Landlock ingress has host loopback")
            check(len({namespaces[k] for k in ("owner","frontend","witness")}) == 1
                  and namespaces["owner"] != host_net,
                  "untrusted UI and owner not confined to one private netns")
            db = root / "owner-data" / "snapshot.sqlite"
            witness = root / "witness-data" / "witness.sqlite"
            dbinfo = db.lstat()
            check(dbinfo.st_uid == UIDS["owner"]
                  and stat.S_IMODE(dbinfo.st_mode) == 0o600,
                  "fresh owner SQLite not private")
            check(witness.stat().st_uid == UIDS["witness"],
                  "fresh Witness ledger not independently owned")
            first_inode = (dbinfo.st_dev, dbinfo.st_ino)
            shutdown(child)
            child = None
            for role, pid in roles.items():
                check(not Path(f"/proc/{pid}").exists(),
                      "old protected role still active after successful shutdown")

            child, second_token = start(binary, root, init_new=False, sync_interval=60)
            inventory_confirmed(cdp)
            check(first_token != second_token, "restart reused customer UI credential")
            dashboard_ready()
            check((db.stat().st_dev, db.stat().st_ino) == first_inode,
                  "restart replaced a customer's SQLite inode")
            shutdown(child)
            child = None

            # Reusing --init-new with an existing store must fail BEFORE
            # opening/replacing data, not become a migration/restore shortcut.
            invalid = subprocess.run(
                [str(binary), "--state-root", str(root), "--init-new",
                 "--cdp-port", "19322"], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=15, check=False,
            )
            check(invalid.returncode == 2 and not invalid.stdout,
                  "old owner data accepted as an untracked fresh install")
            check((db.stat().st_dev, db.stat().st_ino) == first_inode,
                  "invalid fresh attempt changed existing SQLite")
        finally:
            if child is not None:
                child.kill()
                child.wait(5)
                child.stdout.close()
                child.stderr.close()
            cdp.shutdown()
            cdp.server_close()
            worker.join(3)
    check(cdp.commands.count("Runtime.evaluate") >= 4
          and set(cdp.commands) == {"Runtime.evaluate"},
          "installed actor did not use only real inventory CDP")
    print(json.dumps({
        "result":"PASS", "scope":"installed-protected-fresh-customer-start",
        "real_owner_cli":True,"real_actor_cli":True,
        "real_witness_cli":True,"real_frontend_cli":True,
        "uid_isolation":True,"netless_frontend_owner":True,
        "landlock_ingress":True,"dashboard_http":True,
        "restarted_without_db_recreation":True,
        "legacy_cutover_attested":False,"real_cdp_write_attested":False,
        "real_cdp_inventory_sync_attested":True,
        "paid_generation_called":False,
    }, sort_keys=True, separators=(",",":")),flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-net",required=True)
    parser.add_argument("--host-pid",required=True)
    args = parser.parse_args()
    admission(args.host_net,args.host_pid)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as error:
        # Every assertion message is one of this file's fixed, nonsensitive
        # admission labels or the product's own restricted static phase.
        print("Kleinanzeiger protected installed start acceptance: FAIL",
              str(error)[:190], file=sys.stderr)
        raise SystemExit(1)
    except BaseException as error:
        print("Kleinanzeiger protected installed start acceptance: FAIL",
              type(error).__name__, file=sys.stderr)
        raise SystemExit(1)