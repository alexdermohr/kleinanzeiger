from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import mark_api.private_web_runtime as runtime
from mark_api.write_api import WriteApiAccess, WriteCapability


class DirectPrivateWebFactoryAdmissionTests(unittest.TestCase):
    def test_unattested_factories_fail_before_dependency_browser_or_store(self):
        access = WriteApiAccess(
            principal="synthetic-only",
            bearer_token="synthetic-valid-token-123456",
            capabilities=frozenset(WriteCapability),
            writes_enabled=True,
        )
        builders = (
            ("inventory", runtime.build_private_web_inventory_runtime, {"cdp_port": 9222}),
            ("content", runtime.build_private_web_content_runtime, {"cdp_port": 9222}),
            ("media", runtime.build_private_web_media_create_runtime, {"cdp_port": 9222}),
            ("write_api", runtime.build_private_web_write_api_runtime, {
                "cdp_port": 9222, "store": object(), "access": access,
                "core_writes_enabled": True, "media_writes_enabled": True}),
        )
        with (
            patch.object(runtime, "require_private_web_runtime_dependency",
                         side_effect=AssertionError("dependency access before legal admission")) as dependency,
            patch.object(runtime, "_build_private_web_owner_reader",
                         side_effect=AssertionError("CDP access before legal admission")) as browser,
            patch.object(runtime, "acquire_write_api_store_lock",
                         side_effect=AssertionError("store lock before legal admission")) as lock,
            patch.dict(os.environ, {
                "MARK_PRIVATE_WEB_APPROVED": "true",
                "MARK_PLATFORM_WRITES_ENABLED": "true",
            }),
        ):
            for label, builder, options in builders:
                with self.subTest(builder=label):
                    with self.assertRaisesRegex(RuntimeError, "not authorized"):
                        builder(**options)
            dependency.assert_not_called()
            browser.assert_not_called()
            lock.assert_not_called()
