"""Bounded customer catalog over existing SQLite and dashboard HTTP.

No platform, paid AI, or customer account is used. The 10k fixture is
synthetic, inserted into an isolated SQLite file in one transaction.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from http.client import HTTPConnection
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from urllib.parse import quote

from mark_api.dashboard import create_server
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.query import MarkQueryService
from mark_api.storage import SnapshotStore


T0 = datetime(2026, 10, 26, 9, tzinfo=timezone.utc)


class PortfolioCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="kleinanzeiger-catalog-")
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "ads.sqlite"
        self.store = SnapshotStore(self.db)
        self.query = MarkQueryService(self.store)

    def add(
        self, ad_id: str, title: str, state: LifecycleState = LifecycleState.ACTIVE,
        *, observed_at: datetime = T0, source: str = "owner",
        views: int | None = None,
    ) -> None:
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id=ad_id, title=title, lifecycle_state=state,
            observed_at=observed_at, source=source, views=views,
        ))

    def get(self, path: str) -> tuple[int, dict[str, object]]:
        conn = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=12)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            conn.close()

    def start_server(self) -> None:
        self.server = create_server(self.store, port=0)
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.server.shutdown)

    def test_indexed_cursor_and_strict_types(self) -> None:
        self.add("00003", "C")
        self.add("00001", "A")
        self.add("00002", "B")
        self.add("00001", "A neu", observed_at=T0 + timedelta(minutes=1))
        self.assertEqual(self.store.catalog_ad_ids_page(limit=2), ("00001", "00002"))
        self.assertEqual(
            self.store.catalog_ad_ids_page(after="00002", limit=5), ("00003",)
        )
        for kwargs in (
            {"limit": 0}, {"limit": True}, {"limit": 257},
            {"after": None}, {"after": "\n"}, {"after": "a"*161},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.catalog_ad_ids_page(**kwargs)

    def test_filtered_pages_preserve_existing_provenance_and_history_semantics(self) -> None:
        self.add("10001", "Lampe alt", views=9)
        self.add("10002", 'Łódź "Vase"', state=LifecycleState.PAUSED, views=5)
        self.add("10003", "Lampe neu", views=10)
        self.add("10001", "Lampe korrigiert", views=None,
                 observed_at=T0 + timedelta(minutes=1))
        self.add("10004", "Interner Katalog", source="local-catalog",
                 state=LifecycleState.UNKNOWN)
        page1 = self.query.catalog_page(limit=2)
        self.assertEqual([r["ad_id"] for r in page1["ads"]], ["10001", "10002"])
        self.assertTrue(page1["has_more"])
        self.assertEqual(page1["next_after"], "10002")
        page2 = self.query.catalog_page(limit=2, after=page1["next_after"])
        self.assertEqual([r["ad_id"] for r in page2["ads"]], ["10003", "10004"])
        self.assertFalse(page2["has_more"])
        # A new content snapshot does not silently erase the last known metric.
        self.assertEqual(page1["ads"][0]["title"], "Lampe korrigiert")
        self.assertEqual(page1["ads"][0]["views"], 9)
        self.assertTrue(page1["ads"][0]["metric_evidence"]["views"]["last_known"])
        self.assertEqual(
            [r["ad_id"] for r in self.query.catalog_page(search="łódź")["ads"]],
            ["10002"],
        )
        self.assertEqual(
            [r["ad_id"] for r in self.query.catalog_page(search="Lampe")["ads"]],
            ["10001", "10003"],
        )
        self.assertEqual(
            self.query.catalog_page(search="alt")["ads"], [],
            "filter must use current title, not old historical titles",
        )
        self.assertEqual(
            [r["ad_id"] for r in self.query.catalog_page(status="paused")["ads"]],
            ["10002"],
        )
        self.assertEqual(
            [r["ad_id"] for r in self.query.catalog_page(search="10004")["ads"]],
            ["10004"],
        )
        legacy = {r.ad_id: r for r in self.query.latest_ads()}
        for row in page1["ads"] + page2["ads"]:
            self.assertEqual(row["title"], legacy[row["ad_id"]].title)
            self.assertEqual(row["lifecycle_state"], legacy[row["ad_id"]].lifecycle_state.value)
        # Selection from this read-only route has no Writer object or effect.

    def test_http_validation_and_read_only_compatibility(self) -> None:
        self.add("88888", "Vase", views=3)
        self.add("88889", "Tisch", state=LifecycleState.PAUSED)
        self.start_server()
        status, payload = self.get("/api/catalog/ads?limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["ads"][0]["ad_id"], "88888")
        self.assertEqual(payload["next_after"], "88888")
        status, payload = self.get("/api/catalog/ads?limit=1&after=88888")
        self.assertEqual(status, 200)
        self.assertEqual(payload["ads"][0]["ad_id"], "88889")
        self.assertIsNone(payload["next_after"])
        status, payload = self.get("/api/catalog/ads?q=Vase&status=active")
        self.assertEqual(status, 200)
        self.assertEqual([r["ad_id"] for r in payload["ads"]], ["88888"])
        status, original = self.get("/api/ads")
        self.assertEqual(status, 200)
        self.assertEqual(len(original), 2)
        for bad in (
            "?limit=0", "?limit=101", "?limit=9999", "?limit=abc",
            "?limit=1&limit=2", "?evil=1", "?status=published",
            "?q="+quote("\n"), "?q="+("x"*121), "?q=%FF",
            "?after="+("x"*161),
        ):
            with self.subTest(bad=bad):
                status, response = self.get("/api/catalog/ads"+bad)
                self.assertEqual(status, 400)
                self.assertEqual(response, {"error": "invalid_catalog_query"})
        self.assertEqual(self.get("/api/catalog/ads?status=unknown")[0], 200)
        # Percent encoding can be much longer than the decoded valid search.
        self.assertEqual(
            self.get("/api/catalog/ads?q=" + quote("猫" * 60))[0], 200,
        )
        self.assertEqual(
            self.get("/api/catalog/ads?q=" + quote("😀" * 120))[0], 200,
        )

    def test_exact_ad_read_is_indexed_strict_and_observation_only(self) -> None:
        self.add("88888", "Erster Titel", views=6)
        self.add("88889", "Andere Anzeige", state=LifecycleState.PAUSED)
        self.add("88888", "Aktueller Titel", views=None,
                 observed_at=T0 + timedelta(minutes=2))
        result = self.query.catalog_ad("88888")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["title"], "Aktueller Titel")
        self.assertEqual(result["views"], 6)
        self.assertEqual(result["lifecycle_state"], "active")
        self.assertEqual(result["ad_id"], "88888")
        self.assertIsNone(self.query.catalog_ad("99999"))
        for bad in ("", " 88888", "88888x", "a" * 33, "\n", "８８８８８"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.query.catalog_ad(bad)

        self.start_server()
        status, payload = self.get("/api/catalog/ad?id=88888")
        self.assertEqual(status, 200)
        self.assertEqual(payload["ad"], result)
        self.assertEqual(self.get("/api/catalog/ad?id=99999"),
                         (404, {"error": "unknown_catalog_ad"}))
        for bad in ("", "?id=", "?id=a", "?id=88888&id=88889",
                    "?id=88888&evil=1", "?id=" + "1" * 33,
                    "?id=%FF", "?id=%0A", "?q=88888"):
            with self.subTest(bad=bad):
                self.assertEqual(
                    self.get("/api/catalog/ad" + bad),
                    (400, {"error": "invalid_catalog_ad"}),
                )

    def test_corrupt_native_key_fails_closed(self) -> None:
        self.add("10001", "Vase")
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO ad_snapshots "
                "(ad_id, observed_at, source, lifecycle_state, title) "
                "VALUES (?, ?, ?, ?, ?)",
                (sqlite3.Binary(b"bad-blob-key"), T0.isoformat(), "owner", "active", "bad"),
            )
        self.start_server()
        status, body = self.get("/api/catalog/ads")
        self.assertEqual(status, 503)
        self.assertEqual(body, {"error": "database_unavailable"})

    def test_ten_thousand_synthetic_ads_remain_server_paginated(self) -> None:
        # A large dataset in a *single* local SQL fixture transaction avoids
        # making 10,000 separate connection openings part of the test itself.
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executemany(
                """INSERT INTO ad_snapshots
                   (ad_id, observed_at, source, lifecycle_state, title, views)
                   VALUES (?, ?, 'owner', 'active', ?, ?)""",
                (
                    (str(1_000_000_000 + index), T0.isoformat(),
                     "Artikel " + str(index), index)
                    for index in range(10_000)
                ),
            )
        first = self.query.catalog_page(limit=50)
        self.assertEqual(len(first["ads"]), 50)
        self.assertEqual(first["ads"][0]["ad_id"], "1000000000")
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_after"], "1000000049")
        next_page = self.query.catalog_page(limit=50, after=first["next_after"])
        self.assertEqual(next_page["ads"][0]["ad_id"], "1000000050")
        self.assertEqual(len(next_page["ads"]), 50)
        near_end = self.query.catalog_page(limit=50, after="1000009950")
        self.assertEqual(len(near_end["ads"]), 49)
        self.assertFalse(near_end["has_more"])
        self.assertEqual(near_end["ads"][-1]["ad_id"], "1000009999")
        self.start_server()
        status, response = self.get("/api/catalog/ads?limit=50")
        self.assertEqual(status, 200)
        self.assertEqual(len(response["ads"]), 50)
        # The response is the selected page, never a full 10,000-ad JSON.
        self.assertLess(len(json.dumps(response)), 35_000)
        status, summary = self.get("/api/summary")
        self.assertEqual(status, 200)
        self.assertEqual(summary["tracked_ads"], 10_000)
        self.assertEqual(summary["current_ads"], 10_000)
        # Exact-ID preflight must locate an arbitrary tail record without
        # loading the first 100 search matches or 10,000 JSON objects.
        status, one = self.get("/api/catalog/ad?id=1000009999")
        self.assertEqual(status, 200)
        self.assertEqual(one["ad"]["ad_id"], "1000009999")
        self.assertEqual(one["ad"]["title"], "Artikel 9999")
        self.assertLess(len(json.dumps(one)), 2_000)


if __name__ == "__main__":
    unittest.main()