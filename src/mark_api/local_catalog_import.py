"""Explicit, unverified local ad catalog import; never a platform owner read.

The only inputs are files explicitly supplied by the caller. Every row has
immutable user-supplied provenance, UNKNOWN lifecycle and no write authority.
No browser, platform adapter, email inbox or network client is imported.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .domain import AdSnapshot, LifecycleState
from .storage import LOCAL_CATALOG_SOURCE, SnapshotStore

_SCHEMA = "mark-local-catalog-v1"
_SOURCE = LOCAL_CATALOG_SOURCE
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_ROWS_PER_FILE = 2500
_MAX_TOTAL_ROWS = 5000
_MAX_COUNTER = 1_000_000_000
_AD_ID = re.compile(r"[0-9]{5,20}\Z")
_FIELDS = frozenset({
    "ad_id", "observed_at", "title", "description",
    "views", "watch_count", "reply_count",
})
_COUNTERS = ("views", "watch_count", "reply_count")


class CatalogImportError(ValueError):
    """Non-sensitive explanation of an invalid local catalog."""


@dataclass(frozen=True, slots=True)
class CatalogImportReport:
    parsed_files: int
    parsed_rows: int
    inserted_snapshots: int
    duplicate_snapshots: int
    ad_ids: tuple[str, ...]


def _unique_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for key, value in pairs:
        if key in fields:
            raise CatalogImportError("local catalog contains repeated JSON keys")
        fields[key] = value
    return fields


def _read_regular_file(path: Path) -> bytes:
    # Reject symlinks, devices and FIFOs without following links or blocking.
    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if (
        type(nofollow) is not int or nofollow == 0
        or type(nonblock) is not int or nonblock == 0
    ):
        raise CatalogImportError("secure local catalog file opening is unavailable")
    flags = os.O_RDONLY | nofollow | nonblock | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise CatalogImportError("local catalog is not a readable regular file") from None
    with os.fdopen(descriptor, "rb") as file:
        meta = os.fstat(file.fileno())
        if not stat.S_ISREG(meta.st_mode):
            raise CatalogImportError("local catalog must be a regular file")
        if meta.st_size > _MAX_FILE_BYTES:
            raise CatalogImportError("local catalog exceeds size limit")
        try:
            data = file.read(_MAX_FILE_BYTES + 1)
        except OSError:
            raise CatalogImportError("local catalog could not be read") from None
        if len(data) > _MAX_FILE_BYTES:
            raise CatalogImportError("local catalog exceeds size limit")
        if not data:
            raise CatalogImportError("local catalog cannot be empty")
        return data


def _snapshot(row: object) -> AdSnapshot:
    if type(row) is not dict:
        raise CatalogImportError("local catalog ad entry must be an object")
    if not {"ad_id", "observed_at"}.issubset(row):
        raise CatalogImportError("local catalog ad entry is incomplete")
    if not set(row).issubset(_FIELDS):
        raise CatalogImportError("local catalog contains an unsupported ad field")

    ad_id = row["ad_id"]
    if type(ad_id) is not str or not _AD_ID.fullmatch(ad_id):
        raise CatalogImportError("local catalog ad_id must be 5-20 ASCII digits")
    raw_date = row["observed_at"]
    if type(raw_date) is not str or len(raw_date) > 64:
        raise CatalogImportError("local catalog observation time is invalid")
    try:
        instant = datetime.fromisoformat(raw_date)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise CatalogImportError("local catalog observation time needs an offset")
        normalized_time = instant.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise CatalogImportError("local catalog observation time is invalid") from None

    text: dict[str, str | None] = {}
    for key, limit in (("title", 200), ("description", 8000)):
        value = row.get(key)
        if value is not None:
            if (
                type(value) is not str
                or not value.strip()
                or len(value) > limit
                or "\x00" in value
            ):
                raise CatalogImportError("local catalog " + key + " is invalid")
        text[key] = value

    counts: dict[str, int | None] = {}
    for field in _COUNTERS:
        number = row.get(field)
        if number is not None and (
            type(number) is not int or number < 0 or number > _MAX_COUNTER
        ):
            raise CatalogImportError("local catalog counter is invalid")
        counts[field] = number

    return AdSnapshot(
        ad_id=ad_id,
        observed_at=normalized_time,
        source=_SOURCE,
        lifecycle_state=LifecycleState.UNKNOWN,
        title=text["title"],
        description=text["description"],
        views=counts["views"],
        watch_count=counts["watch_count"],
        reply_count=counts["reply_count"],
        metric_source=_SOURCE if any(value is not None for value in counts.values()) else None,
    )


def _parse_file(path: Path) -> tuple[AdSnapshot, ...]:
    data = _read_regular_file(path)
    try:
        decoded = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_json_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                CatalogImportError("local catalog contains an invalid number")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise CatalogImportError("local catalog is not valid UTF-8 JSON") from None
    if (
        type(decoded) is not dict
        or set(decoded) != {"schema", "ads"}
        or decoded["schema"] != _SCHEMA
        or type(decoded["ads"]) is not list
    ):
        raise CatalogImportError("local catalog schema is unsupported")
    if len(decoded["ads"]) > _MAX_ROWS_PER_FILE:
        raise CatalogImportError("local catalog contains too many observations")
    return tuple(_snapshot(row) for row in decoded["ads"])


def _stored_fields(snapshot: AdSnapshot) -> tuple[object, ...]:
    return (
        snapshot.lifecycle_state.value,
        snapshot.title,
        snapshot.description,
        snapshot.views,
        snapshot.watch_count,
        snapshot.reply_count,
        snapshot.metric_source,
    )


def import_local_catalog_files(
    store: SnapshotStore,
    paths: Iterable[Path | str],
) -> CatalogImportReport:
    """Stage every source file first, then atomically dedupe and append.

    A conflicting record for the same exact (ad_id, source, UTC instant)
    aborts the whole catalog batch without overwriting existing evidence.
    Re-importing the same observations is idempotent even across restarts.
    """
    if not isinstance(store, SnapshotStore):
        raise TypeError("store must be SnapshotStore")
    files = tuple(Path(path) for path in paths)
    if not files:
        raise CatalogImportError("at least one local catalog file is required")
    if len(files) > 32:
        raise CatalogImportError("too many local catalog files")

    staged: dict[tuple[str, str], AdSnapshot] = {}
    parsed_rows = 0
    for path in files:
        rows = _parse_file(path)
        parsed_rows += len(rows)
        if parsed_rows > _MAX_TOTAL_ROWS:
            raise CatalogImportError("too many local catalog observations")
        for row in rows:
            identity = (row.ad_id, row.observed_at.isoformat())
            original = staged.get(identity)
            if original is not None and original != row:
                raise CatalogImportError("conflicting local catalog observations")
            staged[identity] = row

    inserted = 0
    # One writer transaction covers every file, identity check and insertion.
    # An exact BINARY key check never merges IDs which merely differ in case.
    with store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        store._validate_store_schema(connection)
        for row in staged.values():
            records = connection.execute(
                """
                SELECT lifecycle_state, title, description, views,
                       watch_count, reply_count, metric_source
                FROM ad_snapshots
                WHERE ad_id COLLATE BINARY = ? AND observed_at = ? AND source = ?
                """,
                (row.ad_id, row.observed_at.isoformat(), _SOURCE),
            ).fetchall()
            if records:
                expected = _stored_fields(row)
                if any(tuple(record) != expected for record in records):
                    raise CatalogImportError(
                        "local catalog conflicts with a previous observation"
                    )
                continue
            store._insert_ad_snapshot(connection, row)
            inserted += 1

    return CatalogImportReport(
        parsed_files=len(files),
        parsed_rows=parsed_rows,
        inserted_snapshots=inserted,
        duplicate_snapshots=parsed_rows - inserted,
        ad_ids=tuple(sorted({row.ad_id for row in staged.values()})),
    )
