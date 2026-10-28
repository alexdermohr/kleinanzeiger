"""Regression checks for the installed native Windows runtime."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mark_api.private_web_cdp import (
    _ACTIVE_CDP_TARGET_CLAIMS, _reserve_exclusive_cdp_target,
    PrivateWebCdpWriteNotAttemptedError,
)
from mark_api.private_web_media import PrivateWebMediaHandleStore
from mark_api.storage import SnapshotStore
from mark_api.write_api import acquire_write_api_store_lock


@unittest.skipUnless(os.name == "nt", "native Windows only")
class WindowsInstalledProductTests(unittest.TestCase):
    def test_browser_target_exclusive_cross_process(self):
        port = 24721
        first = _reserve_exclusive_cdp_target(port)
        try:
            self.assertTrue(first.is_live())
            with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
                _reserve_exclusive_cdp_target(port)
            code = (
                "from mark_api.private_web_cdp import _reserve_exclusive_cdp_target;"
                "_reserve_exclusive_cdp_target(24721)"
            )
            competing = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertNotEqual(competing.returncode, 0)
            self.assertIn("target_busy", competing.stderr)
        finally:
            first.close()
            _ACTIVE_CDP_TARGET_CLAIMS.pop(id(first), None)
        second = _reserve_exclusive_cdp_target(port)
        try:
            self.assertTrue(second.is_live())
        finally:
            second.close()
            _ACTIVE_CDP_TARGET_CLAIMS.pop(id(second), None)

    def test_attached_browser_read_and_unknown_write_fence(self):
        import json
        from mark_api.private_web_cdp import _LoopbackCdpClient, PrivateWebCdpError

        class PageList:
            def __init__(self, url):
                self.url = url
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return None
            def geturl(self):
                return self.url
            def read(self, *_):
                return json.dumps([{
                    "type": "page",
                    "webSocketDebuggerUrl": (
                        "ws://127.0.0.1:24722/devtools/page/win-synthetic"
                    ),
                }]).encode("utf-8")

        class FakeTcp:
            def close(self):
                pass

        class FakeWs:
            def __init__(self):
                self.sent = []
                self.closed = False
            def send(self, msg):
                self.sent.append(json.loads(msg))
            def recv(self):
                return json.dumps({
                    "id": self.sent[-1]["id"],
                    "result": {"cookies": []},
                })
            def close(self):
                self.closed = True

        sockets = []
        def ws_factory(*_args, **_kwargs):
            sock = FakeWs()
            sockets.append(sock)
            return sock

        client = _LoopbackCdpClient(
            "http://127.0.0.1:24722",
            timeout_seconds=1,
            opener=lambda url, timeout: PageList(url),
            tcp_socket_factory=lambda *args, **kwargs: FakeTcp(),
            websocket_factory=ws_factory,
        )
        self.assertEqual(client.call("Network.getCookies")["cookies"], [])
        client.close()
        self.assertTrue(sockets[0].closed)

        # A matching CDP ACK for mouse input does not establish platform
        # settlement: a second cooperating Windows writer remains denied.
        unsafe = _LoopbackCdpClient(
            "http://127.0.0.1:24722",
            timeout_seconds=1,
            opener=lambda url, timeout: PageList(url),
            tcp_socket_factory=lambda *args, **kwargs: FakeTcp(),
            websocket_factory=ws_factory,
        )
        unsafe.call("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": 1, "y": 2, "button": "left",
        })
        with self.assertRaises(PrivateWebCdpError):
            unsafe.close()
        with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
            _reserve_exclusive_cdp_target(24722)

    def test_multiple_chrome_tabs_attach_only_canonical_management_tab(self):
        """Actual Windows target mutex + selection; no real network or profile."""
        import json
        from mark_api.private_web_cdp import _LoopbackCdpClient

        manager_url = "https://www.kleinanzeigen.de/m-meine-anzeigen.html"
        base = "ws://127.0.0.1:24723/devtools/page/"
        targets = [
            {"type": "page", "url": "https://example.invalid/news",
             "webSocketDebuggerUrl": base + "other"},
            {"type": "page", "url": manager_url,
             "webSocketDebuggerUrl": base + "manager-b"},
            {"type": "page", "url": manager_url + "?ref=mine",
             "webSocketDebuggerUrl": base + "manager-a"},
        ]

        class PageList:
            def __init__(self, address, pages):
                self.address, self.pages = address, pages
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return None
            def geturl(self):
                return self.address
            def read(self, *_):
                return json.dumps(self.pages).encode("utf-8")

        class FakeTcp:
            def close(self):
                pass

        class FakeWs:
            def __init__(self):
                self.sent = []
                self.closed = False
            def send(self, message):
                self.sent.append(message)
            def close(self):
                self.closed = True

        selected = []
        for pages in (targets, list(reversed(targets))):
            def ws_factory(url, **_options):
                socket = FakeWs()
                selected.append((url, socket))
                return socket
            client = _LoopbackCdpClient(
                "http://127.0.0.1:24723",
                timeout_seconds=1,
                opener=lambda url, timeout, pages=pages: PageList(url, pages),
                tcp_socket_factory=lambda *args, **kw: FakeTcp(),
                websocket_factory=ws_factory,
            )
            self.assertEqual(client._target_websocket_url, base + "manager-a")
            client.close()

        self.assertEqual([url for url, _ in selected], [base + "manager-a"] * 2)
        self.assertTrue(all(socket.closed and not socket.sent
                            for _, socket in selected))

    def test_sqlite_exclusive_writer(self):
        with tempfile.TemporaryDirectory() as root:
            db = Path(root) / "anzeigen.sqlite"
            store = SnapshotStore(db, create_if_missing=True)
            del store
            first = acquire_write_api_store_lock(
                SnapshotStore(db, create_if_missing=False)
            )
            try:
                with self.assertRaises(RuntimeError):
                    acquire_write_api_store_lock(
                        SnapshotStore(db, create_if_missing=False)
                    )
            finally:
                first.close()
            next_lease = acquire_write_api_store_lock(
                SnapshotStore(db, create_if_missing=False)
            )
            next_lease.close()

    def test_staged_media_survives_restart_when_pending(self):
        profile = os.environ.get("LOCALAPPDATA")
        self.assertTrue(profile)
        protected: set[str] = set()
        with tempfile.TemporaryDirectory(prefix="ka-win-", dir=profile) as root:
            directory = Path(root) / "staged"
            store = PrivateWebMediaHandleStore(
                directory=directory,
                protected_refs=lambda: frozenset(protected),
            )
            ref = store.stage_media("beispiel.png", b"\x89PNG\r\n\x1a\n" + b"0" * 24)
            protected.add(ref)
            self.assertTrue(Path(store.resolve((ref,))[0].path).is_file())
            store.close()
            reopened = PrivateWebMediaHandleStore(
                directory=directory, protected_refs=lambda: frozenset(protected),
            )
            self.assertEqual(
                Path(reopened.resolve((ref,))[0].path).read_bytes()[:8],
                b"\x89PNG\r\n\x1a\n",
            )
            protected.clear()
            reopened.discard((ref,))
            reopened.close()

    def test_media_path_must_stay_in_user_profile(self):
        from mark_api.windows_runtime import require_private_windows_directory
        local = Path(os.environ["LOCALAPPDATA"])
        with tempfile.TemporaryDirectory(prefix="ka-path-", dir=local) as root:
            dest = Path(root) / "private"
            self.assertEqual(require_private_windows_directory(dest, create=True), dest)
        with self.assertRaises(OSError):
            require_private_windows_directory(Path("C:/Windows/System32"))


if __name__ == "__main__":
    unittest.main()