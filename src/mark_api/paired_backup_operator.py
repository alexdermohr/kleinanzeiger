"""Seal a trusted *offline* SQLite/Witness pair as one generation.

This is a build block for an independent operator, NOT the live backup authority.
The caller must independently prove source quiescence, process/FD/mmap revocation,
source ownership and trusted provenance before these two cold images exist.
The routine never stops services, touches Kleinanzeigen or authorizes restore.
There is deliberately no live/Compose entrypoint.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Iterator

from .fence_witness import FenceWitnessError, _private_directory
from .paired_restore_preflight import (
    PairPreflightError, _canonical, _no_sidecars, _pinned_file,
    _sha256_fd, _witness_owner_absent, inspect_offline_pair, verify_offline_pair,
)


class PairedBackupOperatorError(RuntimeError):
    """The cold pair is not an independently anchored backup generation."""


_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_GENERATION = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_MAX_HEAD = 4096
_ZERO = "0" * 64


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _private_root(path: Path) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path != Path(os.path.normpath(path))
    ):
        raise PairedBackupOperatorError("operator directory must be absolute and normalized")
    try:
        _private_directory(path, os.geteuid(), mode=0o700)
    except FenceWitnessError as exc:
        raise PairedBackupOperatorError("operator directory is not privately owned") from exc
    return path


def _separate_roots(source: Path, output: Path, anchor: Path) -> None:
    roots = (source, output, anchor)
    for i, first in enumerate(roots):
        for second in roots[i + 1:]:
            if first == second or first in second.parents or second in first.parents:
                raise PairedBackupOperatorError("offline inputs, outputs and anchor overlap")


def _require_digest(value: str, label: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise PairedBackupOperatorError(f"{label} SHA-256 anchor is invalid")
    return value


def _unoccupied(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise PairedBackupOperatorError("generation artifact already exists; no blind retry")


def _fsync_dir(directory: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(directory, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _new_file(path: Path, data: bytes) -> None:
    fd = os.open(
        path, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
        os.O_CLOEXEC | os.O_NOFOLLOW, 0o600,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise PairedBackupOperatorError("cold pair file write failed")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _copy_pinned(fd: int, destination: Path) -> str:
    info = os.fstat(fd)
    if not 100 <= info.st_size <= 512 * 1024 * 1024:
        raise PairedBackupOperatorError("source cold image size is unsafe")
    out = os.open(
        destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
        os.O_CLOEXEC | os.O_NOFOLLOW, 0o600,
    )
    sha = hashlib.sha256()
    try:
        offset = 0
        while offset < info.st_size:
            block = os.pread(fd, min(1024 * 1024, info.st_size - offset), offset)
            if not block:
                raise PairedBackupOperatorError("source cold image was shortened")
            sha.update(block)
            view = memoryview(block)
            while view:
                count = os.write(out, view)
                if count <= 0:
                    raise PairedBackupOperatorError("cold image copy failed")
                view = view[count:]
            offset += len(block)
        os.fsync(out)
    finally:
        os.close(out)
    return sha.hexdigest()


def _read_head(anchor: Path, previous_sha: str | None) -> dict[str, object] | None:
    head_file = anchor / "latest.json"
    try:
        head_file.lstat()
    except FileNotFoundError:
        if previous_sha is not None:
            raise PairedBackupOperatorError("previous anchored generation is missing")
        return None
    if previous_sha is None:
        raise PairedBackupOperatorError("an existing generation requires its pinned head digest")
    _require_digest(previous_sha, "previous head")
    with _pinned_file(head_file) as fd:
        size = os.fstat(fd).st_size
        if not 1 <= size <= _MAX_HEAD:
            raise PairedBackupOperatorError("head anchor length invalid")
        raw = os.pread(fd, size, 0)
    if _digest(raw) != previous_sha:
        raise PairedBackupOperatorError("external previous generation digest mismatches")
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PairedBackupOperatorError("previous generation record is invalid") from exc
    if (
        type(obj) is not dict
        or set(obj) != {
            "schema_version", "generation", "manifest_sha256", "predecessor_sha256",
        }
        or type(obj["schema_version"]) is not int
        or obj["schema_version"] != 1
        or type(obj["generation"]) is not str
        or _GENERATION.fullmatch(obj["generation"]) is None
        or type(obj["manifest_sha256"]) is not str
        or _SHA.fullmatch(obj["manifest_sha256"]) is None
        or type(obj["predecessor_sha256"]) is not str
        or _SHA.fullmatch(obj["predecessor_sha256"]) is None
        or _canonical(obj) != raw
    ):
        raise PairedBackupOperatorError("previous generation schema is not canonical")
    record_file = anchor / (obj["generation"] + ".json")
    with _pinned_file(record_file) as fd:
        if os.fstat(fd).st_size != len(raw) or os.pread(fd, len(raw), 0) != raw:
            raise PairedBackupOperatorError("head anchor disagrees with immutable record")
    return obj


def _assert_no_ambiguous_artifacts(
    anchor: Path, output: Path, *, has_head: bool,
) -> None:
    """Fail closed on interrupted generations before creating a new chain tip.

    A missing HEAD alone never proves first initialization: an old valid
    source pair may have been published already and only the mutable pointer
    rolled back or was lost in a crash. Never silently re-bootstrap around
    retained history or other generations.
    """
    if not has_head:
        if any(p.name != ".authority.lock" for p in anchor.iterdir()):
            raise PairedBackupOperatorError(
                "head is absent but anchored generation history remains"
            )
        if any(output.iterdir()):
            raise PairedBackupOperatorError(
                "head is absent but a cold generation or partial copy remains"
            )
        return
    # A previous generation has an externally pinned HEAD. A retained
    # unpublished staging image or incomplete atomic-head handoff is UNKNOWN,
    # not harmless: inspect and reconcile before another generation.
    if any(p.name == ".inflight" for p in anchor.iterdir()):
        raise PairedBackupOperatorError("unsettled generation requires operator recovery")
    if any(p.name.startswith(".next-") for p in anchor.iterdir()):
        raise PairedBackupOperatorError("unsettled head update requires operator recovery")
    if any(p.name.startswith(".partial-") for p in output.iterdir()):
        raise PairedBackupOperatorError("unsettled partial generation requires recovery")


@contextmanager
def _authority_lock(anchor: Path) -> Iterator[None]:
    path = anchor / ".authority.lock"
    try:
        _new_file(path, b"")
        _fsync_dir(anchor)
    except FileExistsError:
        pass
    with _pinned_file(path) as fd:
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
            raise PairedBackupOperatorError("operator lock is untrusted")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PairedBackupOperatorError("another operator is sealing a generation") from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def seal_offline_pair(
    source_sqlite: Path,
    source_witness: Path,
    output_root: Path,
    anchor_root: Path,
    *,
    expected_sqlite_sha256: str,
    expected_witness_sha256: str,
    expected_previous_head_sha256: str | None,
    generation: str | None = None,
) -> dict[str, object]:
    """Create-only, digest-bound cold generation under a separate operator anchor.

    Inputs MUST be trusted cold images already staged into an owner-private
    directory after an independently attested host cutover. The supplied
    source digests MUST come from a trusted, separate attestation channel.
    No caller-provided boolean or self-asserted proof is accepted as a host
    attestation. A successful result remains explicitly NOT restore authority.

    The anchor lives outside output_root; callers must preserve/pin its latest
    SHA independently. Failures leave any uncertain, newly written artifacts
    in place for operator inspection, never retry/overwrite automatically.
    """
    sql = source_sqlite
    witness = source_witness
    if not isinstance(sql, Path) or not isinstance(witness, Path):
        raise PairedBackupOperatorError("cold input paths must be exact Path objects")
    if sql == witness or sql.name.endswith(("-wal", "-shm", "-journal")):
        raise PairedBackupOperatorError("cold source identities are invalid")
    source_dir = _private_root(sql.parent)
    _private_root(witness.parent)
    if witness.parent != source_dir:
        raise PairedBackupOperatorError("trusted cold images require one private source root")
    output = _private_root(output_root)
    anchor = _private_root(anchor_root)
    _separate_roots(source_dir, output, anchor)
    sqlite_sha = _require_digest(expected_sqlite_sha256, "SQLite image")
    witness_sha = _require_digest(expected_witness_sha256, "Witness image")
    if expected_previous_head_sha256 is not None:
        _require_digest(expected_previous_head_sha256, "previous head")
    if generation is None:
        generation = secrets.token_hex(16)
    if type(generation) is not str or _GENERATION.fullmatch(generation) is None:
        raise PairedBackupOperatorError("generation must be 32 lowercase hex digits")
    temporary = output / (".partial-" + generation)
    published = output / generation

    try:
        # The operator's anchor locks serialize HEAD publication. They do NOT
        # quiesce a live SQLite writer or revoke historical mmap descriptors.
        with _authority_lock(anchor):
            prior = _read_head(anchor, expected_previous_head_sha256)
            _assert_no_ambiguous_artifacts(anchor, output, has_head=prior is not None)
            _unoccupied(temporary)
            _unoccupied(published)
            _unoccupied(anchor / (generation + ".json"))
            _unoccupied(anchor / (".next-" + generation))
            _no_sidecars(sql)
            _no_sidecars(witness)
            with ExitStack() as stack:
                stack.enter_context(_witness_owner_absent(witness))
                sqlite_fd = stack.enter_context(_pinned_file(sql))
                witness_fd = stack.enter_context(_pinned_file(witness))
                if (
                    os.fstat(sqlite_fd).st_dev, os.fstat(sqlite_fd).st_ino
                ) == (
                    os.fstat(witness_fd).st_dev, os.fstat(witness_fd).st_ino
                ):
                    raise PairedBackupOperatorError("cold images share one source inode")
                if (
                    _sha256_fd(sqlite_fd) != sqlite_sha
                    or _sha256_fd(witness_fd) != witness_sha
                ):
                    raise PairedBackupOperatorError(
                        "cold source bytes disagree with independent image anchors"
                    )
                # Crash fence: persist generation intent BEFORE any output
                # effect. If publication stops at any later phase (including
                # after rename but before its head record), the marker stays
                # in the independent owner root and prohibits a new chain tip.
                inflight = anchor / ".inflight"
                _new_file(inflight, generation.encode("ascii"))
                _fsync_dir(anchor)
                os.mkdir(temporary, 0o700)
                staged_sqlite = temporary / "mark.sqlite"
                staged_witness = temporary / "witness.sqlite"
                if (
                    _copy_pinned(sqlite_fd, staged_sqlite) != sqlite_sha
                    or _copy_pinned(witness_fd, staged_witness) != witness_sha
                    or _sha256_fd(sqlite_fd) != sqlite_sha
                    or _sha256_fd(witness_fd) != witness_sha
                ):
                    raise PairedBackupOperatorError("cold input changed during staging")
                _new_file(temporary / "witness.sqlite.lock", b"")
                observed = inspect_offline_pair(
                    staged_sqlite, staged_witness,
                    expected_sqlite_sha256=sqlite_sha,
                    expected_witness_sha256=witness_sha,
                )
                manifest = _canonical({
                    "schema_version": 5, "generation": generation, **observed,
                })
                _new_file(temporary / "manifest.json", manifest)
                _fsync_dir(temporary)
            # Re-check source path identities and sidecars before publishing.
            _no_sidecars(sql)
            _no_sidecars(witness)
            # A rename within the private output directory publishes one
            # complete image pair. Never mutate either image after publication.
            _unoccupied(published)
            os.rename(temporary, published)
            _fsync_dir(output)
            manifest_sha = _digest(manifest)
            verified = verify_offline_pair(
                published / "mark.sqlite", published / "witness.sqlite",
                published / "manifest.json",
                expected_manifest_sha256=manifest_sha,
            )
            if verified["generation"] != generation:
                raise PairedBackupOperatorError("published pair generation drifted")
            head = _canonical({
                "schema_version": 1,
                "generation": generation,
                "manifest_sha256": manifest_sha,
                "predecessor_sha256": expected_previous_head_sha256 or _ZERO,
            })
            # A create-only retained record precedes the atomically replaced
            # HEAD. A crash can leave an orphan, never a falsely pinned pair.
            _new_file(anchor / (generation + ".json"), head)
            _fsync_dir(anchor)
            _new_file(anchor / (".next-" + generation), head)
            os.replace(anchor / (".next-" + generation), anchor / "latest.json")
            _fsync_dir(anchor)
            # Only a durably committed head releases this serial operator
            # intent. Errors never clear the marker: recovery is manual.
            os.unlink(inflight)
            _fsync_dir(anchor)
            return {
                "generation": generation,
                "manifest_sha256": manifest_sha,
                "head_sha256": _digest(head),
                "sqlite_sha256": sqlite_sha,
                "witness_sha256": witness_sha,
                "offline_only": True,
                "live_source_quiescence_attested": False,
                "restore_authorized": False,
                "external_write_authorized": False,
                "fences": verified,
            }
    except PairedBackupOperatorError:
        raise
    except (
        OSError, PairPreflightError, FenceWitnessError, ValueError,
        TypeError, OverflowError, RecursionError,
    ) as exc:
        raise PairedBackupOperatorError(
            "cold pair was not safely sealed; inspect artifacts before retry"
        ) from exc