"""Regression for GUI control scripts that outlive a .venv-only rollback.

A fake pre-v0.1.24 offline_launcher has the original create_server/main
interface but deliberately rejects --stop-on-stdin. No customer database,
Chrome profile, provider or marketplace call is used.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPAT = ROOT / "scripts" / "windows-offline-compat.py"

_OLD_MODULE = """
import argparse
from threading import Event


class _Server:
    def __init__(self):
        self.stopped = Event()

    def serve_forever(self):
        if not self.stopped.wait(timeout=6):
            raise RuntimeError("legacy offline launcher did not stop")

    def shutdown(self):
        self.stopped.set()

    def server_close(self):
        pass


def create_server(*args, **kwargs):
    return _Server()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.parse_args(argv)
    server = create_server()
    print("LEGACY_READY", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    print("LEGACY_STOPPED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


class WindowsOfflineCompatibilityTests(unittest.TestCase):
    def _legacy(self, directory: Path) -> dict[str, str]:
        package = directory / "mark_api"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "offline_launcher.py").write_text(_OLD_MODULE, encoding="utf-8")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(directory)
        environment["PYTHONNOUSERSITE"] = "1"
        return environment

    def test_pre_024_module_rejects_new_flag(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kleinanzeiger-old-parser-") as temp:
            directory = Path(temp)
            environment = self._legacy(directory)
            result = subprocess.run(
                [
                    sys.executable, "-m", "mark_api.offline_launcher",
                    "--db", str(directory / "fixture.sqlite"),
                    "--stop-on-stdin",
                ],
                capture_output=True, text=True, env=environment, timeout=10,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments: --stop-on-stdin", result.stderr)

    def test_gui_bridge_stops_old_offline_launcher_on_stop_and_parent_eof(self) -> None:
        self.assertTrue(COMPAT.is_file())
        for control in ("STOP\n", ""):
            with self.subTest(control=repr(control)):
                with tempfile.TemporaryDirectory(prefix="kleinanzeiger-old-stop-") as temp:
                    directory = Path(temp)
                    environment = self._legacy(directory)
                    result = subprocess.run(
                        [sys.executable, str(COMPAT), "--db", str(directory / "fixture.sqlite")],
                        input=control, capture_output=True, text=True,
                        env=environment, timeout=10,
                    )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("LEGACY_READY", result.stdout)
                self.assertIn("LEGACY_STOPPED", result.stdout)
                self.assertNotIn("unrecognized arguments", result.stderr)


if __name__ == "__main__":
    unittest.main()
