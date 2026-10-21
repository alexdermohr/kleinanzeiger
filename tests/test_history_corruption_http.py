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

from mark_api.dashboard import create_server
from mark_api.domain import (
    AdSnapshot, AdClassification, InboundMessageEvent,
    ReactionSnapshot, LifecycleState,
)
from mark_api.storage import SnapshotStore


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
