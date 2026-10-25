"""Synthetic CDP transport invariants for the customer product.

These tests never contact a browser or Kleinanzeigen. Real CDP access requires
a validated loopback endpoint and an already authenticated browser session.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from mark_api.private_web_cdp import (
    _LoopbackCdpClient, CdpCookieProvider, CdpPrivateWebPage,
    _validated_loopback_endpoint,
)
from mark_api.private_web_cdp_media import CdpPrivateWebMediaPage
from mark_api.adapters.management import MANAGEMENT_URL, UrllibHttpTransport, TransportFailure


class SyntheticClient:
    def __init__(self) -> None:
        self.calls = []
        self.closed = False

    def call(self, method, params=None):
        self.calls.append((method, params))
        return {"result": {"value": True}}

    def close(self) -> None:
        self.closed = True


class DirectTransportAdmissionTests(unittest.TestCase):
    def test_only_exact_loopback_cdp_endpoint_is_admitted_before_network(self) -> None:
        forbidden = (
            "http://0.0.0.0:9222", "http://localhost:9222",
            "https://127.0.0.1:9222", "http://127.0.0.1:9222/other",
            "http://127.0.0.1:9222?x=1", "http://user:pw@127.0.0.1:9222",
            "http://127.0.0.1:0",
        )
        with patch.object(_LoopbackCdpClient, "_connect", side_effect=AssertionError("network must not begin")) as network:
            for value in forbidden:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    _LoopbackCdpClient(value, timeout_seconds=1.0)
            network.assert_not_called()
        self.assertEqual(_validated_loopback_endpoint("http://127.0.0.1:9222"), ("http://127.0.0.1:9222", 9222))

    def test_synthetic_factories_are_usable_without_network_or_environment_grants(self) -> None:
        for page_cls in (CdpCookieProvider, CdpPrivateWebPage, CdpPrivateWebMediaPage):
            with self.subTest(factory=page_cls.__name__):
                fake = SyntheticClient()
                page = page_cls.from_port(9222, client_factory=lambda: fake)
                with patch.dict(os.environ, {"MARK_PRIVATE_WEB_APPROVED": "false"}):
                    self.assertIs(page._client(), fake)
                    self.assertIs(page._client(), fake)
                page.close()
                self.assertTrue(fake.closed)

    def test_factory_truthiness_is_not_evaluated(self) -> None:
        for page_cls in (CdpCookieProvider, CdpPrivateWebPage, CdpPrivateWebMediaPage):
            with self.subTest(factory=page_cls.__name__):
                calls = []
                fake = SyntheticClient()

                class Trap:
                    def __bool__(self):
                        raise AssertionError("untrusted factory truthiness evaluated")

                    def __call__(self):
                        calls.append("created")
                        return fake

                page = page_cls.from_port(9222, client_factory=Trap())
                self.assertEqual(calls, [])
                self.assertIs(page._client(), fake)
                self.assertEqual(calls, ["created"])
                page.close()

    def test_explicit_synthetic_cdp_effects_follow_existing_method_contract(self) -> None:
        fake = SyntheticClient()
        self.assertTrue(CdpPrivateWebPage._runtime_value(fake, "true"))
        CdpPrivateWebPage._dispatch_browser_click(fake, x=10.0, y=15.0)
        self.assertEqual(
            [method for method, _ in fake.calls],
            ["Runtime.evaluate", "Input.dispatchMouseEvent", "Input.dispatchMouseEvent"],
        )
        self.assertEqual(
            [params["type"] for method, params in fake.calls if method == "Input.dispatchMouseEvent"],
            ["mousePressed", "mouseReleased"],
        )

    def test_synthetic_media_handle_is_released_with_best_effort_cleanup(self) -> None:
        fake = SyntheticClient()
        page = CdpPrivateWebMediaPage.from_port(9222)
        page._media_file_object_id = "synthetic-handle"
        page._client_instance = fake
        page._release_media_handle()
        self.assertIsNone(page._media_file_object_id)
        self.assertEqual(fake.calls, [
            ("Runtime.releaseObject", {"objectId": "synthetic-handle"})
        ])
        page.close()

    def test_legacy_private_management_http_remains_denied_before_network(self) -> None:
        with (
            patch("mark_api.adapters.management.urlopen",
                  side_effect=AssertionError("direct HTTP must not begin")) as network,
            patch.dict(os.environ, {
                "MARK_PRIVATE_WEB_APPROVED": "true",
                "MARK_PLATFORM_WRITES_ENABLED": "true",
            }),
        ):
            with self.assertRaises(TransportFailure):
                UrllibHttpTransport().get(
                    MANAGEMENT_URL, headers={"Cookie": "synthetic-only"})
            network.assert_not_called()
