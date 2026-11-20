"""Offline fixtures for the public, rate-limited SACD HTML pathway.

No tests call Kleinanzeigen or modify real user systemd services.
"""
import json
from itertools import count
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mark_api.bargain_hunter import DealInputError
from mark_api.public_sacd import fetch_html, parse_search_html, run_public, _SameHostRedirect, _trusted_https_context
from mark_api.radar_watch import RadarWatch

GOOD = "Denon DCD-1700NE SACD Player silber"
GOOD2 = "Marantz SA-15S1 SACD Player Silber"
GOOD3 = "Pioneer DV-668AV-S SACD Player Silber"


def card(uid, title=GOOD, price="450 €", path=None):
    path = path if path is not None else f"/s-anzeige/sacd-player/{uid}-172-511"
    return (f'<li><article class="flex" data-adid="{uid}" data-href="{path}">'
            f'<div><h3 class="heading"><a href="{path}">{title}</a></h3>'
            f'<p class="text-title3 font-strong text-secondary">{price}</p>'
            '</div></article></li>')


def page(*cards):
    return '<html><ul id="srchrslt-adtable">' + "".join(cards) + '</ul></html>'


class PublicSacdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = RadarWatch(self.root / "data", self.root / "units")
        self.profile = self.manager.create_public_category()
        clock = patch('mark_api.public_sacd._attempt_time', side_effect=count(1800000000, 1801))
        clock.start()
        self.addCleanup(clock.stop)

    def test_tls_keeps_existing_os_trust_store(self):
        context = Mock()
        context.get_ca_certs.return_value = [{"subject": "preinstalled trust"}]
        with patch("mark_api.public_sacd.ssl.create_default_context", return_value=context), \
             patch("mark_api.public_sacd.Path.is_file") as exists:
            self.assertIs(_trusted_https_context(), context)
        context.load_verify_locations.assert_not_called()
        exists.assert_not_called()

    def test_tls_uses_system_ca_when_isolated_python_lacks_it(self):
        context = Mock()
        context.get_ca_certs.side_effect = [[], [{"subject": "NixOS roots"}]]
        with patch("mark_api.public_sacd.ssl.create_default_context", return_value=context), \
             patch("mark_api.public_sacd.Path.is_file", return_value=True):
            self.assertIs(_trusted_https_context(), context)
        context.load_verify_locations.assert_called_once_with(
            cafile="/etc/ssl/certs/ca-certificates.crt")

    def test_tls_missing_system_ca_fails_closed(self):
        context = Mock()
        context.get_ca_certs.return_value = []
        with patch("mark_api.public_sacd.ssl.create_default_context", return_value=context), \
             patch("mark_api.public_sacd.Path.is_file", return_value=False), \
             patch("mark_api.public_sacd.Path.is_dir", return_value=False), \
             self.assertRaisesRegex(DealInputError, "Zertifikatsbündel"):
            _trusted_https_context()
        context.load_verify_locations.assert_not_called()

    def test_tls_untrusted_bundle_still_fails_closed(self):
        context = Mock()
        context.get_ca_certs.return_value = []
        with patch("mark_api.public_sacd.ssl.create_default_context", return_value=context), \
             patch("mark_api.public_sacd.Path.is_file", return_value=True), \
             patch("mark_api.public_sacd.Path.is_dir", return_value=False), \
             self.assertRaisesRegex(DealInputError, "Zertifikatsbündel"):
            _trusted_https_context()
        context.load_verify_locations.assert_called_once()

    def test_tls_directory_only_store_defers_trust_to_verified_handshake(self):
        # CA directory entries may be loaded lazily during a TLS handshake.
        # A zero-count pre-handshake certificate list does not make that store invalid.
        context = Mock()
        context.get_ca_certs.return_value = []
        with patch("mark_api.public_sacd.ssl.create_default_context", return_value=context), \
             patch("mark_api.public_sacd.Path.is_file", return_value=False), \
             patch("mark_api.public_sacd.Path.is_dir", return_value=True), \
             patch("mark_api.public_sacd.ssl.get_default_verify_paths") as defaults:
            defaults.return_value.capath = "/fake/openssl-hashed-ca-directory"
            self.assertIs(_trusted_https_context(), context)
        context.load_verify_locations.assert_not_called()

    def test_tls_empty_default_and_missing_directory_rejected(self):
        context = Mock()
        context.get_ca_certs.return_value = []
        with patch("mark_api.public_sacd.ssl.create_default_context", return_value=context), \
             patch("mark_api.public_sacd.Path.is_file", return_value=False), \
             patch("mark_api.public_sacd.ssl.get_default_verify_paths") as defaults, \
             self.assertRaisesRegex(DealInputError, "Zertifikatsbündel"):
            defaults.return_value.capath = None
            _trusted_https_context()

    def test_parser_decodes_prices_and_rejects_unwanted(self):
        html = page(card("1234567", GOOD, "1.250 €"),
                    card("1234568", GOOD2, "400 € VB"),
                    card("1234569", "Marantz ND8006 CD SACD-Qualität silber", "300 €"),
                    card("1234570", "Sony SACD Player schwarz", "22 €"),
                    card("1234571", GOOD3, "499,50 €"),
                    card("1234572", "CD Player silber", "12 €"))
        rows, stats = parse_search_html(html, observed_at="2026-11-19T07:00:00+00:00")
        self.assertEqual(stats, {"visible_cards": 6, "accepted_fixed_price": 2,
                                 "ignored_or_unpriced": 4})
        self.assertEqual([x["price_eur"] for x in rows], ["1250", "499.50"])
        self.assertTrue(all(x["url"].startswith("https://www.kleinanzeigen.de/s-anzeige/") for x in rows))
        self.assertTrue(all(x["shipping_eur"] is None for x in rows))

    def test_bad_html_and_links_are_not_successful_empty_results(self):
        for html in ('<html>Bitte anmelden</html>', page(),
                     '<html><ul id="srchrslt-adtable"><li>unbekannt</li></ul></html>',
                     page(card("1234567", path="/s-anzeige/test/9999999-172-123")),
                     page(card("1234567", path="https://evil.example/1234567")),
                     page(card("1234567"), card("1234567")),
                     page(card("1234567", price=""))):
            with self.subTest(html=html[:110]), self.assertRaises(DealInputError):
                parse_search_html(html)

    def test_first_scan_is_baseline_then_one_price_change(self):
        text = page(card("1234567", GOOD, "350 €"),
                    card("1234568", GOOD2, "700 €"))
        with patch("mark_api.bargain_radar.shutil.which", return_value=None):
            baseline = run_public(self.profile, fetcher=lambda _: text)
            self.assertEqual((baseline["candidate_events"],baseline["known_candidate_count"]), (0,2))
            self.assertEqual(baseline["new"], 0)
            repeat = run_public(self.profile, fetcher=lambda _: text)
            self.assertEqual((repeat["candidate_events"],repeat["price_changes"]), (0,0))
            changed = run_public(self.profile, fetcher=lambda _: text.replace("350 €", "250 €"))
            self.assertEqual((changed["candidate_events"],changed["price_changes"]), (1,1))
            self.assertEqual(changed["offers"][0]["previous_price_eur"], "350.00")
            self.assertEqual(changed["offers"][0]["price_eur"], "250.00")
            no_replay = run_public(self.profile, fetcher=lambda _: text.replace("350 €", "250 €"))
            self.assertEqual(no_replay["candidate_events"], 0)
        self.assertEqual(no_replay["known_candidate_count"],2)
        report=(self.profile.parent/"bericht.html").read_text(encoding="utf-8")
        self.assertIn("Historisch beobachtet",report)
        self.assertIn("keine modellpreisbewertung",report.casefold())
        self.assertEqual(json.loads((self.profile.parent/"bericht.json").read_text())["known_candidate_count"],2)

    def test_report_lists_lowest_observed_asking_price_first(self):
        html = page(card("1234567", GOOD, "950 €"),
                    card("1234568", GOOD2, "200 €"),
                    card("1234569", GOOD3, "450 €"))
        with patch("mark_api.bargain_radar.shutil.which", return_value=None):
            result = run_public(self.profile, fetcher=lambda _: html)
        self.assertEqual([row["id"] for row in result["known_candidates"]],
                         ["1234568", "1234569", "1234567"])
        self.assertEqual(result["hits"], 0)
        self.assertTrue(all(row["peer_median_eur"] is None for row in result["known_candidates"]))
        page_html = (self.profile.parent / "bericht.html").read_text()
        self.assertLess(page_html.index(GOOD2), page_html.index(GOOD3))
        self.assertLess(page_html.index(GOOD3), page_html.index(GOOD))

    def test_failed_initial_report_does_not_raise_false_first_alert_on_retry(self):
        # The database file survives a failure inside the first transaction,
        # but no committed report/run history does; retry must still baseline.
        original = page(card("1234567", GOOD, "350 €"))
        with patch("mark_api.bargain_radar.atomic_report",
                   side_effect=OSError("disk full")), patch(
                       "mark_api.bargain_radar.shutil.which", return_value=None
                   ), self.assertRaises(OSError):
            run_public(self.profile, fetcher=lambda _: original)
        self.assertTrue((self.profile.parent / "history.sqlite").is_file())
        with patch("mark_api.bargain_radar.shutil.which", return_value=None):
            result = run_public(self.profile, fetcher=lambda _: original)
        self.assertEqual((result["new"],result["candidate_events"],result["known_candidate_count"]),(0,0,1))
        self.assertEqual(result["offers"],[])
        self.assertEqual(result["hits"],0)

    def test_failures_retain_last_report_and_snapshot(self):
        data=page(card("1234567"))
        with patch("mark_api.bargain_radar.shutil.which", return_value=None):
            run_public(self.profile,fetcher=lambda _:data)
        originals={p:p.read_bytes() for p in
                   (self.profile.parent/"angebote.json",self.profile.parent/"bericht.html",
                    self.profile.parent/"bericht.json")}
        for error in (lambda _: (_ for _ in ()).throw(DealInputError("HTTP 403")),
                      lambda _: "<html>blocked</html>"):
            with self.subTest(error=error), self.assertRaises(DealInputError):
                run_public(self.profile,fetcher=error)
            self.assertEqual({p:p.read_bytes() for p in originals}, originals)
        self.assertEqual(json.loads((self.profile.parent/"bericht.json").read_text())["candidate_events"],0)

    def test_symlink_snapshot_not_followed(self):
        good=page(card("1234567"))
        with patch("mark_api.bargain_radar.shutil.which", return_value=None):
            run_public(self.profile, fetcher=lambda _: good)
        target=self.root/"unrelated.json"
        target.write_text('["foreign"]')
        snapshot=self.profile.parent/"angebote.json"
        snapshot.unlink()
        snapshot.symlink_to(target)
        with self.assertRaises(ValueError):
            run_public(self.profile, fetcher=lambda _: good)
        self.assertEqual(target.read_text(),'["foreign"]')

    def test_renderer_escapes_untrusted_text(self):
        text=page(card("1234567", GOOD + " &lt;script&gt;alert(1)&lt;/script&gt;"))
        with patch("mark_api.bargain_radar.shutil.which", return_value=None):
            run_public(self.profile, fetcher=lambda _: text)
        report=(self.profile.parent/"bericht.html").read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;",report)
        self.assertNotIn("<script>alert(1)</script>",report)

    def test_public_profile_and_timer_isolated_from_mail(self):
        old=self.manager.create_category()
        self.assertNotEqual(old,self.profile)
        self.assertEqual(self.manager.create_public_category(),self.profile)
        self.assertEqual(self.manager.profiles(),[(old,"SACD-Player silber"),
                      (self.profile,"SACD-Player silber (direkte Suche)")])
        unit=self.manager.unit_content(self.profile)
        self.assertEqual(len(unit),2)
        trigger,service=unit
        self.assertTrue(trigger.endswith(".timer"))
        self.assertIn("OnUnitActiveSec=30min",unit[trigger])
        self.assertIn("WantedBy=timers.target",unit[trigger])
        self.assertIn("mark_api.public_sacd",unit[service])
        self.assertNotIn("PathModified",unit[trigger])
        self.assertIn("PathModified",self.manager.unit_content(old)[next(iter(self.manager.unit_content(old)))])

    def test_public_start_stop_readable_and_keeps_units(self):
        active=False
        def fake(argv,**kw):
            nonlocal active
            if argv[:2] == ["systemctl","--user"]:
                if "enable" in argv: active=True
                if "disable" in argv: active=False
                return subprocess.CompletedProcess(argv,(0 if active else 3) if "is-active" in argv else 0)
            return subprocess.CompletedProcess(argv,0)
        with patch("mark_api.radar_watch.subprocess.run",side_effect=fake):
            self.assertIn("öffentliche Suchseite",self.manager.watch(self.profile,"start"))
            self.assertIn("Monitoring aktiv",self.manager.watch(self.profile,"status"))
            self.assertIn("gestoppt",self.manager.watch(self.profile,"stop"))

    def test_redirects_never_leave_public_search_host(self):
        request=__import__("urllib.request",fromlist=["Request"]).Request(
            "https://www.kleinanzeigen.de/s-sacd-player-silber/k0")
        for url in ("https://evil.example/s-abc","http://www.kleinanzeigen.de/s-abc",
                    "https://www.kleinanzeigen.de/login",
                    "https://www.kleinanzeigen.de.evil.example/s-abc"):
            with self.subTest(url=url), self.assertRaises(DealInputError):
                _SameHostRedirect().redirect_request(request,None,302,"go",{},url)
        self.assertIsNotNone(_SameHostRedirect().redirect_request(request,None,302,"go",{},
                               "https://www.kleinanzeigen.de/s-sacd-player-silber/k0"))


    def test_sequential_public_fetches_are_rate_limited(self):
        good = page(card("1234567"))
        calls = []
        def fetch(_):
            calls.append(1)
            return good
        with patch("mark_api.public_sacd._attempt_time",
                   side_effect=[1800000000, 1800000001, 1800001801, 1800001802, 1800003602]):
            run_public(self.profile, fetcher=fetch)
            with self.assertRaisesRegex(DealInputError, "30 Minuten"):
                run_public(self.profile, fetcher=fetch)
            with self.assertRaisesRegex(DealInputError, "HTTP 403"):
                run_public(self.profile, fetcher=lambda _: (_ for _ in ()).throw(DealInputError("HTTP 403")))
            with self.assertRaisesRegex(DealInputError, "30 Minuten"):
                run_public(self.profile, fetcher=fetch)
            run_public(self.profile, fetcher=fetch)
        self.assertEqual(calls, [1, 1])

    def test_timer_and_manual_scan_cannot_overlap(self):
        from mark_api.public_sacd import _single_public_run
        from mark_api.bargain_hunter import DealInputError
        with _single_public_run(self.profile):
            with self.assertRaisesRegex(DealInputError, "läuft bereits"):
                run_public(self.profile, fetcher=lambda _: page(card("1234567")))

    def test_public_kde_button_does_not_ask_for_a_model(self):
        from kleinanzeiger.linux_center import Center
        center = Center("/usr/bin/kdialog", db=self.root / "no-db")
        with patch("mark_api.radar_watch.RadarWatch", return_value=self.manager), \
             patch.object(center, "text"), patch.object(center, "dialog", return_value=None) as dialog:
            center.radar_monitoring("public-sacd")
        self.assertEqual(dialog.call_count, 1)
        self.assertEqual(dialog.call_args.args[0], "--yesno")
        self.assertEqual(self.manager.create_public_category(), self.profile)

    def test_input_url_not_configurable(self):
        with self.assertRaises(DealInputError):
            fetch_html("http://127.0.0.1:9000")
        original=json.loads(self.profile.read_text(encoding="utf-8"))
        changed={**original,"search_url":"http://127.0.0.1/"}
        self.profile.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            run_public(self.profile,fetcher=lambda _:page(card("1234567")))

if __name__=="__main__":
    unittest.main()