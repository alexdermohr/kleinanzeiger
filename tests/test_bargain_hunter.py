"""Tests for the isolated buyer-side analysis; no network or real accounts."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mark_api.bargain_hunter import (
    DealInputError, assess_offers, euro, load_offers, matches_model,
    normalize_offer, safe_listing_url, search_url,
)
from mark_api.bargain_hunter_cli import main, render_report

MODEL = "Lenovo ThinkPad T14 Gen 3"


def row(index, price, *, title=None, extra=None):
    offer = {
        "id": str(1234567800 + index),
        "title": title or f"Lenovo ThinkPad T14 Gen 3 i5 16GB SSD Variante {index}",
        "price": {"amount": price, "currency_code": "EUR"},
        "ad_url": f"https://www.kleinanzeigen.de/s-anzeige/lenovo-t14-gen-3/{1234567800+index}",
        "observed_at": "2026-11-08T08:00:00+01:00",
    }
    if extra:
        offer.update(extra)
    return offer


class DealEngineTests(unittest.TestCase):
    def test_euro_parsing_and_missing(self):
        self.assertEqual(euro("1.299,50 €"), Decimal("1299.50"))
        self.assertEqual(euro("19.90 EUR"), Decimal("19.90"))
        self.assertIsNone(euro(None))
        self.assertEqual(euro(0), Decimal("0.00"))
        for value in (True, float("nan"), "-5", "1e5", "0 EUR teuer",
                      "1,200.55", "99999999999999"):
            with self.subTest(value=value), self.assertRaises(DealInputError):
                euro(value)

    def test_kleinanzeigen_links_only(self):
        ok = "https://www.kleinanzeigen.de/s-anzeige/rad/1234567890"
        self.assertEqual(safe_listing_url(ok), ok)
        for url in ("http://www.kleinanzeigen.de/s-anzeige/a/1",
                    "javascript:alert(1)",
                    "https://kleinanzeigen.de.evil.test/s-anzeige/a/1",
                    "https://user:pass@www.kleinanzeigen.de/s-anzeige/a/1",
                    "https://www.kleinanzeigen.de:443/s-anzeige/a/1",
                    "https://www.kleinanzeigen.de:ungültig/s-anzeige/a/1",
                    "https://www.kleinanzeigen.de/s-anzeige/a\n/1",
                    "https://www.kleinanzeigen.de/meine-anzeigen"):
            with self.subTest(url=url), self.assertRaises(DealInputError):
                safe_listing_url(url)

    def test_model_generation_must_match(self):
        self.assertTrue(matches_model("Lenovo ThinkPad T14 Gen 3 16GB", MODEL))
        self.assertFalse(matches_model("Lenovo ThinkPad T14 Gen 1 3 GHz", MODEL))
        self.assertFalse(matches_model("Lenovo ThinkPad E16 Gen 3", MODEL))
        self.assertFalse(matches_model("ThinkPad T14 3. Generation 16GB", MODEL))  # conservative nonmatch
        self.assertFalse(matches_model("Lenovo ThinkPad T14 Gen 3 defekt", MODEL))
        self.assertFalse(matches_model("Lenovo ThinkPad T14 Gen 3 Ersatzteile", MODEL))

    def test_distinct_iphone_editions(self):
        self.assertTrue(matches_model("Apple iPhone 14 Pro 128GB", "iPhone 14 Pro"))
        self.assertFalse(matches_model("Apple iPhone 14 Pro Max 128GB", "iPhone 14 Pro"))
        self.assertFalse(matches_model("Apple iPhone 14 Plus", "iPhone 14"))

    def test_discount_median_with_independent_peers(self):
        offers = [normalize_offer(row(0, 100))]
        offers += [normalize_offer(row(i, 200 + i * 10)) for i in range(1, 6)]
        offers += [normalize_offer(row(8, 1, title="Lenovo ThinkPad T14 Gen 1"))]
        report = assess_offers(offers, MODEL)
        best = next(r for r in report if r["id"] == str(1234567800))
        self.assertEqual(best["classification"], "auffällig günstig")
        self.assertEqual(best["peer_count"], 5)
        self.assertEqual(best["peer_median_eur"], "230.00")
        self.assertEqual(best["discount_pct"], "56.5")
        self.assertNotIn(str(1234567808), [r["id"] for r in report])

    def test_no_false_claim_when_peers_insufficient(self):
        offers = [normalize_offer(row(i, 100+i)) for i in range(3)]
        report = assess_offers(offers, MODEL)
        self.assertEqual(len(report), 3)
        self.assertTrue(all(r["classification"] == "nicht bewertbar" for r in report))
        self.assertTrue(all(r["peer_median_eur"] is None for r in report))

    def test_missing_price_cannot_be_best_deal(self):
        offers = [normalize_offer(row(0, None))]
        offers += [normalize_offer(row(i, 300+i)) for i in range(1, 7)]
        row0 = next(r for r in assess_offers(offers, MODEL) if r["id"] == str(1234567800))
        self.assertEqual(row0["classification"], "nicht bewertbar")

    def test_shipping_effect_and_uncertain_sources(self):
        offers = [normalize_offer(row(0, 100, extra={"shipping_eur": 20}))]
        offers += [normalize_offer(row(i, 200)) for i in range(1, 6)]
        result = assess_offers(offers, MODEL)[0]
        self.assertEqual(result["comparison_price_eur"], "120.00")
        self.assertIn("Angebotspreisen", " ".join(result["notes"]))
        self.assertIn("Zustand nicht belegt", result["notes"])

    def test_currency_is_not_assumed(self):
        for price in ({"amount": 99, "currency_code": "USD"}, "VB", -1):
            with self.subTest(price=price), self.assertRaises(DealInputError):
                normalize_offer(row(1, price))

    def test_csv_json_import_and_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            csv_path = folder / "beispiele.csv"
            csv_path.write_text(
                "id,title,price,url,observed_at\n"
                "1234567801,Lenovo ThinkPad T14 Gen 3,300,"
                "https://www.kleinanzeigen.de/s-anzeige/lenovo/1234567801,2026-11-08\n"
                "1234567801,Lenovo ThinkPad T14 Gen 3,290,"
                "https://www.kleinanzeigen.de/s-anzeige/lenovo/1234567801,2026-11-09\n",
                encoding="utf-8",
            )
            result = load_offers(csv_path)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].price_eur, Decimal("290.00"))
            json_path = folder / "beispiele.json"
            json_path.write_text(json.dumps({"success": True, "data": {"ads": [row(4, 170)]}}),
                                 encoding="utf-8")
            self.assertEqual(load_offers(json_path)[0].price_eur, Decimal("170.00"))

    def test_invalid_input_is_not_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            file = folder / "a.json"
            for text in ("[", '{"data":{"ads":"text"}}', '[{"id":"x"}]'):
                file.write_text(text, encoding="utf-8")
                with self.assertRaises(DealInputError):
                    load_offers(file)
            file.write_bytes(b" " * 8_000_001)
            with self.assertRaises(DealInputError):
                load_offers(file)

    def test_html_escapes_untrusted_title_and_source(self):
        value = normalize_offer(row(1, 99, title="<script>alert(1)</script>",
                                    extra={"source": '<svg onload="alert(1)">' }))
        result = assess_offers([value], "script")
        page = render_report("script", result, generated_at="Test")
        self.assertNotIn("<script>", page)
        self.assertNotIn("<svg onload", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&lt;svg", page)

    def test_manual_search_link(self):
        link = search_url("ThinkPad T14 Gen 3", maximum_eur=500)
        self.assertIn("keywords=ThinkPad+T14+Gen+3", link)
        self.assertIn("maxPrice=500", link)
        for bad in (0, -10, True):
            with self.assertRaises(DealInputError):
                search_url("Laptop", maximum_eur=bad)

    def test_cli_writes_local_report_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "angebote.json"
            report = folder / "schnäppchen.html"
            source.write_text(json.dumps([row(i, 100+i) for i in range(6)]),
                              encoding="utf-8")
            self.assertEqual(main(["--datei", str(source), "--modell", MODEL,
                                   "--bericht", str(report)]), 0)
            self.assertIn("Schnäppchenjäger", report.read_text(encoding="utf-8"))
            self.assertIn("Angebotspreisen", report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()