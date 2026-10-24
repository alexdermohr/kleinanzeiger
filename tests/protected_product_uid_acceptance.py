"""Root-only installed protected product start/restart in disposable PID/netns.

The live installed role modules are exercised. No real CDP, account, provider,
platform mutation or host cutover is performed. Failures never mint an
operator-recovery clearance or mark old customer databases as safe.
"""
from __future__ import annotations

import argparse
from contextlib import closing
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


def start(binary, root, init_new):
    argv = [str(binary), "--state-root", str(root), "--cdp-port", "19322",
            "--sync-interval", "0"]
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
        try:
            child, first_token = start(binary, root, init_new=True)
            dashboard_ready()
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

            child, second_token = start(binary, root, init_new=False)
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
    print(json.dumps({
        "result":"PASS", "scope":"installed-protected-fresh-customer-start",
        "real_owner_cli":True,"real_actor_cli":True,
        "real_witness_cli":True,"real_frontend_cli":True,
        "uid_isolation":True,"netless_frontend_owner":True,
        "landlock_ingress":True,"dashboard_http":True,
        "restarted_without_db_recreation":True,
        "legacy_cutover_attested":False,"real_cdp_write_attested":False,
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