"""Restore a verified customer snapshot over an existing, inactive Windows database.

The selected backup is always restored into a unique *new* file first. Before
its atomic activation, preserve the previous customer state as a verified
independent backup. Failure never intentionally deletes or renames the old DB.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import sys
from urllib.parse import quote

from mark_api.backup_cli import backup_store
from mark_api.windows_backup import restore_native_windows


def restore_existing(db: Path, selected: Path) -> str:
    if os.name != "nt":
        raise RuntimeError("Windows restore only")
    if not db.is_file() or not selected.is_file():
        raise RuntimeError("database or verified backup missing")
    # The installed Windows backup module validates the exact private
    # LocalAppData paths, SQLite schema, manifest, file identity and hash.
    stage = db.with_name(".kleinanzeiger-restore-" + secrets.token_hex(16) + ".sqlite")
    try:
        verified_sha = restore_native_windows(selected, stage)
        if hashlib.sha256(stage.read_bytes()).hexdigest() != verified_sha:
            raise RuntimeError("restored snapshot changed before activation")

        now = datetime.now()
        recovery_name = f"Kleinanzeiger-{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}.sqlite"
        recovery = db.parent / "Backups" / recovery_name
        receipt = backup_store(db, backup_db=recovery)
        if (not recovery.is_file()
                or not Path(str(recovery) + ".manifest.json").is_file()
                or hashlib.sha256(recovery.read_bytes()).hexdigest() != receipt.backup_sha256):
            raise RuntimeError("current-state recovery backup not verified")

        # Checkpoint committed WAL frames and refuse replacement if another
        # process is holding sidecars. This leaves the old database unchanged.
        uri = "file:" + quote(str(db), safe="/") + "?mode=rw"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is not None and (
                int(checkpoint[0]) != 0 or int(checkpoint[1]) != int(checkpoint[2])
            ):
                raise RuntimeError("current database still busy")
        if any(Path(str(db) + suffix).exists()
               for suffix in ("-wal", "-shm", "-journal")):
            raise RuntimeError("database sidecar still active")

        # Same-directory rename: old *or* verified new DB remains the active
        # filename even if the machine fails during the single filesystem swap.
        os.replace(stage, db)
        return recovery_name
    finally:
        # This unique path can never be an existing customer DB. Never unlink
        # the selected backup, old DB or the automatic recovery backup.
        stage.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        recovery_name = restore_existing(args.db, args.backup)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print("RESTORE_EXISTING_FAILED " + type(exc).__name__, file=sys.stderr)
        return 1
    print("RESTORE_PREVIOUS_SAVED " + recovery_name, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
