"""Fail-closed historic ad ID collation: prevent false keyset grouping.

All databases are synthetic, local and disposable. Never touch an account or
production SQLite file. This is a schema/identity check, not an A6 repair.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.storage import SnapshotStore


_HISTORY_TABLES = (
    "ad_snapshots", "reaction_snapshots",
    "inbound_message_events", "ad_classifications",
)


class HistoryCollationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="mark-collation-p2-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _store(self, key: str) -> SnapshotStore:
        return SnapshotStore(self.root / (key + ".sqlite"))

    @staticmethod
    def _rewrite_history_table(
        store: SnapshotStore, table: str, *, column_collation: str = "BINARY",
        index_collation: str | None = None, drop_index: bool = False,
    ) -> None:
        # The fixture first uses the real product DDL. Rebuild exactly one
        # table, without changing unrelated write-recovery tables.
        with closing(sqlite3.connect(store.path)) as db, db:
            original = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()[0]
            needle = "ad_id TEXT NOT NULL"
            assert original.count(needle) == 1
            modified = original.replace(
                needle, f"ad_id TEXT COLLATE {column_collation} NOT NULL", 1,
            )
            db.execute(f"DROP TABLE {table}")
            db.execute(modified)
            if not drop_index:
                first = "ad_id"
                if index_collation is not None:
                    first += f" COLLATE {index_collation}"
                db.execute(
                    f"CREATE INDEX idx_{table}_ad_id_observed "
                    f"ON {table}({first}, observed_at, id)"
                )

    def test_native_binary_key_identity_preserves_separate_case_histories(self) -> None:
        store = self._store("canonical")
        t0 = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
        for step, ad_id in enumerate(("A", "a", "A", "a")):
            store.append_ad_snapshot(AdSnapshot(
                ad_id=ad_id, observed_at=t0 + timedelta(minutes=step),
                source="synthetic", lifecycle_state=LifecycleState.ACTIVE,
                views=step,
            ))
        self.assertTrue(store.is_ready())
        self.assertEqual(store.tracked_ad_ids(), ("A", "a"))
        self.assertEqual(
            tuple((ad_id, len(history)) for ad_id, history in store.ad_histories()),
            (("A", 2), ("a", 2)),
        )

    def test_preexisting_nonbinary_history_columns_refused_without_migration(self) -> None:
        for table in _HISTORY_TABLES:
            for collation in ("NOCASE", "RTRIM"):
                with self.subTest(table=table, collation=collation):
                    store = self._store(f"{table}-{collation}")
                    self._rewrite_history_table(
                        store, table, column_collation=collation,
                    )
                    self.assertFalse(store.is_ready())
                    with self.assertRaises(sqlite3.DatabaseError):
                        SnapshotStore(store.path, create_if_missing=False)
                    with closing(sqlite3.connect(store.path)) as check:
                        self.assertEqual(
                            check.execute(
                                "SELECT sql FROM sqlite_master WHERE name=?",
                                (table,),
                            ).fetchone()[0].count(f"COLLATE {collation}"),
                            1,
                        )

    def test_explicit_binary_index_override_cannot_hide_nocase_column(self) -> None:
        store = self._store("override")
        self._rewrite_history_table(
            store, "ad_snapshots",
            column_collation="NOCASE", index_collation="BINARY",
        )
        # PRAGMA index_xinfo alone reports BINARY in this deceptive schema.
        with closing(sqlite3.connect(store.path)) as db:
            key = db.execute(
                "PRAGMA index_xinfo('idx_ad_snapshots_ad_id_observed')"
            ).fetchone()
            self.assertEqual(key[4], "BINARY")
        self.assertFalse(store.is_ready())
        with self.assertRaises(sqlite3.DatabaseError):
            SnapshotStore(store.path, create_if_missing=False)

    def test_nonbinary_index_override_and_missing_index_fail_closed(self) -> None:
        for suffix, index_collation, drop in (
            ("index-nocase", "NOCASE", False),
            ("missing-index", None, True),
        ):
            with self.subTest(suffix=suffix):
                store = self._store(suffix)
                self._rewrite_history_table(
                    store, "ad_snapshots", index_collation=index_collation,
                    drop_index=drop,
                )
                self.assertFalse(store.is_ready())
                with self.assertRaises(sqlite3.DatabaseError):
                    SnapshotStore(store.path, create_if_missing=False)


    def test_existing_store_readers_reject_post_start_collation_change(self) -> None:
        calls = {
            "ad_snapshots": lambda s: (s.ad_history("A"), s.tracked_ad_ids()),
            "reaction_snapshots": lambda s: s.reaction_history("A"),
            "inbound_message_events": lambda s: s.inbound_message_ad_ids(),
            "ad_classifications": lambda s: s.classification_history("A"),
        }
        for table, read in calls.items():
            with self.subTest(table=table):
                store = self._store(f"live-{table}")
                self._rewrite_history_table(
                    store, table, column_collation="NOCASE",
                )
                self.assertFalse(store.is_ready())
                with self.assertRaises(sqlite3.DatabaseError):
                    read(store)

        store = self._store("live-batched")
        self._rewrite_history_table(
            store, "ad_snapshots", column_collation="RTRIM",
        )
        with self.assertRaises(sqlite3.DatabaseError):
            tuple(store.ad_histories())

    def test_paged_reader_rejects_schema_drift_after_releasing_first_page(self) -> None:
        store = self._store("paged-drift")
        t0 = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
        with closing(sqlite3.connect(store.path)) as db, db:
            db.executemany(
                "INSERT INTO ad_snapshots "
                "(ad_id, observed_at, source, lifecycle_state) "
                "VALUES (?, ?, ?, ?)",
                [
                    (ad_id, (t0 + timedelta(seconds=step)).isoformat(),
                     "synthetic", LifecycleState.ACTIVE.value)
                    for ad_id in ("A", "B", "C")
                    for step in range(300)
                ],
            )
        stream = store.ad_histories()
        try:
            first_id, first_group = next(stream)
            self.assertEqual((first_id, len(first_group)), ("A", 300))
            # This DDL succeeds only because the active generator does not
            # retain a read lock while yielding the complete first group.
            self._rewrite_history_table(
                store, "ad_snapshots", column_collation="NOCASE",
            )
            with self.assertRaises(sqlite3.DatabaseError):
                tuple(stream)
        finally:
            stream.close()


if __name__ == "__main__":
    unittest.main()
