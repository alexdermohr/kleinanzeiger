"""SQLite handles close on all transaction exits; no leaked writer/read locks."""
from __future__ import annotations

import gc
import sqlite3
import tempfile
import unittest
import warnings
from pathlib import Path

from mark_api.storage import SnapshotStore


class SQLiteConnectionLifetimeTests(unittest.TestCase):
    def test_successful_transaction_commits_and_closes_handle(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SnapshotStore(Path(temp) / "state.sqlite")
            with store._connect() as conn:
                conn.execute(
                    "INSERT INTO ad_snapshots (ad_id, observed_at, source, "
                    "lifecycle_state) VALUES (?, ?, ?, ?)",
                    ("safe-ad", "2026-10-21T09:00:00+00:00", "synthetic", "active"),
                )
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
            self.assertEqual(store.tracked_ad_ids(), ("safe-ad",))

    def test_exception_rolls_back_and_closes_handle(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SnapshotStore(Path(temp) / "state.sqlite")
            with self.assertRaisesRegex(RuntimeError, "synthetic rollback"):
                with store._connect() as conn:
                    conn.execute("INSERT INTO ad_snapshots "
                                 "(ad_id, observed_at, source, lifecycle_state) "
                                 "VALUES (?, ?, ?, ?)",
                                 ("must-rollback", "2026-10-21T09:00:00+00:00",
                                  "synthetic", "active"))
                    raise RuntimeError("synthetic rollback")
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
            self.assertEqual(store.tracked_ad_ids(), ())

    def test_query_only_transaction_does_not_hold_reader_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SnapshotStore(Path(temp) / "state.sqlite")
            with store._connect() as reader:
                reader.execute("PRAGMA query_only=ON")
                reader.execute("SELECT * FROM ad_snapshots").fetchall()
            with self.assertRaises(sqlite3.ProgrammingError):
                reader.execute("SELECT 1")
            with store._connect() as writer:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("INSERT INTO ad_snapshots "
                               "(ad_id, observed_at, source, lifecycle_state) "
                               "VALUES (?, ?, ?, ?)",
                               ("writer", "2026-10-21T09:00:00+00:00",
                                "synthetic", "active"))
            self.assertEqual(store.tracked_ad_ids(), ("writer",))

    def test_repeated_calls_do_not_emit_unclosed_sqlite_warnings(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SnapshotStore(Path(temp) / "state.sqlite")
            gc.collect()
            with warnings.catch_warnings(record=True) as found:
                warnings.simplefilter("always", ResourceWarning)
                for _ in range(60):
                    self.assertTrue(store.is_ready())
                    self.assertEqual(store.tracked_ad_ids(), ())
                    self.assertEqual(store.last_successful_sync_attempt(), None)
                gc.collect()
            leaks = [str(w.message) for w in found
                     if issubclass(w.category, ResourceWarning)
                     and "unclosed database" in str(w.message)]
            self.assertEqual(leaks, [], "SQLite handles leaked across storage calls")


if __name__ == "__main__":
    unittest.main()
