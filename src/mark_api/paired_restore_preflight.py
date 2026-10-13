"""Read-only OFFLINE paired SQLite/Witness restore candidate inspection.

Neither this preflight nor its outputs authorize backup, restore, deployment,
recovery clearance or platform actions. It requires privately staged, inert
operator-owned copies and a manifest SHA256 independently pinned outside both
backup volumes. Live owners, historical writable mappings, malicious root,
and source-generation attestation require independent controls.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing, contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from typing import Iterator

from .container_draft import container_draft_denied
from .fence_witness import FenceLedger, FenceWitnessError
from .storage import SnapshotStore

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_GENERATION = re.compile(r"[0-9a-f]{32}\Z")
_MAX_MANIFEST = 16 * 1024 * 1024
_MAX_ROWS = 250_000
# A deliberately bounded in-memory copy is safer than reopening a mutable
# pathname; oversized restore candidates require a different attested path.
_MAX_IMAGE_BYTES = 512 * 1024 * 1024
_FENCE_KEYS = frozenset({
    "sqlite_started", "sqlite_sync_open", "sqlite_quarantined",
    "sqlite_media_unresolved",
    "dashboard_pending",
    "ambiguous_creates", "ambiguous_final_creates",
    "witness_started", "witness_unresolved",
    "witness_chain_tip",
})


class PairPreflightError(RuntimeError):
    """A restored candidate pair cannot pass the conservative offline check."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    obj: dict[str, object] = {}
    for key, value in pairs:
        if key in obj:
            raise PairPreflightError("duplicate JSON member in manifest")
        obj[key] = value
    return obj


def _manifest(raw: bytes, expected_sha: str) -> dict[str, object]:
    if not isinstance(expected_sha, str) or _SHA.fullmatch(expected_sha) is None:
        raise ValueError("independent expected manifest SHA-256 required")
    if not raw or len(raw) > _MAX_MANIFEST:
        raise PairPreflightError("manifest length is invalid")
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise PairPreflightError("manifest violates independent digest anchor")
    try:
        manifest = json.loads(
            raw, object_pairs_hook=_unique_object,
            parse_constant=lambda _token: (_ for _ in ()).throw(
                PairPreflightError("nonfinite JSON constant")
            ),
        )
    except (UnicodeError, ValueError, RecursionError, OverflowError) as exc:
        raise PairPreflightError("manifest must use strict JSON") from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {
            "schema_version", "generation", "sqlite_sha256",
            "witness_sha256", "fences",
        }
        or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 4
        or not isinstance(manifest["generation"], str)
        or _GENERATION.fullmatch(manifest["generation"]) is None
        or any(
            not isinstance(manifest[key], str)
            or _SHA.fullmatch(manifest[key]) is None
            for key in ("sqlite_sha256", "witness_sha256")
        )
        or not isinstance(manifest["fences"], dict)
        or set(manifest["fences"]) != _FENCE_KEYS
        or _canonical(manifest) != raw
    ):
        raise PairPreflightError("manifest does not bind one canonical pair generation")
    return manifest


def _private_path(path: Path) -> Path:
    if (
        not isinstance(path, Path) or not path.is_absolute()
        or path != Path(os.path.normpath(path))
    ):
        raise PairPreflightError("offline path must be absolute and normalized")
    # The immediate parent may look private while a grandparent is a
    # symlink. Check every lexical ancestor without following it. The
    # separate FD-bound parser eliminates later ancestor-swap races.
    for ancestor in path.parents:
        try:
            component = ancestor.lstat()
        except OSError as exc:
            raise PairPreflightError("offline source ancestor unavailable") from exc
        if not stat.S_ISDIR(component.st_mode):
            raise PairPreflightError("offline path has a symlink or invalid ancestor")
    parent = path.parent.lstat()
    if (
        parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise PairPreflightError("offline source directory must be private")
    return path


def _private_file(info: os.stat_result) -> bool:
    return (
        stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
        and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
    )


@contextmanager
def _pinned_file(path: Path) -> Iterator[int]:
    _private_path(path)
    fd: int | None = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        original, published = os.fstat(fd), path.lstat()
        if (
            not _private_file(original) or not _private_file(published)
            or (original.st_dev, original.st_ino) != (
                published.st_dev, published.st_ino
            )
        ):
            raise PairPreflightError("offline source inode or ownership invalid")
        yield fd
        def identity(info: os.stat_result) -> tuple[int, ...]:
            return (
                info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                info.st_ctime_ns, info.st_mode, info.st_uid, info.st_nlink,
            )
        if (
            identity(os.fstat(fd)) != identity(original)
            or identity(path.lstat()) != identity(original)
        ):
            raise PairPreflightError("offline image changed while verifying")
    except OSError as exc:
        raise PairPreflightError("offline source unreadable or replaced") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _sha256_fd(fd: int) -> str:
    # Fail before CPU-expensive hashing or SQLite allocation. Avoid parsing
    # huge sparse attacker-provided images, even in diagnostic mode.
    size = os.fstat(fd).st_size
    if not 100 <= size <= _MAX_IMAGE_BYTES:
        raise PairPreflightError("offline image exceeds bounded verification size")
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while data := os.read(fd, 1024 * 1024):
        digest.update(data)
    return digest.hexdigest()


def _no_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            path.with_name(path.name + suffix).lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise PairPreflightError("offline SQLite sidecar cannot be attested") from exc
        raise PairPreflightError("offline SQLite sidecars are forbidden")


@contextmanager
def _witness_owner_absent(path: Path) -> Iterator[None]:
    """Refuse an active cooperative witness process, without ledger writes."""
    lock_path = path.with_name(path.name + ".lock")
    try:
        lock_path.lstat()
    except FileNotFoundError as exc:
        # The owner's protocol can create a missing .lock with O_CREAT and
        # acquire it while our inspection is in flight. A private staged
        # candidate without the original lock is NOT attestably quiescent.
        # We cannot safely create one here: the verifier is read-only.
        raise PairPreflightError(
            "offline witness owner lock is missing; quiescence is unverified"
        ) from exc
    with _pinned_file(lock_path) as fd:
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
            raise PairPreflightError("witness lock mode is not protocol-compatible")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PairPreflightError("witness owner remains active") from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def _read_pinned_image(fd: int) -> bytes:
    """Materialize one size-bounded source inode; no path-based reopen."""
    info = os.fstat(fd)
    if not 100 <= info.st_size <= _MAX_IMAGE_BYTES:
        raise PairPreflightError("offline image exceeds bounded in-memory reader")
    blocks: list[bytes] = []
    offset = 0
    while offset < info.st_size:
        block = os.pread(fd, min(1024 * 1024, info.st_size - offset), offset)
        if not block:
            raise PairPreflightError("pinned image shortened during read")
        blocks.append(block)
        offset += len(block)
    return b"".join(blocks)


def _readonly(
    path: Path, *, pinned_fd: int | None = None,
    expected_sha256: str | None = None,
) -> sqlite3.Connection:
    """Parse only verified FD-sourced SQLite bytes in a private memory DB.

    Do not point SQLite at /proc/self/fd/N: some VFS versions resolve it to
    a replaceable textual pathname, reintroducing the directory-swap bug.
    """
    if pinned_fd is None:
        with _pinned_file(path) as descriptor:
            return _readonly(
                path, pinned_fd=descriptor, expected_sha256=expected_sha256,
            )
    if type(pinned_fd) is not int or pinned_fd < 0:
        raise PairPreflightError("pinned source descriptor is invalid")
    raw = _read_pinned_image(pinned_fd)
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str)
        or _SHA.fullmatch(expected_sha256) is None
        or hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        # A transient write between FD-hash and deserialization must not
        # change the exact contents that the memory connection parses.
        raise PairPreflightError("in-memory image differs from pinned digest")
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA trusted_schema=OFF")
        if not callable(getattr(conn, "deserialize", None)):
            raise PairPreflightError("pinned SQLite deserialization unavailable")
        conn.deserialize(raw)
        trusted = conn.execute("PRAGMA trusted_schema").fetchone()
        if trusted is None or trusted[0] != 0:
            raise PairPreflightError("SQLite trusted_schema isolation unavailable")
        conn.execute("PRAGMA query_only=ON")
        readonly = conn.execute("PRAGMA query_only").fetchone()
        if readonly is None or readonly[0] != 1:
            raise PairPreflightError("SQLite read-only query fence unavailable")
        conn.row_factory = sqlite3.Row
        return conn
    except BaseException:
        conn.close()
        raise


def _require_witness_delete_header(fd: int) -> None:
    """Check original rollback journal bytes (memory DB uses journal=memory)."""
    header = os.pread(fd, 20, 0)
    if (
        len(header) != 20 or header[:16] != b"SQLite format 3\x00"
        or header[18:20] != b"\x01\x01"
    ):
        raise PairPreflightError("Witness file is not a DELETE-journal SQLite image")


def _rows(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    rows = conn.execute(sql + f" LIMIT {_MAX_ROWS + 1}").fetchall()
    if len(rows) > _MAX_ROWS:
        raise PairPreflightError("bounded recovery inventory limit exceeded")
    return rows


def _text(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise PairPreflightError("invalid recovered operation identity")
    return value


def _sqlite_inventory(conn: sqlite3.Connection) -> dict[str, object]:
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise PairPreflightError("SQLite integrity check failed")
    SnapshotStore._validate_store_schema(conn)
    if conn.execute("PRAGMA user_version").fetchone()[0] != 2:
        raise PairPreflightError("SQLite recovery schema v2 is required")
    starts = [
        [_text(row[0]), _text(row[1])] for row in _rows(
            conn, "SELECT idempotency_key,request_sha256 "
            "FROM write_api_requests WHERE execution_started_at IS NOT NULL "
            "ORDER BY idempotency_key",
        )
    ]
    if any(_SHA.fullmatch(row[1]) is None for row in starts):
        raise PairPreflightError("SQLite started operation digest invalid")
    # Owner-quarantined write results have canonical 202/UNKNOWN responses
    # and may have no create checkpoint or final media receipt. They remain
    # non-retryable until separately attested, operation-bound clearance.
    # Mirror SnapshotStore.create_recovery_pending's canonical prefix test.
    quarantined = []
    for row in _rows(
        conn,
        "SELECT w.idempotency_key,w.request_sha256,w.response_json "
        "FROM write_api_requests AS w "
        "LEFT JOIN write_recovery_clearances AS c "
        "ON c.idempotency_key=w.idempotency_key "
        "AND c.request_sha256=w.request_sha256 "
        "WHERE w.state='completed' AND w.response_status=202 "
        "AND c.idempotency_key IS NULL ORDER BY w.idempotency_key",
    ):
        key, sha, response = row
        key, sha = _text(key), _text(sha)
        if _SHA.fullmatch(sha) is None or not isinstance(response, str):
            raise PairPreflightError("SQLite completed write response invalid")
        if response.startswith('{"error":"write_outcome_unverified",'):
            if response != SnapshotStore._unverified_write_api_response(key):
                raise PairPreflightError("SQLite owner quarantine response invalid")
            quarantined.append([key, sha])
    # HTTP-completed media Writes are a SECOND independent durable source of
    # ambiguity. The browser/executor may fail after invoking the platform,
    # persisting e.g. HTTP 500 without ever writing a Create checkpoint or
    # create_operation_receipts row. Mirror the exact media response
    # classification of SnapshotStore.create_recovery_pending(), not HTTP
    # status alone. Do not mistake response persistence for external proof.
    media_unresolved = []
    for row in _rows(
        conn,
        "SELECT w.idempotency_key,w.request_sha256,w.response_json "
        "FROM write_api_requests AS w "
        "LEFT JOIN write_recovery_clearances AS c "
        "ON c.idempotency_key=w.idempotency_key "
        "AND c.request_sha256=w.request_sha256 "
        "WHERE w.state='completed' AND c.idempotency_key IS NULL "
        "AND instr(w.response_json,'media_persistence_confirmed')>0 "
        "ORDER BY w.idempotency_key",
    ):
        key, sha, response = row
        key, sha = _text(key), _text(sha)
        if _SHA.fullmatch(sha) is None or not isinstance(response, str):
            raise PairPreflightError("SQLite media recovery identity invalid")
        try:
            envelope = json.loads(response, object_pairs_hook=_unique_object)
        except (ValueError, TypeError, RecursionError, OverflowError) as exc:
            raise PairPreflightError("SQLite media response malformed") from exc
        if (
            not isinstance(envelope, dict)
            or not isinstance(envelope.get("media_persistence_confirmed"), bool)
        ):
            raise PairPreflightError("SQLite media response outcome invalid")
        receipt = envelope.get("operation_receipt")
        if receipt is None:
            media_unresolved.append([key, sha])
            continue
        if not isinstance(receipt, dict):
            raise PairPreflightError("SQLite media receipt invalid")
        outcome, invoked = receipt.get("outcome"), receipt.get("writer_invoked")
        if not isinstance(outcome, str) or not isinstance(invoked, bool):
            raise PairPreflightError("SQLite media receipt outcome invalid")
        if outcome not in {"confirmed", "ambiguous", "precondition_failed"}:
            raise PairPreflightError("SQLite media outcome unsupported")
        if invoked and outcome == "ambiguous":
            media_unresolved.append([key, sha])
    pending_sync = [
        int(row[0]) for row in _rows(
            conn, "SELECT id FROM sync_attempts WHERE outcome='in_progress' ORDER BY id",
        )
    ]
    if any(row <= 0 for row in pending_sync):
        raise PairPreflightError("SQLite sync attempt identity invalid")
    dashboard = [
        [_text(row[0]), _text(row[1]), _text(row[2]), row[3]]
        for row in _rows(
            conn, "SELECT scope,resource_key,idempotency_key,acknowledged "
            "FROM dashboard_pending_writes ORDER BY scope",
        )
    ]
    if any(type(row[3]) is not int or row[3] not in (0, 1) for row in dashboard):
        raise PairPreflightError("SQLite dashboard pending fence invalid")
    # A durable before_media_post_read checkpoint always has
    # outcome='confirmed' (SnapshotStore.append_create_operation_checkpoint).
    # The browser executor may crash BEFORE persisting its final receipt.
    # Only a narrowly matched and media-confirmed final receipt settles
    # this local checkpoint; claimed browser results do NOT independently
    # establish an external platform outcome or recovery clearance.
    ambiguous = [
        int(row[0]) for row in _rows(
            conn,
            "SELECT c.id FROM create_operation_checkpoints AS c "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM create_operation_receipts AS r "
            "WHERE c.operation=r.operation "
            "AND c.created_ad_id=r.created_ad_id "
            "AND c.started_at=r.started_at "
            "AND c.authorization_by IS NOT NULL "
            "AND c.authorization_by=r.authorization_by "
            "AND c.authorization_reference IS NOT NULL "
            "AND c.authorization_reference=r.authorization_reference "
            "AND c.post_snapshot_json IS r.post_snapshot_json "
            "AND c.confirmation_post_snapshot_json IS r.confirmation_post_snapshot_json "
            "AND c.content_post_snapshot_json IS r.content_post_snapshot_json "
            "AND r.outcome='confirmed' "
            "AND r.writer_invoked=1 "
            "AND r.media_persistence_confirmed=1 "
            "AND r.media_post_read_status='confirmed'"
            ") ORDER BY c.id",
        )
    ]
    if any(row <= 0 for row in ambiguous):
        raise PairPreflightError("SQLite ambiguous create identity invalid")
    # Checkpoints are not the only root of a Create recovery fence: a media
    # submit can durably persist an AMBIGUOUS final receipt directly, without
    # any earlier confirmed checkpoint. Mirror SnapshotStore's exact
    # create_recovery_pending predicate, including its operation-bound
    # independent clearance join. A missing source request/claim is NOT an
    # implicit clearance: the unmatched receipt remains an unresolved root.
    ambiguous_final = [
        int(row[0]) for row in _rows(
            conn,
            "SELECT r.id FROM create_operation_receipts AS r "
            "LEFT JOIN write_api_requests AS w "
            "ON r.authorization_reference = ('write-api:' || w.idempotency_key) "
            "LEFT JOIN write_recovery_clearances AS c "
            "ON c.idempotency_key = w.idempotency_key "
            "AND c.request_sha256 = w.request_sha256 "
            "WHERE r.operation='create' AND r.outcome='ambiguous' "
            "AND r.writer_invoked=1 AND r.media_post_read_status IS NOT NULL "
            "AND c.idempotency_key IS NULL ORDER BY r.id",
        )
    ]
    if any(row <= 0 for row in ambiguous_final):
        raise PairPreflightError("SQLite ambiguous final create identity invalid")
    return {
        "sqlite_started": starts,
        "sqlite_sync_open": pending_sync,
        "sqlite_quarantined": quarantined,
        "sqlite_media_unresolved": media_unresolved,
        "dashboard_pending": dashboard,
        "ambiguous_creates": ambiguous,
        "ambiguous_final_creates": ambiguous_final,
    }


_DDL_LEXEME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[(),;=]")


def _witness_ddl_tokens(sql: str) -> tuple[tuple[str, str], ...]:
    """Tokenize only the canonical v2 DDL subset; do not normalize literals.

    A lossy lower()/split() comparison also normalizes the contents of
    single-quoted SQL literals and merges identifier/keyword boundaries.
    This tokenization rejects comments, quoted identifiers, SQL operators,
    Unicode lookalikes and anything outside the actually emitted DDL.
    """
    if not isinstance(sql, str) or not 0 < len(sql) <= 16384:
        raise PairPreflightError("witness DDL length or type invalid")
    tokens: list[tuple[str, str]] = []
    offset = 0
    while offset < len(sql):
        c = sql[offset]
        if c in " \t\r\n\f\v":
            offset += 1
            continue
        if c == "'":
            begin = offset
            offset += 1
            while offset < len(sql):
                if sql[offset] != "'":
                    offset += 1
                    continue
                if offset + 1 < len(sql) and sql[offset + 1] == "'":
                    offset += 2
                    continue
                offset += 1
                tokens.append(("literal", sql[begin:offset]))
                break
            else:
                raise PairPreflightError("unterminated witness SQL literal")
            continue
        match = _DDL_LEXEME.match(sql, offset)
        if match is None:
            raise PairPreflightError("unsupported witness DDL token")
        tokens.append(("sql", match.group(0).upper()))
        offset = match.end()
    return tuple(tokens)


def _verify_witness_security_schema(conn: sqlite3.Connection) -> None:
    """Check full v2 tables/indexes/triggers, not attacker-chosen names alone.

    Canonical expected definitions reproduce FenceLedger initialization.
    Token sequences preserve string-literal case/content and word boundaries,
    while ignoring harmless source formatting and SQL keyword case.
    """
    expected_sql = {
        ("table", "sessions"): (
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, "
            "state TEXT NOT NULL CHECK(state IN ('active','sealed')), "
            "opened_at TEXT NOT NULL, sealed_at TEXT)"
        ),
        ("table", "events"): (
            "CREATE TABLE events (seq INTEGER PRIMARY KEY, "
            "session_id TEXT NOT NULL, kind TEXT NOT NULL, "
            "request_key TEXT, request_sha256 TEXT, action TEXT, "
            "outcome TEXT, response_sha256 TEXT, evidence_reference TEXT, "
            "created_at TEXT NOT NULL, previous_sha256 TEXT NOT NULL, "
            "row_sha256 TEXT NOT NULL)"
        ),
        ("index", "one_start_per_key"): (
            "CREATE UNIQUE INDEX one_start_per_key ON events(request_key) "
            "WHERE kind='started'"
        ),
        ("index", "one_completion_per_key"): (
            "CREATE UNIQUE INDEX one_completion_per_key ON events(request_key) "
            "WHERE kind='completed'"
        ),
        ("index", "one_runtime_clearance_per_key"): (
            "CREATE UNIQUE INDEX one_runtime_clearance_per_key "
            "ON events(request_key) WHERE kind='runtime_cleared'"
        ),
        ("index", "one_operator_clearance_per_key"): (
            "CREATE UNIQUE INDEX one_operator_clearance_per_key "
            "ON events(request_key) WHERE kind='operator_cleared'"
        ),
        ("trigger", "events_no_update"): (
            "CREATE TRIGGER events_no_update BEFORE UPDATE ON events "
            "BEGIN SELECT RAISE(ABORT,'append-only events'); END"
        ),
        ("trigger", "events_no_delete"): (
            "CREATE TRIGGER events_no_delete BEFORE DELETE ON events "
            "BEGIN SELECT RAISE(ABORT,'append-only events'); END"
        ),
    }
    expected = {
        key: _witness_ddl_tokens(ddl) for key, ddl in expected_sql.items()
    }
    observed = {
        (str(kind), str(name)): _witness_ddl_tokens(ddl)
        for kind, name, ddl in conn.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE type IN ('table','index','trigger','view') "
            "AND name NOT GLOB 'sqlite_*'"
        )
    }
    if observed != expected:
        raise PairPreflightError(
            "witness recovery schema has missing, altered or unexpected security objects"
        )
    # Non-textual SQLite properties also bind the expected rowid and exact
    # four partial UNIQUE indexes. Names or SQL text alone are insufficient.
    primary = [
        row for row in conn.execute("PRAGMA table_xinfo('events')")
        if row[5] != 0
    ]
    if (
        len(primary) != 1 or primary[0][1] != "seq"
        or primary[0][2] != "INTEGER" or primary[0][5] != 1
        or primary[0][6] != 0
    ):
        raise PairPreflightError("witness event sequence is not rowid primary key")
    indexes = {
        str(row[1]): (int(row[2]), str(row[3]), int(row[4]))
        for row in conn.execute("PRAGMA index_list('events')")
    }
    if indexes != {
        "one_start_per_key": (1, "c", 1),
        "one_completion_per_key": (1, "c", 1),
        "one_runtime_clearance_per_key": (1, "c", 1),
        "one_operator_clearance_per_key": (1, "c", 1),
    }:
        raise PairPreflightError("witness partial UNIQUE index properties invalid")


def _witness_inventory(conn: sqlite3.Connection) -> dict[str, object]:
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise PairPreflightError("Witness integrity check failed")
    # The exact original DELETE-journal source header was checked on its
    # pinned FD. Deserialized in-memory SQLite reports journal_mode=memory,
    # which must not be confused with a WAL-mode source that lacked sidecars.
    journal = conn.execute("PRAGMA journal_mode").fetchone()
    if (
        conn.execute("PRAGMA user_version").fetchone()[0] != 2
        or journal is None or journal[0].lower() != "memory"
    ):
        raise PairPreflightError("unsupported witness schema or journal")
    _verify_witness_security_schema(conn)
    FenceLedger._verify_operator_index(conn)
    FenceLedger._verify_chain(conn)
    starts = [
        [_text(row[0]), _text(row[1])] for row in _rows(
            conn, "SELECT request_key,request_sha256 FROM events "
            "WHERE kind='started' ORDER BY request_key",
        )
    ]
    if any(_SHA.fullmatch(row[1]) is None for row in starts):
        raise PairPreflightError("witness started request digest invalid")
    if conn.execute(
        "SELECT 1 FROM events AS c WHERE c.kind='operator_cleared' "
        "AND NOT EXISTS (SELECT 1 FROM events AS s WHERE s.kind='started' "
        "AND s.request_key=c.request_key AND s.request_sha256=c.request_sha256 "
        "AND s.session_id=c.session_id) LIMIT 1"
    ).fetchone():
        raise PairPreflightError("witness operator clearance is not operation-bound")
    unresolved = [
        [_text(row[0]), _text(row[1])] for row in _rows(
            conn, "SELECT s.request_key,s.request_sha256 FROM events AS s "
            "WHERE s.kind='started' AND NOT EXISTS ("
            "SELECT 1 FROM events AS c WHERE c.kind='operator_cleared' "
            "AND c.request_key=s.request_key AND c.request_sha256=s.request_sha256 "
            "AND c.session_id=s.session_id) ORDER BY s.request_key",
        )
    ]
    tip_row = conn.execute(
        "SELECT seq,row_sha256 FROM events ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    tip = [int(tip_row[0]), tip_row[1]] if tip_row else [0, "0" * 64]
    if tip[0] < 0 or not isinstance(tip[1], str) or _SHA.fullmatch(tip[1]) is None:
        raise PairPreflightError("witness chain tip invalid")
    return {
        "witness_started": starts,
        "witness_unresolved": unresolved,
        "witness_chain_tip": tip,
    }


def inspect_offline_pair(
    sqlite_image: Path, witness_image: Path, *,
    expected_sqlite_sha256: str | None = None,
    expected_witness_sha256: str | None = None,
) -> dict[str, object]:
    """Observe private quiescent copies without minting backup authority.

    If given, BOTH independent SHA anchors must match the pinned file bytes
    BEFORE opening either file with SQLite. Unanchored calls are diagnostic,
    not acceptable for admitting an untrusted external restore candidate.
    """
    if (expected_sqlite_sha256 is None) != (expected_witness_sha256 is None):
        raise ValueError("both external image digests must be supplied together")
    if expected_sqlite_sha256 is not None and (
        not isinstance(expected_sqlite_sha256, str)
        or _SHA.fullmatch(expected_sqlite_sha256) is None
        or not isinstance(expected_witness_sha256, str)
        or _SHA.fullmatch(expected_witness_sha256) is None
    ):
        raise ValueError("external candidate image SHA-256 digests are invalid")
    sql, witness = _private_path(sqlite_image), _private_path(witness_image)
    if sql == witness:
        raise PairPreflightError("paired images must be distinct")
    _no_sidecars(sql)
    _no_sidecars(witness)
    try:
        with ExitStack() as stack:
            stack.enter_context(_witness_owner_absent(witness))
            sql_fd = stack.enter_context(_pinned_file(sql))
            wit_fd = stack.enter_context(_pinned_file(witness))
            sql_info, wit_info = os.fstat(sql_fd), os.fstat(wit_fd)
            if (sql_info.st_dev, sql_info.st_ino) == (wit_info.st_dev, wit_info.st_ino):
                raise PairPreflightError("paired images resolve to same inode")
            sql_hash, wit_hash = _sha256_fd(sql_fd), _sha256_fd(wit_fd)
            if expected_sqlite_sha256 is not None and (
                sql_hash != expected_sqlite_sha256
                or wit_hash != expected_witness_sha256
            ):
                # This is the trust boundary: do not open or parse either
                # untrusted candidate image before BOTH separately pinned
                # source-file digests have matched the trusted manifest.
                raise PairPreflightError(
                    "offline candidate content differs from independent image digest anchors"
                )
            _require_witness_delete_header(wit_fd)
            with (
                closing(_readonly(
                    sql, pinned_fd=sql_fd, expected_sha256=sql_hash,
                )) as sql_db,
                closing(_readonly(
                    witness, pinned_fd=wit_fd, expected_sha256=wit_hash,
                )) as wit_db,
            ):
                sql_fences = _sqlite_inventory(sql_db)
                wit_fences = _witness_inventory(wit_db)
            if sql_fences["sqlite_started"] != wit_fences["witness_started"]:
                raise PairPreflightError("pair has unmatched durable write starts")
            if _sha256_fd(sql_fd) != sql_hash or _sha256_fd(wit_fd) != wit_hash:
                raise PairPreflightError("offline image contents changed during scan")
            _no_sidecars(sql)
            _no_sidecars(witness)
            return {
                "sqlite_sha256": sql_hash,
                "witness_sha256": wit_hash,
                "fences": {**sql_fences, **wit_fences},
            }
    except PairPreflightError:
        raise
    except (
        OSError, sqlite3.Error, FenceWitnessError, ValueError, TypeError,
        IndexError, KeyError, OverflowError, UnicodeError, RecursionError,
    ) as exc:
        raise PairPreflightError("paired offline images cannot be verified") from exc


def verify_offline_pair(
    sqlite_image: Path, witness_image: Path, manifest: Path, *,
    expected_manifest_sha256: str,
) -> dict[str, object]:
    """Read-only comparison to a manifest SHA anchored by an independent owner.

    The caller MUST independently attest a stopped/cut-over source generation
    and the external location of the expected manifest hash. This function
    cannot establish those prerequisites and cannot authorize restore.
    """
    if not isinstance(expected_manifest_sha256, str) or (
        _SHA.fullmatch(expected_manifest_sha256) is None
    ):
        raise ValueError("independent expected manifest SHA256 is required")
    manifest_path = _private_path(manifest)
    try:
        with _pinned_file(manifest_path) as fd:
            size = os.fstat(fd).st_size
            if not 0 < size <= _MAX_MANIFEST:
                raise PairPreflightError("manifest length invalid")
            os.lseek(fd, 0, os.SEEK_SET)
            anchored = _manifest(os.read(fd, _MAX_MANIFEST + 1),
                                 expected_manifest_sha256)
            observed = inspect_offline_pair(
                sqlite_image, witness_image,
                expected_sqlite_sha256=anchored["sqlite_sha256"],
                expected_witness_sha256=anchored["witness_sha256"],
            )
            if {
                key: anchored[key] for key in (
                    "sqlite_sha256", "witness_sha256", "fences",
                )
            } != observed:
                raise PairPreflightError("restored generation or pending fences differ")
            fences = observed["fences"]
            return {
                "generation": anchored["generation"],
                "manifest_sha256": expected_manifest_sha256,
                "sqlite_started_count": len(fences["sqlite_started"]),
                "witness_unresolved_count": len(fences["witness_unresolved"]),
                "dashboard_pending_count": len(fences["dashboard_pending"]),
                "sync_open_count": len(fences["sqlite_sync_open"]),
                "ambiguous_create_count": (
                    len(fences["ambiguous_creates"])
                    + len(fences["ambiguous_final_creates"])
                    + len(fences["sqlite_quarantined"])
                    + len(fences["sqlite_media_unresolved"])
                ),
                "sqlite_quarantined_count": len(fences["sqlite_quarantined"]),
                "sqlite_media_unresolved_count": len(fences["sqlite_media_unresolved"]),
                "ambiguous_final_create_count": len(fences["ambiguous_final_creates"]),
                "offline_only": True,
                "restore_authorized": False,
                "external_write_authorized": False,
            }
    except PairPreflightError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise PairPreflightError("offline manifest cannot be verified") from exc


def main(argv: list[str] | None = None) -> int:
    if container_draft_denied():
        print("mark-api: unattested container draft cannot run Python entrypoints", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(
        description="Read-only OFFLINE SQLite/Witness pair preflight; "
        "never authorizes restore, deployment or platform writes.",
    )
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument("--witness", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = verify_offline_pair(
            args.sqlite, args.witness, args.manifest,
            expected_manifest_sha256=args.expected_manifest_sha256,
        )
    except (PairPreflightError, ValueError):
        # No internal data, path or manifest contents in CLI error output.
        print("mark-api: OFFLINE pair restore candidate NOT VERIFIED", file=sys.stderr)
        return 2
    print(_canonical(receipt).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())