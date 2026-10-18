"""No real platform accesses: injected counters prove early legacy denials."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from mark_api.adapters._legacy_admission import LegacyPlatformAdmissionDenied
from mark_api.adapters.browser_bot import BrowserBotAdapter, BrowserBotRuntime, ProcessResult
from mark_api.adapters.monkrel import MonkrelMobileApiAdapter
from mark_api.adapters.monkrel_http import MonkrelPrivateHttpContentClient
from mark_api.adapters.management import (
    MANAGEMENT_URL, HttpResponse, ManagementReadAdapter,
    TransportFailure, UrllibHttpTransport,
)
from mark_api.adapters.monkrel_runtime import MonkrelPrivateHttpRuntimeClient
from mark_api.private_web_runtime import _NoRedirectManagementTransport
from mark_api.domain import LifecycleState
from mark_api.results import ReadStatus


class _Upstream:
    max_retries = 1

    def __init__(self):
        self.calls = []

    @property
    def user_id(self):
        self.calls.append("user_id")
        return "synthetic"

    def _request(self, *args, **kwargs):
        self.calls.append("_request")
        raise AssertionError("unauthorized private HTTP")

    def my_ads(self, **kwargs):
        self.calls.append("my_ads")
        raise AssertionError("unauthorized mobile inventory")

    def conversations(self, **kwargs):
        self.calls.append("conversations")
        raise AssertionError("unauthorized inbox")

    def pause_ad(self, ad_id):
        self.calls.append("pause_ad")
        raise AssertionError("unauthorized pause")

    def activate_ad(self, ad_id):
        self.calls.append("activate_ad")
        raise AssertionError("unauthorized activate")

    def delete_ad(self, ad_id):
        self.calls.append("delete_ad")
        raise AssertionError("unauthorized delete")

    def update_ad(self, ad_id, **kwargs):
        self.calls.append("update_ad")
        raise AssertionError("unauthorized update")


class _Runner:
    def __init__(self):
        self.calls = []

    def run(self, argv, *, cwd, input_text, timeout_seconds):
        self.calls.append(argv)
        raise AssertionError("unauthorized browser-bot subprocess")


class LegacyPlatformDefaultDenyTests(unittest.TestCase):
    def test_mobile_reads_and_writes_reject_before_upstream(self):
        upstream = _Upstream()
        adapter = MonkrelMobileApiAdapter(upstream)
        self.assertEqual(adapter.read_ads().status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual(adapter.read_ad("1234567890").status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual(adapter.read_reactions("1234567890").status, ReadStatus.TRANSPORT_ERROR)
        for state in (LifecycleState.ACTIVE, LifecycleState.PAUSED):
            with self.assertRaises(LegacyPlatformAdmissionDenied):
                adapter.set_state("1234567890", state)
        with self.assertRaises(LegacyPlatformAdmissionDenied):
            adapter.delete_ad("1234567890")
        with self.assertRaises(LegacyPlatformAdmissionDenied):
            adapter.update_content("1234567890", title="synthetic")
        self.assertEqual(upstream.calls, [])

    def test_private_http_denies_before_user_id_and_get_or_put(self):
        upstream = _Upstream()
        adapter = MonkrelPrivateHttpContentClient(upstream)
        with self.assertRaises(LegacyPlatformAdmissionDenied):
            adapter.read_invariant("1234567890")
        with self.assertRaises(LegacyPlatformAdmissionDenied):
            adapter.update_ad("1234567890", title="synthetic")
        self.assertEqual(upstream.calls, [])

    def test_private_http_write_denies_before_upstream_property_access(self):
        class SideEffectingPolicy(_Upstream):
            @property
            def max_retries(self):
                self.calls.append("max_retries_property")
                raise AssertionError("unauthorized policy property read")

        upstream = SideEffectingPolicy()
        adapter = MonkrelPrivateHttpContentClient(upstream)
        with self.assertRaises(LegacyPlatformAdmissionDenied):
            adapter.update_ad("1234567890", title="synthetic")
        self.assertEqual(upstream.calls, [])

    def test_raw_management_http_denied_before_urlopen(self):
        with (
            patch("mark_api.adapters.management.urlopen",
                  side_effect=AssertionError("unapproved management HTTP")) as urlopen,
            patch.dict(os.environ, {
                "MARK_PRIVATE_WEB_APPROVED": "true",
                "MARK_PROSELLERS_API_ENTITLEMENT_CONFIRMED": "true",
            }),
        ):
            with self.assertRaisesRegex(TransportFailure, "not authorized"):
                UrllibHttpTransport().get(
                    MANAGEMENT_URL, headers={"Cookie": "synthetic only"}
                )
            urlopen.assert_not_called()

    def test_management_adapter_rejects_before_cookie_provider_and_fake_http(self):
        seen = []
        def cookie_provider():
            seen.append("cookie_provider")
            raise AssertionError("unauthorized CDP cookie read")
        class SyntheticTransport:
            def get(self, url, *, headers):
                seen.append("transport")
                return HttpResponse(200, b'{"ads":[],"paging":{"last":1}}')

        adapter = ManagementReadAdapter(
            cookie_provider=cookie_provider,
            transport=SyntheticTransport(),
        )
        with patch.dict(os.environ, {
            "MARK_PRIVATE_WEB_APPROVED": "true",
            "MARK_PLATFORM_WRITES_ENABLED": "true",
            "MARK_PROSELLERS_API_ENTITLEMENT_CONFIRMED": "true",
        }):
            for read in (
                adapter.read_ads,
                lambda: adapter.read_ad("1234567890"),
                lambda: adapter._read_page(cookies="synthetic", page=1),
            ):
                with self.subTest(entry=read):
                    result=read()
                    self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)
                    self.assertEqual(result.error, "legacy_platform_not_authorized")
        self.assertEqual(seen, [])

    def test_no_redirect_relay_get_denies_before_injected_http_opener(self):
        # The internal no-redirect class is directly importable and must not
        # be an independent platform transport escape hatch.
        transport = _NoRedirectManagementTransport(timeout_seconds=0.1)
        with (
            patch.object(transport, "_open",
                         side_effect=AssertionError("unauthorized relay HTTP")) as opener,
            patch.dict(os.environ, {
                "MARK_PRIVATE_WEB_APPROVED": "true",
                "MARK_PROSELLERS_API_ENTITLEMENT_CONFIRMED": "true",
            }),
        ):
            with self.assertRaisesRegex(TransportFailure, "not authorized"):
                transport.get(
                    MANAGEMENT_URL, headers={"Cookie": "synthetic-only"},
                )
            opener.assert_not_called()

    def test_construction_never_truth_tests_injected_management_transport(self):
        class HostileTransport:
            def __init__(self):
                self.calls = []
            def __bool__(self):
                self.calls.append("bool")
                raise AssertionError("unauthorized transport truth-test")
            def get(self, url, *, headers):
                self.calls.append("HTTP")
                raise AssertionError("unauthorized HTTP")
        transport = HostileTransport()
        reader = ManagementReadAdapter(
            cookie_provider=lambda: "synthetic", transport=transport,
        )
        self.assertIs(reader._transport, transport)
        self.assertEqual(reader.read_ads().status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual(transport.calls, [])

    def test_construction_never_truth_tests_external_browser_bot_runner(self):
        class HostileRunner(_Runner):
            def __bool__(self):
                self.calls.append("bool")
                raise AssertionError("unauthorized browser-bot runner truth-test")
        runner = HostileRunner()
        runtime = BrowserBotRuntime(
            python_executable=Path(sys.executable),
            bot_checkout=Path("/tmp"),
            config_path=Path("/tmp/mark-synthetic-unused.yaml"),
        )
        bot = BrowserBotAdapter(runtime, runner=runner)
        self.assertIs(bot._runner, runner)
        with self.assertRaises(LegacyPlatformAdmissionDenied):
            bot.sync("1234567890")
        self.assertEqual(runner.calls, [])

    def test_direct_monkrel_runtime_wrapper_denies_before_upstream(self):
        upstream = _Upstream()
        wrapper = MonkrelPrivateHttpRuntimeClient(
            upstream, content_updater=upstream
        )
        for operation in (
            lambda: wrapper.my_ads(),
            lambda: wrapper.pause_ad("1234567890"),
            lambda: wrapper.activate_ad("1234567890"),
            lambda: wrapper.delete_ad("1234567890"),
            lambda: wrapper.conversations(),
            lambda: wrapper.messages("conv-synthetic"),
            lambda: wrapper.update_ad("1234567890", title="synthetic"),
        ):
            with self.assertRaises(LegacyPlatformAdmissionDenied):
                operation()
        self.assertEqual(upstream.calls, [])

    def test_browser_bot_never_dispatches_real_runner(self):
        runner = _Runner()
        runtime = BrowserBotRuntime(
            python_executable=Path(sys.executable),
            bot_checkout=Path("/tmp"),
            config_path=Path("/tmp/mark-synthetic-config-not-created.yaml"),
        )
        adapter = BrowserBotAdapter(runtime, runner=runner)
        with self.assertRaises(LegacyPlatformAdmissionDenied):
            adapter.sync("1234567890")
        with self.assertRaises(LegacyPlatformAdmissionDenied):
            adapter.update_content("1234567890", title="synthetic")
        self.assertEqual(runner.calls, [])

    def test_environment_flags_cannot_enable_unofficial_transport(self):
        upstream = _Upstream()
        with patch.dict(os.environ, {
            "MARK_ALLOW_PRIVATE_WEB_READS": "true",
            "MARK_PRIVATE_WEB_APPROVED": "true",
            "MARK_PLATFORM_WRITES_ENABLED": "true",
            "MARK_PROSELLERS_API_ENTITLEMENT_CONFIRMED": "true",
        }):
            self.assertEqual(
                MonkrelMobileApiAdapter(upstream).read_ads().status,
                ReadStatus.TRANSPORT_ERROR,
            )
            with self.assertRaises(LegacyPlatformAdmissionDenied):
                MonkrelPrivateHttpContentClient(upstream).read_invariant("1234567890")
        self.assertEqual(upstream.calls, [])


if __name__ == "__main__":
    unittest.main()
