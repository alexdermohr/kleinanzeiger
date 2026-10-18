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
from mark_api.adapters.monkrel_runtime import MonkrelPrivateHttpRuntimeClient
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
