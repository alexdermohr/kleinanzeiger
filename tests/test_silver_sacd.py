"""Synthetic mail fixtures only. No platform access or real user units."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mark_api import radar_watch
from mark_api.bargain_radar import run
from mark_api.search_mail import load_search_mail
from mark_api.silver_sacd import CATEGORY, LABEL, SEARCH_URL, rejection, model_key, assess

MODELS = ['Marantz SA-11S1', 'T+A D 1260 R', 'Denon DCD-1500AE',
          'Yamaha CD-S1000', 'Onkyo C-S5VL', 'Sony SCD-XB790', 'Pioneer PD-50']


class SilverSacdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = radar_watch.RadarWatch(self.root / 'data', self.root / 'units')
        self.profile = self.manager.create_category()
        self.inbox = self.profile.parent / 'suchmails'
        self.now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)

    def mail(self, uid=100, title='Marantz SA-11S1 SACD-Player silber', price=1000,
             instant=None, date=True, url=None):
        msg = EmailMessage()
        msg['From'] = 'hinweise@kleinanzeigen.de'
        if date:
            msg['Date'] = format_datetime(instant or self.now)
        msg.set_content(f'{title}\n{price} EUR\n' +
                        (url or f'https://www.kleinanzeigen.de/s-anzeige/player/{uid}') + '\n')
        source = self.root / 'export.eml'
        source.write_bytes(msg.as_bytes())
        return self.manager.deliver(self.profile, source)

    def test_seven_distinct_models_and_silver_stems(self):
        for i, model in enumerate(MODELS):
            color = ['silber', 'silver', 'silberfarben', 'silberner', 'silver-finish'][i % 5]
            with self.subTest(model=model):
                title = f'{model} SACD-Player {color}'
                self.assertIsNone(rejection(title))
                self.mail(100+i, title)
        rows, diagnostics = load_search_mail(self.inbox, LABEL, category=CATEGORY)
        self.assertEqual(len(rows), 7)
        self.assertFalse(diagnostics)

    def test_real_marketplace_title_forms_without_cross_model_pricing(self):
        # Current public search-title shapes; they are not proof of
        # actual playback, seller integrity, image colour or live availability.
        accepted = [
            'T+A T + A SADV 1245 R High End CD/SACD-Player in silber',
            'Denon DCD 1700 ne SACD Silber',
            'Denon DCD-3000NE SACD-Player Silber - B-Ware!',
            'SACD-Player Yamaha CDS-3000 in silber.',
            'SACD-Player Yamaha CDS-3000 mit CDs in silber',
            'Marantz SA-15S2 SACD-Player silber/gold wie neu, OVP',
            'Denon DVD-3910 - CD SACD Player - Fast High-End - Silber -',
            'ONKYO DV-SP 503 E - hochwertiger DVD/CD/SACD Spieler - SILBER',
        ]
        for title in accepted:
            with self.subTest(title=title):
                self.assertIsNone(rejection(title))
        for title in (
            'Marantz ND8006 - CD- und Netzwerkplayer - SACD-Qualität - Silber',
            'DCD-800 NE CD Player SACD-Qualität silber',
            'Sony DSD Streaming DAC ohne SACD Player silber',
        ):
            with self.subTest(rejected=title):
                self.assertIsNotNone(rejection(title))

    def test_model_keys_conservatively_normalize_known_hifi_variants(self):
        self.assertEqual(model_key("Denon DCD 1700 NE SACD Silber"), "denon:DCD1700NE")
        self.assertEqual(model_key("Denon DCD-1700NE SACD Player silber"), "denon:DCD1700NE")
        self.assertNotEqual(model_key("Denon DCD-1700AE SACD Player silber"),
                            model_key("Denon DCD-1700NE SACD Player silber"))
        self.assertEqual(model_key("ONKYO DV-SP 503 E SACD Spieler SILBER"), "onkyo:DVSP503E")
        self.assertEqual(model_key("Yamaha CD-S1000 SACD silber"), "yamaha:CDS1000")
        self.assertEqual(model_key("Yamaha CDS-3000 SACD silber"), "yamaha:CDS3000")
        self.assertIsNone(model_key("SACD Player silber, Modell unbekannt"))

    def test_spaced_s1_and_s2_generations_never_share_price_peers(self):
        from mark_api.bargain_hunter import normalize_offer
        now = datetime.now(timezone.utc).isoformat()
        s1 = "Marantz SA-15 S1 SACD Player silber"
        s2 = "Marantz SA-15 S2 SACD Player silber"
        self.assertEqual(model_key(s1), "marantz:SA15S1")
        self.assertEqual(model_key("Marantz SA-15S1 SACD Player silber"), model_key(s1))
        self.assertEqual(model_key(s2), "marantz:SA15S2")
        self.assertNotEqual(model_key(s1), model_key(s2))
        observed = [
            normalize_offer(dict(
                id=str(9900000 + i), title=s1 if i == 0 else s2,
                price_eur=str(400 if i == 0 else 1000),
                url=f"https://www.kleinanzeigen.de/s-anzeige/player/{9900000 + i}",
                source="offline fixture", observed_at=now, condition="unbekannt"
            )) for i in range(5)
        ]
        assessed = assess(observed)
        self.assertEqual(len(assessed), 5)
        self.assertEqual(
            next(row for row in assessed if row["id"] == "9900000")["classification"],
            "nicht bewertbar",
        )
        self.assertTrue(all(row["peer_count"] == 0 for row in assessed))

    def test_price_reference_excludes_old_or_different_model_peers(self):
        from mark_api.bargain_hunter import normalize_offer
        now = datetime.now(timezone.utc).replace(microsecond=0)
        def offer(index, title, price, observed=now):
            return normalize_offer(dict(
                id=str(8800000 + index), title=title, price_eur=str(price),
                url=f"https://www.kleinanzeigen.de/s-anzeige/player/{8800000 + index}",
                source="offline fixture", observed_at=observed.isoformat(),
                condition="unbekannt"))
        cheap = offer(1, "Denon DCD-1700NE SACD Player silber", 400)
        same = [offer(i, "Denon DCD 1700 NE SACD Player silber", 1000 + 100*i)
                for i in range(2, 6)]
        old_peer = offer(6, "Denon DCD-1700NE SACD Player silber", 2500,
                         now - timedelta(days=5))
        different_variant = offer(7, "Denon DCD-1700AE SACD Player silber", 9000)
        other_maker = offer(8, "Marantz DCD-1700NE SACD Player silber", 8000)
        all_rows = assess([cheap, *same, old_peer, different_variant, other_maker])
        result = next(item for item in all_rows if item["id"] == cheap.uid)
        self.assertEqual(result["classification"], "auffällig günstig")
        self.assertEqual(result["peer_count"], 4)
        self.assertEqual(result["peer_median_eur"], "1350.00")
        self.assertEqual(result["model_key"], "denon:DCD1700NE")
        self.assertEqual(next(item for item in all_rows if item["id"] == old_peer.uid)["peer_count"], 0)
        self.assertEqual(next(item for item in all_rows if item["id"] == different_variant.uid)["peer_count"], 0)
        self.assertEqual(next(item for item in all_rows if item["id"] == other_maker.uid)["peer_count"], 0)

    def test_player_with_included_or_missing_accessories(self):
        for title in (
            'Marantz SA-11S1 SACD-Player silber mit Fernbedienung',
            'Marantz SA-11S1 SACD-Player silber ohne Fernbedienung',
            'Marantz SA-11S1 SACD-Player silber inkl. Verpackung',
            'T+A SADV 1245 R SACD-Player silber samt Kabel',
        ):
            with self.subTest(title=title):
                self.assertIsNone(rejection(title))
        for title in (
            'Fernbedienung für Marantz SA-11S1 SACD-Player silber',
            'Marantz SA-11S1 SACD-Player silber Zubehör',
            'Marantz SA-11S1 SACD-Player silber mit Fernbedienung defekt',
        ):
            with self.subTest(rejected=title):
                self.assertIsNotNone(rejection(title))

    def test_negative_titles(self):
        for title in ['Marantz ND8006 SACD Player silber', 'Marantz ND-8006 silber Qualität SACD Player',
                      'CD Player silber', 'DSD Stream-only SACD Player silber',
                      'SACD Player schwarz', 'SACD Player möglicherweise silber?',
                      'SACD Player', 'SACD Player nicht silber', 'SACD Player silber oder schwarz',
                      'SACD Disk silber', 'SACD Disc für Player silber',
                      'Fernbedienung für SACD Player silber', 'SACD Player silber Zubehör',
                      'SACD Player silber defekt', 'SACD Player silber kaputt',
                      'SACD Player silber ohne Funktion', 'SACD Player silber gesucht', 'Lautsprecher SACD Player silber',
                      'SACD Player silber CD-only', 'SACD Player silber ohne SACD']:
            with self.subTest(title=title):
                self.assertIsNotNone(rejection(title))

    def test_missing_color_has_diagnostic(self):
        self.mail(title='Sony SCD-XB790 SACD Player')
        result = run(self.profile)
        self.assertEqual(result['imported'], 0)
        self.assertIn('Silberangabe', result['diagnostics'][0]['reason'])

    def test_category_never_calls_model_assessment(self):
        self.mail()
        with patch('mark_api.bargain_radar.assess_offers') as assessor:
            self.assertEqual(run(self.profile)['candidate_events'], 1)
        assessor.assert_not_called()

    def test_mail_security_contract(self):
        self.mail(100, date=False)
        self.mail(101, instant=self.now-timedelta(days=4))
        self.mail(102, url='https://www.kleinanzeigen.de/s-anzeige/player/102?tracking=1')
        result = run(self.profile)
        self.assertEqual(result['imported'], 0)
        self.assertEqual(len(result['diagnostics']), 3)

    def test_idempotent_profile_cli_isolated_from_same_label_model(self):
        config = json.loads(self.profile.read_text())
        self.assertEqual(config['category'], CATEGORY)
        self.assertTrue(config['notify'])
        self.assertEqual(config['search_url'], SEARCH_URL)
        self.assertEqual(self.profile.parent.name, hashlib.sha256(('category:'+CATEGORY).encode()).hexdigest())
        exact = self.manager.create(LABEL)
        self.assertNotEqual(exact, self.profile)
        run(self.profile)
        before = {p: p.read_bytes() for p in self.profile.parent.iterdir() if p.is_file()}
        with patch.object(radar_watch, 'RadarWatch', return_value=self.manager), patch('builtins.print'):
            for _ in range(2):
                self.assertEqual(radar_watch.main(['create-silver-sacd']), 0)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(len(self.manager.profiles()), 2)

    def test_report_notifier_dedupe_price_change_and_no_false_bargain(self):
        for i, model in enumerate(MODELS):
            self.mail(100+i, f'{model} SACD Player silber', 100 if i == 0 else 9000)
        with patch('mark_api.bargain_radar.shutil.which', return_value='/mock/notify-send'), patch(
                'mark_api.bargain_radar.subprocess.run') as notify:
            first = run(self.profile)
            self.assertEqual((first['new'], first['candidate_events'], first['hits']), (7, 7, 0))
            notify.assert_called_once()
            self.assertIn('passende silberne SACD-Player', notify.call_args.args[0][-1])
            self.assertNotIn('günstig', notify.call_args.args[0][-1])
            page = (self.profile.parent / 'bericht.html').read_text()
            self.assertIn('nicht bewertbar', page)
            self.assertNotIn('Vergleichsmedian', page)
            self.assertNotIn('rabatt', page.casefold())
            for offer in first['offers']:
                self.assertEqual(offer['classification'], 'nicht bewertbar')
                self.assertIsNone(offer['peer_median_eur'])
                self.assertIsNone(offer['discount_pct'])
                self.assertEqual(offer['peer_count'], 0)
            original = self.mail(100, f'{MODELS[0]} SACD Player silber', 100)
            self.assertEqual(self.mail(100, f'{MODELS[0]} SACD Player silber', 100), original)
            repeat = run(self.profile)
            self.assertEqual((repeat['new'], repeat['candidate_events'], repeat['hits']), (0, 0, 0))
            self.assertEqual(repeat['offers'], [])
            self.assertEqual(repeat['known_candidate_count'], 7)
            page = (self.profile.parent / 'bericht.html').read_text()
            self.assertIn(MODELS[0], page)
            self.assertIn('Verfügbarkeit nicht erneut geprüft', page)
            self.assertIn('7 historisch beobachtete Kandidaten', page)
            self.assertNotIn('7 passende Geräteänderungen', page)
            self.assertIn('7 historisch beobachtete Kandidaten', self.manager.local_status(self.profile))
            self.assertEqual(notify.call_count, 1)
            self.mail(100, f'{MODELS[0]} SACD Player silber', 80, self.now+timedelta(minutes=1))
            changed = run(self.profile)
            self.assertEqual((changed['price_changes'], changed['candidate_events'], changed['hits']), (1, 1, 0))
            self.assertEqual(next(x for x in changed['known_candidates'] if x['id'] == '100')['price_eur'], '80.00')
            changed_html = (self.profile.parent / 'bericht.html').read_text()
            self.assertIn('Preis-/Versandänderung:', changed_html)
            self.assertIn('zuvor Preis 100.00 EUR', changed_html)
            self.assertIn('jetzt Preis 80.00 EUR', changed_html)
            self.assertEqual(notify.call_count, 2)
            self.assertEqual(json.loads((self.profile.parent/'bericht.json').read_text()), changed)
            self.assertEqual(run(self.profile)['offers'], [])

    def test_empty_inbox_retains_observations_and_status(self):
        self.mail()
        run(self.profile)
        for item in self.inbox.iterdir():
            item.unlink()
        result = run(self.profile)
        self.assertEqual((result['candidate_events'], result['known_candidate_count']), (0, 1))
        status = self.manager.local_status(self.profile)
        self.assertIn('Keine EML-Dateien; keine Angebote', status)
        self.assertIn('Letzte lokale Auswertung:', status)
        self.assertIn('bericht.html', status)
        self.assertIn('kein neuer Marktplatzfeed', status)

    def test_category_cannot_switch_to_model_history_or_exports(self):
        run(self.profile)
        config = json.loads(self.profile.read_text())
        del config['category']
        self.profile.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'eigene Historie'):
            run(self.profile)
        config.update(category=CATEGORY, input_type='exports')
        self.profile.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'Kategorie benötigt EML'):
            run(self.profile)

    def test_status_stop_no_real_services_and_unit_contract(self):
        content = list(self.manager.unit_content(self.profile).values())
        self.assertIn('PathChanged=', content[0])
        self.assertIn('PathModified=', content[0])
        self.assertIn('Type=oneshot', content[1])
        self.assertNotIn('timer', ''.join(content))
        with patch.object(radar_watch.subprocess, 'run', return_value=subprocess.CompletedProcess([], 3)) as invoke:
            for action in ('status', 'stop'):
                self.assertIn('gestoppt', self.manager.watch(self.profile, action))
        self.assertTrue(all('is-active' in c.args[0] for c in invoke.call_args_list))
        self.assertFalse(list(self.manager.units.glob('*.service')))


if __name__ == '__main__':
    unittest.main()
