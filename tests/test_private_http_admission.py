from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from mark_api.adapters.management import MANAGEMENT_URL, TransportFailure
from mark_api.private_web_runtime import _NoRedirectManagementTransport
from mark_api.private_web_media_verify import _default_fetch


class DirectPrivateHttpAdmissionTests(unittest.TestCase):
    def test_internal_owner_transport_denied_before_open(self):
        transport = _NoRedirectManagementTransport(timeout_seconds=0.5)
        with (
            patch.object(transport, "_open",
                         side_effect=AssertionError("unexpected owner HTTP")) as network,
            patch.dict(os.environ, {
                "MARK_PRIVATE_WEB_APPROVED": "true",
                "MARK_PLATFORM_WRITES_ENABLED": "true",
            }),
        ):
            with self.assertRaisesRegex(TransportFailure, "not authorized"):
                transport.get(MANAGEMENT_URL, headers={"Cookie": "synthetic"})
            network.assert_not_called()

    def test_default_public_listing_and_media_fetch_denied_before_opener(self):
        cases = ("https://www.kleinanzeigen.de/s-anzeige/1234567890",
                 "https://img.kleinanzeigen.de/api/v1/prod-ads/images/test.jpg")
        with (
            patch("mark_api.private_web_media_verify.build_opener",
                  side_effect=AssertionError("unexpected public HTTP")) as network,
            patch.dict(os.environ, {
                "MARK_PRIVATE_WEB_APPROVED": "true",
                "MARK_PROSELLERS_API_ENTITLEMENT_CONFIRMED": "true",
            }),
        ):
            for url in cases:
                with self.subTest(url=url):
                    with self.assertRaisesRegex(OSError, "not authorized"):
                        _default_fetch(url, 1.0, 2048)
            network.assert_not_called()
