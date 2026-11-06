"""Actual local Dashboard HTTP for owner-price planning, no marketplace action."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from mark_api.dashboard import create_server, _DASHBOARD_HTML, _DASHBOARD_JS
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.storage import SnapshotStore


class OwnerPriceHttpTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="klaz-owner-price-http-")
        self.addCleanup(temp.cleanup)
        self.store = SnapshotStore(Path(temp.name) / "customer.sqlite")
        self.server = create_server(
            self.store, port=0, local_session_token="l" * 32,
        )
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.now = datetime.now(timezone.utc)
        self.seed("10001", source="kleinanzeigen-management")

    def seed(self, ad: str, *, source: str, state=LifecycleState.ACTIVE):
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id=ad, title="Lokale Testanzeige", description="Kein echter Kunde",
            source=source, lifecycle_state=state, observed_at=self.now,
        ))

    def request(self, method: str, path: str, value=None, *,
                authorized=True, origin=True, raw=None):
        port = self.server.server_address[1]
        headers = {}
        if authorized:
            headers.update({
                "X-Mark-Dashboard-Write": "1",
                "X-Mark-Dashboard-Token": "l" * 32,
            })
        if origin:
            headers["Origin"] = f"http://127.0.0.1:{port}"
        if value is not None or raw is not None:
            headers["Content-Type"] = "application/json"
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            payload = raw if raw is not None else (
                json.dumps(value).encode() if value is not None else None)
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, json.loads(data) if resp.getheader(
                "Content-Type", "").startswith("application/json") else data
        finally:
            conn.close()

    def intent(self, **changes):
        body = {
            "action": "set", "ad_id": "10001", "manual_price_eur": 70,
            "minimum_price_eur": 45, "reduction_step_eur": 10,
            "wait_days": 0, "expected_revision": 0, "request_key": "a" * 64,
        }
        body.update(changes)
        return body

    def test_customer_read_write_reload_and_delete_without_external_effect(self):
        detail = "/api/portfolio/owner-price-rules/detail?ad_id=10001"
        status, no_rule = self.request("GET", detail)
        self.assertEqual(status, 200, no_rule)
        self.assertTrue(no_rule["owner"]["confirmed"])
        self.assertIsNone(no_rule["rule"])
        code, saved = self.request(
            "POST", "/api/portfolio/owner-price-rules/mutate", self.intent())
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved["suggested_price_eur"], 60)
        self.assertEqual((saved["platform_writes"], saved["provider_requests"]), (0, 0))
        self.assertFalse(saved["platform_price_verified"])
        self.assertTrue(saved["requires_live_price_recheck"])
        self.assertEqual(self.request(
            "POST", "/api/portfolio/owner-price-rules/mutate",
            self.intent(),
        ), (200, saved))
        self.assertEqual(self.request("GET", detail)[1]["rule"]["manual_price_eur"], 70)
        status, page = self.request("GET", "/api/portfolio/owner-price-rules?limit=1")
        self.assertEqual(status, 200)
        self.assertEqual((page["total"], len(page["rules"])), (1, 1))
        self.assertEqual(page["rules"][0]["price_source"],
                         "manually_entered_not_platform_verified")
        self.assertIn('id="owner-price-ad-id"', _DASHBOARD_HTML)
        self.assertIn('id="owner-price-save"', _DASHBOARD_HTML)
        self.assertIn('loadOwnerPriceRule', _DASHBOARD_JS)
        deleted = {"action": "delete", "ad_id": "10001",
                   "expected_revision": 1, "request_key": "b" * 64}
        self.assertEqual(self.request(
            "POST", "/api/portfolio/owner-price-rules/mutate", deleted,
        )[1]["deleted"], True)
        self.assertEqual(self.request("GET", detail)[1]["rule"], None)
        self.assertEqual(self.request(
            "GET", "/api/portfolio/owner-price-rules")[1]["total"], 0)

    def test_local_session_required_strict_replay_body_and_unknown_identity(self):
        detail = "/api/portfolio/owner-price-rules/detail?ad_id=10001"
        self.assertEqual(self.request("GET", detail, authorized=False)[0], 403)
        route = "/api/portfolio/owner-price-rules/mutate"
        self.assertEqual(self.request(
            "POST", route, self.intent(), authorized=False)[0], 403)
        self.assertEqual(self.request(
            "POST", route, self.intent(), origin=False)[0], 403)
        self.assertEqual(self.request(
            "POST", route, self.intent(), raw=b'{"action":"set","action":"delete"}'
        )[0], 400)
        self.assertEqual(self.request(
            "POST", route, self.intent(), raw=b'not json'
        )[0], 400)
        self.assertEqual(self.request("POST", route, self.intent(
            ad_id="10002", request_key="b" * 64))[0], 409)
        self.seed("10002", source="kleinanzeigen-email")
        self.assertEqual(self.request("POST", route, self.intent(
            ad_id="10002", request_key="c" * 64))[0], 409)
        for body in (self.intent(manual_price_eur=True),
                     self.intent(minimum_price_eur=100),
                     self.intent(wait_days=3651),
                     self.intent(extra_field=0)):
            with self.subTest(body=body):
                self.assertIn(self.request("POST", route, body)[0], (400, 409))
        self.assertEqual(self.request("POST", route, self.intent())[0], 200)
        self.assertEqual(self.request(
            "POST", route, self.intent(reduction_step_eur=20))[0], 409)
        for query in (
            "?ad_id=invalid", "?sku=10001", "?ad_id=10001&ad_id=10001",
            "?ad_id=", "?ad_id=10001&limit=1",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.request(
                    "GET", "/api/portfolio/owner-price-rules/detail" + query)[0], 400)
        for query in ("?limit=51", "?after=bad", "?limit=1&limit=1", "?q=10001"):
            with self.subTest(query=query):
                self.assertEqual(self.request(
                    "GET", "/api/portfolio/owner-price-rules" + query)[0], 400)

    def test_paused_or_unverified_owner_keeps_rule_without_false_proposal(self):
        route = "/api/portfolio/owner-price-rules/mutate"
        self.assertEqual(self.request("POST", route, self.intent())[0], 200)
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id="10001", observed_at=self.now + timedelta(seconds=1),
            source="kleinanzeigen-management", lifecycle_state=LifecycleState.PAUSED,
        ))
        page = self.request("GET", "/api/portfolio/owner-price-rules")[1]
        self.assertEqual(page["rules"][0]["state"], "owner_unconfirmed")
        self.assertIsNone(page["rules"][0]["suggested_price_eur"])
        self.assertEqual(self.request("POST", route, self.intent(
            request_key="d" * 64, expected_revision=1))[0], 409)


if __name__ == "__main__":
    unittest.main()
