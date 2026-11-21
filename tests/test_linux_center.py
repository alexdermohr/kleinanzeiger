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

    def test_legacy_session_keeps_existing_account_and_denies_second_launcher(self):
        with (
            patch.object(mod.linux_launcher, "_other_kleinanzeiger_running", return_value=True),
            patch.object(mod.subprocess, "Popen") as spawned,
            patch.object(self.center, "message") as message,
        ):
            self.assertIs(self.center.start("konto"), False)
        spawned.assert_not_called()
        message.assert_called_once()
        self.assertEqual(self.center.last_status, "Andere Sitzung läuft")

    def test_start_reuses_launcher_and_rejects_duplicate(self):
        child = MagicMock()
        child.poll.return_value = None
        with (
            patch.object(mod.subprocess, "Popen", return_value=child) as spawn,
            patch.object(mod.linux_launcher, "_other_kleinanzeiger_running", return_value=False),
            patch.object(self.center, "message"),
        ):
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

    def test_local_search_mail_radar_uses_profile_without_model_prompt(self):
        profile = self.root / "search-mail.json"
        profile.write_text('{"model":"ThinkPad","input_type":"eml"}')
        response = json.dumps({"new": 2, "price_changes": 1, "hits": 1})
        with patch.object(self.center, "dialog", side_effect=["radar", str(profile)]) as dialog, patch.object(
            self.center, "backend", return_value=response
        ) as backend, patch.object(self.center, "message") as notify:
            self.center.bargains()
        self.assertEqual(dialog.call_count, 2)
        backend.assert_called_once_with("mark_api.bargain_hunter_cli", "--radar", str(profile))
        self.assertIn("1 auffällig günstige", notify.call_args.args[0])

    def test_search_mail_radar_cancellation_and_malformed_summary(self):
        with patch.object(self.center, "dialog", side_effect=["radar", None]), patch.object(
            self.center, "backend"
        ) as backend:
            self.center.bargains()
        backend.assert_not_called()
        with patch.object(self.center, "dialog", side_effect=["radar", "/tmp/profile.json"]), patch.object(
            self.center, "backend", return_value='{"new":true,"price_changes":0,"hits":0}'
        ):
            with self.assertRaises(ValueError):
                self.center.bargains()

    def test_new_radar_profile_explicit_watch_consent_and_cancel(self):
        from mark_api.radar_watch import RadarWatch
        manager = RadarWatch(self.root / "radars", self.root / "units")
        with patch("mark_api.radar_watch.RadarWatch", return_value=manager), patch.object(
            self.center, "dialog", side_effect=["new", "Testmodell 42", None]
        ), patch.object(self.center, "text") as text, patch.object(manager, "watch") as watch:
            self.center.bargains()
        watch.assert_not_called()
        self.assertIn("atomar", text.call_args.args[0])
        profile = manager.create("Testmodell 42")
        with patch("mark_api.radar_watch.RadarWatch", return_value=manager), patch.object(
            self.center, "dialog", side_effect=["new", "Testmodell 42", ""]
        ), patch.object(self.center, "text"), patch.object(self.center, "message"), patch.object(
            manager, "watch", return_value="Monitoring aktiv"
        ) as watch:
            self.center.bargains()
        watch.assert_called_once_with(profile, "start")

    def test_silver_sacd_button_idempotent_opt_in_and_explanation(self):
        from mark_api.radar_watch import RadarWatch
        from mark_api.silver_sacd import SEARCH_URL
        manager = RadarWatch(self.root / "radars", self.root / "units")
        for consent in (None, ""):
            with patch("mark_api.radar_watch.RadarWatch", return_value=manager), patch.object(
                self.center, "dialog", side_effect=["silver-sacd", consent]
            ) as dialog, patch.object(self.center, "text") as text, patch.object(
                self.center, "message"
            ), patch.object(manager, "watch", return_value="Monitoring aktiv") as watch:
                self.center.bargains()
            self.assertIn("Silberne SACD-Player beobachten", dialog.call_args_list[0].args)
            explanation = "\n".join(c.args[0] for c in text.call_args_list)
            self.assertIn(SEARCH_URL, explanation)
            self.assertIn("Suchauftrag speichern", explanation)
            self.assertIn("echte SACD-Discs", explanation)
            self.assertIn("Eingangsordner", explanation)
            if consent is None:
                watch.assert_not_called()
            else:
                watch.assert_called_once_with(manager.create_category(), "start")
        self.assertEqual(len(manager.profiles()), 1)

    def test_public_personal_price_target_menu_edits_only_private_settings(self):
        from mark_api.radar_watch import RadarWatch
        manager = RadarWatch(self.root / "radars", self.root / "units")
        profile = manager.create_public_category()
        code = "denon:DCD1700NE"
        models = {code: code + " · Angebotspreis 350.00 EUR"}
        before = profile.read_bytes()
        with patch("mark_api.radar_watch.RadarWatch", return_value=manager), \
             patch.object(self.center, "dialog", side_effect=["0", "price-target", code, "300"]), \
             patch.object(manager, "watch", return_value="Monitoring aktiv"), \
             patch.object(manager, "price_target_models", return_value=models), \
             patch.object(self.center, "message") as message:
            self.center.radar_monitoring("watch")
        self.assertEqual(manager.price_targets(profile), {code: "300.00"})
        self.assertEqual(before, profile.read_bytes())
        self.assertIn("kein Marktpreisvergleich", message.call_args.args[0])
        with patch("mark_api.radar_watch.RadarWatch", return_value=manager), \
             patch.object(self.center, "dialog", side_effect=["0", "price-target", code, ""]), \
             patch.object(manager, "watch", return_value="Monitoring aktiv"), \
             patch.object(manager, "price_target_models", return_value=models), \
             patch.object(self.center, "message") as message:
            self.center.radar_monitoring("watch")
        self.assertEqual(manager.price_targets(profile), {})
        self.assertIn("entfernt", message.call_args.args[0])

    def test_public_notification_test_uses_desktop_without_fake_history(self):
        from mark_api.radar_watch import RadarWatch
        manager = RadarWatch(self.root / "radars", self.root / "units")
        profile = manager.create_public_category()
        with patch("mark_api.radar_watch.RadarWatch", return_value=manager), \
             patch.object(self.center, "dialog", side_effect=["0", "notify-test"]), \
             patch.object(manager, "watch", return_value="Monitoring aktiv"), \
             patch.object(mod.shutil, "which", return_value="/usr/bin/notify-send"), \
             patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as sent, \
             patch.object(self.center, "message") as message:
            self.center.radar_monitoring("watch")
        argv = sent.call_args.args[0]
        self.assertEqual(argv[0], "/usr/bin/notify-send")
        self.assertIn("Funktionstest", argv[-1])
        self.assertFalse(sent.call_args.kwargs["shell"])
        self.assertIn("keine Marktänderung", message.call_args.args[0])
        self.assertFalse((profile.parent / "history.sqlite").exists())

    def test_watch_status_stop_and_invalid_prompt(self):
        from mark_api.radar_watch import RadarWatch
        manager = RadarWatch(self.root / "radars", self.root / "units")
        profile = manager.create("Testmodell 42")
        with patch("mark_api.radar_watch.RadarWatch", return_value=manager), patch.object(
            self.center, "dialog", side_effect=["watch", "0", "stop"]
        ), patch.object(manager, "watch", return_value="Monitoring gestoppt") as watch, patch.object(
            self.center, "message"
        ):
            self.center.bargains()
        self.assertEqual([c.args for c in watch.call_args_list], [(profile, "status"), (profile, "stop")])
        with patch("mark_api.radar_watch.RadarWatch", return_value=manager), patch.object(
            self.center, "dialog", side_effect=["new", " "]
        ), patch.object(self.center, "text") as text:
            self.center.bargains()
        self.assertIn("Modellbezeichnung", text.call_args.args[0])

    def test_watch_status_uses_plaintext_with_local_details(self):
        from mark_api.radar_watch import RadarWatch
        manager = RadarWatch(self.root / "radars", self.root / "units")
        manager.create_category()
        with patch("mark_api.radar_watch.RadarWatch", return_value=manager), patch.object(
            self.center, "dialog", side_effect=["0", "status"]
        ), patch.object(manager, "watch", return_value="Monitoring gestoppt"), patch.object(
            self.center, "text"
        ) as text:
            self.center.radar_monitoring("watch")
        displayed = text.call_args.args[0]
        self.assertIn("Monitoring gestoppt", displayed)
        self.assertIn("Eingangsquelle nicht eingerichtet/nicht belegt", displayed)
        self.assertIn("bericht.html", displayed)

    def test_monitoring_report_open_is_explicit_and_safe(self):
        from mark_api.radar_watch import RadarWatch
        manager = RadarWatch(self.root / "radars", self.root / "units")
        profile = manager.create_category()
        with patch("mark_api.radar_watch.RadarWatch", return_value=manager), patch.object(
            self.center, "dialog", side_effect=["0", "report"]
        ), patch.object(manager, "watch", return_value="Monitoring aktiv"), patch.object(
            mod.subprocess, "run"
        ) as opening, patch.object(self.center, "message") as message:
            self.center.radar_monitoring("watch")
        opening.assert_not_called()
        self.assertIn("Noch kein Radarbericht", message.call_args.args[0])
        report = profile.parent / "bericht.html"
        report.write_text("<h1>SACD</h1>", encoding="utf-8")
        with patch("mark_api.radar_watch.RadarWatch", return_value=manager), patch.object(
            self.center, "dialog", side_effect=["0", "report"]
        ), patch.object(manager, "watch", return_value="Monitoring aktiv"), patch.object(
            mod.shutil, "which", return_value="/usr/bin/xdg-open"
        ), patch.object(mod.subprocess, "run") as opening:
            self.center.radar_monitoring("watch")
        self.assertEqual(opening.call_args.args[0], ["/usr/bin/xdg-open", str(report)])
        self.assertFalse(opening.call_args.kwargs["shell"])
        report.unlink()
        report.symlink_to(self.root / "outside.html")
        with patch("mark_api.radar_watch.RadarWatch", return_value=manager), patch.object(
            self.center, "dialog", side_effect=["0", "report"]
        ), patch.object(manager, "watch", return_value="Monitoring aktiv"), patch.object(
            self.center, "message"
        ) as warning, patch.object(mod.subprocess, "run") as opening:
            self.center.radar_monitoring("watch")
        opening.assert_not_called()
        self.assertIn("keine Suchprofile", warning.call_args.args[0])

    def test_choose_model_profile_and_import_local_eml(self):
        from mark_api.radar_watch import RadarWatch
        import hashlib
        manager = RadarWatch(self.root / "radars", self.root / "units")
        one = manager.create("Testmodell 42")
        two = manager.create("Anderes Modell 13")
        source = self.root / "export.eml"
        source.write_bytes(b"synthetic local exported message")
        with patch("mark_api.radar_watch.RadarWatch", return_value=manager), patch.object(
            self.center, "dialog", side_effect=["import", "0", str(source)]
        ) as dialog, patch.object(self.center, "message") as message:
            self.center.bargains()
        self.assertEqual(dialog.call_count, 3)
        args = dialog.call_args_list[1].args
        self.assertIn("Testmodell 42", args)
        self.assertIn("Anderes Modell 13", args)
        self.assertEqual(
            [p.name for p in (two.parent / "suchmails").glob("*.eml")],
            [hashlib.sha256(source.read_bytes()).hexdigest() + ".eml"],
        )
        self.assertFalse(list((one.parent / "suchmails").glob("*.eml")))
        self.assertEqual(source.read_bytes(), b"synthetic local exported message")
        self.assertIn("atomar übernommen", message.call_args.args[0])

    def test_empty_profile_listing_does_not_start_monitor(self):
        from mark_api.radar_watch import RadarWatch
        manager = RadarWatch(self.root / "radars", self.root / "units")
        with patch("mark_api.radar_watch.RadarWatch", return_value=manager), patch.object(
            self.center, "dialog", side_effect=["watch"]
        ), patch.object(self.center, "message") as message, patch.object(
            manager, "watch"
        ) as watcher:
            self.center.bargains()
        watcher.assert_not_called()
        self.assertIn("keine Suchprofile", message.call_args.args[0])

    def test_entrypoint_version_and_desktop_file_match(self):
        import tomllib
        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((root / "pyproject.toml").read_text())
        self.assertRegex(metadata["project"]["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(metadata["project"]["scripts"]["kleinanzeiger-center"], "kleinanzeiger.linux_center:main")
        self.assertIn(f"Kleinanzeiger {metadata['project']['version']}", mod.CHANGES)
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

    def test_background_release_check_offline_and_available(self):
        with patch.object(mod.linux_release_check, "latest_release", return_value="1.8.0") as lookup, \
             patch.object(mod, "version", return_value="1.7.1"):
            self.center.begin_release_check()
            self.center._release_thread.join(timeout=2)
            self.assertTrue(self.center._release_checked)
            self.assertIn("1.8.0", self.center._update_caption())
            self.center.begin_release_check()
            lookup.assert_called_once()

    def test_updates_open_official_release_only_on_user_yes(self):
        self.center._release_checked = True
        self.center._release_latest = "1.8.0"
        with patch.object(mod, "version", return_value="1.7.1"), \
             patch.object(self.center, "dialog", return_value=None), \
             patch.object(mod.subprocess, "run") as launch:
            self.center.show_updates()
        launch.assert_not_called()
        with patch.object(mod, "version", return_value="1.7.1"), \
             patch.object(self.center, "dialog", return_value=""), \
             patch.object(mod.shutil, "which", return_value="/usr/bin/xdg-open"), \
             patch.object(mod.subprocess, "run") as launch:
            self.center.show_updates()
        self.assertEqual(launch.call_args.args[0][-1],
                         mod.linux_release_check.release_url("1.8.0"))
        self.assertFalse(launch.call_args.kwargs["shell"])

    def test_update_check_does_not_block_offline_operations(self):
        self.center._release_checked = True
        self.center._release_latest = None
        with patch.object(mod, "version", return_value="1.7.1"), patch.object(
            self.center, "message"
        ) as display:
            self.center.show_updates()
        self.assertIn("nicht möglich", display.call_args.args[0])
        self.assertIn("\n", display.call_args.args[0])
        self.assertIn("Schnäppchenjäger", mod.MANUAL)
        self.assertIn("Hintergrund", mod.MANUAL)


if __name__ == "__main__":
    unittest.main()
