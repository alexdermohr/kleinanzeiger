"""Offline checks of the native NixOS launcher and Chrome crash isolation."""
from __future__ import annotations

import importlib.util
import json
from urllib.parse import unquote
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

    def test_chrome_accepts_normal_and_flattened_nixos_argv(self):
        mod = _load()
        normal = (
            b"/run/current-system/sw/bin/chrome\0"
            b"--user-data-dir=/home/alex/.local/share/kleinanzeiger/chrome-profile\0"
            b"--remote-debugging-port=50365\0"
        )
        flattened = (
            b"/nix/store/chrome --simulate-outdated-no-au=2099 "
            b"--remote-debugging-port=50365 "
            b"--user-data-dir=/home/alex/.local/share/kleinanzeiger/chrome-profile\0"
        )
        for argv in (normal, flattened):
            with self.subTest(cmdline_kind="flat" if argv==flattened else "normal"):
                self.assertEqual(mod._chrome_flag_value(argv, b"remote-debugging-port"), b"50365")
                self.assertEqual(
                    mod._chrome_flag_value(argv, b"user-data-dir"),
                    b"/home/alex/.local/share/kleinanzeiger/chrome-profile",
                )
        self.assertIsNone(mod._chrome_flag_value(
            b"/bin/chrome\0--app=--remote-debugging-port=50365\0",
            b"remote-debugging-port",
        ))
        self.assertNotEqual(
            mod._chrome_flag_value(
                b"/bin/chrome --user-data-dir=/home/alex/.local/share/kleinanzeiger/chrome-profile-fake",
                b"user-data-dir",
            ),
            b"/home/alex/.local/share/kleinanzeiger/chrome-profile",
        )

    def test_chooser_uses_running_original_profile_without_second_browser(self):
        mod = _load()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod._DB = root / "db.sqlite3"
            mod._ROOT = root
            app = unittest.mock.MagicMock()
            app.wait.return_value = 0
            app.poll.return_value = 0
            with (
                patch.object(mod, "version", return_value="0.1.23"),
                patch.object(mod, "_existing_chrome_port",
                             side_effect=lambda p: 50365 if p==mod._OLD_PROFILE else None),
                patch.object(mod, "_start_chrome", return_value=50365) as browser,
                patch.object(mod.subprocess, "Popen", return_value=app),
                patch.object(mod, "_open_dashboard", return_value=True) as dashboard,
                patch.object(mod, "_free_port", return_value=41234),
                patch("sys.argv", ["kleinanzeiger-linux", "konto"]),
            ):
                self.assertEqual(mod.main(), 0)
            browser.assert_called_once_with(mod._OLD_PROFILE)
            self.assertEqual(dashboard.call_args.kwargs["browser_profile"], mod._OLD_PROFILE)
            self.assertEqual(dashboard.call_args.kwargs["cdp_port"],50365)

    def test_chrome_signins_survive_browser_restart_in_same_profile(self):
        mod = _load()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old-signed-in-profile"
            new = root / "new-empty-profile"
            old.mkdir()
            new.mkdir()
            # Synthetic stand-in for Chrome's stored session data. The app
            # must never read, copy, delete or rewrite Chrome's cookie DB.
            cookies = old / "Default" / "Network" / "Cookies"
            cookies.parent.mkdir(parents=True)
            cookies.write_bytes(b"unchanged synthetic saved browser login")
            with (
                patch.object(mod, "_OLD_PROFILE", old),
                patch.object(mod, "_PROFILE", new),
                patch.object(mod, "_existing_chrome_port", return_value=None),
            ):
                self.assertEqual(mod._choose_persistent_chrome_profile(), old)
            self.assertEqual(cookies.read_bytes(), b"unchanged synthetic saved browser login")
            self.assertFalse((new / "Default").exists())

    def test_existing_active_product_chrome_takes_precedence(self):
        mod = _load()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            with (
                patch.object(mod, "_OLD_PROFILE", old),
                patch.object(mod, "_PROFILE", new),
                patch.object(
                    mod, "_existing_chrome_port",
                    side_effect=lambda profile: 52991 if profile == new else None,
                ),
            ):
                self.assertEqual(mod._choose_persistent_chrome_profile(), new)

    def test_new_profile_is_only_default_when_no_previous_profile_exists(self):
        mod = _load()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new = root / "old", root / "new"
            with (
                patch.object(mod, "_OLD_PROFILE", old),
                patch.object(mod, "_PROFILE", new),
                patch.object(mod, "_existing_chrome_port", return_value=None),
            ):
                self.assertEqual(mod._choose_persistent_chrome_profile(), new)
                self.assertFalse(old.exists())
                self.assertFalse(new.exists())

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
                patch.object(mod, "version", return_value="0.1.23"),
                patch("sys.argv", ["kleinanzeiger-linux", "offline"]),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as caught:
                    mod.main()
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(list(external.iterdir()), "no data outside app root")

    def test_dashboard_is_opened_in_exact_owned_chrome_cdp(self):
        mod = _load()
        url = "http://127.0.0.1:45678/#write_token=synthetic-only"
        from unittest.mock import MagicMock
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps({
            "id": "test-owned-target", "type": "page", "url": url
        }).encode("utf-8")
        response.__enter__.return_value = response
        with (
            patch.object(mod, "_is_cdp_ready", return_value=True),
            patch.object(mod, "_existing_chrome_port", return_value=9222),
            patch.object(mod, "urlopen", return_value=response) as opened,
            patch.object(mod.subprocess, "Popen") as browser_spawner,
        ):
            self.assertTrue(mod._open_in_own_chrome(9222, url, 45678))
            browser_spawner.assert_not_called()
        request = opened.call_args.args[0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(
            request.full_url.split("/json/new?", 1)[0],
            "http://127.0.0.1:9222",
        )
        self.assertEqual(unquote(request.full_url.split("/json/new?", 1)[1]), url)

    def test_account_dashboard_refuses_foreign_or_dead_chrome(self):
        mod = _load()
        url = "http://127.0.0.1:45678/#write_token=synthetic-only"
        with (
            patch.object(mod, "_is_cdp_ready", return_value=True),
            patch.object(mod, "_existing_chrome_port", return_value=3999),
            patch.object(mod, "urlopen") as opened,
        ):
            self.assertFalse(mod._open_in_own_chrome(9222, url, 45678))
            opened.assert_not_called()

    def test_account_dashboard_refuses_external_url(self):
        mod = _load()
        with patch.object(mod, "urlopen") as opened:
            self.assertFalse(mod._open_in_own_chrome(9222, "https://example.invalid/", 45678))
            opened.assert_not_called()

    def test_account_start_does_not_launch_default_browser(self):
        code = SRC.read_text(encoding="utf-8")
        self.assertIn("if cdp_port is not None:", code)
        self.assertIn("if not _open_in_own_chrome(", code)
        self.assertIn("browser_profile=browser_profile", code)
        self.assertIn("else:\\n                    opener = shutil.which", code.replace("\n", "\\n"))

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
