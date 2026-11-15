"""CDP product transports are not the deprecated private HTTP adapters."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from mark_api.adapters._legacy_admission import LegacyPlatformAdmissionDenied
from mark_api.private_web_runtime import (
    _NoRedirectManagementTransport,
    build_private_web_inventory_runtime,
    build_private_web_content_runtime,
    build_private_web_media_create_runtime,
    build_private_web_write_api_runtime,
)


class CdpLegacyBoundaryTests(unittest.TestCase):
    def test_new_cdp_inventory_factory_is_available_without_legacy_permission(self):
        with patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"):
            reader = build_private_web_inventory_runtime(
                cdp_port=19679, timeout_seconds=1.0,
            )
        reader.close()

    def test_new_cdp_content_factory_is_available_without_legacy_permission(self):
        with patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"):
            runtime = build_private_web_content_runtime(
                cdp_port=19679, timeout_seconds=1.0,
            )
        runtime.close()

    def test_new_cdp_media_factory_is_available_without_legacy_permission(self):
        with patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"):
            runtime = build_private_web_media_create_runtime(
                cdp_port=19679, timeout_seconds=1.0,
            )
        runtime.close()

    def test_new_write_runtime_validates_technical_access_not_legacy_permission(self):
        with self.assertRaisesRegex(TypeError, "access must be WriteApiAccess"):
            build_private_web_write_api_runtime(cdp_port=19679, store=None, access=None)

    def test_legacy_http_transport_is_still_denied_before_any_network_use(self):
        with self.assertRaisesRegex(Exception, "legacy Kleinanzeigen platform automation"):
            _NoRedirectManagementTransport().get(
                "https://www.kleinanzeigen.de", headers={},
            )


if __name__ == "__main__":
    unittest.main()
