"""Synthetic layouts only; no real saved-search templates or mail accounts."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mark_api.bargain_radar import run
from mark_api.bargain_hunter_cli import main


class SearchMailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'suchmails').mkdir()
        self.profile = self.root / 'profile.json'
        example = Path(__file__).resolve().parents[1] / 'examples/search-mail-radar.json'
        self.config = json.loads(example.read_text())
        self.config['notify'] = True
        self.profile.write_text(json.dumps(self.config))
        self.now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
        for i in range(5):
            self.mail(i, 100 if i == 0 else 300, html=i == 1, multipart=i == 2)

    def mail(self, i, price, html=False, multipart=False, title='Lenovo ThinkPad T14 Gen 3', url=None, approved=True, age=0):
        msg = EmailMessage()
        msg['From'] = 'Kleinanzeigen <hinweise@kleinanzeigen.de>' if approved else 'official@evil.invalid'
        msg['Date'] = format_datetime(self.now - timedelta(hours=age))
        url = url or f'https://www.kleinanzeigen.de/s-anzeige/test/{100+i}'
        plain = f'{title}\n{price} EUR\n{url}\n'
        markup = f'<a href="{url}">{title}</a><p>{price} EUR</p>'
        msg.set_content(markup if html else plain, subtype='html' if html else 'plain')
        if multipart:
            msg.add_alternative(markup, subtype='html')
        raw = msg.as_bytes()
        name = f'{i}.eml'
        (self.root / 'suchmails' / name).write_bytes(raw)

    def test_e2e_repeat_drop_notify_and_readonly(self):
        before = {p: p.read_bytes() for p in (self.root / 'suchmails').iterdir()}
        with patch('mark_api.bargain_radar.shutil.which', return_value='/usr/bin/notify-send'), patch('mark_api.bargain_radar.subprocess.run') as notify:
            self.assertEqual(run(self.profile)['hits'], 1)
            self.assertEqual(notify.call_count, 1)
            self.assertEqual(run(self.profile)['hits'], 0)
            self.assertEqual(notify.call_count, 1)
            self.now += timedelta(minutes=1)
            self.mail(0, 80)
            self.assertEqual(run(self.profile)['price_changes'], 1)
            self.assertEqual(notify.call_count, 2)
        for p, raw in before.items():
            if p.name != '0.eml':
                self.assertEqual(p.read_bytes(), raw)

    def test_spoof_stale_tracking_wrong_group_free_and_incomplete(self):
        for kwargs in (dict(approved=False), dict(age=72), dict(url='https://tracker.invalid/a'),
                       dict(title='Hülle Lenovo ThinkPad T14 Gen 3'), dict(price='VB'), dict(price=0),
                       dict(url='https://www.kleinanzeigen.de/s-anzeige/test/100?tracking=1')):
            with self.subTest(kwargs=kwargs):
                self.mail(0, **({'price': 100} | kwargs))
                result = run(self.profile)
                self.assertEqual(result['hits'], 0)
                self.assertTrue(result['diagnostics'])
        self.mail(0, 100, title='')
        self.assertTrue(run(self.profile)['diagnostics'])

    def test_duplicates_conflict_and_defender_failure(self):
        self.mail(9, 100, url='https://www.kleinanzeigen.de/s-anzeige/test/100')
        self.assertEqual(run(self.profile)['imported'], 5)
        self.mail(9, 90, url='https://www.kleinanzeigen.de/s-anzeige/test/100')
        with self.assertRaises(ValueError):
            run(self.profile)
        self.mail(9, 100, url='https://www.kleinanzeigen.de/s-anzeige/test/100')
        with patch('mark_api.bargain_radar.atomic_report', side_effect=PermissionError('Defender denied')):
            self.assertEqual(main(['--radar', str(self.profile)]), 2)

    def test_old_inbox_files_do_not_permanently_block_new_mail(self):
        import os
        old = (self.now - timedelta(days=4)).timestamp()
        for i in range(102):
            stale = self.root / "suchmails" / f"old-{i}.eml"
            stale.write_text("old")
            os.utime(stale, (old, old))
        result = run(self.profile)
        self.assertEqual(result["new"], 5)
        self.assertEqual(result["hits"], 1)
        self.assertTrue(any("102 alte EML" in note["reason"] for note in result["diagnostics"]))

    def test_disappearing_mail_during_age_scan_is_diagnostic(self):
        from mark_api.search_mail import load_search_mail

        self.mail(9, 25)
        stat = Path.stat
        calls = 0

        def disappearing(path, *args, **kwargs):
            nonlocal calls
            if path.name == '9.eml':
                calls += 1
                if calls == 2:
                    raise FileNotFoundError('EML was moved during age check')
            return stat(path, *args, **kwargs)

        with patch.object(Path, 'stat', disappearing):
            rows, diagnostics = load_search_mail(
                self.root / 'suchmails', self.config['model'])
        self.assertEqual(len(rows), 5)
        self.assertTrue(any(item['file'] == '9.eml' for item in diagnostics))

    def test_mail_date_utc_overflow_diagnosed_without_blocking_valid_peers(self):
        # Valid RFC date syntax may overflow when timezone offset is normalized
        # into year 10000, before max_age_hours can reject it.
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = "alerts@kleinanzeigen.de"
        msg["Date"] = "Fri, 31 Dec 9999 23:59:59 -0100"
        msg.set_content(
            "Lenovo ThinkPad T14 Gen 3\\n10 EUR\\n"
            "https://www.kleinanzeigen.de/s-anzeige/thinkpad/9000"
        )
        (self.root / "suchmails" / "overflow.eml").write_bytes(msg.as_bytes())
        result = run(self.profile)
        self.assertEqual(result["new"], 5)
        self.assertEqual(result["hits"], 1)
        self.assertTrue(any(item["file"] == "overflow.eml" for item in result["diagnostics"]))
        self.assertEqual(run(self.profile)["new"], 0)

    def test_deeply_nested_mime_is_diagnostic_without_suppressing_valid_mails(self):
        # A legal-sized message with excessive MIME nesting exceeds Python's
        # recursion depth in email.parser before its part-count limit is reached.
        boundary_count = 1100
        lines = ['From: alerts@kleinanzeigen.de',
                 'Date: Mon, 16 Nov 2026 10:00:00 +0000',
                 'MIME-Version: 1.0',
                 'Content-Type: multipart/mixed; boundary="b0"', '']
        for i in range(boundary_count):
            lines += [f'--b{i}', 'Content-Type: multipart/mixed; boundary="b' + str(i+1) + '"', '']
        lines += [f'--b{boundary_count}', 'Content-Type: text/plain', '',
                  'Lenovo ThinkPad T14 Gen 3', '10 EUR',
                  'https://www.kleinanzeigen.de/s-anzeige/thinkpad/9000']
        for i in reversed(range(boundary_count + 1)):
            lines += [f'--b{i}--']
        raw = '\n'.join(lines).encode()
        self.assertLess(len(raw), 1_000_000)
        (self.root / 'suchmails/9.eml').write_bytes(raw)
        result = run(self.profile)
        self.assertEqual(result['new'], 5)
        self.assertEqual(result['hits'], 1)
        self.assertTrue(any(x['file']=='9.eml' for x in result['diagnostics']))

    def test_overlong_title_is_diagnostic_and_does_not_block_valid_peers(self):
        self.mail(9, 20, title="Lenovo ThinkPad T14 Gen 3 " + "X" * 400)
        result = run(self.profile)
        self.assertEqual(result["new"], 5)
        self.assertEqual(result["hits"], 1)
        self.assertTrue(any(item["file"] == "9.eml" for item in result["diagnostics"]))
        self.assertEqual(run(self.profile)["new"], 0)

    def test_empty_bounded_disabled_and_output_collision(self):
        for p in (self.root / 'suchmails').iterdir():
            p.unlink()
        self.assertEqual(run(self.profile)['hits'], 0)
        self.assertTrue(run(self.profile)['diagnostics'])
        self.mail(0, 100)
        (self.root / 'suchmails/0.eml').write_bytes(b'x' * 1_000_001)
        self.assertTrue(run(self.profile)['diagnostics'])
        self.config['report'] = 'suchmails/0.eml'
        self.profile.write_text(json.dumps(self.config))
        with self.assertRaises(ValueError):
            run(self.profile)

    def test_defender_first_run_retry_and_notifications_disabled(self):
        with patch('mark_api.bargain_radar.atomic_report', side_effect=PermissionError('Defender')):
            with self.assertRaises(PermissionError):
                run(self.profile)
        self.config['notify'] = False
        self.profile.write_text(json.dumps(self.config))
        with patch('mark_api.bargain_radar.subprocess.run') as notify:
            self.assertEqual(run(self.profile)['hits'], 1)
            notify.assert_not_called()

    def test_unverified_header_and_multipart_disagreement(self):
        msg = EmailMessage()
        msg['From'] = 'official@kleinanzeigen.de'
        msg['Date'] = format_datetime(self.now)
        msg['Authentication-Results'] = 'fake; dkim=pass'
        msg.set_content('Lenovo ThinkPad T14 Gen 3\n100 EUR\nhttps://www.kleinanzeigen.de/s-anzeige/t/100')
        msg.add_alternative('<a href="https://www.kleinanzeigen.de/s-anzeige/t/100">Lenovo ThinkPad T14 Gen 3</a><p>90 EUR</p>', subtype='html')
        raw = msg.as_bytes()
        (self.root / 'suchmails/0.eml').write_bytes(raw)
        self.assertTrue(run(self.profile)['diagnostics'])
        self.assertIn('Multipart', run(self.profile)['diagnostics'][0]['reason'])

    def test_html_ambiguity_currency_and_multipart_disagreement(self):
        p = self.root / 'suchmails/0.eml'
        for body in ('<a href="https://www.kleinanzeigen.de/s-anzeige/t/100">Lenovo ThinkPad T14 Gen 3</a><p>100 USD</p>',
                     '<script>alert(1)</script>', '<a href="x">A</a><a href="y">B</a>'):
            msg = EmailMessage()
            msg['From'] = 'alerts@kleinanzeigen.de'
            msg['Date'] = format_datetime(self.now)
            msg.set_content(body, subtype='html')
            raw = msg.as_bytes()
            p.write_bytes(raw)
            self.assertTrue(run(self.profile)['diagnostics'])


if __name__ == '__main__':
    unittest.main()
