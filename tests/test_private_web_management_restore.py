"""Browser management restoration after verified editor post-read (synthetic only)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch

from mark_api.domain import AdSnapshot
from mark_api.private_web import PrivateWebEditorSnapshot, PrivateWebEditorState
from mark_api.private_web_cdp import (
    CdpPrivateWebOwnerReader, CdpPrivateWebPage, PrivateWebCdpError,
)
from mark_api.results import ReadResult, ReadStatus


class ManagementRestoreTests(TestCase):
    def _owner(self, browser):
        class Owner:
            def read_ads(self):
                if browser["page"] != "management":
                    return ReadResult.failure(
                        ReadStatus.TRANSPORT_ERROR, error="inventory_wrong_target"
                    )
                return ReadResult.success_nonempty((
                    AdSnapshot(
                        ad_id="12345",
                        observed_at=datetime(2026, 10, 24, tzinfo=timezone.utc),
                        source="synthetic-management",
                    ),
                ))
        return Owner()

    @staticmethod
    def _editor():
        return PrivateWebEditorSnapshot(
            state=PrivateWebEditorState.READY,
            ad_id="12345",
            title="Saved title",
            description="Saved description",
        )

    def test_owner_read_returns_to_management_before_followup_inventory(self):
        browser = {"page": "management", "restores": 0}
        class Page:
            def open_editor(self, ad_id):
                assert ad_id == "12345"
                browser["page"] = "editor"
            def read_editor(self):
                return ManagementRestoreTests._editor()
            def restore_management(self, ad_id):
                assert ad_id == "12345" and browser["page"] == "editor"
                browser["page"] = "management"
                browser["restores"] += 1
            def close(self):
                pass
        owner = self._owner(browser)
        result = CdpPrivateWebOwnerReader(
            owner_reader=owner, page_factory=Page, ad_id="12345",
        ).read_ads()
        self.assertTrue(result.is_success, result)
        self.assertEqual(browser, {"page": "management", "restores": 1})
        self.assertTrue(owner.read_ads().is_success)

    def test_owner_read_refuses_missing_restore_operation(self):
        browser = {"page": "management"}
        class Page:
            def open_editor(self, ad_id):
                browser["page"] = "editor"
            def read_editor(self):
                return ManagementRestoreTests._editor()
            def close(self):
                pass
        result = CdpPrivateWebOwnerReader(
            owner_reader=self._owner(browser), page_factory=Page, ad_id="12345",
        ).read_ads()
        self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)

    def test_owner_read_refuses_failed_restoration(self):
        browser = {"page": "management"}
        class Page:
            def open_editor(self, ad_id):
                browser["page"] = "editor"
            def read_editor(self):
                return ManagementRestoreTests._editor()
            def restore_management(self, ad_id):
                raise RuntimeError("synthetic browser restoration failure")
            def close(self):
                pass
        result = CdpPrivateWebOwnerReader(
            owner_reader=self._owner(browser), page_factory=Page, ad_id="12345",
        ).read_ads()
        self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual(result.error, "private_web_owner_read_failed")

    def test_native_page_restores_exact_verified_management_target(self):
        class Client:
            def __init__(self):
                self.path = "/p-anzeige-bearbeiten.html"
                self.calls = []
            def call(self, method, params=None):
                self.calls.append((method, params))
                if method == "Page.navigate":
                    assert params == {
                        "url": "https://www.kleinanzeigen.de/m-meine-anzeigen.html"
                    }
                    self.path = "/m-meine-anzeigen.html"
                    return {"frameId": "synthetic"}
                assert method == "Runtime.evaluate"
                expr = params["expression"]
                if "return globalThis[key] === true" in expr:
                    return {"result": {"value": True}}
                if "oldDocument" in expr:
                    return {"result": {"value": {
                        "readyState": "complete",
                        "origin": "https://www.kleinanzeigen.de",
                        "path": self.path,
                        "oldDocument": False,
                    }}}
                return {"result": {"value": {
                    "readyState": "complete",
                    "origin": "https://www.kleinanzeigen.de",
                    "path": self.path,
                    "adId": "12345",
                }}}
            def close(self):
                pass

        client = Client()
        with patch(
            "mark_api.private_web_cdp._require_private_web_cdp_authority",
            return_value=None,
        ):
            page = CdpPrivateWebPage(
                "http://127.0.0.1:9222", client_factory=lambda: client,
            )
            page._bound_ad_id = "12345"
            page.restore_management("12345")
        self.assertEqual(client.path, "/m-meine-anzeigen.html")
        self.assertEqual(sum(m == "Page.navigate" for m, _ in client.calls), 1)

    def test_native_page_rejects_wrong_editor_target_without_navigation(self):
        class Client:
            def __init__(self):
                self.calls = []
            def call(self, method, params=None):
                self.calls.append(method)
                raise AssertionError("browser must not be contacted")
            def close(self):
                pass

        client = Client()
        with patch(
            "mark_api.private_web_cdp._require_private_web_cdp_authority",
            return_value=None,
        ):
            page = CdpPrivateWebPage(
                "http://127.0.0.1:9222", client_factory=lambda: client,
            )
            page._bound_ad_id = "12345"
            with self.assertRaises(PrivateWebCdpError):
                page.restore_management("54321")
        self.assertEqual(client.calls, [])


    def test_native_restore_polls_transient_context_loss_without_second_navigation(self):
        class Client:
            def __init__(self):
                self.page = "/p-anzeige-bearbeiten.html"
                self.navigations = 0
                self.polls = 0

            def call(self, method, params=None):
                if method == "Page.navigate":
                    self.navigations += 1
                    self.page = "/m-meine-anzeigen.html"
                    return {"frameId": "synthetic"}
                assert method == "Runtime.evaluate"
                expr = params["expression"]
                if "return globalThis[key] === true" in expr:
                    return {"result": {"value": True}}
                if "oldDocument" in expr:
                    self.polls += 1
                    if self.polls == 1:
                        # Chrome may discard the old document during navigate.
                        # This is a *read* failure, not permission to navigate
                        # or submit a second time.
                        return {"exceptionDetails": {"text": "context vanished"}}
                    return {"result": {"value": {
                        "readyState": "complete",
                        "origin": "https://www.kleinanzeigen.de",
                        "path": self.page,
                        "oldDocument": False,
                    }}}
                return {"result": {"value": {
                    "readyState": "complete",
                    "origin": "https://www.kleinanzeigen.de",
                    "path": self.page,
                    "adId": "12345",
                }}}

            def close(self):
                pass

        client = Client()
        with patch(
            "mark_api.private_web_cdp._require_private_web_cdp_authority",
            return_value=None,
        ):
            page = CdpPrivateWebPage(
                "http://127.0.0.1:9222", client_factory=lambda: client,
                sleep=lambda _: None,
            )
            page._bound_ad_id = "12345"
            page.restore_management("12345")
        self.assertEqual(client.navigations, 1)
        self.assertEqual(client.polls, 2)
        self.assertEqual(client.page, "/m-meine-anzeigen.html")
