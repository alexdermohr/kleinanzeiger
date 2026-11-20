"""Local fixtures and simulated systemctl only; never enable actual user services."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import tempfile
import sys
import unittest
from unittest.mock import patch
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mark_api import radar_watch as mod
from mark_api.bargain_radar import run


@unittest.skipUnless(sys.platform == "linux", "Linux profile and systemd contracts")
class RadarWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = mod.RadarWatch(self.root / 'data', self.root / 'units')
        self.profile = self.manager.create('Testmodell 42')

    def test_model_profiles_sorted_and_invalid_ignored(self):
        self.assertEqual(self.manager.profiles(), [(self.profile, "Testmodell 42")])
        second = self.manager.create("Anderes Modell 13")
        self.assertEqual(self.manager.profiles(), [(second, "Anderes Modell 13"),
                                                  (self.profile, "Testmodell 42")])
        untrusted = self.manager.data / "unexpected"
        untrusted.mkdir()
        (untrusted / "profil.json").write_text('{"model":"Hacked"}')
        self.assertEqual(len(self.manager.profiles()), 2)

    def test_create_existing_separate_and_private(self):
        before = self.profile.read_bytes()
        self.assertEqual(self.manager.create(' Testmodell 42 '), self.profile)
        self.assertEqual(self.profile.read_bytes(), before)
        self.assertNotEqual(self.manager.create('Testmodell 43'), self.profile)
        self.assertTrue(json.loads(before)['notify'])
        self.assertEqual(self.profile.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.profile.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(list((self.profile.parent / 'suchmails').iterdir()), [])
        self.assertFalse((self.profile.parent / 'history.sqlite').exists())

    def test_personal_targets_are_atomic_private_removable_and_preserve_profile(self):
        profile = self.manager.create_public_category()
        original = profile.read_bytes()
        self.assertEqual(self.manager.price_targets(profile), {})
        self.assertEqual(self.manager.set_price_target(profile, "denon:DCD1700NE", "250,00"),
                         {"denon:DCD1700NE": "250.00"})
        self.assertEqual(self.manager.set_price_target(profile, "yamaha:CDS1000", "800"),
                         {"denon:DCD1700NE": "250.00", "yamaha:CDS1000": "800.00"})
        self.assertEqual(self.manager.create_public_category(), profile)
        self.assertEqual(profile.read_bytes(), original)
        target = profile.parent / "preisziele.json"
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(target.read_text())["targets"], self.manager.price_targets(profile))
        self.assertEqual(self.manager.set_price_target(profile, "denon:DCD1700NE", None),
                         {"yamaha:CDS1000": "800.00"})
        unchanged = target.read_bytes()
        for value in ("0", "-5", "NaN", "1e5", "100001", "abc", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.manager.set_price_target(profile, "yamaha:CDS1000", value)
            self.assertEqual(target.read_bytes(), unchanged)
        with self.assertRaises(ValueError):
            self.manager.set_price_target(profile, "invalid-model", "200")
        self.assertEqual(target.read_bytes(), unchanged)
        with self.assertRaises(ValueError):
            self.manager.set_price_target(self.profile, "yamaha:CDS1000", "200")

    def test_existing_config_and_sqlite_never_overwritten(self):
        self.profile.write_text('{}')
        with self.assertRaises(ValueError):
            self.manager.create('Testmodell 42')
        self.assertEqual(self.profile.read_text(), '{}')
        other = self.manager.create('Andere 42')
        db = other.parent / 'history.sqlite'
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute('CREATE TABLE foreign_data (value TEXT)')
        original = db.read_bytes()
        self.manager.create('Andere 42')
        self.assertEqual(db.read_bytes(), original)
        with self.assertRaises(ValueError):
            run(other)
        self.assertEqual(db.read_bytes(), original)

    def test_collision_and_existing_file(self):
        with patch.object(mod.hashlib, 'sha256') as digest:
            digest.return_value.hexdigest.return_value = self.profile.parent.name
            with self.assertRaises(ValueError):
                self.manager.create('Anderes Modell')
        digest = hashlib.sha256(b'Existing').hexdigest()
        target = self.manager.data / digest
        target.write_text('unchanged')
        with self.assertRaises((ValueError, OSError)):
            self.manager.create('Existing')
        self.assertEqual(target.read_text(), 'unchanged')

    def test_validation_and_malicious_literal_identity(self):
        for value in ('', ' ', 'x', 'ab\ncd', 'ab\x00', 'a' * 201, '; $()'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.manager.create(value)
        value = '../../Model; $(touch INJECTED) `%h` "'
        profile = self.manager.create(value)
        self.assertEqual(profile.parent.parent, self.manager.data)
        self.assertEqual(json.loads(profile.read_text())['model'], value)
        self.assertNotIn('INJECTED', ''.join(self.manager.unit_content(profile).values()))

    def test_symlinks_all_managed_paths(self):
        for path in (self.profile, self.profile.parent / 'suchmails'):
            saved = path.with_name(path.name + '.saved')
            path.rename(saved)
            path.symlink_to(saved, target_is_directory=saved.is_dir())
            with self.assertRaises(ValueError):
                self.manager.load(self.profile)
            path.unlink()
            saved.rename(path)
        report = self.profile.parent / 'bericht.html'
        report.symlink_to(self.root / 'absent')
        with self.assertRaises(ValueError):
            self.manager.create('Testmodell 42')

    def test_delivery_hash_idempotent_distinct_content_same_name(self):
        source = self.root / 'mail.eml'
        source.write_bytes(b'local export')
        target = self.manager.deliver(self.profile, source)
        self.assertEqual(target.name, hashlib.sha256(source.read_bytes()).hexdigest() + '.eml')
        stamp = target.stat().st_mtime_ns
        self.assertEqual(self.manager.deliver(self.profile, source), target)
        self.assertEqual(target.stat().st_mtime_ns, stamp)
        source.write_bytes(b'changed export')
        second = self.manager.deliver(self.profile, source)
        self.assertNotEqual(target, second)
        self.assertEqual(target.read_bytes(), b'local export')
        self.assertEqual(source.read_bytes(), b'changed export')
        self.assertFalse(list(self.profile.parent.glob('.delivery-*')))

    def test_delivery_invalid_sources_and_target_symlink(self):
        for name, content in (('empty', b''), ('large', b'x' * 1000001)):
            path = self.root / name
            path.write_bytes(content)
            with self.assertRaises(ValueError):
                self.manager.deliver(self.profile, path)
        source = self.root / 'source'
        source.write_bytes(b'hello')
        link = self.root / 'link'
        link.symlink_to(source)
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        for path in (link, fifo, self.root / 'missing', self.root):
            with self.assertRaises((ValueError, OSError)):
                self.manager.deliver(self.profile, path)
        target = self.profile.parent / 'suchmails' / (hashlib.sha256(b'hello').hexdigest() + '.eml')
        target.symlink_to(source)
        with self.assertRaises(ValueError):
            self.manager.deliver(self.profile, source)

    def test_delivery_hash_conflict_never_overwrites(self):
        source = self.root / 'export'
        source.write_bytes(b'exported mail')
        target = self.profile.parent / 'suchmails' / (hashlib.sha256(source.read_bytes()).hexdigest() + '.eml')
        target.write_bytes(b'foreign content')
        with self.assertRaisesRegex(ValueError, 'Zustellkonflikt'):
            self.manager.deliver(self.profile, source)
        self.assertEqual(target.read_bytes(), b'foreign content')

    def test_unit_symlink_never_calls_systemctl(self):
        self.manager.units.mkdir()
        target = self.root / 'foreign'
        target.write_text('foreign unit')
        unit = self.manager.units / next(iter(self.manager.unit_content(self.profile)))
        unit.symlink_to(target)
        with patch.object(mod.subprocess, 'run') as invoked, self.assertRaises(ValueError):
            self.manager.watch(self.profile, 'stop')
        invoked.assert_not_called()
        self.assertEqual(target.read_text(), 'foreign unit')

    def test_atomic_publication_is_complete(self):
        source = self.root / 'mail'
        source.write_bytes(b'finished mail')
        link = os.link
        def publish(src, dst):
            self.assertEqual(Path(src).read_bytes(), source.read_bytes())
            self.assertNotEqual(Path(src).parent, Path(dst).parent)
            self.assertFalse(Path(dst).exists())
            link(src, dst)
        with patch.object(mod.os, 'link', side_effect=publish):
            self.manager.deliver(self.profile, source)

    def test_unit_contract_and_escaping(self):
        contents = self.manager.unit_content(self.profile)
        path, service = contents.values()
        self.assertIn('PathChanged=', path)
        self.assertIn('PathModified=', path)
        self.assertIn('WantedBy=default.target', path)
        self.assertNotIn('timer', path + service)
        self.assertIn('Type=oneshot', service)
        self.assertIn('StartLimitIntervalSec=0', service)
        self.assertIn('StandardOutput=null', service)
        self.assertIn('StandardError=journal', service)
        self.assertIn('"-I" "-m" "mark_api.bargain_hunter_cli" "--radar"', service)
        self.assertIn('PATH=%h/.nix-profile/bin:/run/current-system/sw/bin:/usr/bin:/bin', service)
        self.assertEqual(mod.quoted('a %$"\\\n', command=True), '"a\\x20%%$$\\x22\\x5c\\x0a"')

    def test_executable_dollars_are_literal_but_argument_dollars_are_escaped(self):
        with patch.object(mod.sys, 'executable', '/tmp/Python % $/python'):
            service = list(self.manager.unit_content(self.profile).values())[1]
        self.assertIn('ExecStart="/tmp/Python\\x20%%\\x20$/python"', service)
        self.assertNotIn('$$/python', service)

    def test_watch_enable_status_stop_reenable_and_no_shell(self):
        active = False
        calls = []
        def fake(argv, **kwargs):
            nonlocal active
            calls.append(argv)
            self.assertFalse(kwargs['shell'])
            if argv[0] != 'systemctl':
                return subprocess.CompletedProcess(argv, 0)
            if 'enable' in argv:
                active = True
            if 'disable' in argv:
                active = False
            return subprocess.CompletedProcess(argv, (0 if active else 3) if 'is-active' in argv else 0)
        with patch.object(mod.subprocess, 'run', side_effect=fake):
            self.assertIn('gestoppt', self.manager.watch(self.profile, 'stop'))
            for _ in range(2):
                self.assertIn('aktiv', self.manager.watch(self.profile, 'start'))
                self.assertIn('aktiv', self.manager.watch(self.profile, 'status'))
            originals = {p: p.read_bytes() for p in self.manager.units.glob('*.service')}
            for _ in range(2):
                self.assertIn('gestoppt', self.manager.watch(self.profile, 'stop'))
            self.manager.watch(self.profile, 'start')
        self.assertTrue(any('stop' in c and c[-1].endswith('.service') for c in calls))
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)

    def test_foreign_unit_and_failed_start_rollback(self):
        def fake(argv, **kwargs):
            code = 1 if 'enable' in argv else (3 if 'is-active' in argv else 0)
            return subprocess.CompletedProcess(argv, code)
        with patch.object(mod.subprocess, 'run', side_effect=fake) as invoked:
            with self.assertRaises(ValueError):
                self.manager.watch(self.profile, 'start')
            self.assertFalse(list(self.manager.units.glob('*.path')))
            self.assertTrue(any('disable' in c.args[0] for c in invoked.call_args_list))
        unit = self.manager.units / next(iter(self.manager.unit_content(self.profile)))
        unit.write_text('foreign')
        for action in ('start', 'status', 'stop'):
            with patch.object(mod.subprocess, 'run') as invoked, self.assertRaises(ValueError):
                self.manager.watch(self.profile, action)
            invoked.assert_not_called()
        self.assertEqual(unit.read_text(), 'foreign')

    def test_successful_enable_but_inactive_path_rolls_back(self):
        def fake(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 3 if 'is-active' in argv else 0)
        with patch.object(mod.subprocess, 'run', side_effect=fake), self.assertRaisesRegex(ValueError, 'nicht aktiv'):
            self.manager.watch(self.profile, 'start')
        self.assertFalse(list(self.manager.units.glob('*.path')))

    def test_timeout_rollback_still_removes_created_units(self):
        def fake(argv, **kwargs):
            if 'enable' in argv or 'disable' in argv:
                raise subprocess.TimeoutExpired(argv, 45)
            return subprocess.CompletedProcess(argv, 3 if 'is-active' in argv else 0)
        with patch.object(mod.subprocess, 'run', side_effect=fake), self.assertRaisesRegex(ValueError, 'Rollback unvollständig'):
            self.manager.watch(self.profile, 'start')
        self.assertFalse(list(self.manager.units.glob('*.path')))
        self.assertFalse(list(self.manager.units.glob('*.service')))

    def test_path_setting_is_literal_and_rejects_control_characters(self):
        self.assertEqual(mod.path_setting(Path('/tmp/a b%$"\\c/inbox')), '/tmp/a b%%$"\\c/inbox')
        with self.assertRaises(ValueError):
            mod.path_setting(Path('/tmp/new\nline/inbox'))

    def test_cli_create_delivery_and_invalid_model(self):
        with patch.object(mod, 'RadarWatch', return_value=self.manager), patch('builtins.print'):
            self.assertEqual(mod.main(['create', 'Testmodell 42']), 0)
            source = self.root / 'export'
            source.write_bytes(b'local exported mail')
            self.assertEqual(mod.main(['deliver', str(self.profile), str(source)]), 0)
            self.assertEqual(mod.main(['create', ' ']), 2)

    def test_installed_probe_failure_creates_no_units(self):
        with patch.object(mod.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(ValueError, 'installieren'):
                self.manager.watch(self.profile, 'start')
        self.assertFalse(list(self.manager.units.glob('*.service')))

    def test_delivered_mail_actual_first_repeat_price_change(self):
        now = datetime.now(timezone.utc) - timedelta(minutes=5)
        source = self.root / 'export.eml'
        def mail(i, price, instant):
            msg = EmailMessage()
            msg['From'] = 'hinweise@kleinanzeigen.de'
            msg['Date'] = format_datetime(instant)
            msg.set_content(f'Testmodell 42\n{price} EUR\nhttps://www.kleinanzeigen.de/s-anzeige/test/{100+i}\n')
            source.write_bytes(msg.as_bytes())
            self.manager.deliver(self.profile, source)
        for i in range(5):
            mail(i, 100 if i == 0 else 300, now)
        unknown = self.profile.parent / 'suchmails' / 'unknown.eml'
        unknown.write_bytes(b'unsupported unknown mail')
        with patch('mark_api.bargain_radar.shutil.which', return_value=None):
            first = run(self.profile)
            repeat = run(self.profile)
            mail(0, 80, now + timedelta(minutes=1))
            changed = run(self.profile)
        self.assertEqual((first['new'], first['hits']), (5, 1))
        self.assertEqual((repeat['new'], repeat['price_changes'], repeat['hits']), (0, 0, 0))
        self.assertEqual((changed['price_changes'], changed['hits']), (1, 1))
        self.assertTrue(first['diagnostics'])
        self.assertEqual(unknown.read_bytes(), b'unsupported unknown mail')
        self.assertTrue((self.profile.parent / 'bericht.html').exists())
        self.assertEqual(json.loads((self.profile.parent / 'bericht.json').read_text())['price_changes'], 1)


if __name__ == '__main__':
    unittest.main()