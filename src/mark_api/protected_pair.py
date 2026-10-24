"""Create-only cold backup and restore for a STOPPED fresh A6 protected store.

Only a root-provisioned product store is admitted. A live process, pending
sidecar, untrusted actor media handle, unacknowledged operator intent or
ambiguous prior state is a hard refusal. This is NOT legacy host cutover.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys

from . import protected_product as product
from .backup_intent_authority import BackupIntentAuthority, _state
from .container_draft import container_draft_denied
from .paired_backup_operator import (
    _read_head, _validate_anchored_history,
)
from .paired_restore_preflight import verify_offline_pair, _no_sidecars


class ProtectedPairError(RuntimeError):
    """An offline customer operation lacks trusted source/target identity."""


_MAX_FILE = 512 * 1024 * 1024
_MARKER = ".protected-pair-v1.json"


def _private_new_dir(path: Path) -> None:
    product._root_path(path, fresh=True)
    os.mkdir(path, 0o700)
    os.chmod(path, 0o700)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _strict_file(path: Path, uid: int) -> os.stat_result:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or info.st_uid != uid
        or info.st_gid != uid or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1 or not 100 <= info.st_size <= _MAX_FILE
    ):
        raise ProtectedPairError("private SQLite/Witness identity invalid")
    return info


def _copy_exact(source: Path, destination: Path, *,
                source_uid: int, destination_uid: int) -> str:
    original = _strict_file(source, source_uid)
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    output_fd = -1
    digest = hashlib.sha256()
    try:
        pinned = os.fstat(source_fd)
        if (pinned.st_dev, pinned.st_ino) != (original.st_dev, original.st_ino):
            raise ProtectedPairError("cold source inode changed")
        output_fd = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
        )
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(output_fd, view)
                if written <= 0:
                    raise ProtectedPairError("cold copy incomplete")
                view = view[written:]
        finished = os.fstat(source_fd)
        pathname = _strict_file(source, source_uid)
        for info in (finished, pathname):
            if (
                info.st_dev, info.st_ino, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns
            ) != (
                original.st_dev, original.st_ino, original.st_size,
                original.st_mtime_ns, original.st_ctime_ns
            ):
                raise ProtectedPairError("cold source changed during backup")
        if os.fstat(output_fd).st_size != original.st_size:
            raise ProtectedPairError("cold copy length differs")
        os.fchown(output_fd, destination_uid, destination_uid)
        os.fchmod(output_fd, 0o600)
        os.fsync(output_fd)
    finally:
        os.close(source_fd)
        if output_fd >= 0:
            os.close(output_fd)
    _fsync_dir(destination.parent)
    return digest.hexdigest()


def _no_actor_media(root: Path) -> None:
    # The current pair format covers SQLite/Witness, not actor-owned drafts.
    # Refuse instead of silently losing staged images across a restore.
    directory = product._paths(root)["media"]
    if not directory.exists() and not directory.is_symlink():
        return
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
           != (product.ACTOR, product.ACTOR, 0o700)
    ):
        raise ProtectedPairError("private actor media identity invalid")
    marker = directory / ".mark-private-media-ready-v1"
    entries = list(directory.iterdir())
    if len(entries) != 1 or entries[0] != marker:
        raise ProtectedPairError("unresolved actor media drafts require separate retention")
    mark = marker.lstat()
    if (not stat.S_ISREG(mark.st_mode) or mark.st_uid != product.ACTOR
            or stat.S_IMODE(mark.st_mode) != 0o600 or mark.st_size != 0
            or mark.st_nlink != 1):
        raise ProtectedPairError("actor media readiness marker invalid")


def _no_journals(root: Path) -> None:
    paths = product._paths(root)
    for key in ("db", "wdb"):
        _no_sidecars(paths[key])


def _admit_offline(root: Path) -> int:
    product._root_admission()
    if container_draft_denied():
        raise ProtectedPairError("unattested container cannot handle protected data")
    product._root_path(root, fresh=False)
    product._uids_free()
    product._attest_existing(root)
    _no_journals(root)
    _no_actor_media(root)
    lock = product._lock(root)
    try:
        product._uids_free()
        product._attest_existing(root)
        _no_journals(root)
        _no_actor_media(root)
        return lock
    except BaseException:
        os.close(lock)
        raise


def _disjoint(first: Path, second: Path) -> None:
    if first == second or first in second.parents or second in first.parents:
        raise ProtectedPairError("source and destination roots overlap")


def backup(root: Path, destination: Path) -> dict[str, object]:
    _disjoint(root, destination)
    lock = _admit_offline(root)
    try:
        _private_new_dir(destination)
        for subdir in ("cold", "history", "anchor", "intents"):
            os.mkdir(destination / subdir, 0o700)
        cold = destination / "cold"
        paths = product._paths(root)
        _copy_exact(
            paths["db"], cold / "mark.sqlite",
            source_uid=product.OWNER, destination_uid=0,
        )
        _copy_exact(
            paths["wdb"], cold / "witness.sqlite",
            source_uid=product.WITNESS, destination_uid=0,
        )
        witness_lock = os.open(
            cold / "witness.sqlite.lock",
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
            | os.O_CLOEXEC, 0o600,
        )
        os.fsync(witness_lock)
        os.close(witness_lock)
        _fsync_dir(cold)
        product._uids_free()
        product._attest_existing(root)
        _no_journals(root)
        _no_actor_media(root)
        authority = BackupIntentAuthority(destination / "intents")
        generation = secrets.token_hex(16)
        authority.prepare(
            generation, cold / "mark.sqlite", cold / "witness.sqlite",
            previous_head_sha256=None,
        )
        result = authority.seal_prepared(
            generation, destination / "history", destination / "anchor",
        )
        if result.get("external_operator_intent_acknowledged") is not True:
            raise ProtectedPairError("independent backup intent not acknowledged")
        record = {
            "version": 1, "generation": generation,
            "head_sha256": result["head_sha256"],
            "manifest_sha256": result["manifest_sha256"],
            "actor_media_staging_empty": True,
        }
        raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        fd = os.open(
            destination / _MARKER, os.O_CREAT | os.O_EXCL | os.O_WRONLY
            | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
        )
        try:
            view = memoryview(raw)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise ProtectedPairError("backup marker write incomplete")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_dir(destination)
        return {
            "generation": generation,
            "head_sha256": result["head_sha256"],
            "manifest_sha256": result["manifest_sha256"],
            "backup_complete": True,
            "restore_authorized": False,
            "legacy_cutover_attested": False,
        }
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)


def restore(backup_root: Path, target: Path, *,
            expected_head_sha256: str) -> dict[str, object]:
    _disjoint(backup_root, target)
    product._root_admission()
    if container_draft_denied():
        raise ProtectedPairError("unattested container cannot restore a store")
    product._uids_free()
    product._root_path(target, fresh=True)
    if (
        type(expected_head_sha256) is not str
        or len(expected_head_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_head_sha256)
    ):
        raise ProtectedPairError("independent backup HEAD digest required")
    # Protected store roots are 0755; the independent backup authority is
    # deliberately 0700. Never use the product-root validator on a backup.
    from .paired_backup_operator import _private_root
    _private_root(backup_root)
    if (backup_root.lstat().st_uid != 0
            or stat.S_IMODE(backup_root.lstat().st_mode) != 0o700):
        raise ProtectedPairError("backup authority root invalid")
    marker = backup_root / _MARKER
    info = marker.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1 or not 1 <= info.st_size <= 1024):
        raise ProtectedPairError("backup completion marker invalid")
    marker_bytes = marker.read_bytes()
    record = json.loads(marker_bytes)
    if (type(record) is not dict or
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
            != marker_bytes):
        raise ProtectedPairError("backup completion marker not canonical")
    if (
        type(record) is not dict
        or set(record) != {
            "version", "generation", "head_sha256",
            "manifest_sha256", "actor_media_staging_empty",
        }
        or record["version"] != 1
        or record["head_sha256"] != expected_head_sha256
        or record["actor_media_staging_empty"] is not True
    ):
        raise ProtectedPairError("backup marker disagrees with independent HEAD")
    history = backup_root / "history"
    anchor = backup_root / "anchor"
    _private_root(history)
    _private_root(anchor)
    intent = BackupIntentAuthority(backup_root / "intents")
    tip, records = _state(intent.root)
    if tip != expected_head_sha256 or record["generation"] not in records:
        raise ProtectedPairError("backup intent was not independently acknowledged")
    if "acknowledged" not in records[record["generation"]]:
        raise ProtectedPairError("backup generation not acknowledged")
    head = _read_head(anchor, expected_head_sha256)
    if (head is None or head["generation"] != record["generation"]
            or head["manifest_sha256"] != record["manifest_sha256"]):
        raise ProtectedPairError("backup HEAD and manifest mismatch")
    _validate_anchored_history(anchor, history, expected_head_sha256, head)
    if (anchor / ".inflight").exists():
        raise ProtectedPairError("backup intent remains unresolved")
    generation = history / record["generation"]
    images = (generation / "mark.sqlite", generation / "witness.sqlite")
    verified = verify_offline_pair(
        images[0], images[1], generation / "manifest.json",
        expected_manifest_sha256=record["manifest_sha256"],
    )
    if verified["generation"] != record["generation"]:
        raise ProtectedPairError("backup pair generation not verified")
    product._uids_free()
    product._root_path(target, fresh=True)
    os.mkdir(target, 0o755)
    os.chmod(target, 0o755)
    for name, (uid, gid, mode) in product.DIRS.items():
        directory = target / name
        os.mkdir(directory, 0o700)
        os.chown(directory, uid, gid)
        os.chmod(directory, mode)
    paths = product._paths(target)
    for image, output, uid in (
        (images[0], paths["db"], product.OWNER),
        (images[1], paths["wdb"], product.WITNESS),
    ):
        observed = _copy_exact(
            image, output, source_uid=0, destination_uid=uid,
        )
        expected = (
            verified["sqlite_sha256"]
            if uid == product.OWNER else verified["witness_sha256"]
        )
        if observed != expected:
            raise ProtectedPairError("restored file digest mismatch")
    product._attest_existing(target)
    _fsync_dir(paths["db"].parent)
    _fsync_dir(paths["wdb"].parent)
    _fsync_dir(target)
    return {
        "restored_to_new_store": True,
        "restored_generation": verified["generation"],
        "sqlite_started_count": verified["sqlite_started_count"],
        "witness_unresolved_count": verified["witness_unresolved_count"],
        "no_platform_write_performed": True,
        "legacy_cutover_attested": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cold, create-only protected A6 pair backup/restore; never live or legacy cutover.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("backup")
    make.add_argument("--state-root", type=Path, required=True)
    make.add_argument("--backup-root", type=Path, required=True)
    recover = commands.add_parser("restore")
    recover.add_argument("--backup-root", type=Path, required=True)
    recover.add_argument("--new-state-root", type=Path, required=True)
    recover.add_argument("--expected-head-sha256", required=True)
    args = parser.parse_args(argv)
    old_umask = os.umask(0o077)
    try:
        try:
            if args.command == "backup":
                result = backup(args.state_root, args.backup_root)
            else:
                result = restore(
                    args.backup_root, args.new_state_root,
                    expected_head_sha256=args.expected_head_sha256,
                )
        except Exception:
            print("Kleinanzeiger: protected pair backup/restore unavailable", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    raise SystemExit(main())
