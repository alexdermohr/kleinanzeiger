"""Admission regressions; no real browser, account or platform access."""
from __future__ import annotations

import os
import io
import unittest
from contextlib import ExitStack, redirect_stderr
from unittest.mock import patch

from mark_api.adapters._legacy_admission import LegacyPlatformAdmissionDenied
import mark_api.private_web_runtime as runtime
import mark_api.private_web_runtime_smoke as smoke
from mark_api.write_api import WriteApiAccess, WriteCapability


class PrivateWebRuntimeAdmissionTests(unittest.TestCase):
    def test_direct_builders_deny_before_setup_even_with_environment_flags(self):
        access = WriteApiAccess(
            principal="synthetic", bearer_token="synthetic-runtime-token",
            capabilities=frozenset(WriteCapability),
        )
        builders = (
            (runtime.build_private_web_inventory_runtime, {}),
            (runtime.build_private_web_content_runtime, {}),
            (runtime.build_private_web_media_create_runtime, {"cdp_session": object()}),
            (runtime.build_private_web_write_api_runtime, {
                "store": object(), "access": access,
                "core_writes_enabled": True, "media_writes_enabled": True,
            }),
        )
        with ExitStack() as stack:
            effects = [stack.enter_context(patch.object(
                runtime, name, side_effect=AssertionError("setup before admission"),
            )) for name in (
                "require_private_web_runtime_dependency", "_build_private_web_owner_reader",
                "CdpManagementInventoryReader", "build_private_web_content_runtime",
                "acquire_write_api_store_lock", "compose_private_web_write_api_runtime",
            )]
            stack.enter_context(patch.dict(os.environ, {
                "MARK_PRIVATE_WEB_APPROVED": "true",
                "MARK_PLATFORM_WRITES_ENABLED": "true",
            }))
            for builder, options in builders:
                with self.subTest(builder=builder.__name__):
                    with self.assertRaisesRegex(LegacyPlatformAdmissionDenied, "not authorized"):
                        builder(cdp_port=9222, **options)
            for effect in effects:
                effect.assert_not_called()

    def test_default_and_explicit_real_smoke_deny_before_factory_or_local_setup(self):
        real_factory = smoke.build_private_web_inventory_runtime
        # Patch the public name as well as checking the actual imported factory;
        # the omitted default must never retain an unguarded captured callable.
        with ExitStack() as stack:
            effects = [stack.enter_context(patch.object(
                smoke, name, side_effect=AssertionError("effect before admission"),
            )) for name in ("SnapshotStore", "create_server")]
            effects.append(stack.enter_context(patch.object(
                smoke.tempfile, "TemporaryDirectory",
                side_effect=AssertionError("temporary storage before admission"),
            )))
            for options in ({}, {"runtime_factory": real_factory}):
                with self.subTest(options=options):
                    with self.assertRaisesRegex(LegacyPlatformAdmissionDenied, "mark-api-offline"):
                        smoke.run_private_web_runtime_smoke(9222, **options)
            with patch.object(smoke, "build_private_web_inventory_runtime") as factory:
                for options in ({}, {"runtime_factory": factory}):
                    with self.assertRaises(LegacyPlatformAdmissionDenied):
                        smoke.run_private_web_runtime_smoke(9222, **options)
                factory.assert_not_called()
            for effect in effects:
                effect.assert_not_called()

    def test_smoke_cli_denies_default_before_setup(self):
        with patch.object(smoke, "build_private_web_inventory_runtime") as factory:
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                smoke.main(["--cdp-port", "9222"])
            self.assertEqual(result.exception.code, 2)
            factory.assert_not_called()
