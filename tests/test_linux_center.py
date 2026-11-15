"""Synthetic KDE control flows: no real dialogs, account, Chrome or customer DB."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from kleinanzeiger import linux_center as mod
from mark_api.storage import SnapshotStore


class CenterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "test.sqlite"
        self.center = mod.Center("/fake/kdialog", self.db)

    def test_dialog_cancel_error_and_literal_argv(self):
        for code, expected in ((0, "x"), (1, None)):
            with patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], code, "x\n")) as run:
                self.assertEqual(self.center.dialog("--inputbox", "$(touch nope); `false`"), expected)
                self.assertFalse(run.call_args.kwargs["shell"])
                self.assertEqual(run.call_args.args[0][-1], "$(touch nope); `false`")
        with patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 2, "")):
            with self.assertRaises(OSError):
                self.center.dialog("--msgbox", "test")

    def test_single_instance_and_release(self):
        with mod.instance_lock(self.root) as first:
            self.assertTrue(first)
            with mod.instance_lock(self.root) as second:
                self.assertFalse(second)
        with mod.instance_lock(self.root) as third:
            self.assertTrue(third)

    def test_symlink_lock_does_not_touch_target(self):
        target = self.root / "untouched"
        target.write_text("original")
        (self.root / "center.lock").symlink_to(target)
        with self.assertRaises(OSError):
            with mod.instance_lock(self.root):
                self.fail("must reject")
        self.assertEqual(target.read_text(), "original")

    def test_start_reuses_launcher_and_rejects_duplicate(self):
        child = MagicMock()
        child.poll.return_value = None
        with patch.object(mod.subprocess, "Popen", return_value=child) as spawn, patch.object(self.center, "message"):
            self.center.start("konto")
            self.center.start("offline")
        spawn.assert_called_once()
        argv = spawn.call_args.args[0]
        self.assertEqual(argv[-2:], ["kleinanzeiger.linux_launcher", "konto"])
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])
        self.assertFalse(spawn.call_args.kwargs["shell"])

    def test_stop_escalates_and_reaps(self):
        child = MagicMock(pid=123456)
        child.wait.side_effect = [subprocess.TimeoutExpired("test", 18), subprocess.TimeoutExpired("test", 3), 0]
        self.center.client = child
        with patch.object(mod.os, "killpg") as kill:
            self.center.stop()
        self.assertEqual([c.args[1] for c in kill.call_args_list], [signal.SIGINT, signal.SIGTERM, signal.SIGKILL])
        self.assertIsNone(self.center.client)
        self.assertEqual(child.wait.call_count, 3)

    def test_stop_reaps_already_exited_process(self):
        self.center.client = MagicMock(pid=123456)
        with patch.object(mod.os, "killpg", side_effect=ProcessLookupError):
            self.center.stop()
        self.assertIsNone(self.center.client)

    def test_backend_environment_and_failure_does_not_expose_output(self):
        with patch.dict(os.environ, {"KLEINANZEIGER_IMPORT_REQUEST": "override"}), patch.object(
            mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 2, "secret /home/private", "token")
        ) as run:
            with self.assertRaisesRegex(ValueError, "^backend failed$"):
                self.center.backend("mark_api.backup_cli", "--db", str(self.db))
        self.assertNotIn("KLEINANZEIGER_IMPORT_REQUEST", run.call_args.kwargs["env"])
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], 180)

    def test_preview_requires_valid_hash_and_explicit_yes(self):
        self.db.touch()
        valid = {"status": "preview", "can_import": True, "sha256": "a" * 64}
        for preview, confirm, calls in ((valid, None, 1), (valid, "", 2),
                                       ({**valid, "can_import": False}, "", 1),
                                       ({**valid, "sha256": "bad"}, "", 1)):
            with self.subTest(preview=preview, confirm=confirm), patch.object(
                self.center, "dialog", side_effect=["/tmp/articles.csv", confirm]
            ), patch.object(self.center, "text") as viewer, patch.object(self.center, "message"), patch.object(
                self.center, "backend", side_effect=[json.dumps(preview), '{"status":"imported"}']
            ) as backend:
                self.center.articles()
                viewer.assert_called_once()
                self.assertEqual(backend.call_count, calls)
                if calls == 2:
                    self.assertEqual(backend.call_args.args[-2:], ("--expected-sha256", "a" * 64))

    def test_real_import_rejects_file_changed_after_preview(self):
        SnapshotStore(self.db)
        source = self.root / "items.csv"
        source.write_text('sku,title,description,category,price_eur\nA,Lampe,Text,Haus > Licht,12\n')
        def show_preview(_):
            source.write_text(source.read_text().replace("Lampe", "Andere Lampe"))
        with patch.object(self.center, "dialog", side_effect=[str(source), ""]), patch.object(
            self.center, "text", side_effect=show_preview
        ):
            with self.assertRaises(ValueError):
                self.center.articles()
        from mark_api.portfolio_drafts import drafts_page
        self.assertEqual(drafts_page(SnapshotStore(self.db))["drafts"], [])

    def test_real_import_backup_and_weekly_use_temporary_database(self):
        SnapshotStore(self.db)
        source = self.root / "items.csv"
        source.write_text("sku,title,description,category,price_eur\nA,Lampe,Text,Haus > Licht,12\n")
        with patch.object(self.center, "dialog", side_effect=[str(source), ""]), patch.object(
            self.center, "text"
        ), patch.object(self.center, "message"):
            self.center.articles()
        from mark_api.portfolio_drafts import drafts_page
        self.assertEqual(drafts_page(SnapshotStore(self.db))["total"], 1)
        target = self.root / "backup.sqlite"
        with patch.object(self.center, "dialog", return_value=str(target)), patch.object(self.center, "message"):
            self.center.backup()
        self.assertEqual(drafts_page(SnapshotStore(target, create_if_missing=False, require_existing_schema=True))["total"], 1)
        original = target.read_bytes()
        with patch.object(self.center, "dialog", return_value=str(target)):
            with self.assertRaises(ValueError):
                self.center.backup()
        self.assertEqual(target.read_bytes(), original)
        out = self.root / "reports"
        out.mkdir()
        with patch.object(self.center, "dialog", return_value=str(out)), patch.object(self.center, "message"):
            self.center.weekly()
        self.assertEqual(len(list(out.glob("*.html"))), 1)

    def test_real_process_is_reaped_on_stop(self):
        import sys
        self.center.client = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        child = self.center.client
        self.center.stop()
        self.assertIsNotNone(child.poll())

    def test_plain_text_preview_is_temporary_and_removed(self):
        seen = []
        def view(*args):
            path = Path(args[1])
            seen.append(path)
            self.assertEqual(path.read_text(), "<b>untrusted</b>")
            self.assertEqual(args[0], "--textbox")
        with patch.object(self.center, "dialog", side_effect=view):
            self.center.text("<b>untrusted</b>")
        self.assertFalse(seen[0].exists())

    def test_backup_and_weekly_cli_arguments_and_cancellation(self):
        self.db.touch()
        with patch.object(self.center, "dialog", return_value="/tmp/new.sqlite"), patch.object(
            self.center, "backend"
        ) as backend, patch.object(self.center, "message"):
            self.center.backup()
            backend.assert_called_once_with("mark_api.backup_cli", "--db", str(self.db), "--backup", "/tmp/new.sqlite")
        with patch.object(self.center, "dialog", return_value=None), patch.object(self.center, "backend") as backend:
            self.center.backup()
            self.center.weekly()
            backend.assert_not_called()

    def test_missing_or_symlink_database_never_calls_cli(self):
        for symlink in (False, True):
            if symlink:
                self.db.symlink_to(self.root / "missing")
            with patch.object(self.center, "backend") as backend:
                with self.assertRaises(ValueError):
                    self.center.backup()
                backend.assert_not_called()

    def test_bargain_search_is_only_encoded_manual_url(self):
        with patch.object(self.center, "dialog", side_effect=["search", "ThinkPad; $(false)"]), patch.object(
            mod.shutil, "which", return_value="/fake/xdg-open"
        ), patch.object(mod.subprocess, "run") as run:
            self.center.bargains()
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertTrue(run.call_args.args[0][1].startswith("https://www.kleinanzeigen.de/"))
        self.assertNotIn(" ", run.call_args.args[0][1])

    def test_local_bargains_cli_creates_report_without_browser(self):
        source = self.root / "offers.json"
        source.write_text("[]")
        report = self.root / "report.html"
        with patch.object(self.center, "dialog", side_effect=["file", "ThinkPad T14", str(source), str(report)]), patch.object(
            self.center, "message"
        ):
            self.center.bargains()
        self.assertIn("Schnäppchenjäger", report.read_text())

    def test_entrypoint_version_and_desktop_file_match(self):
        import tomllib
        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((root / "pyproject.toml").read_text())
        self.assertEqual(metadata["project"]["version"], "1.0.14")
        self.assertEqual(metadata["project"]["scripts"]["kleinanzeiger-center"], "kleinanzeiger.linux_center:main")
        desktop = (root / "docs" / "kleinanzeiger-center.desktop").read_text()
        self.assertIn("Exec=kleinanzeiger-center\n", desktop)
        self.assertNotIn("/home/", desktop)

    def test_diagnostics_never_includes_raw_failure_or_paths(self):
        self.center.last_status = "Fehlgeschlagen"
        with patch.object(mod, "version", return_value="1.0.14"), patch.object(self.center, "text") as shown:
            self.center.diagnostics()
        output = shown.call_args.args[0]
        self.assertNotIn(str(self.root), output)
        self.assertIn("1.0.14", output)
        self.assertIn("Fehlgeschlagen", output)

    def test_run_sanitizes_failure_and_always_stops_on_exit(self):
        with patch.object(self.center, "dialog", side_effect=["backup", None]), patch.object(
            self.center, "backup", side_effect=ValueError("SECRET /home/private")
        ), patch.object(self.center, "message") as message, patch.object(self.center, "stop") as stop:
            self.assertEqual(self.center.run(), 0)
        self.assertNotIn("SECRET", message.call_args.args[0])
        stop.assert_called_once()

    def test_main_stops_and_restores_signals_after_interruption(self):
        from contextlib import contextmanager
        @contextmanager
        def locked(_):
            yield True
        before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
        center = MagicMock()
        center.run.side_effect = KeyboardInterrupt
        with patch.object(mod.shutil, "which", return_value="/fake/kdialog"), patch.object(
            mod, "Center", return_value=center
        ), patch.object(mod, "instance_lock", locked):
            self.assertEqual(mod.main(), 130)
        center.stop.assert_called_once()
        for sig, handler in before.items():
            self.assertEqual(signal.getsignal(sig), handler)

    def test_missing_kdialog_exits_without_starting_client(self):
        with patch.object(mod.shutil, "which", return_value=None), patch.object(mod.subprocess, "Popen") as spawn:
            self.assertEqual(mod.main(), 2)
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
