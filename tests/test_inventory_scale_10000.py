"""Native Windows 10k owner inventory: all synthetic, no account or network I/O."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mark_api.adapters.management import (
    HttpResponse, MAX_PAGES, MAX_INVENTORY_ITEMS, ManagementReadAdapter,
)
from mark_api.private_web_cdp import _MANAGEMENT_MAX_PAGES
from mark_api.private_web_inventory_cdp import CdpManagementInventoryReader
from mark_api.results import ReadResult, ReadStatus
from mark_api.storage import SnapshotStore


NOW = datetime(2026, 10, 27, 6, tzinfo=timezone.utc)


def make_pages(pages=200, per_page=50):
    """Predictable unique synthetic IDs, no real Kleinanzeigen requests."""
    result = []
    for page in range(pages):
        first = 1_000_000_000 + page * per_page
        ads = [
            {"id": str(first + offset), "title": f"Artikel {first + offset}",
             "state": "active", "viewCount": 3}
            for offset in range(per_page)
        ]
        result.append({"ads": ads, "paging": {"last": pages}})
    return result


class FakeCdp:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []
        self.closed = False

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method != "Runtime.evaluate":
            raise AssertionError("inventory probe must not write or navigate")
        if not params.get("awaitPromise"):
            return {"result": {"value": {
                "readyState": "complete",
                "origin": "https://www.kleinanzeigen.de",
                "path": "/m-meine-anzeigen.html",
            }}}
        if not self.pages:
            raise AssertionError("unasked page read")
        payload = self.pages.pop(0)
        return {"result": {"value": {
            "status": 200,
            "body": json.dumps(payload),
        }}}

    def close(self):
        self.closed = True


class FakeLegacyTransport:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, *, headers):
        self.calls.append((url, headers))
        if not self.pages:
            raise AssertionError("unexpected page")
        return HttpResponse(status_code=200, body=json.dumps(self.pages.pop(0)).encode())


class InventoryScaleTests(unittest.TestCase):
    def cdp(self, pages):
        fake = FakeCdp(pages)
        reader = CdpManagementInventoryReader(
            cdp_port=19610, timeout_seconds=0.2,
            client_factory=lambda: fake,
            clock=lambda: NOW,
        )
        with patch("mark_api.private_web_inventory_cdp._require_private_web_cdp_authority"):
            result = reader.read_ads()
        return fake, result

    def legacy(self, pages):
        fake = FakeLegacyTransport(pages)
        reader = ManagementReadAdapter(
            cookie_provider=lambda: "synthetic",
            transport=fake,
            endpoint="https://example.invalid/owner",
            clock=lambda: NOW,
        )
        with patch("mark_api.adapters._legacy_admission.require_legacy_platform_authority"):
            result = reader.read_ads()
        return fake, result

    def test_exact_200_pages_10000_ads_windows_and_sqlite(self):
        self.assertEqual(MAX_PAGES, _MANAGEMENT_MAX_PAGES)
        self.assertGreaterEqual(MAX_PAGES, 200)
        self.assertGreaterEqual(MAX_INVENTORY_ITEMS, 10_000)
        fake, read = self.cdp(make_pages())
        self.assertEqual(read.status, ReadStatus.SUCCESS_NONEMPTY)
        self.assertTrue(fake.closed)
        self.assertEqual(len(read.value), 10_000)
        self.assertEqual(read.value[0].ad_id, "1000000000")
        self.assertEqual(read.value[-1].ad_id, "1000009999")
        gets = [
            params for method, params in fake.calls
            if params and params.get("awaitPromise")
        ]
        self.assertEqual(len(gets), 200)
        self.assertIn("pageNum=200", gets[-1]["expression"])
        for request in gets:
            self.assertEqual(request["returnByValue"], True)
            self.assertIn('credentials: "same-origin"', request["expression"])
            self.assertNotIn("Page.navigate", request["expression"])
        self.assertFalse(any(method == "Page.navigate" for method, _ in fake.calls))
        with TemporaryDirectory() as temp:
            store = SnapshotStore(Path(temp) / "customers.sqlite",create_if_missing=True)
            written = store.append_inventory_result(
                read, tracked_ad_ids=(), observed_at=NOW,
                source="private-web-product-launcher",
            )
            self.assertEqual(written, 10_000)
            self.assertEqual(len(store.tracked_ad_ids()), 10_000)
            self.assertEqual(store.ad_history("1000009999")[-1].title, "Artikel 1000009999")

    def test_legacy_200_pages_are_complete_and_readonly(self):
        fake, read = self.legacy(make_pages())
        self.assertEqual(read.status, ReadStatus.SUCCESS_NONEMPTY)
        self.assertEqual(len(read.value), 10_000)
        self.assertEqual(len(fake.calls), 200)
        self.assertTrue(fake.calls[-1][0].endswith("pageNum=200"))
        self.assertTrue(all(call[0].startswith("https://example.invalid/owner?")
                            for call in fake.calls))

    def test_declared_paging_over_hard_limit_fails_without_partial_success(self):
        bad = {"ads": [{"id": "1000000000"}],
               "paging": {"last": MAX_PAGES + 1}}
        for get in (self.cdp, self.legacy):
            with self.subTest(reader=get.__name__):
                fake, result = get([bad])
                self.assertEqual(result.status, ReadStatus.PARSE_ERROR)
                self.assertIsNone(result.value)
                if isinstance(fake, FakeCdp):
                    self.assertTrue(fake.closed)
                    self.assertEqual(
                        len([1 for _, params in fake.calls if params.get("awaitPromise")]), 1,
                    )
                else:
                    self.assertEqual(len(fake.calls), 1)

    def test_12001_items_never_return_partial_inventory(self):
        # 241 pages x 50 == 12,050; must stop before falsely declaring 12k.
        overflow = make_pages(pages=241, per_page=50)
        for get in (self.cdp, self.legacy):
            with self.subTest(reader=get.__name__):
                fake, result = get(overflow)
                self.assertEqual(result.status, ReadStatus.PARSE_ERROR)
                self.assertIsNone(result.value)
                self.assertLessEqual(
                    len(fake.calls) if isinstance(fake, FakeLegacyTransport) else
                    len([1 for _, params in fake.calls if params.get("awaitPromise")]),
                    241,
                )

    def test_cross_page_duplicate_owner_ad_fails_closed(self):
        duplicate = make_pages(pages=2, per_page=1)
        duplicate[1]["ads"][0]["id"] = duplicate[0]["ads"][0]["id"]
        for get in (self.cdp, self.legacy):
            with self.subTest(reader=get.__name__):
                _, result = get(duplicate)
                self.assertEqual(result.status, ReadStatus.PARSE_ERROR)
                self.assertIsNone(result.value)

    def test_pagination_drift_keeps_existing_database_unchanged(self):
        pages = make_pages(pages=2, per_page=1)
        pages[1]["paging"]["last"] = 3
        _, result = self.cdp(pages)
        self.assertEqual(result.status, ReadStatus.PARSE_ERROR)
        with TemporaryDirectory() as temp:
            store = SnapshotStore(Path(temp) / "customers.sqlite",create_if_missing=True)
            committed = store.append_inventory_result(
                result, tracked_ad_ids=(), observed_at=NOW,
                source="private-web-product-launcher",
            )
            self.assertEqual(committed, 0)
            self.assertEqual(store.tracked_ad_ids(), ())


if __name__ == "__main__":
    unittest.main()
