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
