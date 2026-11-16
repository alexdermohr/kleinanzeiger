"""Synthetic end-to-end radar runs, with real SQLite and CLI entrypoint."""
import contextlib
from contextlib import closing
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mark_api.bargain_hunter_cli import main
from mark_api.bargain_radar import run


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'input'
        self.source.mkdir()
        self.config = self.root / 'radar.json'
        self.config.write_text(json.dumps(dict(model='Lenovo ThinkPad T14 Gen 3',
            input='input', history='radar.sqlite', report='report.html', json_report='report.json')))
        self.rows = [dict(id=str(i), title='Lenovo ThinkPad T14 Gen 3 <script>alert(1)</script>',
            price=100 if i == 0 else 300, source='<svg onload="bad">',
            observed_at='2026-11-16T10:00:00Z',
            url=f'https://www.kleinanzeigen.de/s-anzeige/test/{i}') for i in range(6)]
        self.write()

    def write(self):
        (self.source / 'list.json').write_text(json.dumps(self.rows))

    def cli(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(['--radar', str(self.config)])

    def result(self):
        return json.loads((self.root / 'report.json').read_text())

    def test_first_repeat_price_change_and_stale_replay(self):
        self.assertEqual(self.cli(), 0)
        self.assertEqual((self.result()['new'], self.result()['hits']), (6, 1))
        self.assertEqual(self.cli(), 0)
        self.assertEqual((self.result()['new'], self.result()['price_changes'], self.result()['hits']), (0, 0, 0))
        self.rows[0].update(price=80, observed_at='2026-11-16T11:00:00Z')
        self.write()
        self.assertEqual(self.cli(), 0)
        event = self.result()['offers'][0]
        self.assertEqual(event['previous_price_eur'], '100.00')
        self.assertEqual(event['event'], 'price_changed')
        self.assertEqual(self.result()['hits'], 1)
        self.rows[0].update(price=100, observed_at='2026-11-16T10:00:00Z')
        self.write()
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['price_changes'], 0)

    def test_unrelated_offer_does_not_consume_profile_identity(self):
        extra = dict(self.rows[0], id='elsewhere', title='iPhone 14 128GB',
                     price=150, url='https://www.kleinanzeigen.de/s-anzeige/test/elsewhere')
        self.rows.append(extra)
        self.write()
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['new'], 6)
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['new'], 0)
        extra.update(title='Lenovo ThinkPad T14 Gen 3', observed_at='2026-11-16T11:00:00Z')
        self.write()
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['new'], 1)
        self.assertEqual(self.result()['offers'][0]['id'], 'elsewhere')

    def test_price_cycle_and_shipping_change(self):
        self.assertEqual(self.cli(), 0)
        for price, shipping, time in ((80, '0', '11'), (100, '0', '12'), (100, '7', '13')):
            self.rows[0].update(price=price, shipping_eur=shipping,
                                observed_at=f'2026-11-16T{time}:00:00Z')
            self.write()
            self.assertEqual(self.cli(), 0)
            self.assertEqual(self.result()['price_changes'], 1)
            self.assertEqual(self.cli(), 0)
            self.assertEqual(self.result()['offers'], [])

    def test_equal_timestamp_price_conflict_rolls_back(self):
        self.assertEqual(self.cli(), 0)
        self.rows[0]['price'] = 80
        self.write()
        self.assertEqual(self.cli(), 2)
        self.rows[0]['observed_at'] = '2026-11-16T11:00:00Z'
        self.write()
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['price_changes'], 1)

    def test_html_and_missing_peers(self):
        self.rows = self.rows[:1]
        self.write()
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['hits'], 0)
        self.assertEqual(self.result()['offers'][0]['classification'], 'nicht bewertbar')
        page = (self.root / 'report.html').read_text()
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('&lt;svg', page)
        self.assertNotIn('<script>', page)
        self.assertNotIn('<svg', page)

    def test_broken_input_does_not_consume_events(self):
        (self.source / 'bad.csv').write_text('id,title,price,url\n1,"broken')
        self.assertEqual(self.cli(), 2)
        self.assertFalse((self.root / 'radar.sqlite').exists())
        (self.source / 'bad.csv').unlink()
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['new'], 6)
        (self.source / 'list.json').write_text('[invalid')
        self.assertEqual(self.cli(), 2)
        self.write()
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['new'], 0)

    def test_foreign_database_is_unchanged(self):
        dbpath = self.root / 'radar.sqlite'
        with closing(sqlite3.connect(dbpath)) as db, db:
            db.execute('CREATE TABLE seller (secret TEXT)')
            db.execute("INSERT INTO seller VALUES ('private')")
        before = dbpath.read_bytes()
        self.assertEqual(self.cli(), 2)
        self.assertEqual(dbpath.read_bytes(), before)

    def test_real_lock_in_other_process_then_retry(self):
        self.assertEqual(self.cli(), 0)
        with closing(sqlite3.connect(self.root / 'radar.sqlite')) as db, db:
            db.execute('BEGIN IMMEDIATE')
            command = [sys.executable, '-m', 'mark_api.bargain_hunter_cli', '--radar', str(self.config)]
            import os
            result = subprocess.run(command, env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')},
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn('locked', result.stderr)
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['new'], 0)

    def test_output_failure_rolls_back(self):
        self.assertEqual(self.cli(), 0)
        self.rows[0].update(price=80, observed_at='2026-11-16T11:00:00Z')
        self.write()
        with patch('mark_api.bargain_radar.atomic_report', side_effect=OSError('disk full')):
            self.assertEqual(self.cli(), 2)
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['price_changes'], 1)

    def test_conflicting_archive_and_output_collision(self):
        (self.source / 'other.json').write_text(json.dumps([dict(self.rows[0], price=50)]))
        self.assertEqual(self.cli(), 2)
        (self.source / 'other.json').unlink()
        config = json.loads(self.config.read_text())
        config['report'] = 'input/list.json'
        self.config.write_text(json.dumps(config))
        self.assertEqual(self.cli(), 2)

    def test_single_file_and_optional_json(self):
        config = json.loads(self.config.read_text())
        config['input'] = 'input/list.json'
        del config['json_report']
        self.config.write_text(json.dumps(config))
        self.assertEqual(self.cli(), 0)
        self.assertTrue((self.root / 'report.html').is_file())
        self.assertFalse((self.root / 'report.json').exists())

    def test_stale_peers_are_excluded(self):
        self.assertEqual(self.cli(), 0)
        for item in self.rows[1:]:
            item.update(price=400, observed_at='2026-11-16T11:00:00Z')
        self.write()
        self.assertEqual(self.cli(), 0)
        for item in self.rows[1:]:
            item.update(price=300, observed_at='2026-11-16T10:00:00Z')
        self.rows[0].update(price=80, observed_at='2026-11-16T12:00:00Z')
        self.write()
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['hits'], 0)
        self.assertEqual(self.result()['offers'][0]['peer_count'], 0)

    def test_first_report_failure_can_retry(self):
        with patch('mark_api.bargain_radar.atomic_report', side_effect=OSError('disk full')):
            self.assertEqual(self.cli(), 2)
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['new'], 6)

    def test_profile_change_requires_separate_history(self):
        self.assertEqual(self.cli(), 0)
        config = json.loads(self.config.read_text())
        config['model'] = 'iPhone 14'
        self.config.write_text(json.dumps(config))
        self.assertEqual(self.cli(), 2)

    def test_csv_folder_and_shipping_change(self):
        (self.source / 'list.json').unlink()
        import csv
        with (self.source / 'list.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['new'], 6)
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.result()['offers'], [])


if __name__ == '__main__':
    unittest.main()
