"""G4: read-only, sync-journal-bound owner inventory changes.

The owner browser's observations are *not* publication/write permissions.
This service compares two actual successful, non-overlapping owner sync
attempts. Failed/in-progress/ambiguous snapshots cannot prove deletion or
sales. Titles never establish a local-draft-to-platform-ad association.
"""
from __future__ import annotations

from contextlib import closing
from datetime import timezone
import hashlib
import json
import sqlite3
from typing import Any

from .domain import AdSnapshot, LifecycleState
from .adapters.management import DEFAULT_SOURCE as _OBSERVED_SOURCE
from .storage import (
    SnapshotStore, SyncAttemptRecord, _sync_attempt_from_row,
    _unresolved_sync_attempt_summary,
)


_OWNER_SOURCE = "private-web-owner-inventory"
_PAGE_MAX = 100
_HISTORY_LIMIT = 4096
_SUCCESS = frozenset(("success_empty", "success_nonempty"))


class InventoryDiffError(ValueError):
    """Invalid user cursor or changed owner/database generation."""


def _validate_cursor(after: object, limit: object, epoch: object) -> None:
    if (
        type(after) is not str or len(after) > 160
        or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in after)
        or type(limit) is not int or not 1 <= limit <= _PAGE_MAX
        or (epoch is not None and (
            type(epoch) is not str or len(epoch) != 64
            or any(c not in "0123456789abcdef" for c in epoch)
        ))
    ):
        raise InventoryDiffError("Ungueltiger Bestandscursor oder Seitengroesse.")


def _attempt(conn: sqlite3.Connection, sql: str) -> SyncAttemptRecord | None:
    row = conn.execute(sql, (_OWNER_SOURCE,)).fetchone()
    return _sync_attempt_from_row(row) if row is not None else None


def _epoch(conn: sqlite3.Connection, store: SnapshotStore,
           latest: SyncAttemptRecord | None, watermark: int, *,
           latest_global: SyncAttemptRecord | None,
           unresolved: int, oldest_unresolved: SyncAttemptRecord | None) -> str:
    version = conn.execute("PRAGMA schema_version").fetchone()
    identity = store._identity_path.stat()
    # Every field that affects the displayed "verified" gate must change the
    # cursor. This includes non-owner periodic recovery, even with NO new ad
    # observations. The global newest journal generation captures open/settled
    # transitions and unresolved_count/oldest tracks older pending attempts.
    material = (
        identity.st_dev, identity.st_ino,
        version[0] if version is not None else None,
        watermark, latest.id if latest is not None else None,
        latest.outcome if latest is not None else None,
        latest_global.id if latest_global is not None else None,
        latest_global.source if latest_global is not None else None,
        latest_global.outcome if latest_global is not None else None,
        (latest_global.completed_at.isoformat()
         if latest_global is not None and latest_global.completed_at is not None
         else None),
        unresolved, oldest_unresolved.id if oldest_unresolved is not None else None,
    )
    return hashlib.sha256(json.dumps(material).encode("ascii")).hexdigest()


def _overlapping_other_sync(
    conn: sqlite3.Connection,
    current: SyncAttemptRecord | None,
    previous: SyncAttemptRecord | None,
) -> bool:
    """Do not attribute time-matched snapshots to the wrong journal attempt.

    Owner and periodic browser reads share the management snapshot source.
    Without a persisted per-snapshot attempt ID, even a *completed* overlapping
    read makes the owner-round provenance ambiguous. This gate deliberately
    rejects that interval instead of claiming ownership from timestamps alone.
    """
    windows = tuple(
        record for record in (current, previous)
        if record is not None and record.completed_at is not None
    )
    if not windows:
        return False
    starts = [row.started_at.astimezone(timezone.utc) for row in windows]
    ends = [row.completed_at.astimezone(timezone.utc) for row in windows]
    identities = {row.id for row in windows}
    # All journal times are normalized to UTC on insertion by _sync_time().
    # SQL only reduces candidate count; strict overlap is checked as instants.
    for raw in conn.execute(
        "SELECT * FROM sync_attempts WHERE started_at <= ? "
        "AND (completed_at IS NULL OR completed_at >= ?) ORDER BY id",
        (max(ends).isoformat(), min(starts).isoformat()),
    ):
        record = _sync_attempt_from_row(raw)
        if record.id in identities:
            continue
        other_start = record.started_at.astimezone(timezone.utc)
        other_end = (
            record.completed_at.astimezone(timezone.utc)
            if record.completed_at is not None else None
        )
        for owner_start, owner_end in zip(starts, ends):
            if other_start <= owner_end and (
                other_end is None or other_end >= owner_start
            ):
                return True
    return False


def _owner_history(conn: sqlite3.Connection, ad_id: str) -> tuple[AdSnapshot, ...] | None:
    rows = conn.execute(
        "SELECT id,ad_id,observed_at,source,lifecycle_state,title,"
        "description,views,watch_count,reply_count,metric_source "
        "FROM ad_snapshots WHERE ad_id=? COLLATE BINARY "
        "AND source IN (?,?) ORDER BY id DESC LIMIT ?",
        (ad_id, _OBSERVED_SOURCE, _OWNER_SOURCE, _HISTORY_LIMIT + 1),
    ).fetchall()
    if len(rows) > _HISTORY_LIMIT:
        return None
    return SnapshotStore._decode_ad_history(rows)


def _at_attempt(
    snapshots: tuple[AdSnapshot, ...],
    attempt: SyncAttemptRecord | None,
) -> AdSnapshot | None:
    if attempt is None or attempt.completed_at is None:
        return None
    start = attempt.started_at.astimezone(timezone.utc)
    end = attempt.completed_at.astimezone(timezone.utc)
    matched = [
        snapshot for snapshot in snapshots
        if start <= snapshot.observed_at.astimezone(timezone.utc) <= end
    ]
    return matched[-1] if matched else None


def _snapshot_info(item: AdSnapshot | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "observed_at": item.observed_at.astimezone(timezone.utc).isoformat(),
        "source": item.source, "state": item.lifecycle_state.value,
        "title": item.title, "description": item.description,
        "views": item.views, "watch_count": item.watch_count,
        "reply_count": item.reply_count,
    }


def _change(
    ad_id: str, *, verified: bool, snapshots: tuple[AdSnapshot, ...] | None,
    current_attempt: SyncAttemptRecord | None,
    previous_attempt: SyncAttemptRecord | None,
) -> dict[str, Any]:
    current = (
        _at_attempt(snapshots, current_attempt)
        if verified and snapshots is not None else None
    )
    previous = (
        _at_attempt(snapshots, previous_attempt)
        if verified and snapshots is not None else None
    )
    changed_fields: list[str] = []
    if not verified:
        state = "unverified"
        reason = "Die letzte vollständige Owner-Synchronisierung ist nicht bestätigt."
    elif snapshots is None:
        state = "unverified"
        reason = "Lokale Beobachtungshistorie über der lesbaren Grenze."
    elif previous_attempt is None:
        state = "no_baseline"
        reason = "Noch kein zweiter bestätigter Owner-Abgleich vorhanden."
    elif current is None:
        state = "unclear"
        reason = "Keine eindeutige Beobachtung in dieser bestätigten Runde."
    elif current.lifecycle_state in (LifecycleState.ABSENT, LifecycleState.UNKNOWN):
        state = "unclear"
        reason = "Nicht sichtbar oder Status unklar; kein Beweis für Verkauf oder Löschung."
    elif previous is None or previous.lifecycle_state is LifecycleState.ABSENT:
        state = "newly_observed"
        reason = "Erstmals sichtbar seit der vorherigen bestätigten Owner-Runde."
    else:
        if current.lifecycle_state != previous.lifecycle_state:
            changed_fields.append("state")
        for label in ("title", "description"):
            old = getattr(previous, label)
            new = getattr(current, label)
            if old is not None and new is not None and old != new:
                changed_fields.append(label)
        if changed_fields:
            state = "changed"
            reason = "Geprüfte Plattformbeobachtung weicht von der letzten Runde ab."
        elif current.lifecycle_state is LifecycleState.PAUSED:
            state = "paused"
            reason = "Von der Plattform beobachtete Pause, keine Reservierungsbehauptung."
        else:
            state = "unchanged"
            reason = "Keine beobachtete Text- oder Statusänderung."
    return {
        "ad_id": ad_id, "state": state, "reason": reason,
        "changed_fields": changed_fields,
        "current": _snapshot_info(current),
        "previous": _snapshot_info(previous),
        "platform_writes": 0, "local_draft_match": None,
    }


def owner_inventory_diff_page(
    store: SnapshotStore, *, after: str = "", limit: int = 50,
    expected_epoch: str | None = None,
) -> dict[str, Any]:
    """Return an ID-keyset page pinned to one authoritative journal generation.

    No new tables, platform reads, background refreshes or ad writes.
    Pagination after a sync/update requires a restart rather than mixing eras.
    """
    if type(store) is not SnapshotStore:
        raise TypeError("A verified SnapshotStore is required.")
    _validate_cursor(after, limit, expected_epoch)
    with closing(store._connect()) as conn:
        conn.execute("PRAGMA query_only=ON")
        # The native-key check opens the consistent read transaction and
        # validates malformed TEXT/BLOB IDs before any page is disclosed.
        store._assert_native_history_ad_ids(conn, "ad_snapshots")
        unresolved, oldest_unresolved = _unresolved_sync_attempt_summary(conn)
        global_row = conn.execute(
            "SELECT * FROM sync_attempts ORDER BY id DESC LIMIT 1"
        ).fetchone()
        latest_global = (
            _sync_attempt_from_row(global_row) if global_row is not None else None
        )
        latest = _attempt(
            conn, "SELECT * FROM sync_attempts WHERE source=? ORDER BY id DESC LIMIT 1",
        )
        successes = tuple(
            _sync_attempt_from_row(row) for row in conn.execute(
                "SELECT * FROM sync_attempts WHERE source=? "
                "AND outcome IN ('success_empty','success_nonempty') "
                "ORDER BY id DESC LIMIT 2", (_OWNER_SOURCE,),
            )
        )
        successful = successes[0] if successes else None
        previous = successes[1] if len(successes) > 1 else None
        overlap = (
            previous is not None and successful is not None
            and successful.started_at < previous.completed_at
        )
        verified = bool(
            unresolved == 0 and latest is not None
            and latest.outcome in _SUCCESS and successful is not None
            and latest.id == successful.id and not overlap
            and not _overlapping_other_sync(conn, successful, previous)
        )
        maximum = conn.execute("SELECT MAX(id) AS max_id FROM ad_snapshots").fetchone()
        watermark = maximum["max_id"] if maximum and maximum["max_id"] is not None else 0
        if type(watermark) is not int or watermark < 0:
            raise sqlite3.DatabaseError("Invalid owner snapshot high-watermark.")
        epoch = _epoch(
            conn, store, latest, watermark, latest_global=latest_global,
            unresolved=unresolved, oldest_unresolved=oldest_unresolved,
        )
        if expected_epoch is not None and epoch != expected_epoch:
            raise InventoryDiffError("Bestand seit letzter Seite geaendert; bitte neu laden.")
        rows = conn.execute(
            "SELECT DISTINCT ad_id FROM ad_snapshots WHERE source IN (?,?) "
            "AND ad_id > ? COLLATE BINARY "
            "ORDER BY ad_id COLLATE BINARY LIMIT ?",
            (_OBSERVED_SOURCE, _OWNER_SOURCE, after, limit + 1),
        ).fetchall()
        items = [
            store._required_observation_ad_id(row["ad_id"])
            for row in rows[:limit]
        ]
        count = conn.execute(
            "SELECT COUNT(DISTINCT ad_id) FROM ad_snapshots "
            "WHERE source IN (?,?)",
            (_OBSERVED_SOURCE, _OWNER_SOURCE),
        ).fetchone()[0]
        results = [
            _change(ad_id, verified=verified,
                    snapshots=_owner_history(conn, ad_id) if verified else (),
                    current_attempt=latest if verified else None,
                    previous_attempt=previous if verified else None)
            for ad_id in items
        ]
        return {
            "state": "ready" if verified else "unverified",
            "source": _OWNER_SOURCE,
            "latest_attempt_id": latest.id if latest else None,
            "previous_success_id": previous.id if previous else None,
            "confirmed_at": (
                latest.completed_at.isoformat() if verified else None
            ),
            "unresolved_sync_attempts": unresolved,
            "epoch": epoch, "total": count,
            "items": results, "has_more": len(rows) > limit,
            "next_after": items[-1] if len(rows) > limit else None,
            "platform_writes": 0,
        }
