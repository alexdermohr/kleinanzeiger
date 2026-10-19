from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from mark_api.private_web_cdp import (
    _LoopbackCdpClient, CdpCookieProvider, CdpPrivateWebPage, PrivateWebCdpError,
)
from mark_api.private_web_cdp_media import CdpPrivateWebMediaPage
from mark_api.adapters.management import (
    MANAGEMENT_URL, UrllibHttpTransport, TransportFailure,
)


class DirectTransportAdmissionTests(unittest.TestCase):
    def test_real_cdp_client_blocked_before_discovery_or_socket(self):
        with patch.object(_LoopbackCdpClient, "_connect",
                          side_effect=AssertionError("CDP network must not begin")) as connect:
            with self.assertRaises(PrivateWebCdpError) as ctx:
                _LoopbackCdpClient("http://127.0.0.1:9222", timeout_seconds=1.0)
            self.assertEqual(ctx.exception.stage, "not_authorized")
            for cls in (CdpCookieProvider, CdpPrivateWebPage, CdpPrivateWebMediaPage):
                with self.subTest(factory=cls.__name__):
                    page=cls.from_port(9222)
                    with self.assertRaises(PrivateWebCdpError):
                        page._client()
            connect.assert_not_called()

    def test_default_private_management_http_blocked_before_urlopen(self):
        with (
            patch("mark_api.adapters.management.urlopen",
                  side_effect=AssertionError("HTTP network must not begin")) as network,
            patch.dict(os.environ, {
                "MARK_PRIVATE_WEB_APPROVED": "true",
                "MARK_PLATFORM_WRITES_ENABLED": "true",
                "MARK_PROSELLERS_API_ENTITLEMENT_CONFIRMED": "true",
            }),
        ):
            with self.assertRaisesRegex(TransportFailure, "not authorized"):
                UrllibHttpTransport().get(
                    MANAGEMENT_URL, headers={"Cookie": "synthetic-only"})
            network.assert_not_called()
