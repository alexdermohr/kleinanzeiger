"""Synthetic, unverified local catalog import: no marketplace or network."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timezone
from unittest.mock import patch
import unittest
from contextlib import closing
from pathlib import Path

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.local_catalog_import import (
    CatalogImportError, import_local_catalog_files,
)
from mark_api.query import MarkQueryService
from mark_api.storage import SnapshotStore

_TIME = "2026-10-22T04:01:00+02:00"


def entry(ad_id="1234567890", **values):
    return {
        "ad_id": ad_id,
        "observed_at": _TIME,
        "title": "Lokaler Entwurf",
        "views": 12,
        **values,
    }


class LocalCatalogImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mark-catalog-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = SnapshotStore(self.root / "mark.sqlite")

    def catalog(self, name, rows):
        path = self.root / name
        path.write_text(
            json.dumps({"schema": "mark-local-catalog-v1", "ads": rows}),
            encoding="utf-8",
        )
        return path

    def test_exact_utc_timestamp_provenance_and_replay_are_idempotent(self):
        first = self.catalog("first.json", [
            entry(),
            entry("1234567891", observed_at="2026-10-22T02:00:00Z",
                  title="Second", watch_count=0, reply_count=4),
        ])
        result = import_local_catalog_files(self.store, (first,))
        self.assertEqual((result.parsed_files, result.parsed_rows), (1, 2))
        self.assertEqual(result.inserted_snapshots, 2)
        self.assertEqual(result.duplicate_snapshots, 0)
        repeated = import_local_catalog_files(self.store, (first,))
        self.assertEqual(repeated.inserted_snapshots, 0)
        self.assertEqual(repeated.duplicate_snapshots, 2)
        with closing(sqlite3.connect(self.store.path)) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM ad_snapshots").fetchone()[0], 2
            )
        view = MarkQueryService(self.store).latest_ads()
        self.assertEqual([item.ad_id for item in view], ["1234567890", "1234567891"])
        self.assertEqual([item.lifecycle_state for item in view],
                         [LifecycleState.UNKNOWN, LifecycleState.UNKNOWN])
        self.assertEqual(view[0].views, 12)
        self.assertEqual(view[0].source, "user-supplied-local-catalog")
        self.assertFalse(view[0].present, "manual file cannot establish owner presence")
        self.assertFalse(view[1].present, "second manual row is also unverified")
        summary = MarkQueryService(self.store).summary()
        self.assertEqual(summary.tracked_ads, 2)
        self.assertEqual(summary.current_ads, 0)
        self.assertEqual(summary.unknown_state_ads, 2)
        self.assertEqual(view[0].observed_at.isoformat(), "2026-10-22T02:01:00+00:00")
        self.assertEqual(
            view[0].metric_evidence["views"].source,
            "user-supplied-local-catalog",
        )
        self.assertEqual(view[1].watch_count, 0)
        self.assertEqual(view[1].reply_count, 4)
        self.assertEqual(self.store.sync_status()["state"], "never_attempted")

    def test_future_manual_catalog_cannot_demote_or_spoof_authoritative_owner(self):
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id="1234567890",
            observed_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
            source="synthetic-authenticated-owner-read",
            lifecycle_state=LifecycleState.ACTIVE,
            title="Known owner title",
            views=17,
        ))
        forged_future = self.catalog("unverified-future.json", [
            entry(
                title="Unverified and future title",
                observed_at="2027-10-22T02:01:00+00:00",
                views=9999,
            ),
        ])
        import_local_catalog_files(self.store, (forged_future,))
        projected = MarkQueryService(self.store).latest_ads()[0]
        self.assertTrue(projected.present)
        self.assertEqual(projected.lifecycle_state, LifecycleState.ACTIVE)
        self.assertEqual(projected.source, "synthetic-authenticated-owner-read")
        self.assertEqual(projected.title, "Known owner title")
        self.assertEqual(projected.views, 17)
        self.assertEqual(
            projected.metric_evidence["views"].source,
            "synthetic-authenticated-owner-read",
        )
        self.assertEqual(MarkQueryService(self.store).summary().current_ads, 1)

    def test_unverified_title_does_not_inherit_verified_source(self):
        manual = self.catalog("local-content.json", [
            entry(title="Attacker controlled local title", views=40),
        ])
        import_local_catalog_files(self.store, (manual,))
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id="1234567890",
            observed_at=datetime(2026, 10, 22, 7, tzinfo=timezone.utc),
            source="synthetic-owner-observation",
            lifecycle_state=LifecycleState.ACTIVE,
            title=None,
            views=None,
        ))
        projected = MarkQueryService(self.store).latest_ads()[0]
        self.assertTrue(projected.present)
        self.assertEqual(projected.source, "synthetic-owner-observation")
        self.assertIsNone(
            projected.title,
            "owner source cannot attribute a local title to owner evidence",
        )
        self.assertEqual(projected.views, 40)
        self.assertEqual(
            projected.metric_evidence["views"].source,
            "user-supplied-local-catalog",
        )

    def test_even_tampered_local_state_cannot_claim_owner_presence(self):
        manual = self.catalog("tampered-state.json", [entry()])
        import_local_catalog_files(self.store, (manual,))
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute(
                "UPDATE ad_snapshots SET lifecycle_state='active' "
                "WHERE source='user-supplied-local-catalog'"
            )
        projected = MarkQueryService(self.store).latest_ads()[0]
        self.assertFalse(projected.present)
        self.assertEqual(projected.lifecycle_state, LifecycleState.UNKNOWN)

    def test_overflowing_utc_instants_fail_before_any_insert(self):
        for i, value in enumerate((
            "0001-01-01T00:00:00+23:59",
            "9999-12-31T23:59:59-23:59",
        )):
            with self.subTest(value=value):
                catalog = self.catalog(f"utc-overflow-{i}.json", [
                    entry(observed_at=value),
                ])
                with self.assertRaises(CatalogImportError):
                    import_local_catalog_files(self.store, (catalog,))
                self.assertEqual(self.store.tracked_ad_ids(), ())

    def test_missing_secure_open_flags_are_never_silently_disabled(self):
        catalog = self.catalog("guarded-open.json", [entry()])
        for flag in ("O_NOFOLLOW", "O_NONBLOCK"):
            with self.subTest(flag=flag):
                with patch.object(os, flag, 0):
                    with self.assertRaises(CatalogImportError):
                        import_local_catalog_files(self.store, (catalog,))
        self.assertEqual(self.store.tracked_ad_ids(), ())

    def test_all_files_stage_before_any_database_write(self):
        good = self.catalog("good.json", [entry()])
        bad = self.catalog("bad.json", [entry(ad_id="1234567892", views=-3)])
        with self.assertRaises(CatalogImportError):
            import_local_catalog_files(self.store, (good, bad))
        self.assertEqual(self.store.tracked_ad_ids(), ())

    def test_same_ad_instant_different_payload_aborts_whole_batch(self):
        bad = self.catalog("conflict.json", [
            entry(), entry(title="Stale but conflicting"),
        ])
        with self.assertRaisesRegex(CatalogImportError, "conflicting"):
            import_local_catalog_files(self.store, (bad,))
        self.assertEqual(self.store.tracked_ad_ids(), ())

    def test_conflict_with_stored_exact_origin_rolls_back_other_new_rows(self):
        original = self.catalog("original.json", [entry()])
        import_local_catalog_files(self.store, (original,))
        different = self.catalog("different.json", [
            entry("1234567892"), entry(title="Forged replacement"),
        ])
        with self.assertRaisesRegex(CatalogImportError, "conflicts"):
            import_local_catalog_files(self.store, (different,))
        self.assertEqual(self.store.tracked_ad_ids(), ("1234567890",))
        self.assertEqual(len(self.store.ad_history("1234567890")), 1)

    def test_identical_duplicates_across_files_insert_only_once(self):
        first = self.catalog("one.json", [entry()])
        second = self.catalog("two.json", [entry()])
        report = import_local_catalog_files(self.store, (first, second))
        self.assertEqual(report.inserted_snapshots, 1)
        self.assertEqual(report.duplicate_snapshots, 1)
        self.assertEqual(report.parsed_files, 2)

    def test_untrusted_json_schema_and_keys_are_rejected_without_writes(self):
        raw = (
            b'{"schema":"mark-local-catalog-v1",'
            b'"ads":[{"ad_id":"1234567890","ad_id":"1234567891",'
            b'"observed_at":"2026-10-22T00:00:00Z"}]}'
        )
        bad = self.root / "duplicate-keys.json"
        bad.write_bytes(raw)
        with self.assertRaisesRegex(CatalogImportError, "repeated JSON keys"):
            import_local_catalog_files(self.store, (bad,))
        for case in (
            entry(ad_id=1234567890),
            entry(ad_id="1234"),
            entry(observed_at="2026-10-22T00:00:00"),
            entry(observed_at="invalid"),
            entry(views=True),
            entry(views=1.1),
            entry(views=-1),
            entry(views=1_000_000_001),
            entry(title=""),
            entry(description="x" * 8001),
            entry(lifecycle_state="active"),
            entry(source="authentic-owner"),
            entry(metric_source="verified"),
        ):
            with self.subTest(case=str(case)[:75]):
                test_file = self.catalog("bad-row.json", [case])
                with self.assertRaises(CatalogImportError):
                    import_local_catalog_files(self.store, (test_file,))
        self.assertEqual(self.store.tracked_ad_ids(), ())

    def test_special_file_and_symlink_cannot_be_imported(self):
        good = self.catalog("allowed.json", [entry()])
        alias = self.root / "alias.json"
        alias.symlink_to(good)
        with self.assertRaises(CatalogImportError):
            import_local_catalog_files(self.store, (alias,))
        if hasattr(os, "mkfifo"):
            pipe = self.root / "not-a-file.pipe"
            os.mkfifo(pipe)
            with self.assertRaises(CatalogImportError):
                import_local_catalog_files(self.store, (pipe,))
        self.assertEqual(self.store.tracked_ad_ids(), ())

    def test_file_size_bound_and_batch_size_bound(self):
        too_big = self.root / "too-big.json"
        too_big.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(CatalogImportError, "size limit"):
            import_local_catalog_files(self.store, (too_big,))
        too_many = self.catalog("too-many.json", [entry()] * 2501)
        with self.assertRaisesRegex(CatalogImportError, "too many observations"):
            import_local_catalog_files(self.store, (too_many,))
        self.assertEqual(self.store.tracked_ad_ids(), ())

    def test_concurrent_same_file_replays_serialize_without_double_import(self):
        catalog = self.catalog("two-writers.json", [entry()])
        barrier = threading.Barrier(2)
        imported = []
        errors = []

        def worker():
            try:
                barrier.wait(timeout=4)
                result = import_local_catalog_files(self.store, (catalog,))
                imported.append(result.inserted_snapshots)
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=worker) for _ in range(2)]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join(timeout=6)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(sorted(imported), [0, 1])
        self.assertEqual(len(self.store.ad_history("1234567890")), 1)

    def test_tampered_stored_observation_is_not_silently_repaired(self):
        catalog = self.catalog("source.json", [entry()])
        import_local_catalog_files(self.store, (catalog,))
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute(
                "UPDATE ad_snapshots SET views=999 WHERE ad_id='1234567890'"
            )
        with self.assertRaisesRegex(CatalogImportError, "conflicts"):
            import_local_catalog_files(self.store, (catalog,))
        self.assertEqual(self.store.ad_history("1234567890")[0].views, 999)


if __name__ == "__main__":
    unittest.main()
