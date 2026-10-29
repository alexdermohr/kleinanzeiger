"""Shipped G4/G5 HTTP: real SQLite, local capabilities, strict requests, no marketplace effects."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from mark_api.dashboard import create_server, _DASHBOARD_HTML, _DASHBOARD_JS
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.portfolio_drafts import import_drafts, preview_import
from mark_api.portfolio_price_rules import price_rule_detail
from mark_api.results import ReadResult, ReadStatus
from mark_api.storage import SnapshotStore


class PortfolioInsightsHttpTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix="kleinanzeiger-insights-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = SnapshotStore(self.root / "customer.sqlite")
        self.server = create_server(self.store, port=0, local_session_token="l" * 32)
        worker = Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.start = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)
        path = self.root / "articles.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["sku", "title", "description", "category", "price_eur"])
            for sku in ("SKU-1", "SKU-2"):
                writer.writerow([sku, "Lampe", "Lokaler Artikel", "Haus > Leuchten", 50])
        preview = preview_import(self.store, path)
        import_drafts(self.store, path, preview["sha256"])

    def request(self, method, path, data=None, *, token=True, origin=True, headers=None, raw=None):
        port = self.server.server_address[1]
        head = {}
        if token:
            head.update({"X-Mark-Dashboard-Write": "1", "X-Mark-Dashboard-Token": "l" * 32})
        if origin:
            head["Origin"] = f"http://127.0.0.1:{port}"
        if data is not None or raw is not None:
            head["Content-Type"] = "application/json"
        head.update(headers or {})
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = raw if raw is not None else (json.dumps(data) if data is not None else None)
            connection.request(method, path, body=body, headers=head)
            response = connection.getresponse()
            value = response.read()
            return response.status, (json.loads(value) if response.getheader("Content-Type", "").startswith("application/json") else value.decode("utf-8", errors="replace"))
        finally:
            connection.close()

    def rule(self, **changes):
        data = {"action": "set", "sku": "SKU-1", "minimum_price_eur": 40,
                "reduction_step_eur": 20, "wait_days": 0,
                "expected_revision": 0, "request_key": "a" * 64}
        data.update(changes)
        return data

    def round(self, minute, source, items, *, failed=False, tracked=()):
        start = self.start + timedelta(minutes=minute)
        attempt = self.store.begin_sync_attempt(source=source, started_at=start)
        records = tuple(AdSnapshot(ad_id=ad_id, title=title,
                       observed_at=start + timedelta(seconds=2),
                       lifecycle_state=LifecycleState.ACTIVE,
                       source="kleinanzeigen-management") for ad_id, title in items)
        result = (ReadResult.failure(ReadStatus.TRANSPORT_ERROR) if failed else
                  (ReadResult.success_nonempty(records) if records else ReadResult.success_empty(())))
        self.store.append_inventory_result(result, source=source, attempt_id=attempt,
                                          observed_at=start + timedelta(seconds=2),
                                          completed_at=start + timedelta(seconds=5), tracked_ad_ids=tracked)

    def test_g4_compares_actual_launcher_and_explicit_retry_not_only_cli_rounds(self):
        self.round(0, "private-web-product-launcher", [("10001", "Alt"), ("10002", "Bleibt")])
        self.round(10, "private-web-periodic-readonly", [("10001", "Neu"), ("10002", "Bleibt"), ("10003", "Dazu")])
        code, result = self.request("GET", "/api/portfolio/inventory-diff")
        self.assertEqual(code, 200, result)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["source"], "private-web-periodic-readonly")
        self.assertEqual(result["previous_source"], "private-web-product-launcher")
        self.assertEqual(result["platform_writes"], 0)
        rows = {item["ad_id"]: item for item in result["items"]}
        self.assertEqual(rows["10001"]["state"], "changed")
        self.assertEqual(rows["10001"]["previous"]["title"], "Alt")
        self.assertEqual(rows["10001"]["current"]["title"], "Neu")
        self.assertEqual(rows["10002"]["state"], "unchanged")
        self.assertEqual(rows["10003"]["state"], "newly_observed")
        self.assertIsNotNone(result["previous_confirmed_at"])
        self.assertIsNotNone(result["confirmed_at"])

    def test_g4_failed_latest_read_is_not_a_confirmed_empty_or_deletion(self):
        self.round(0, "private-web-product-launcher", [("10001", "Belegt")])
        self.round(10, "private-web-periodic-readonly", [], failed=True)
        result = self.request("GET", "/api/portfolio/inventory-diff")[1]
        self.assertEqual(result["state"], "unverified")
        self.assertEqual(result["items"][0]["state"], "unverified")
        self.assertIsNone(result["confirmed_at"])
        self.assertIsNotNone(result["last_confirmed_at"])
        self.assertEqual(len(self.store.ad_history("10001")), 1)
        self.round(20, "private-web-periodic-readonly", [], tracked=("10001",))
        result = self.request("GET", "/api/portfolio/inventory-diff")[1]
        self.assertEqual(result["items"][0]["current"]["state"], "absent")
        self.assertEqual(result["items"][0]["state"], "unclear")
        self.assertIsNone(result["items"][0]["local_draft_match"])

    def test_g4_page_drift_requires_restart_and_never_blends_generations(self):
        self.round(0, "private-web-product-launcher", [("10001", "A"), ("10002", "B")])
        first = self.request("GET", "/api/portfolio/inventory-diff?limit=1")[1]
        query = f"/api/portfolio/inventory-diff?limit=1&after=10001&epoch={first['epoch']}"
        self.assertEqual(self.request("GET", query)[1]["items"][0]["ad_id"], "10002")
        self.assertEqual(self.request("GET", "/api/portfolio/inventory-diff?after=10001")[0], 400)
        self.round(10, "private-web-periodic-readonly", [], failed=True)
        self.assertEqual(self.request("GET", query)[0], 409)

    def test_g4_global_state_filter_finds_changes_beyond_first_page_and_pins_filter(self):
        original = [(f"{i:05d}", f"Titel {i}") for i in range(1, 121)]
        revised = [(number, f"Geändert {number}" if number in {"00030", "00120"} else title)
                   for number, title in original]
        self.round(0, "private-web-product-launcher", original)
        self.round(10, "private-web-periodic-readonly", revised)
        code, page = self.request("GET", "/api/portfolio/inventory-diff?state=changed&limit=1")
        self.assertEqual(code, 200, page)
        self.assertEqual(page["state_filter"], "changed")
        self.assertTrue(page["filter_is_global"])
        self.assertIsNone(page["total"])
        self.assertEqual(page["total_unfiltered"], 120)
        self.assertEqual([item["ad_id"] for item in page["items"]], ["00030"])
        self.assertTrue(page["has_more"])
        second = self.request("GET", f"/api/portfolio/inventory-diff?state=changed&limit=1&after=00030&epoch={page['epoch']}")[1]
        self.assertEqual([item["ad_id"] for item in second["items"]], ["00120"])
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_after"])
        # Changing the selection while retaining its old cursor cannot mix pages.
        self.assertEqual(self.request("GET", f"/api/portfolio/inventory-diff?state=unchanged&limit=1&after=00030&epoch={page['epoch']}")[0], 409)
        self.assertEqual(self.request("GET", "/api/portfolio/inventory-diff?state=changes")[0], 400)
        self.assertEqual(self.request("GET", "/api/portfolio/inventory-diff?state=changed&state=unchanged")[0], 400)
        self.round(20, "private-web-periodic-readonly", [], failed=True)
        _, unverified = self.request("GET", "/api/portfolio/inventory-diff?state=changed")
        self.assertEqual(unverified["state"], "unverified")
        self.assertEqual(unverified["items"], [])
        self.assertIsNone(unverified["confirmed_at"])

    def test_g4_filter_finds_a_single_change_at_ad_10000(self):
        originals = [(f"{i:05d}", "Unveraendert") for i in range(1, 10001)]
        current = originals[:-1] + [("10000", "Geaendert am Ende")]
        self.round(0, "private-web-product-launcher", originals)
        self.round(10, "private-web-periodic-readonly", current)
        status, result = self.request("GET", "/api/portfolio/inventory-diff?state=changed")
        self.assertEqual(status, 200, result)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["total_unfiltered"], 10000)
        self.assertEqual([item["ad_id"] for item in result["items"]], ["10000"])
        self.assertFalse(result["has_more"])
        self.assertEqual(result["platform_writes"], 0)

    def test_g5_rule_is_bedienbar_persistent_idempotent_and_never_changes_price(self):
        path = "/api/portfolio/price-rules/mutate"
        detail = self.request("GET", "/api/portfolio/price-rules/detail?sku=SKU-1")[1]
        self.assertEqual(detail["local_price_eur"], 50)
        self.assertIsNone(detail["rule"])
        code, saved = self.request("POST", path, self.rule())
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(saved["platform_writes"], 0)
        self.assertFalse(saved["published"])
        self.assertEqual(self.request("POST", path, self.rule())[1], saved)
        self.assertEqual(self.request("POST", path, self.rule(reduction_step_eur=5))[0], 409)
        self.assertEqual(self.request("POST", path, self.rule(request_key="b"*64))[0], 409)
        detail = price_rule_detail(SnapshotStore(self.store.path), sku="SKU-1")
        self.assertEqual(detail["rule"]["suggested_price_eur"], 40)
        self.assertEqual(detail["rule"]["local_price_eur"], 50)
        self.assertEqual(detail["rule"]["price_source"], "local_imported_draft")
        self.assertEqual(self.store.tracked_ad_ids(), ())
        with closing(self.store._connect()) as conn:
            self.assertEqual(conn.execute("SELECT price_eur FROM portfolio_drafts WHERE sku='SKU-1'").fetchone()[0], 50)
        deletion = {"action": "delete", "sku": "SKU-1", "expected_revision": 1, "request_key": "c"*64}
        self.assertEqual(self.request("POST", path, deletion)[0], 200)
        self.assertEqual(self.request("POST", path, deletion)[0], 200)
        self.assertEqual(self.request("GET", "/api/portfolio/price-rules")[1]["total"], 0)
        new = self.request("POST", path, self.rule(request_key="d"*64))[1]
        self.assertEqual(new["revision"], 3)
        self.assertEqual(self.request("POST", path, {**deletion, "request_key": "e"*64})[0], 409)

    def test_g5_wait_time_is_rule_time_not_invented_platform_price_time(self):
        self.assertEqual(self.request("POST", "/api/portfolio/price-rules/mutate", self.rule(wait_days=7))[0], 200)
        item = self.request("GET", "/api/portfolio/price-rules/detail?sku=SKU-1")[1]["rule"]
        self.assertEqual(item["state"], "waiting")
        self.assertIsNone(item["suggested_price_eur"])
        delta = datetime.fromisoformat(item["eligible_at"]) - datetime.fromisoformat(item["rule_updated_at"])
        self.assertEqual(delta, timedelta(days=7))
        self.assertEqual(item["price_source"], "local_imported_draft")

    def test_read_auth_and_mutation_origin_token_and_native_host_remain_required(self):
        for path in ("/api/portfolio/inventory-diff", "/api/portfolio/price-rules", "/api/portfolio/price-rules/detail?sku=SKU-1"):
            self.assertEqual(self.request("GET", path, token=False)[0], 403)
            self.assertEqual(self.request("GET", path, headers={"Host": "foreign.invalid"})[0], 421)
        path = "/api/portfolio/price-rules/mutate"
        for kwargs in ({"token": False}, {"origin": False}, {"headers": {"Origin": "http://evil.invalid"}},
                       {"headers": {"Authorization": "Bearer not-authority"}},
                       {"headers": {"Sec-Fetch-Site": "cross-site"}}, {"headers": {"Host": "foreign.invalid"}}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.request("POST", path, self.rule(), **kwargs)[0], 421 if "Host" in kwargs.get("headers", {}) else 403)
        self.assertEqual(self.request("GET", "/api/portfolio/price-rules")[1]["total"], 0)

    def test_query_body_and_method_negatives_are_nonmutating(self):
        for query in ("?limit=0", "?limit=51", "?limit=true", "?limit=2&limit=3", "?unknown=1", "?limit=%FF"):
            self.assertEqual(self.request("GET", "/api/portfolio/price-rules" + query)[0], 400)
        path = "/api/portfolio/price-rules/mutate"
        for payload in ([], {**self.rule(), "unexpected": True}, {**self.rule(), "action": "apply"}):
            self.assertEqual(self.request("POST", path, payload)[0], 400)
        self.assertEqual(self.request("POST", path, raw=b'{"action":"set","action":"delete"}')[0], 400)
        self.assertEqual(self.request("POST", path, self.rule(), headers={"Idempotency-Key": "a"*64})[0], 400)
        self.assertEqual(self.request("POST", path + "?x=1", self.rule())[0], 404)
        for mutation in ({"sku": "MISSING"}, {"minimum_price_eur": True}, {"minimum_price_eur": 51},
                         {"wait_days": -1}, {"reduction_step_eur": 0}, {"expected_revision": True}):
            self.assertEqual(self.request("POST", path, self.rule(**mutation))[0], 409)
        self.assertGreaterEqual(self.request("DELETE", path)[0], 400)
        self.assertEqual(self.request("GET", "/api/portfolio/price-rules")[1]["total"], 0)

    def test_database_failure_exposes_no_private_paths_and_pure_detail_does_not_create_rules(self):
        self.assertEqual(self.request("GET", "/api/portfolio/price-rules/detail?sku=SKU-1")[0], 200)
        with closing(self.store._connect()) as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='portfolio_price_rules'").fetchone())
        with patch("mark_api.dashboard.read_insight", side_effect=OSError("private customer path")):
            code, body = self.request("GET", "/api/portfolio/price-rules")
        self.assertEqual(code, 503)
        self.assertNotIn("private customer path", str(body))

    def test_shipped_navigation_and_article_action_are_connected(self):
        for name in ("inventory-diff-panel", "price-rules-panel", "nav-inventory-diff", "nav-price-rules",
                     "price-rule-original", "price-rule-min", "price-rule-step", "price-rule-wait", "price-rule-preview"):
            self.assertIn('id="' + name + '"', _DASHBOARD_HTML)
        self.assertIn('priceRuleButton.addEventListener("click", () => loadPriceRule(draft.sku))', _DASHBOARD_JS)
        self.assertIn('readInsight("/api/portfolio/inventory-diff?', _DASHBOARD_JS)
        self.assertIn('fetch("/api/portfolio/price-rules/mutate"', _DASHBOARD_JS)
        self.assertIn('renderPortfolioInsights();', _DASHBOARD_JS)


if __name__ == "__main__":
    unittest.main()