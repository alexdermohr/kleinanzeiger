"""Synthetic negative HTTP tests for native SQLite BLOBs in TEXT-affinity snapshots."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
import json
import sqlite3
import tempfile
import threading
import unittest

from mark_api.dashboard import create_server
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.storage import SnapshotStore


class SnapshotNativeTextHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SnapshotStore(Path(self.tmp.name) / "mark.sqlite")
        self.store.append_ad_snapshot(
            AdSnapshot(
                ad_id="1", observed_at=datetime(2026, 10, 21, 11, tzinfo=timezone.utc),
                source="synthetic", lifecycle_state=LifecycleState.ACTIVE,
                title="valid title", description="valid description",
            )
        )
        self.server = create_server(self.store, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        host, port = self.server.server_address
        self.base = f"http://{host}:{port}"

    def change_text(self, field: str, value: str | bytes) -> None:
        with closing(sqlite3.connect(self.store.path)) as connection:
            with connection:
                connection.execute(
                    f"UPDATE ad_snapshots SET {field}=? WHERE ad_id='1'",
                    (sqlite3.Binary(value) if isinstance(value, bytes) else value,),
                )

    def test_blob_source_and_content_return_sanitized_http_not_disconnect(self) -> None:
        for field, original in (
            ("source", "synthetic"),
            ("title", "valid title"),
            ("description", "valid description"),
        ):
            with self.subTest(field=field):
                self.change_text(field, b"untrusted-synthetic-blob")
                try:
                    for route in ("/api/ads", "/api/ads/1/history"):
                        with self.subTest(route=route):
                            with self.assertRaises(HTTPError) as caught:
                                urlopen(self.base + route, timeout=3)
                            self.assertEqual(caught.exception.code, 503)
                            self.assertEqual(
                                json.loads(caught.exception.read()),
                                {"error": "database_unavailable"},
                            )
                    # /api/summary may not project the damaged text column;
                    # regardless, it must provide a valid JSON HTTP response.
                    try:
                        with urlopen(self.base + "/api/summary", timeout=3) as response:
                            code, body = response.status, response.read()
                    except HTTPError as response:
                        code, body = response.code, response.read()
                    self.assertIn(code, (200, 503))
                    self.assertIsInstance(json.loads(body), dict)
                    self.assertNotIn(b"untrusted-synthetic-blob", body)
                finally:
                    self.change_text(field, original)
                with urlopen(self.base + "/api/ads", timeout=3) as response:
                    self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()