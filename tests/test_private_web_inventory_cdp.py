"""Synthetic tests for the owner-only CDP inventory; no real browser/network."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from mark_api.private_web_inventory_cdp import CdpManagementInventoryReader
from mark_api.private_web_runtime import build_private_web_inventory_runtime
from mark_api.results import ReadResult, ReadStatus


class FakeClient:
    def __init__(self, pages, *, origin="https://www.kleinanzeigen.de", path="/m-meine-anzeigen.html"):
        self.pages = list(pages)
        self.origin, self.path = origin, path
        self.calls = []
        self.closed = False

    def call(self, method, params=None):
        params = dict(params or {})
        self.calls.append((method, params))
        if method == "Page.navigate":
            return {"frameId": "synthetic"}
        if method != "Runtime.evaluate":
            raise AssertionError(method)
        if params.get("awaitPromise"):
            if not self.pages:
                raise AssertionError("unexpected second management page read")
            payload = self.pages.pop(0)
            if isinstance(payload, Exception):
                raise payload
            return {"result": {"value": payload}}
        if len([m for m, _ in self.calls if m == "Runtime.evaluate" and not _.get("awaitPromise")]) == 1:
            return {"result": {"value": True}}
        return {"result": {"value": {
            "readyState": "complete",
            "origin": self.origin,
            "path": self.path,
            "oldDocument": False,
        }}}

    def close(self):
        self.closed = True


def payload(ads, *, last=1, status=200):
    return {"status": status, "body": json.dumps({"ads": ads, "paging": {"last": last}})}


class BrowserInventoryTests(unittest.TestCase):
    @staticmethod
    def reader(client, **kwargs):
        return CdpManagementInventoryReader(
            cdp_port=19610, timeout_seconds=0.2,
            clock=lambda: datetime(2026, 10, 19, tzinfo=timezone.utc),
            client_factory=lambda: client,
            sleep=lambda _: None,
            monotonic=lambda: 2.0,
            **kwargs,
        )

    def test_owner_inventory_uses_only_fixed_same_origin_get_without_cookie_export(self):
        client = FakeClient([
            payload([{"id": "1234567890", "title": "Vase", "state": "active", "viewCount": 5, "watchCount": 2}],last=2),
            payload([{"id": "1234567891", "title": "Tisch", "state": "paused", "viewCount": 9}],last=2),
        ])
        result = self.reader(client).read_ads()
        self.assertEqual(result.status, ReadStatus.SUCCESS_NONEMPTY)
        self.assertEqual([item.ad_id for item in result.value], ["1234567890", "1234567891"])
        self.assertEqual(result.value[0].views, 5)
        self.assertTrue(client.closed)
        navigations = [p for method, p in client.calls if method == "Page.navigate"]
        self.assertEqual(navigations, [{"url": "https://www.kleinanzeigen.de/m-meine-anzeigen.html"}])
        fetches = [p for method, p in client.calls if method == "Runtime.evaluate" and p.get("awaitPromise")]
        self.assertEqual(len(fetches), 2)
        for page, request in enumerate(fetches, start=1):
            self.assertTrue(request["returnByValue"])
            self.assertIn("pageNum=" + str(page), request["expression"])
            self.assertIn('credentials: "same-origin"', request["expression"])
            self.assertIn('mode: "same-origin"', request["expression"])
            self.assertIn('redirect: "error"', request["expression"])
            self.assertIn("location.origin", request["expression"])
            self.assertNotIn("Cookie", request["expression"])
            self.assertNotIn("POST", request["expression"])
            self.assertNotIn("DELETE", request["expression"])

    def test_successful_empty_is_not_transport_failure(self):
        client = FakeClient([payload([])])
        result = self.reader(client).read_ads()
        self.assertEqual(result.status, ReadStatus.SUCCESS_EMPTY)
        self.assertEqual(result.value, ())
        self.assertTrue(client.closed)

    def test_auth_and_http_errors_do_not_confirm_inventory(self):
        for code, expected in ((401, ReadStatus.UNAUTHENTICATED),
                               (403, ReadStatus.UNAUTHENTICATED),
                               (500, ReadStatus.HTTP_ERROR)):
            with self.subTest(status=code):
                client = FakeClient([{"status": code, "body": ""}])
                self.assertEqual(self.reader(client).read_ads().status, expected)
                self.assertTrue(client.closed)

    def test_invalid_json_and_duplicate_ids_fail_closed(self):
        for items, status in (
            ([{"status": 200, "body": "<html>login</html>"}], ReadStatus.PARSE_ERROR),
            ([payload([{"id": "1234567890"}, {"id": "1234567890"}])], ReadStatus.PARSE_ERROR),
            ([payload([{"id": True}])], ReadStatus.PARSE_ERROR),
            ([payload([{"id": "1"}], last=2), payload([{"id": "2"}], last=3)], ReadStatus.PARSE_ERROR),
            ([{"error": "wrong_origin_or_page"}], ReadStatus.TRANSPORT_ERROR),
            ([{"status": 200, "body": "x" * (2 * 1024 * 1024 + 1)}], ReadStatus.PARSE_ERROR),
        ):
            with self.subTest(payloads=str(items)[:75]):
                client = FakeClient(items)
                result = self.reader(client).read_ads()
                self.assertEqual(result.status, status)
                self.assertTrue(client.closed)

    def test_wrong_browser_origin_or_path_never_fetches(self):
        for origin, path in (("https://attacker.example", "/m-meine-anzeigen.html"),
                             ("https://www.kleinanzeigen.de", "/m-einloggen.html")):
            with self.subTest(origin=origin, path=path):
                client = FakeClient([payload([])], origin=origin, path=path)
                # Immediate synthetic deadline, no real browser or sleeping.
                state = iter([0.0, 1.0, 1.0])
                reader = CdpManagementInventoryReader(
                    cdp_port=19610, timeout_seconds=0.1,
                    client_factory=lambda: client,
                    monotonic=lambda: next(state, 1.0),
                    sleep=lambda _: None,
                )
                result = reader.read_ads()
                self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)
                self.assertTrue(client.closed)
                self.assertFalse(any(
                    method == "Runtime.evaluate" and args.get("awaitPromise")
                    for method, args in client.calls
                ))

    def test_reader_is_closed_and_rejects_nonloopback_or_invalid_ports(self):
        client = FakeClient([payload([])])
        reader = self.reader(client)
        reader.close()
        self.assertEqual(reader.read_ads().status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual(client.calls, [])
        for port in (0, -1, 65536, True, "19610"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                CdpManagementInventoryReader(cdp_port=port)

    def test_production_builder_composes_browser_reader_not_denied_legacy_http(self):
        client = FakeClient([payload([{"id": "1234567890", "state": "active"}])])
        with (
            patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"),
            patch("mark_api.private_web_inventory_cdp._LoopbackCdpClient", return_value=client) as cdp,
            patch("mark_api.private_web_runtime.ManagementReadAdapter",
                  side_effect=AssertionError("legacy manager must stay denied")) as legacy,
            patch("mark_api.private_web_runtime.CdpCookieProvider.from_port",
                  side_effect=AssertionError("cookies must not be exported")) as cookies,
            patch("mark_api.private_web_runtime._NoRedirectManagementTransport",
                  side_effect=AssertionError("direct HTTP must never start")) as http,
        ):
            runtime = build_private_web_inventory_runtime(cdp_port=19610)
            self.assertEqual(client.calls, [])
            result = runtime.read_inventory()
            self.assertEqual(result.status, ReadStatus.SUCCESS_NONEMPTY)
            self.assertEqual(result.value[0].ad_id, "1234567890")
            cdp.assert_called_once()
            legacy.assert_not_called()
            cookies.assert_not_called()
            http.assert_not_called()
            runtime.close()
            self.assertTrue(client.closed)


    def test_full_product_launcher_initial_sync_uses_default_browser_reader(self):
        # Unlike the older launcher tests, do not substitute its inventory
        # runtime factory. Exercise its real CDP read-only factory and SQLite.
        import tempfile
        from pathlib import Path
        from mark_api.launcher import build_product_launcher
        from mark_api.storage import SnapshotStore

        client = FakeClient([payload([{
            "id": "1234567890", "state": "active", "title": "Eigene Anzeige",
            "viewCount": 7, "watchCount": 1,
        }])])
        observed = {}

        class DisabledWriteRuntime:
            server_address = ("127.0.0.1", 18766)
            def start(self):
                return self.server_address
            def close(self):
                return None

        def write_factory(**kwargs):
            observed["write"] = kwargs
            return DisabledWriteRuntime()

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"),
            patch("mark_api.private_web_inventory_cdp._LoopbackCdpClient",
                  return_value=client),
            patch("mark_api.private_web_runtime.ManagementReadAdapter",
                  side_effect=AssertionError("legacy HTTP path must remain denied")),
            patch("mark_api.private_web_runtime._NoRedirectManagementTransport",
                  side_effect=AssertionError("direct management requests forbidden")),
        ):
            db = Path(tmp) / "state.sqlite"
            launcher = build_product_launcher(
                db_path=db, cdp_port=19610, initialize_db=True,
                dashboard_port=0, write_port=0,
                write_runtime_factory=write_factory,
                clock=lambda: datetime(2026, 10, 19, tzinfo=timezone.utc),
            )
            try:
                self.assertEqual(launcher.startup_inventory_count, 1)
                self.assertEqual(launcher.startup_persisted_count, 1)
                self.assertFalse(launcher.writes_enabled)
                self.assertNotIn("#write_token", launcher.dashboard_url)
                self.assertEqual(SnapshotStore(db).tracked_ad_ids(), ("1234567890",))
                sync = SnapshotStore(db).latest_sync_attempt()
                self.assertIsNotNone(sync)
                self.assertEqual(sync.outcome, "success_nonempty")
                self.assertFalse(observed["write"]["core_writes_enabled"])
                self.assertFalse(observed["write"]["media_writes_enabled"])
                self.assertEqual(observed["write"]["access"].capabilities, frozenset())
            finally:
                launcher.close()
            self.assertTrue(client.closed)

    def test_product_launcher_failed_browser_read_never_starts_write_server(self):
        import tempfile
        from pathlib import Path
        from mark_api.launcher import ProductLauncherError, build_product_launcher
        from mark_api.storage import SnapshotStore

        client = FakeClient([{"status": 403, "body": ""}])
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"),
            patch("mark_api.private_web_inventory_cdp._LoopbackCdpClient",
                  return_value=client),
            patch("mark_api.launcher.build_private_web_write_api_runtime",
                  side_effect=AssertionError("write runtime must never start")) as writer,
        ):
            db = Path(tmp) / "state.sqlite"
            with self.assertRaises(ProductLauncherError):
                build_product_launcher(
                    db_path=db, cdp_port=19610, initialize_db=True,
                    clock=lambda: datetime(2026, 10, 19, tzinfo=timezone.utc),
                )
            writer.assert_not_called()
            self.assertTrue(client.closed)
            self.assertEqual(SnapshotStore(db).tracked_ad_ids(), ())
            sync = SnapshotStore(db).latest_sync_attempt()
            self.assertIsNotNone(sync)
            self.assertEqual(sync.outcome, "failed")


if __name__ == "__main__":
    unittest.main()