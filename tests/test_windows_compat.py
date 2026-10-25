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
