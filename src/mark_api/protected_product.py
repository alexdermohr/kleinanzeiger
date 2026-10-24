"""One native protected customer start: preprovisioned A6 roles, not a new Write engine.

The root-only supervisor coordinates the installed Witness, CDP Actor, SQLite
Owner, full Dashboard and existing AF_UNIX ingress. Frontend/Owner share a
*separate network namespace* with only loopback, away from host CDP. The
host-loopback ingress gets irreversible Landlock TCP CONNECT denial.

--init-new provisions only a NEVER-before-used store under disjoint UIDs.
Existing customer database migration/restore is a separate host-cutover
operation: it is never silently performed by this entrypoint.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import platform
import secrets
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
from time import monotonic, sleep
from urllib.parse import quote

from .container_draft import container_draft_denied

FRONTEND, WITNESS, ACTOR, INGRESS, OWNER = 50042, 50043, 50044, 50045, 50046
PRINCIPAL = "kleinanzeiger-protected-local"
PORT = 8875
DIRS = {
    "owner-data": (OWNER, OWNER, 0o700),
    "witness-data": (WITNESS, WITNESS, 0o700),
    "w-owner": (WITNESS, OWNER, 0o2710),
    "w-actor": (WITNESS, ACTOR, 0o2710),
    "actor-rpc": (ACTOR, OWNER, 0o2710),
    "dashboard-rpc": (OWNER, FRONTEND, 0o2710),
    "ingress-rpc": (FRONTEND, INGRESS, 0o2710),
    "actor-media": (ACTOR, ACTOR, 0o700),
}


class ProtectedProductError(RuntimeError):
    """Only sanitized startup/drain failure is exposed to customers."""


class _LandlockRuleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64),
                ("handled_access_net", ctypes.c_uint64)]


class _LandlockPort(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64),
                ("port", ctypes.c_uint64)]


def _root_admission() -> None:
    if (sys.platform != "linux" or os.geteuid() != 0
            or not callable(getattr(os, "unshare", None))
            or not callable(getattr(os, "setns", None))
            or platform.machine() not in ("x86_64", "aarch64")):
        raise ProtectedProductError("root Linux namespace operator required")
    # No editable checkout or user-writable Python distribution under root.
    import mark_api
    source = Path(mark_api.__file__).resolve(strict=True)
    python = Path(sys.executable).resolve(strict=True)
    if "site-packages" not in source.parts:
        raise ProtectedProductError("installed immutable wheel required")
    for file in (source, python):
        info = file.stat()
        if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            raise ProtectedProductError("untrusted Python or installed package")


def _root_path(path: Path, *, fresh: bool) -> None:
    if (not isinstance(path, Path) or not path.is_absolute()
            or path != Path(os.path.normpath(path))):
        raise ProtectedProductError("canonical absolute state root required")
    current = Path("/")
    for segment in path.parts[1:]:
        current /= segment
        if not current.exists() and not current.is_symlink():
            if current != path or not fresh:
                raise ProtectedProductError("state parent missing")
            break
        info = current.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
            raise ProtectedProductError("untrusted state ancestor")
    if fresh:
        if path.exists() or path.is_symlink():
            raise ProtectedProductError("fresh install cannot adopt previous state")
    else:
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o755):
            raise ProtectedProductError("protected state root differs")


def _paths(root: Path) -> dict[str, Path]:
    return {
        "db": root / "owner-data" / "snapshot.sqlite",
        "wdb": root / "witness-data" / "witness.sqlite",
        "wo": root / "w-owner" / "w.sock",
        "wa": root / "w-actor" / "w.sock",
        "actor": root / "actor-rpc" / "a.sock",
        "dashboard": root / "dashboard-rpc" / "d.sock",
        "ingress": root / "ingress-rpc" / "frontend.sock",
        "media": root / "actor-media" / "handles",
        "lock": root / ".protected.lock",
    }


def _attest_existing(root: Path) -> None:
    for name, expected in DIRS.items():
        info = (root / name).lstat()
        if (not stat.S_ISDIR(info.st_mode)
                or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
                != expected):
            raise ProtectedProductError("protected directory owner or mode differs")
    paths = _paths(root)
    for key, uid in (("db", OWNER), ("wdb", WITNESS)):
        info = paths[key].lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or info.st_gid != uid or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1):
            raise ProtectedProductError("preprovisioned journal identity invalid")
    for key in ("wo", "wa", "actor", "dashboard", "ingress"):
        if paths[key].exists() or paths[key].is_symlink():
            raise ProtectedProductError("stale socket requires independent reconciliation")


def _drop(uid: int, network_fd: int | None = None) -> None:
    if network_fd is not None:
        os.setns(network_fd, os.CLONE_NEWNET)
        os.close(network_fd)
    os.setgroups([])
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise ProtectedProductError("no new privileges not enforced")


def _ingress_landlock() -> None:
    """Same ABI4 network rule as the existing root-owned ingress script."""
    if os.geteuid() != INGRESS:
        raise ProtectedProductError("wrong ingress UID")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long

    def call(number: int, *args: object) -> int:
        result = libc.syscall(number, *args)
        if result < 0:
            raise ProtectedProductError("Landlock network policy unavailable")
        return int(result)

    if call(444, ctypes.c_void_p(), ctypes.c_size_t(), ctypes.c_uint(1)) < 4:
        raise ProtectedProductError("Landlock ABI4 required")
    # BIND/CONNECT are handled: one allowed BIND(8875), zero allowed CONNECT.
    access = _LandlockRuleset(0, 3)
    fd = call(444, ctypes.byref(access), ctypes.sizeof(access), ctypes.c_uint(0))
    try:
        port = _LandlockPort(1, PORT)
        call(445, ctypes.c_int(fd), ctypes.c_int(2),
             ctypes.byref(port), ctypes.c_uint(0))
        call(446, ctypes.c_int(fd), ctypes.c_uint(0))
    finally:
        os.close(fd)


def _private_network() -> int:
    host_fd = os.open("/proc/self/ns/net", os.O_RDONLY | os.O_CLOEXEC)
    guest_fd = None
    try:
        os.unshare(os.CLONE_NEWNET)
        guest_fd = os.open("/proc/self/ns/net", os.O_RDONLY | os.O_CLOEXEC)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
                request = struct.pack("16sH14s", b"lo", 0, b"")
                flags = struct.unpack(
                    "16sH14s", fcntl.ioctl(peer.fileno(), 0x8913, request)
                )[1]
                fcntl.ioctl(peer.fileno(), 0x8914,
                            struct.pack("16sH14s", b"lo", flags | 1, b""))
        finally:
            os.setns(host_fd, os.CLONE_NEWNET)
        return guest_fd
    except BaseException:
        if guest_fd is not None:
            os.close(guest_fd)
        raise
    finally:
        os.close(host_fd)


def _uids_free() -> None:
    # Necessary check before a fresh start, not a substitute for independently
    # witnessed host-/PID-namespace history and writable FD/MAP_SHARED cutover.
    target = {FRONTEND, WITNESS, ACTOR, INGRESS, OWNER}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            line = next(row for row in (proc / "status").read_text().splitlines()
                        if row.startswith("Uid:"))
            owners = {int(x) for x in line.split()[1:]}
        except FileNotFoundError:
            continue
        except (OSError, ValueError, StopIteration):
            raise ProtectedProductError("process identity census incomplete") from None
        if target & owners:
            raise ProtectedProductError("service UID already holds a process")


def _initialize(root: Path) -> None:
    _root_path(root, fresh=True)
    os.mkdir(root, 0o755)
    os.chmod(root, 0o755)
    for name, (uid, gid, mode) in DIRS.items():
        subdir = root / name
        os.mkdir(subdir, 0o700)
        os.chown(subdir, uid, gid)
        os.chmod(subdir, mode)
    paths = _paths(root)
    # A one-shot fork under the exact *future owner UID* creates SQLite.
    # The privileged parent never opens database bytes or initializes a
    # missing ledger on an ordinary restart.
    for role, uid in (("owner", OWNER), ("witness", WITNESS)):
        pid = os.fork()
        if pid == 0:
            try:
                _drop(uid)
                if role == "owner":
                    from .storage import SnapshotStore
                    SnapshotStore(paths["db"], create_if_missing=True)
                else:
                    from .completion_witness import CompletionWitnessLedger
                    with CompletionWitnessLedger(
                        paths["wdb"], principal=PRINCIPAL, initialize=True,
                    ):
                        pass
                os._exit(0)
            except BaseException:
                os._exit(2)
        child, status = os.waitpid(pid, 0)
        if child != pid or not os.WIFEXITED(status) or os.WEXITSTATUS(status):
            # Never delete a partial generation or treat it as fresh again.
            raise ProtectedProductError("fresh storage provisioning failed")
    _attest_existing(root)


def _lock(root: Path) -> int:
    fd = os.open(root / ".protected.lock",
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1):
            raise ProtectedProductError("supervisor lock identity invalid")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _spawn(module: str, uid: int, *, net: int | None,
           config: dict[str, object] | None = None,
           args: tuple[str, ...] = (), ingress: bool = False) -> subprocess.Popen:
    argv = [sys.executable, "-I", "-m", module]
    rd = None
    if config is not None:
        payload = json.dumps(config, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"
        if len(payload) > 8192:
            raise ProtectedProductError("private role frame oversized")
        rd, wr = os.pipe2(os.O_CLOEXEC)
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(wr, view):]
        finally:
            os.close(wr)
        argv.extend(("--config-fd", str(rd)))
    else:
        argv.extend(args)
    try:
        def child_entry() -> None:
            _drop(uid, network_fd=net)
            if ingress:
                _ingress_landlock()
        return subprocess.Popen(
            argv, cwd="/", stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, close_fds=True, bufsize=0,
            pass_fds=tuple(x for x in (rd, net) if x is not None),
            preexec_fn=child_entry,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
                 "LANG": "C.UTF-8", "PYTHONNOUSERSITE": "1"},
        )
    finally:
        if rd is not None:
            os.close(rd)


def _ready(child: subprocess.Popen, uid: int) -> dict[str, object]:
    if child.stdout is None:
        raise ProtectedProductError("missing role readiness channel")
    buf = bytearray()
    deadline = monotonic() + 20
    while monotonic() < deadline and len(buf) < 1024:
        if child.poll() is not None:
            raise ProtectedProductError("protected role exited before ready")
        readable, _, _ = select.select([child.stdout], [], [],
                                       min(.2, deadline - monotonic()))
        if readable:
            chunk = os.read(child.stdout.fileno(), 1024 - len(buf))
            if not chunk:
                raise ProtectedProductError("role readiness EOF")
            buf.extend(chunk)
            if b"\n" in buf:
                break
    try:
        if buf.count(b"\n") != 1 or not buf.endswith(b"\n"):
            raise ValueError()
        reply = json.loads(buf.decode("ascii"))
        if (type(reply) is not dict
                or set(reply) not in ({"ready", "uid"}, {"ready", "uid", "port"})
                or reply["ready"] is not True or
                type(reply["uid"]) is not int or reply["uid"] != uid):
            raise ValueError()
        if "port" in reply and (type(reply["port"]) is not int
                                or not 1 <= reply["port"] <= 65535):
            raise ValueError()
        return reply
    except (ValueError, UnicodeError):
        raise ProtectedProductError("unverified role readiness") from None


def _ready_ingress(proc: subprocess.Popen) -> None:
    deadline = monotonic() + 20
    while monotonic() < deadline:
        if proc.poll() is not None:
            raise ProtectedProductError("trusted HTTP ingress unavailable")
        try:
            with HTTPConnection("127.0.0.1", PORT, timeout=0.5) as http:
                http.request("GET", "/readyz")
                response = http.getresponse()
                response.read(2048)
                if response.status == 200:
                    return
        except OSError:
            pass
        sleep(0.1)
    raise ProtectedProductError("customer dashboard ingress did not become ready")


def _stop(children: list[tuple[str, subprocess.Popen]]) -> bool:
    success = True
    for name, child in reversed(children):
        if child.poll() is None:
            child.send_signal(signal.SIGTERM)
        try:
            child.wait(timeout=90 if name == "owner" else 35)
            if child.returncode != 0:
                success = False
        except subprocess.TimeoutExpired:
            success = False
            child.kill()
            child.wait(timeout=5)
        finally:
            if child.stdout is not None:
                child.stdout.close()
    return success


def run(*, root: Path, init_new: bool = False, cdp_port: int = 9222,
        sync_interval: int = 300) -> int:
    # Emit only static phase names on unexpected host/runtime errors. The
    # customer-facing CLI never logs a UID token, secret, path or traceback.
    def admitted(label, operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except ProtectedProductError:
            raise
        except Exception:
            raise ProtectedProductError(label) from None

    admitted("root runtime admission failed", _root_admission)
    if container_draft_denied():
        raise ProtectedProductError("draft cannot start protected product")
    if (type(cdp_port) is not int or not 1 <= cdp_port <= 65535
            or type(sync_interval) is not int or
            sync_interval != 0 and not 60 <= sync_interval <= 86400):
        raise ProtectedProductError("invalid CDP port/sync cadence")
    admitted("state root inspection failed", _root_path, root, fresh=init_new)
    admitted("service UID census failed", _uids_free)
    if init_new:
        admitted("fresh state provisioning failed", _initialize, root)
    else:
        admitted("protected state attestation failed", _attest_existing, root)
    paths = _paths(root)
    lock = admitted("supervisor lock admission failed", _lock, root)
    net = None
    children: list[tuple[str, subprocess.Popen]] = []
    stopped = [False]

    def request_stop(_sig, _frame):
        stopped[0] = True

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    previous_int = signal.signal(signal.SIGINT, request_stop)
    outcome = 2
    try:
        try:
            net = _private_network()
        except Exception:
            raise ProtectedProductError("private network namespace unavailable") from None
        api = secrets.token_urlsafe(48)
        ui = secrets.token_urlsafe(48)

        def start(name: str, module: str, uid: int,
                  payload: dict[str, object], *, private: bool):
            try:
                child = _spawn(module, uid, net=net if private else None,
                               config=payload)
            except Exception:
                raise ProtectedProductError(name + " process admission failed") from None
            children.append((name, child))
            try:
                return _ready(child, uid)
            except ProtectedProductError:
                raise ProtectedProductError(name + " readiness unavailable") from None

        start("witness", "mark_api.protected_witness", WITNESS, {
            "database": str(paths["wdb"]),
            "owner_socket": str(paths["wo"]),
            "actor_socket": str(paths["wa"]),
            "owner_uid": OWNER, "owner_gid": OWNER,
            "actor_uid": ACTOR, "actor_gid": ACTOR,
            "runtime_uid": FRONTEND, "principal": PRINCIPAL,
        }, private=True)
        start("actor", "mark_api.protected_actor", ACTOR, {
            "socket": str(paths["actor"]),
            "witness_socket": str(paths["wa"]),
            "owner_uid": OWNER, "owner_gid": OWNER,
            "witness_uid": WITNESS, "runtime_uid": FRONTEND,
            "principal": PRINCIPAL, "cdp_port": cdp_port,
            "timeout_seconds": 5.0, "media_enabled": True,
            "media_directory": str(paths["media"]),
        }, private=False)
        owner = start("owner", "mark_api.protected_owner", OWNER, {
            "database": str(paths["db"]),
            "dashboard_socket": str(paths["dashboard"]),
            "witness_socket": str(paths["wo"]),
            "actor_socket": str(paths["actor"]),
            "witness_uid": WITNESS, "actor_uid": ACTOR,
            "runtime_uid": FRONTEND, "runtime_gid": FRONTEND,
            "principal": PRINCIPAL, "bearer_token": api,
            "media_enabled": True, "write_port": 0,
            "sync_interval_seconds": sync_interval,
        }, private=True)
        frontend = start("frontend", "mark_api.protected_frontend", FRONTEND, {
            "owner_socket": str(paths["dashboard"]),
            "owner_uid": OWNER, "principal": PRINCIPAL,
            "owner_port": owner["port"], "api_token": api,
            "ui_token": ui, "dashboard_port": 0,
            "ingress_socket": str(paths["ingress"]),
            "ingress_uid": INGRESS, "ingress_gid": INGRESS,
        }, private=True)
        if frontend["port"] == owner["port"]:
            raise ProtectedProductError("backend and dashboard shared a port")
        try:
            ingress = _spawn(
                "mark_api.dashboard_relay", INGRESS, net=None,
                args=("public-ingress", "--frontend-socket", str(paths["ingress"]),
                      "--frontend-uid", str(FRONTEND), "--port", str(PORT)),
                ingress=True,
            )
        except Exception:
            raise ProtectedProductError("Landlock ingress admission failed") from None
        children.append(("ingress", ingress))
        _ready_ingress(ingress)
        # The write token is only in a fragment, not the server HTTP logs.
        print("Kleinanzeiger: http://127.0.0.1:" + str(PORT)
              + "/#write_token=" + quote(ui, safe=""), flush=True)
        while not stopped[0]:
            if any(process.poll() is not None for _, process in children):
                raise ProtectedProductError("protected role terminated")
            sleep(.2)
        outcome = 0
    finally:
        if not _stop(children):
            outcome = 2
        if net is not None:
            os.close(net)
        os.close(lock)
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Kleinanzeiger one-command protected fresh-install/customer start",
    )
    parser.add_argument("--state-root", type=Path, default=Path("/var/lib/kleinanzeiger"))
    parser.add_argument("--init-new", action="store_true",
                        help="One-time NEW owner/Witness store; never import legacy state")
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--sync-interval", type=int, default=300)
    args = parser.parse_args(argv)
    old_umask = os.umask(0o077)
    try:
        try:
            return run(root=args.state_root, init_new=args.init_new,
                       cdp_port=args.cdp_port, sync_interval=args.sync_interval)
        except ProtectedProductError as error:
            # All these error messages are authored static admission phases.
            print("Kleinanzeiger: protected customer startup unavailable ("
                  + str(error) + ")", file=sys.stderr)
            return 2
        except Exception:
            print("Kleinanzeiger: protected customer startup unavailable", file=sys.stderr)
            return 2
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    raise SystemExit(main())