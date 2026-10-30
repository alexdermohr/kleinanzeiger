"""Offline checks of the native NixOS launcher and Chrome crash isolation."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import contextlib
import io


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "kleinanzeiger" / "linux_launcher.py"


def _load():
    spec = importlib.util.spec_from_file_location("kleinanzeiger_linux_test", SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LinuxLauncherTests(unittest.TestCase):
    def test_crash_state_profile_is_never_the_old_customer_profile(self):
        mod = _load()
        self.assertNotEqual(mod._PROFILE, mod._OLD_PROFILE)
        self.assertEqual(mod._OLD_PROFILE.name, "chrome-profile")
        self.assertEqual(mod._PROFILE.name, "chrome-profile-v2")

    def test_dashboard_url_passes_only_local_matching_port_and_token(self):
        mod = _load()
        raw = "Kleinanzeiger dashboard: http://127.0.0.1:45678/#write_token=test"
        self.assertEqual(mod._dashboard_url(raw, 45678),
                         "http://127.0.0.1:45678/#write_token=test")
        self.assertIsNone(mod._dashboard_url(raw, 45679))
        self.assertIsNone(mod._dashboard_url(
            "Kleinanzeiger dashboard: https://external.invalid/123", 45678))
        self.assertIsNone(mod._dashboard_url(
            "Kleinanzeiger dashboard: http://localhost:45678/", 45678))

    def test_empty_profile_does_not_trigger_any_browser_cleanup(self):
        mod = _load()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self.assertIsNone(mod._existing_chrome_port(directory))
            stale = directory / "SingletonLock"
            stale.symlink_to(f"{socket.gethostname()}-99999997")
            self.assertIsNone(mod._existing_chrome_port(directory))
            self.assertTrue(stale.is_symlink(), "Chrome alone owns stale lock cleanup")

    def test_nonmatching_profile_lock_never_authorizes_a_connection(self):
        mod = _load()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            lock = directory / "SingletonLock"
            lock.symlink_to("foreign-host-123")
            self.assertEqual(mod._existing_chrome_port(directory), 0)

    def test_symlinked_data_directory_blocks_start_without_writes(self):
        mod = _load()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            root.mkdir()
            external = Path(temp) / "external"
            external.mkdir()
            (root / "data").symlink_to(external, target_is_directory=True)
            mod._ROOT = root
            mod._DB = root / "data" / "portfolio.sqlite3"
            with (
                patch.object(mod, "version", return_value="0.1.22"),
                patch("sys.argv", ["kleinanzeiger-linux", "offline"]),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as caught:
                    mod.main()
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(list(external.iterdir()), "no data outside app root")

    def test_public_linux_launcher_is_packaged(self):
        import tomllib

        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["scripts"]["kleinanzeiger-linux"],
                         "kleinanzeiger.linux_launcher:main")
        code = SRC.read_text(encoding="utf-8")
        self.assertNotIn("unlink(", code)
        self.assertNotIn("shutil.rmtree", code)


if __name__ == "__main__":
    unittest.main()
