"""Synthetic 10,000-listing Chrome owner-read contracts; no live platform calls."""
from __future__ import annotations

import json
import re
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import patch

from mark_api.private_web_inventory_cdp import (
    CdpManagementInventoryReader, PrivateWebInventoryReadCancelled,
)
from mark_api.results import ReadStatus


class _PagedOwnerClient:
    """Emit 200 fixed-size management responses without any browser or network."""

    def __init__(
        self, *, pages: int = 200, page_size: int = 50,
        failed_page: int | None = None, drift_page: int | None = None,
        duplicate_page: int | None = None,
    ) -> None:
        self.pages = pages
        self.page_size = page_size
        self.failed_page = failed_page
        self.drift_page = drift_page
        self.duplicate_page = duplicate_page
        self.fetched: list[int] = []
        self.closed = False
        self._send_guard = None

    def bind_cancellable_reads(self, *, stop_requested, send_guard) -> None:
        self._send_guard = send_guard

    def call(self, method, params=None):
        guard = self._send_guard() if self._send_guard else nullcontext()
        with guard:
            return self._reply(method, params or {})

    def _reply(self, method, params):
        if method != "Runtime.evaluate":
            raise AssertionError("owner read cannot navigate or write")
        expression = params.get("expression", "")
        if not params.get("awaitPromise"):
            return {"result": {"value": {
                "readyState": "complete",
                "origin": "https://www.kleinanzeigen.de",
                "path": "/m-meine-anzeigen.html",
            }}}
        match = re.search(r"pageNum=([1-9][0-9]*)", expression)
        if not match or params.get("returnByValue") is not True:
            raise AssertionError("noncanonical owner page request")
        page = int(match.group(1))
        if page != len(self.fetched) + 1 or not 1 <= page <= self.pages:
            raise AssertionError("skipped, repeated or excess management page")
        self.fetched.append(page)
        if page == self.failed_page:
            return {"result": {"value": {"status": 503, "body": ""}}}
        offset = (page - 1) * self.page_size
        ids = [f"{1_000_000_000 + offset + i}" for i in range(self.page_size)]
        if page == self.duplicate_page:
            ids[0] = f"{1_000_000_000}"
        body = json.dumps({
            "ads": [{"id": ad_id, "title": "Gegenstand", "state": "active"} for ad_id in ids],
            "paging": {"last": self.pages + (page == self.drift_page)},
        })
        return {"result": {"value": {"status": 200, "body": body}}}

    def close(self):
        self.closed = True


class FullOwnerInventoryScaleTests(unittest.TestCase):
    """The last page must matter as much as the first, with no partial success."""

    @staticmethod
    def _reader(client: _PagedOwnerClient) -> CdpManagementInventoryReader:
        return CdpManagementInventoryReader(
            cdp_port=19610,
            timeout_seconds=0.2,
            preconnected_client=client,
            client_factory=lambda: client,
            clock=lambda: datetime(2026, 10, 29, tzinfo=timezone.utc),
        )

    def test_200_pages_of_50_yield_10000_distinct_owner_ads(self):
        client = _PagedOwnerClient()
        with patch("mark_api.private_web_inventory_cdp._require_private_web_cdp_authority"):
            result = self._reader(client).read_ads()
        self.assertEqual(result.status, ReadStatus.SUCCESS_NONEMPTY)
        self.assertEqual(len(result.value), 10_000)
        self.assertEqual(len(set(item.ad_id for item in result.value)), 10_000)
        self.assertEqual(result.value[0].ad_id, "1000000000")
        self.assertEqual(result.value[-1].ad_id, "1000009999")
        self.assertEqual(client.fetched, list(range(1, 201)))
        self.assertTrue(client.closed)

    def test_final_page_http_error_cannot_commit_199_pages_as_success(self):
        client = _PagedOwnerClient(failed_page=200)
        with patch("mark_api.private_web_inventory_cdp._require_private_web_cdp_authority"):
            result = self._reader(client).read_ads()
        self.assertEqual(result.status, ReadStatus.HTTP_ERROR)
        self.assertFalse(result.is_success)
        self.assertIsNone(result.value)
        self.assertEqual(client.fetched[-1], 200)
        self.assertTrue(client.closed)

    def test_final_page_duplicate_fails_whole_owner_round(self):
        client = _PagedOwnerClient(duplicate_page=200)
        with patch("mark_api.private_web_inventory_cdp._require_private_web_cdp_authority"):
            result = self._reader(client).read_ads()
        self.assertEqual(result.status, ReadStatus.PARSE_ERROR)
        self.assertEqual(result.error, "duplicate_owner_ids")
        self.assertIsNone(result.value)
        self.assertEqual(len(client.fetched), 200)
        self.assertTrue(client.closed)

    def test_final_page_count_drift_is_never_partial_success(self):
        client = _PagedOwnerClient(drift_page=200)
        with patch("mark_api.private_web_inventory_cdp._require_private_web_cdp_authority"):
            result = self._reader(client).read_ads()
        self.assertEqual(result.status, ReadStatus.PARSE_ERROR)
        self.assertEqual(result.error, "pagination_drift")
        self.assertIsNone(result.value)
        self.assertEqual(len(client.fetched), 200)
        self.assertTrue(client.closed)

    def test_stop_before_last_page_never_returns_9950_as_completed(self):
        client = _PagedOwnerClient()
        with patch("mark_api.private_web_inventory_cdp._require_private_web_cdp_authority"):
            with self.assertRaises(PrivateWebInventoryReadCancelled):
                self._reader(client).read_ads(
                    stop_requested=lambda: len(client.fetched) >= 199,
                )
        self.assertEqual(client.fetched, list(range(1, 200)))
        self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
