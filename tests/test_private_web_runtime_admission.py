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
    def test_product_cdp_builders_reach_technical_setup_not_legacy_gate(self):
        """Current product CDP paths must work; historical transports stay denied."""
        class TechnicalSetupReached(RuntimeError):
            pass

        access = WriteApiAccess(
            principal="synthetic", bearer_token="synthetic-runtime-token",
            capabilities=frozenset(WriteCapability),
        )
        # A factory test cannot touch the browser, DB, credentials or network.
        # The sentinel proves the boundary was reached and no legacy admission
        # was consulted, with or without unrelated environment flags.
        with (
            patch.object(
                runtime._legacy_admission, "require_legacy_platform_authority",
                side_effect=AssertionError("product CDP consulted legacy gate"),
            ) as obsolete_gate,
            patch.dict(os.environ, {
                "MARK_PRIVATE_WEB_APPROVED": "true",
                "MARK_PLATFORM_WRITES_ENABLED": "true",
            }),
            patch.object(
                runtime, "require_private_web_runtime_dependency",
                side_effect=TechnicalSetupReached("product dependency validation"),
            ) as dependency,
        ):
            for builder, options in (
                (runtime.build_private_web_inventory_runtime, {}),
                (runtime.build_private_web_content_runtime, {}),
                (runtime.build_private_web_media_create_runtime, {
                    "cdp_session": object(),
                }),
            ):
                with self.subTest(builder=builder.__name__):
                    with self.assertRaisesRegex(
                        TechnicalSetupReached, "product dependency validation",
                    ):
                        builder(cdp_port=9222, **options)
            self.assertEqual(dependency.call_count, 3)
            obsolete_gate.assert_not_called()
        with (
            patch.object(
                runtime._legacy_admission, "require_legacy_platform_authority",
                side_effect=AssertionError("product write runtime consulted legacy gate"),
            ) as obsolete_gate,
            patch.object(
                runtime, "acquire_write_api_store_lock",
                side_effect=TechnicalSetupReached("local store admission"),
            ) as store_lock,
        ):
            with self.assertRaisesRegex(TechnicalSetupReached, "local store admission"):
                runtime.build_private_web_write_api_runtime(
                    cdp_port=9222, store=object(), access=access,
                    core_writes_enabled=True, media_writes_enabled=True,
                )
            store_lock.assert_called_once()
            obsolete_gate.assert_not_called()

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
