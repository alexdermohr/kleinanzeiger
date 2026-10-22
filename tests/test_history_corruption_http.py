"""Fail-closed malformed SQLite history: real loopback HTTP, no platform."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone, timedelta
from http.client import RemoteDisconnected
from pathlib import Path
import json
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

from mark_api.dashboard import create_server
from mark_api.domain import (
    AdSnapshot, AdClassification, InboundMessageEvent,
    ReactionSnapshot, LifecycleState,
)
from mark_api.storage import (
    SnapshotStore, _HISTORY_BLANK_AD_ID_PREDICATE,
    _HISTORY_AD_ID_WHITESPACE_HEX,
)


class HistoryCorruptionHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SnapshotStore(Path(self.temp.name) / "mark.sqlite")
        self.instant = datetime(2026, 10, 21, tzinfo=timezone.utc)
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id="1234567890", observed_at=self.instant, source="synthetic",
            lifecycle_state=LifecycleState.ACTIVE, views=7,
        ))
        self.store.append_reaction_snapshot(ReactionSnapshot(
            ad_id="1234567890", observed_at=self.instant, source="synthetic",
            conversation_count=3, unique_buyer_count=2, inbound_message_count=4,
        ))
        self.store.append_inbound_message_events((InboundMessageEvent(
            ad_id="1234567890", conversation_id="synthetic-chat",
            provider_message_id="synthetic-message", observed_at=self.instant,
            source="email",
        ),))
        self.store.append_classification(AdClassification(
            ad_id="1234567890", observed_at=self.instant, source="synthetic",
            city="Berlin",
        ))
        self.server = create_server(self.store, port=0)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True,
        )
        self.thread.start()
        self.addCleanup(self._close)

    def _close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def _get(self, path):
        port = self.server.server_address[1]
        try:
            with urlopen(f"http://127.0.0.1:{port}" + path, timeout=4) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()

    def _replace(self, table, column, value):
        # All table names/column names here are fixed local test literals.
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            original = connection.execute(
                f"SELECT {column} FROM {table} LIMIT 1"
            ).fetchone()[0]
            connection.execute(f"UPDATE {table} SET {column}=?", (value,))
        return original

    def _assert_sanitized_503(self, table, column, path, replacement):
        baseline_status, _ = self._get(path)
        self.assertEqual(baseline_status, 200)
        previous = self._replace(table, column, replacement)
        try:
            status, body = self._get(path)
            self.assertEqual(status, 503)
            self.assertEqual(
                json.loads(body), {"error": "database_unavailable"},
            )
            self.assertNotIn(b"not-a-timestamp", body)
            self.assertNotIn(str(self.store.path).encode(), body)
        finally:
            self._replace(table, column, previous)
        self.assertEqual(self._get(path)[0], 200)

    def test_corrupt_timestamp_never_disconnects_dashboard(self):
        cases = (
            ("inbound_message_events", "/api/email-reactions"),
            ("reaction_snapshots", "/api/analytics/ads?metric=conversation_count"),
            ("ad_classifications", "/api/analytics/groups?dimension=city&metric=views"),
        )
        for table, path in cases:
            with self.subTest(table=table):
                self._assert_sanitized_503(
                    table, "observed_at", path, "not-a-timestamp",
                )

    def test_blob_in_required_source_fails_closed_before_json_encoding(self):
        cases = (
            ("inbound_message_events", "/api/email-reactions"),
            ("reaction_snapshots", "/api/analytics/ads?metric=conversation_count"),
            ("ad_classifications", "/api/analytics/groups?dimension=city&metric=views"),
        )
        for table, path in cases:
            with self.subTest(table=table):
                self._assert_sanitized_503(
                    table, "source", path, sqlite3.Binary(b"bad-source"),
                )

    def test_native_blob_ad_ids_must_not_disappear_before_history_lookup(self):
        # Native BLOB values sort differently from TEXT in indexed SQLite.
        # A WHERE ad_id=? filter silently hides them without prevalidation.
        cases = (
            ("reaction_snapshots",
             "/api/analytics/ads?metric=conversation_count"),
            ("inbound_message_events", "/api/email-reactions"),
            ("ad_classifications",
             "/api/analytics/groups?dimension=city&metric=views"),
        )
        for table, path in cases:
            with self.subTest(table=table):
                self._assert_sanitized_503(
                    table, "ad_id", path, sqlite3.Binary(b"native-blob-ad-id"),
                )

    def test_native_blank_text_keys_never_silently_disappear_from_http(self):
        """Issue #180: exact native SQLite TEXT must not hide empty owner keys."""
        paths = (
            ("reaction_snapshots", "/api/analytics/ads?metric=conversation_count"),
            ("inbound_message_events", "/api/email-reactions"),
            ("ad_classifications", "/api/analytics/groups?dimension=city&metric=views"),
        )
        for invalid in ("", "   ", "\t", "\u2003", " \t\u2003 "):
            for table, path in paths:
                with self.subTest(table=table, invalid=repr(invalid)):
                    self._assert_sanitized_503(table, "ad_id", path, invalid)

    def test_native_text_nul_prefix_is_not_a_blank_id(self):
        """SQLite length(TEXT) ends at NUL; an ID with a NUL is nonblank."""
        paths = (
            ("ad_snapshots", "ad_history"),
            ("reaction_snapshots", "reaction_history"),
            ("inbound_message_events", "inbound_message_history"),
            ("ad_classifications", "classification_history"),
        )
        for key in ("\x00A", " \x00A", "\x00", " \x00"):
            for table, reader_name in paths:
                with self.subTest(table=table, key=repr(key)):
                    original = self._replace(table, "ad_id", key)
                    try:
                        self.assertTrue(
                            self.store.is_ready(),
                            "nonblank NUL-prefixed TEXT must remain readable",
                        )
                        rows = getattr(self.store, reader_name)(key)
                        self.assertEqual(len(rows), 1)
                        self.assertEqual(rows[0].ad_id, key)
                    finally:
                        self._replace(table, "ad_id", original)
        self.assertEqual(self._get("/readyz")[0], 200)

    def test_later_page_native_blob_id_refuses_first_history_yield(self):
        """A corrupt BLOB after >256 valid rows must not leak an early group."""
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute("UPDATE ad_snapshots SET ad_id='A' WHERE id=1")
            db.executemany(
                "INSERT INTO ad_snapshots "
                "(ad_id, observed_at, source, lifecycle_state) VALUES (?,?,?,?)",
                [
                    ("B", (self.instant + timedelta(seconds=i)).isoformat(),
                     "synthetic", "active")
                    for i in range(300)
                ],
            )
            db.execute(
                "INSERT INTO ad_snapshots "
                "(ad_id, observed_at, source, lifecycle_state) VALUES (?,?,?,?)",
                (sqlite3.Binary(b"\xffinvalid"), self.instant.isoformat(),
                 "synthetic", "active"),
            )
        pages = self.store.ad_histories()
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                next(pages)
        finally:
            pages.close()

    def test_blank_unicode_id_between_valid_binary_extrema_is_detected(self):
        """An indexed probe must catch whitespace even in the middle of IDs."""
        self.store.append_classification(AdClassification(
            ad_id="A", observed_at=self.instant, source="synthetic",
            city="Berlin",
        ))
        self.store.append_classification(AdClassification(
            ad_id="\u4000", observed_at=self.instant, source="synthetic",
            city="Berlin",
        ))
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute(
                "UPDATE ad_classifications SET ad_id=? WHERE id=1",
                ("\u2003",),
            )
            keys = [
                row[0] for row in db.execute(
                    "SELECT ad_id FROM ad_classifications ORDER BY ad_id"
                )
            ]
        self.assertEqual(keys, ["A", "\u2003", "\u4000"])
        self.assertFalse(self.store.is_ready())
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.classification_history("A")
        status, body = self._get("/api/analytics/groups?dimension=city&metric=views")
        self.assertEqual((status, json.loads(body)),
                         (503, {"error": "database_unavailable"}))

    def test_blank_key_index_is_bounded_and_unicode_semantics_match_python(self):
        """No hidden str.strip() codepoint or fallback full-table read."""
        characters = "".join(
            chr(codepoint) for codepoint in range(0x110000)
            if chr(codepoint).isspace()
        )
        self.assertEqual(
            characters.encode("utf-8").hex().upper(),
            _HISTORY_AD_ID_WHITESPACE_HEX,
        )
        with closing(sqlite3.connect(self.store.path)) as db:
            for table in (
                "ad_snapshots", "reaction_snapshots",
                "inbound_message_events", "ad_classifications",
            ):
                with self.subTest(table=table):
                    name = f"idx_{table}_blank_ad_id"
                    statement = (
                        f"SELECT 1 FROM {table} INDEXED BY {name} "
                        f"WHERE {_HISTORY_BLANK_AD_ID_PREDICATE} LIMIT 1"
                    )
                    planner = " ".join(
                        str(row[3]) for row in db.execute(
                            "EXPLAIN QUERY PLAN " + statement
                        )
                    )
                    self.assertIn(name, planner)
                    self.assertIsNone(db.execute(statement).fetchone())

    def test_later_page_blank_id_refuses_first_history_yield(self):
        """An invalid key after >256 B rows must reject before yielding A."""
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute(
                "UPDATE ad_snapshots SET ad_id=? WHERE id=1",
                ("\u2003",),
            )
            db.execute(
                "INSERT INTO ad_snapshots (ad_id, observed_at, source, lifecycle_state) "
                "VALUES (?,?,?,?)",
                ("A", self.instant.isoformat(), "synthetic", "active"),
            )
            db.executemany(
                "INSERT INTO ad_snapshots (ad_id, observed_at, source, lifecycle_state) "
                "VALUES (?,?,?,?)",
                [
                    ("B", (self.instant + timedelta(seconds=i)).isoformat(),
                     "synthetic", "active")
                    for i in range(300)
                ],
            )
        pages = self.store.ad_histories()
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                next(pages)
        finally:
            pages.close()

    def test_missing_or_forged_blank_key_index_does_not_certify_health(self):
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute("DROP INDEX idx_reaction_snapshots_blank_ad_id")
            db.execute(
                "CREATE INDEX idx_reaction_snapshots_blank_ad_id "
                "ON reaction_snapshots(ad_id) WHERE ad_id = 'impossible'"
            )
        self.assertFalse(self.store.is_ready())
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.reaction_history("1234567890")
        self.assertEqual(
            self._get("/api/analytics/ads?metric=conversation_count")[0], 503,
        )

    def test_existing_canonical_store_adds_only_missing_blank_key_indexes(self):
        tables = ("ad_snapshots", "reaction_snapshots",
                  "inbound_message_events", "ad_classifications")
        with closing(sqlite3.connect(self.store.path)) as db, db:
            for table in tables:
                db.execute(f"DROP INDEX idx_{table}_blank_ad_id")
        self.assertFalse(self.store.is_ready())
        reopened = SnapshotStore(self.store.path, create_if_missing=False)
        self.assertTrue(reopened.is_ready())
        self.assertEqual(reopened.ad_history("1234567890")[0].views, 7)
        self.assertEqual(reopened.reaction_history("1234567890")[0].conversation_count, 3)

    def test_relative_store_history_pagination_keeps_construction_path(self):
        """The connection and per-page inode checks must identify one file."""
        import os
        seeded = self._yield_paged_history_with_pending_rows()
        seeded.close()
        original_cwd = Path.cwd()
        os.chdir(self.temp.name)
        try:
            relative_store = SnapshotStore(Path("mark.sqlite"), create_if_missing=False)
        except BaseException:
            os.chdir(original_cwd)
            raise
        stream = relative_store.ad_histories()
        try:
            first, rows = next(stream)
            self.assertEqual((first, len(rows)), ("1234567890", 1))
            moved = Path(self.temp.name) / "different-cwd"
            moved.mkdir()
            os.chdir(moved)
            # A fresh generator and a suspended page continuation both
            # refer to the file the store actually connected at creation.
            self.assertEqual(
                [(key, len(items)) for key, items in relative_store.ad_histories()],
                [("1234567890", 1), ("A", 200), ("B", 200), ("C", 200)],
            )
            self.assertEqual(
                [(key, len(items)) for key, items in stream],
                [("A", 200), ("B", 200), ("C", 200)],
            )
        finally:
            os.chdir(original_cwd)
            stream.close()

    def test_blob_optional_classification_label_and_reaction_counter(self):
        self._assert_sanitized_503(
            "ad_classifications", "city",
            "/api/analytics/groups?dimension=city&metric=views",
            sqlite3.Binary(b"bad-city"),
        )
        self._assert_sanitized_503(
            "reaction_snapshots", "conversation_count",
            "/api/analytics/ads?metric=conversation_count",
            sqlite3.Binary(b"bad-count"),
        )

    def test_history_key_guard_and_lookup_use_one_wal_snapshot(self):
        """A writer between extrema and WHERE must not forge an empty result."""
        with closing(sqlite3.connect(self.store.path)) as db:
            mode = db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

        for table, reader_name in (
            ("ad_snapshots", "ad_history"),
            ("reaction_snapshots", "reaction_history"),
        ):
            with self.subTest(table=table):
                errors = []
                original_guard = self.store._assert_native_history_ad_ids

                def commit_bad_key():
                    try:
                        with closing(sqlite3.connect(
                            self.store.path, timeout=3,
                        )) as writer, writer:
                            writer.execute(
                                f"UPDATE {table} SET ad_id=? WHERE ad_id=?",
                                (sqlite3.Binary(b"writer-after-validation"),
                                 "1234567890"),
                            )
                    except Exception as exc:
                        errors.append(exc)

                def between_guard_and_keyed_query(connection, checked_table):
                    original_guard(connection, checked_table)
                    if checked_table == table:
                        writer = threading.Thread(
                            target=commit_bad_key, daemon=True,
                        )
                        writer.start()
                        writer.join(timeout=4)
                        self.assertFalse(writer.is_alive(), "WAL writer blocked")
                        self.assertEqual(errors, [])

                reader = getattr(self.store, reader_name)
                try:
                    with patch.object(
                        self.store, "_assert_native_history_ad_ids",
                        side_effect=between_guard_and_keyed_query,
                    ):
                        prior_snapshot = reader("1234567890")
                    self.assertEqual(len(prior_snapshot), 1)
                    self.assertEqual(prior_snapshot[0].ad_id, "1234567890")
                    with self.assertRaises(sqlite3.DatabaseError):
                        reader("1234567890")
                finally:
                    self._replace(table, "ad_id", "1234567890")

    def _replace_history_key_schema(
        self, table, key_declaration=None, index_key=None,
    ):
        # Rebuild only a synthetic temporary test table, never a live store.
        self.assertIn(table, (
            "ad_snapshots", "reaction_snapshots",
            "inbound_message_events", "ad_classifications",
        ))
        with closing(sqlite3.connect(self.store.path)) as db, db:
            ddl = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()[0]
            self.assertIn("ad_id TEXT NOT NULL", ddl)
            ddl = ddl.replace(table, f"{table}_replacement", 1)
            if key_declaration is not None:
                ddl = ddl.replace(
                    "ad_id TEXT NOT NULL", key_declaration, 1,
                )
            db.execute(ddl)
            db.execute(
                f"INSERT INTO {table}_replacement SELECT * FROM {table}"
            )
            db.execute(f"DROP TABLE {table}")
            db.execute(
                f"ALTER TABLE {table}_replacement RENAME TO {table}"
            )
            db.execute(
                f"CREATE INDEX idx_{table}_ad_id_observed "
                f"ON {table}({index_key or 'ad_id'}, observed_at, id)"
            )
            # Preserve the new independently validated blank-key index:
            # a NOCASE rejection must not be a false-positive missing index.
            db.execute(self.store._blank_history_index_sql(table))

    def _assert_history_schema_denied(
        self, *, reader="ad_history", path="/api/ads",
        ad_snapshot=True,
    ):
        self.assertFalse(self.store.is_ready())
        with self.assertRaises(sqlite3.DatabaseError):
            SnapshotStore(self.store.path)
        if ad_snapshot:
            with self.assertRaises(sqlite3.DatabaseError):
                list(self.store.ad_histories())
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.tracked_ad_ids()
        with self.assertRaises(sqlite3.DatabaseError):
            getattr(self.store, reader)("1234567890")
        status, body = self._get(path)
        self.assertEqual(status, 503)
        self.assertEqual(
            json.loads(body), {"error": "database_unavailable"},
        )
        self.assertNotIn(str(self.store.path).encode(), body)

    def test_canonical_binary_ad_identity_groups_exactly(self):
        for i, ad_id in enumerate(("A", "a", "A", "a")):
            self.store.append_ad_snapshot(AdSnapshot(
                ad_id=ad_id,
                observed_at=self.instant + timedelta(seconds=i),
                source="synthetic",
                lifecycle_state=LifecycleState.ACTIVE,
            ))
        groups = dict(self.store.ad_histories())
        self.assertEqual(len(groups["A"]), 2)
        self.assertEqual(len(groups["a"]), 2)
        self.assertEqual(
            set(self.store.tracked_ad_ids()), {"1234567890", "A", "a"},
        )
        self.assertEqual(self._get("/api/ads")[0], 200)
        self.assertTrue(self.store.is_ready())

    def test_nocase_ad_id_is_denied_on_startup_and_live_read(self):
        self.assertEqual(self._get("/api/ads")[0], 200)
        self._replace_history_key_schema("ad_snapshots",
            key_declaration="ad_id TEXT COLLATE NOCASE NOT NULL",
        )
        self._assert_history_schema_denied()

    def test_rtrim_ad_id_is_denied_on_startup_and_live_read(self):
        self._replace_history_key_schema("ad_snapshots",
            key_declaration="ad_id TEXT COLLATE RTRIM NOT NULL",
        )
        self._assert_history_schema_denied()

    def test_binary_column_with_nocase_index_is_denied(self):
        self._replace_history_key_schema("ad_snapshots",
            index_key="ad_id COLLATE NOCASE",
        )
        self._assert_history_schema_denied()

    def test_quoted_custom_column_ddl_fails_closed(self):
        self._replace_history_key_schema("ad_snapshots",
            key_declaration='"ad_id" TEXT COLLATE NOCASE NOT NULL',
        )
        self._assert_history_schema_denied()

    def test_reaction_nocase_never_aliases_other_exact_ad_id(self):
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id="A", observed_at=self.instant, source="synthetic",
            lifecycle_state=LifecycleState.ACTIVE,
        ))
        self.store.append_reaction_snapshot(ReactionSnapshot(
            ad_id="a", observed_at=self.instant, source="synthetic",
            conversation_count=11, unique_buyer_count=8,
            inbound_message_count=4,
        ))
        self.assertEqual(self.store.reaction_history("A"), ())
        self._replace_history_key_schema(
            "reaction_snapshots",
            key_declaration="ad_id TEXT COLLATE NOCASE NOT NULL",
        )
        self.assertFalse(self.store.is_ready())
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.reaction_history("A")
        self._assert_history_schema_denied(
            reader="reaction_history",
            path="/api/analytics/ads?metric=conversation_count",
            ad_snapshot=False,
        )

    def test_inbound_nocase_ad_id_denied(self):
        self._replace_history_key_schema(
            "inbound_message_events",
            key_declaration="ad_id TEXT COLLATE NOCASE NOT NULL",
        )
        self._assert_history_schema_denied(
            reader="inbound_message_history",
            path="/api/email-reactions",
            ad_snapshot=False,
        )

    def test_classification_nocase_ad_id_denied(self):
        self._replace_history_key_schema(
            "ad_classifications",
            key_declaration="ad_id TEXT COLLATE NOCASE NOT NULL",
        )
        self._assert_history_schema_denied(
            reader="classification_history",
            path="/api/analytics/groups?dimension=city&metric=views",
            ad_snapshot=False,
        )

    def test_reaction_binary_column_with_nocase_index_denied(self):
        self._replace_history_key_schema(
            "reaction_snapshots", index_key="ad_id COLLATE NOCASE",
        )
        self._assert_history_schema_denied(
            reader="reaction_history",
            path="/api/analytics/ads?metric=conversation_count",
            ad_snapshot=False,
        )

    def _yield_paged_history_with_pending_rows(self):
        # Synthetic 601-row source, with >1 SQLite keyset page. The first
        # generator yield occurs after the first SQLite connection is closed.
        with closing(sqlite3.connect(self.store.path)) as db, db:
            self.assertEqual(
                db.execute("PRAGMA journal_mode").fetchone()[0], "delete",
            )
            rows = [
                (letter, (self.instant + timedelta(seconds=i)).isoformat(),
                 "synthetic", "active")
                for letter in ("A", "B", "C") for i in range(200)
            ]
            db.executemany(
                "INSERT INTO ad_snapshots "
                "(ad_id, observed_at, source, lifecycle_state) "
                "VALUES (?,?,?,?)",
                rows,
            )
        stream = self.store.ad_histories()
        first, history = next(stream)
        self.assertEqual(first, "1234567890")
        self.assertEqual(len(history), 1)
        return stream

    def test_paged_ad_history_refuses_same_inode_ddl_replacement(self):
        stream = self._yield_paged_history_with_pending_rows()
        try:
            with closing(sqlite3.connect(self.store.path)) as db, db:
                original_ddl = db.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' "
                    "AND name='ad_snapshots'"
                ).fetchone()[0]
                original_version = db.execute(
                    "PRAGMA schema_version",
                ).fetchone()[0]
                db.execute("DROP TABLE ad_snapshots")
                db.execute(original_ddl)
                db.execute(
                    "CREATE INDEX idx_ad_snapshots_ad_id_observed "
                    "ON ad_snapshots(ad_id, observed_at, id)"
                )
                db.execute(self.store._blank_history_index_sql("ad_snapshots"))
                new_version = db.execute(
                    "PRAGMA schema_version",
                ).fetchone()[0]
            self.assertNotEqual(new_version, original_version)
            self.assertTrue(self.store.is_ready())
            # Without per-page identity validation this silently yields a
            # truncated B and discards C, falsely completing the generator.
            with self.assertRaises(sqlite3.DatabaseError):
                list(stream)
        finally:
            stream.close()

    def test_paged_ad_history_refuses_replaced_database_inode(self):
        stream = self._yield_paged_history_with_pending_rows()
        try:
            replacement_path = Path(self.temp.name) / "replacement.sqlite"
            SnapshotStore(replacement_path)
            with closing(sqlite3.connect(self.store.path)) as prior:
                old_version = prior.execute(
                    "PRAGMA schema_version",
                ).fetchone()[0]
            with closing(sqlite3.connect(replacement_path)) as replacement:
                next_version = replacement.execute(
                    "PRAGMA schema_version",
                ).fetchone()[0]
            self.assertEqual(old_version, next_version)
            old_stat = self.store.path.stat()
            replacement_path.replace(self.store.path)
            new_stat = self.store.path.stat()
            self.assertNotEqual(
                (old_stat.st_dev, old_stat.st_ino),
                (new_stat.st_dev, new_stat.st_ino),
            )
            self.assertTrue(self.store.is_ready())
            with self.assertRaises(sqlite3.DatabaseError):
                list(stream)
        finally:
            stream.close()

    def test_inbound_history_actual_instants_and_id_ties_remain_ordered(self):
        first = InboundMessageEvent(
            ad_id="1234567890", conversation_id="synthetic-chat",
            provider_message_id="synthetic-message-2",
            observed_at=self.instant + timedelta(seconds=1), source="email",
        )
        second = InboundMessageEvent(
            ad_id="1234567890", conversation_id="synthetic-chat",
            provider_message_id="synthetic-message-3",
            observed_at=first.observed_at.astimezone(timezone(timedelta(hours=2))),
            source="email",
        )
        self.store.append_inbound_message_events((first, second))
        history = self.store.inbound_message_history("1234567890")
        self.assertEqual(
            tuple(item.provider_message_id for item in history),
            ("synthetic-message", "synthetic-message-2", "synthetic-message-3"),
        )


if __name__ == "__main__":
    unittest.main()
