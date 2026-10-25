"""Native Windows normal-user SQLite snapshot backup and create-only restore.

This is NOT an A6 multi-UID cutover or legacy database migration.
It assumes the customer's own uncompromised Windows profile/ACL authority.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
from urllib.parse import quote

from .storage import SnapshotStore
from .windows_runtime import require_private_windows_directory


class WindowsBackupError(RuntimeError):
    """A Windows customer backup/restore safety precondition failed."""


_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_FORMAT = "kleinanzeiger-windows-new-install-backup-v1"
_SQLITE_HEADER = b"SQLite format 3\x00"


def _customer_path(path: Path, *, existing: bool = True) -> Path:
    if os.name != "nt" or not isinstance(path, Path):
        raise WindowsBackupError("Windows normal-user backup only")
    if not path.is_absolute():
        raise WindowsBackupError("backup path must be absolute")
    candidate = Path(os.path.abspath(path))
    try:
        require_private_windows_directory(candidate.parent)
    except OSError:
        raise WindowsBackupError("backup path must be inside private LocalAppData") from None
    if existing:
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            raise WindowsBackupError("backup source does not exist") from None
        if (
            not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & _REPARSE
            or info.st_nlink != 1
        ):
            raise WindowsBackupError("backup source is not a private regular file")
    elif candidate.exists() or candidate.is_symlink():
        raise WindowsBackupError("backup destination already exists")
    return candidate


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(1 << 20):
            h.update(data)
    return h.hexdigest()


def _verified_connection(
    path: Path, *, immutable: bool = False,
) -> sqlite3.Connection:
    # Live sources must read their WAL; serialized backup images are standalone
    # and must never create or consume a sidecar during verification.
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
    if immutable:
        uri += "&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        SnapshotStore._validate_store_schema(conn)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise WindowsBackupError("SQLite database integrity check failed")
    except Exception:
        conn.close()
        raise
    return conn


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    queries = {
        "ad_snapshots": "SELECT count(*) FROM ad_snapshots",
        "inbound_message_events": "SELECT count(*) FROM inbound_message_events",
        "sync_attempts": "SELECT count(*) FROM sync_attempts",
        "open_sync_attempts": (
            "SELECT count(*) FROM sync_attempts WHERE outcome='in_progress'"
        ),
        "pending_api_writes": (
            "SELECT count(*) FROM write_api_requests WHERE state='in_progress'"
        ),
        "pending_dashboard_writes": (
            "SELECT count(*) FROM dashboard_pending_writes"
        ),
    }
    result = {key: int(conn.execute(query).fetchone()[0]) for key, query in queries.items()}
    if any(value < 0 for value in result.values()):
        raise WindowsBackupError("SQLite record counts are invalid")
    return result


def _create_only_verified_image(image: bytes, target: Path) -> str:
    """Flush and verify a unique private stage, then hardlink without overwrite."""
    if not image.startswith(_SQLITE_HEADER):
        raise WindowsBackupError("serialized SQLite backup is invalid")
    if target.exists() or target.is_symlink():
        raise WindowsBackupError("backup destination already exists")
    expected = hashlib.sha256(image).hexdigest()
    stage = target.parent / ("." + target.name + "." + secrets.token_hex(12) + ".part")
    published = False
    try:
        with stage.open("xb") as stream:
            stream.write(image)
            stream.flush()
            os.fsync(stream.fileno())
        if _sha256(stage) != expected:
            raise WindowsBackupError("staged backup contents changed")
        # os.link fails rather than overwriting an existing customer DB.
        os.link(stage, target)
        published = True
        if _sha256(target) != expected:
            raise WindowsBackupError("published backup contents changed")
        return expected
    except FileExistsError:
        raise WindowsBackupError("backup destination already exists") from None
    except OSError as exc:
        raise WindowsBackupError("Windows create-only backup publication failed") from exc
    finally:
        # Stage is created under this user's validated profile, with an
        # unguessable name. The linked destination is NEVER deleted on error.
        try:
            stage.unlink(missing_ok=True)
        except OSError:
            if not published:
                raise WindowsBackupError("backup stage cleanup failed") from None


def backup_native_windows(source_db: Path, backup_db: Path) -> dict[str, object]:
    """Create a separate, schema-checked WAL-consistent backup in LocalAppData."""
    source = _customer_path(source_db)
    target = _customer_path(backup_db, existing=False)
    if source == target or target.name.endswith(("-wal", "-shm", "-journal")):
        raise WindowsBackupError("invalid backup destination")
    manifest_path = _customer_path(
        target.with_name(target.name + ".manifest.json"), existing=False
    )
    try:
        with closing(_verified_connection(source)) as current:
            # Never copy main/wal/shm separately; SQLite's online backup
            # includes committed WAL frames as one point-in-time snapshot.
            with closing(sqlite3.connect(":memory:")) as snapshot:
                snapshot.row_factory = sqlite3.Row
                current.backup(snapshot, pages=128, sleep=0.05)
                SnapshotStore._validate_store_schema(snapshot)
                if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise WindowsBackupError("backup SQLite integrity failed")
                counts = _counts(snapshot)
                image = snapshot.serialize()
        digest = _create_only_verified_image(image, target)
        # Read back the published file, not only the in-memory image.
        with closing(_verified_connection(target, immutable=True)) as copied:
            if _counts(copied) != counts:
                raise WindowsBackupError("backup evidence counts changed")
        manifest = {
            "format": _FORMAT,
            "filename": target.name,
            "sha256": digest,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "counts": counts,
        }
        # A missing companion manifest means restore must refuse the backup.
        with manifest_path.open("x", encoding="utf-8") as output:
            json.dump(manifest, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        return {
            "created_at": manifest["created_at"],
            "backup_sha256": digest,
            **counts,
        }
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise WindowsBackupError("native Windows SQLite backup failed") from exc


def restore_native_windows(backup_db: Path, destination: Path) -> str:
    """Restore a verified, previously-created Windows snapshot to a NEW file."""
    source = _customer_path(backup_db)
    # The manifest hashes the standalone SQLite image, not WAL/SHM.
    # Never let SQLite overlay unverified frames from adjacent sidecars.
    source_sidecars = tuple(
        source.with_name(source.name + suffix)
        for suffix in ("-wal", "-shm", "-journal")
    )
    if any(path.exists() or path.is_symlink() for path in source_sidecars):
        raise WindowsBackupError("backup has unexpected SQLite sidecars")
    target = _customer_path(destination, existing=False)
    sidecars = tuple(
        target.with_name(target.name + suffix)
        for suffix in ("-wal", "-shm", "-journal")
    )
    if any(path.exists() or path.is_symlink() for path in sidecars):
        raise WindowsBackupError("restore target has existing SQLite sidecars")
    if source == target:
        raise WindowsBackupError("restore source cannot be its destination")
    manifest_path = _customer_path(
        source.with_name(source.name + ".manifest.json")
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise WindowsBackupError("backup provenance manifest missing or invalid") from None
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != _FORMAT
        or manifest.get("filename") != source.name
        or type(manifest.get("sha256")) is not str
        or len(manifest["sha256"]) != 64
        or not isinstance(manifest.get("counts"), dict)
    ):
        raise WindowsBackupError("backup provenance manifest is invalid")
    if _sha256(source) != manifest["sha256"]:
        raise WindowsBackupError("backup hash mismatch")
    try:
        with closing(_verified_connection(source, immutable=True)) as original:
            expected_counts = _counts(original)
            if expected_counts != manifest["counts"]:
                raise WindowsBackupError("backup recovery records do not match manifest")
            image = original.serialize()
        digest = _create_only_verified_image(image, target)
        with closing(_verified_connection(target, immutable=True)) as restored:
            if _counts(restored) != expected_counts:
                raise WindowsBackupError("restore recovery records changed")
        return digest
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise WindowsBackupError("Windows restore failed") from exc


def restore_main(argv: list[str] | None = None) -> int:
    """Restore a Windows backup only into an absent SQLite file."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Restore a verified Windows new-install backup to a NEW SQLite file."
    )
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        digest = restore_native_windows(args.backup, args.db)
    except WindowsBackupError as exc:
        parser.error(str(exc))
    print(json.dumps({
        "scope": "native-windows-user-owned-fresh-file",
        "destination": str(args.db),
        "sha256": digest,
        "result": "restored",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(restore_main())