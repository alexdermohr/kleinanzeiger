"""Default product write runtimes must pin exactly one owned CDP target.

No external browser, credentials, platform requests or paid providers.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mark_api.private_web_runtime import (
    build_private_web_content_runtime, build_private_web_write_api_runtime,
)
from mark_api.storage import SnapshotStore
from mark_api.write_api import WriteApiAccess, WriteCapability


class DefaultOwnedCdpSessionTests(unittest.TestCase):
    def test_content_reader_and_every_writer_borrow_same_native_owner(self):
        with patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"):
            runtime = build_private_web_content_runtime(
                cdp_port=19972, timeout_seconds=1,
            )
        try:
            inventory = runtime._owner_reader
            first = runtime._page_factory()
            second = runtime._page_factory()
            self.assertIs(
                inventory._client_factory.__self__,
                first._client_factory.__self__,
            )
            self.assertIs(
                first._client_factory.__self__,
                second._client_factory.__self__,
            )
        finally:
            runtime.close()

    def test_default_write_content_and_media_share_one_target_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SnapshotStore(Path(temporary) / "write.sqlite")
            access = WriteApiAccess(
                principal="synthetic-session-proof",
                bearer_token="synthetic-token-never-network",
                capabilities=frozenset((
                    WriteCapability.CREATE,
                    WriteCapability.CREATE_MEDIA,
                    WriteCapability.UPDATE_CONTENT,
                )),
                writes_enabled=True,
            )
            with patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"):
                runtime = build_private_web_write_api_runtime(
                    cdp_port=19972,
                    store=store,
                    access=access,
                    core_writes_enabled=True,
                    media_writes_enabled=True,
                    timeout_seconds=1,
                )
            try:
                owner = runtime._content_runtime._owner_reader
                content_page = runtime._content_runtime._page_factory()
                media_page = runtime._media_runtime._page_factory()
                self.assertIs(
                    owner._client_factory.__self__,
                    content_page._client_factory.__self__,
                )
                self.assertIs(
                    content_page._client_factory.__self__,
                    media_page._client_factory.__self__,
                )
            finally:
                runtime.close()


    def test_effectful_native_session_survives_page_retirement_for_inventory(self):
        # The browser might still be on a submitted editor. Closing a page
        # may not release the kernel target claim before its later post-read.
        from mark_api import protected_cdp_session

        class SyntheticNative:
            instances = []

            def __init__(self, endpoint, *, timeout_seconds):
                self.endpoint = endpoint
                self.timeout_seconds = timeout_seconds
                self._possible_browser_side_effect = False
                self.calls = []
                self.closed = False
                type(self).instances.append(self)

            def call(self, method, params=None):
                self.calls.append(method)
                if method == "Page.navigate":
                    self._possible_browser_side_effect = True
                return {"result": {"value": {"readyState": "complete"}}}

            def close(self):
                self.closed = True

        with (
            patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"),
            patch.object(protected_cdp_session, "_LoopbackCdpClient", SyntheticNative),
            patch.object(
                protected_cdp_session, "_require_private_web_cdp_authority",
                return_value=None,
            ),
            patch("mark_api.private_web_cdp._require_private_web_cdp_authority",
                  return_value=None),
        ):
            runtime = build_private_web_content_runtime(
                cdp_port=19972, timeout_seconds=1,
            )
            try:
                page = runtime._page_factory()
                first = page._client()
                first.call("Page.navigate", {"url": "https://www.kleinanzeigen.de/"})
                page.close()
                second = runtime._owner_reader._client_factory()
                second.call("Runtime.evaluate", {"expression": "synthetic read"})
                second.close()
                followup = runtime._page_factory()._client()
                self.assertIs(first._native, second._native)
                self.assertIs(first._native, followup._native)
                self.assertEqual(len(SyntheticNative.instances), 1)
                self.assertEqual(
                    SyntheticNative.instances[0].calls,
                    ["Page.navigate", "Runtime.evaluate"],
                )
                self.assertFalse(SyntheticNative.instances[0].closed)
                followup.close()
            finally:
                runtime.close()
            # A mere transport ACK never releases an effectful owner.
            self.assertFalse(SyntheticNative.instances[0].closed)
