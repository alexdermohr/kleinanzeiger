"""P1 HTTP/UI acceptance: page-only local product drafts, no platform Writes."""
from __future__ import annotations

import csv
from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.parse import quote

from mark_api.dashboard import create_server, _DASHBOARD_HTML, _DASHBOARD_JS
from mark_api.portfolio_drafts import drafts_page, preview_import, import_drafts
from mark_api.storage import SnapshotStore


class PortfolioDraftHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = TemporaryDirectory(prefix="kleinanzeiger-p1-http-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = SnapshotStore(self.root / "kunden.sqlite")
        self.server = create_server(self.store, port=0)
        self.worker = Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, query: str) -> tuple[int, dict[str, object]]:
        conn = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=12)
        try:
            conn.request("GET", query)
            response = conn.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            conn.close()

    def fill(self, count: int) -> None:
        file = self.root / "batch.csv"
        with file.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("sku","title","description","category","price_eur"))
            for i in range(count):
                writer.writerow((f"SKU-{i:05d}", "Lampe " + str(i),
                                 "Fakten aus Artikelkatalog", "Haus > Licht", 22))
        preview = preview_import(self.store, file)
        import_drafts(self.store, file, preview["sha256"])

    def test_only_fifty_appear_and_platform_inventory_remains_unmodified(self) -> None:
        self.assertEqual(self.get("/api/portfolio/drafts")[1]["total"], 0)
        self.fill(1000)
        status, first = self.get("/api/portfolio/drafts?limit=50")
        self.assertEqual(status, 200)
        self.assertEqual(first["total"], 1000)
        self.assertEqual(len(first["drafts"]), 50)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["drafts"][0]["sku"], "SKU-00000")
        second_status, second = self.get(
            "/api/portfolio/drafts?limit=50&after=" + first["next_after"]
        )
        self.assertEqual(second_status, 200)
        self.assertEqual(second["drafts"][0]["sku"], "SKU-00050")
        self.assertEqual(self.get("/api/summary")[1]["tracked_ads"], 0)
        self.assertEqual(self.get("/api/catalog/ads")[1]["ads"], [])
        self.assertEqual(self.store.tracked_ad_ids(), ())
        self.assertEqual(drafts_page(self.store)["total"], 1000)

    def test_filter_and_invalid_input_responses(self) -> None:
        self.fill(200)
        self.assertEqual(self.get("/api/portfolio/drafts?q=SKU-00199")[1]["total"], 1)
        self.assertEqual(self.get("/api/portfolio/drafts?q=Lampe")[1]["total"], 200)
        for bad in ("?limit=0", "?limit=1000", "?limit=2&limit=3", "?what=1",
                    "?q=%FF", "?after=." * 50, "?q=" + quote("\n"),
                    "?q=" + ("a" * 121)):
            with self.subTest(bad=bad):
                code, response = self.get("/api/portfolio/drafts" + bad)
                self.assertEqual(code, 400)
                self.assertEqual(response, {"error": "invalid_draft_query"})

    def test_ui_has_no_new_implicit_platform_submit(self) -> None:
        for key in ('id="nav-drafts"', 'id="drafts-body"', 'id="draft-search"',
                    'id="draft-next"', 'id="draft-prev"'):
            self.assertIn(key, _DASHBOARD_HTML)
        self.assertIn('getJson("/api/portfolio/drafts?"', _DASHBOARD_JS)
        self.assertIn("create-category", _DASHBOARD_JS)
        self.assertIn("create-description", _DASHBOARD_JS)
        self.assertIn("create-media", _DASHBOARD_JS)
        self.assertIn("Es wird nichts veröffentlicht", _DASHBOARD_JS)


if __name__ == "__main__":
    unittest.main()
