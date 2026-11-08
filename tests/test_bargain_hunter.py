"""Tests for the isolated buyer-side analysis; no network or real accounts."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mark_api import bargain_hunter
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
    def test_dedup_uses_utc_instants_not_iso_offset_text(self):
        earlier = row(0, 100, extra={"observed_at": "2026-11-08T09:00:00+01:00"})
        newer = row(0, 200, extra={"observed_at": "2026-11-08T08:30:00Z"})
        unknown = row(0, 900, extra={"observed_at": "nicht angegeben"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "offers.json"
            for records in ((earlier, newer), (newer, earlier),
                            (newer, unknown), (unknown, newer)):
                with self.subTest(observations=[r["observed_at"] for r in records]):
                    path.write_text(json.dumps(records), encoding="utf-8")
                    self.assertEqual(load_offers(path)[0].price_eur, Decimal("200.00"))
            path.write_text(json.dumps([unknown, earlier]), encoding="utf-8")
            self.assertEqual(load_offers(path)[0].price_eur, Decimal("100.00"))

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


    def test_cpu_editions_and_generations_do_not_inflate_price_medians(self):
        cases = (
            ("MacBook Pro 16", "MacBook Pro 16 M1 Pro 32GB RAM 1TB SSD",
             "MacBook Pro 16 M1 Max 32GB RAM 1TB SSD"),
            (MODEL, "Lenovo ThinkPad T14 Gen 3 i7-1165G7 16GB RAM 512GB SSD",
             "Lenovo ThinkPad T14 Gen 3 i7-1265G7 16GB RAM 512GB SSD"),
        )
        for model, candidate, expensive in cases:
            with self.subTest(model=model):
                offers = [normalize_offer(row(0, 1000, title=candidate))]
                offers += [normalize_offer(row(i, 2000, title=expensive))
                           for i in range(1, 5)]
                result = assess_offers(offers, model)
                cheap = next(r for r in result if r["id"] == str(1234567800))
                self.assertEqual(cheap["peer_count"], 0)
                self.assertEqual(cheap["classification"], "nicht bewertbar")

    def test_hdd_and_ssd_not_grouped_at_equal_capacities(self):
        model = "Dell Optiplex 7050"
        candidate = "Dell Optiplex 7050 i5 16GB RAM 500GB HDD"
        peer = "Dell Optiplex 7050 i5 16GB RAM 500GB SSD"
        offers = [normalize_offer(row(0, 100, title=candidate))]
        offers += [normalize_offer(row(i, 300, title=peer)) for i in range(1, 5)]
        assessed = assess_offers(offers, model)
        cheap = next(item for item in assessed if item["id"] == str(1234567800))
        self.assertEqual(cheap["peer_count"], 0)
        self.assertEqual(cheap["classification"], "nicht bewertbar")

    def test_report_explains_shipping_in_comparison_price(self):
        offers = [normalize_offer(row(0, 100, extra={"shipping_eur": "80"}))]
        offers += [normalize_offer(row(i, 200, extra={"shipping_eur": "0"}))
                   for i in range(1, 5)]
        evaluated = assess_offers(offers, MODEL)
        cheap = next(item for item in evaluated if item["id"] == str(1234567800))
        self.assertEqual(cheap["comparison_price_eur"], "180.00")
        self.assertEqual(cheap["discount_pct"], "10.0")
        html = render_report(MODEL, evaluated, generated_at="2026-11-08 11:00 UTC")
        self.assertIn("Vergleichspreis: 180.00 €", html)
        self.assertIn("Angebotspreis: 100.00 €", html)
        self.assertIn("Versand: 80.00 €", html)
        self.assertIn("10.0 % unter dem Angebotsmedian", html)

    def test_medium_first_storage_sizes_do_not_create_false_bargains(self):
        model = "Dell Optiplex 7050"
        candidate = "Dell Optiplex 7050 i5 16GB RAM SSD 1TB"
        peer = "Dell Optiplex 7050 i5 16GB RAM SSD 2TB"
        offers = [normalize_offer(row(0, 100, title=candidate))]
        offers += [normalize_offer(row(i, 400, title=peer)) for i in range(1, 5)]
        result = assess_offers(offers, model)
        cheap = next(item for item in result if item["id"] == str(1234567800))
        self.assertEqual(cheap["peer_count"], 0)
        self.assertEqual(cheap["classification"], "nicht bewertbar")

    def test_medium_first_and_capacity_first_matching_storage_are_comparable(self):
        model = "Dell Optiplex 7050"
        candidate = "Dell Optiplex 7050 i5 16GB RAM SSD 1TB"
        peer = "Dell Optiplex 7050 i5 16GB RAM 1TB SSD"
        offers = [normalize_offer(row(0, 100, title=candidate))]
        offers += [normalize_offer(row(i, 200, title=peer)) for i in range(1, 5)]
        result = assess_offers(offers, model)
        cheap = next(item for item in result if item["id"] == str(1234567800))
        self.assertEqual(cheap["peer_count"], 4)
        self.assertEqual(cheap["classification"], "auffällig günstig")

    def test_cpu_variant_mismatch_never_inflates_reference(self):
        offers = [normalize_offer(row(0, 100))]
        offers += [normalize_offer(row(i, 200)) for i in range(1, 5)]
        offers.append(normalize_offer(row(8, 9000, title="Lenovo ThinkPad T14 Gen 3 i7 16GB SSD")))
        best = next(r for r in assess_offers(offers, MODEL) if r["id"] == str(1234567800))
        self.assertEqual(best["peer_count"], 4)
        self.assertEqual(best["peer_median_eur"], "200.00")

    def test_explicit_ram_and_storage_variants_are_separate(self):
        sample = [
            normalize_offer(row(0, 100,
                title="Lenovo ThinkPad T14 Gen 3 i5 16GB RAM 512GB SSD")),
            *[
                normalize_offer(row(i, 300,
                    title=f"Lenovo ThinkPad T14 Gen 3 i5 32GB RAM 1TB SSD {i}"))
                for i in range(1, 7)
            ],
        ]
        best = next(r for r in assess_offers(sample, MODEL) if r["id"] == str(1234567800))
        self.assertEqual(best["classification"], "nicht bewertbar")
        self.assertEqual(best["peer_count"], 0)

    def test_accessories_are_neither_candidates_nor_peers(self):
        offers = [normalize_offer(row(i, 500, title="iPhone 14 128GB"))
                  for i in range(5)]
        for i, title in enumerate(("Hülle für iPhone 14", "iPhone 14 Ladekabel",
                                   "Panzerglas iPhone 14"), start=10):
            offers.append(normalize_offer(row(i, 5, title=title)))
        report = assess_offers(offers, "iPhone 14")
        self.assertEqual(len(report), 5)
        self.assertTrue(all(r["peer_count"] == 4 for r in report))

    def test_device_with_ovp_remains_a_candidate_and_peer(self):
        offers = [normalize_offer(row(i, 500, title="iPhone 14 128GB mit OVP"))
                  for i in range(5)]
        report = assess_offers(offers, "iPhone 14")
        self.assertEqual(len(report), 5)
        self.assertTrue(all(r["peer_count"] == 4 for r in report))
        self.assertTrue(all(r["peer_median_eur"] == "500.00" for r in report))
        self.assertFalse(matches_model("Hülle für iPhone 14", "iPhone 14"))

    def test_pixel_and_redmi_editions_are_separate(self):
        for model, edition in (("Pixel 9", "Pro"),
                               ("Pixel 9 Pro", "XL"),
                               ("Redmi Note 13", "Pro"),
                               ("Redmi Note 13 Pro", "Plus")):
            with self.subTest(model=model, edition=edition):
                title = f"{model} {edition} 128GB"
                self.assertTrue(matches_model(f"{model} 128GB", model))
                self.assertFalse(matches_model(title, model))
                self.assertTrue(matches_model(title, f"{model} {edition}"))

    def test_defective_condition_is_excluded_and_conditions_stay_separate(self):
        offers = [normalize_offer(row(0, 10, extra={"condition": "defekt"}))]
        offers += [normalize_offer(row(i, 200, extra={"condition": "gebraucht"}))
                   for i in range(1, 6)]
        offers += [normalize_offer(row(6, 20)),
                   normalize_offer(row(7, 1000, extra={"condition": "neu"}))]
        report = {r["id"]: r for r in assess_offers(offers, MODEL)}
        self.assertNotIn(str(1234567800), report)
        self.assertEqual(report[str(1234567801)]["peer_count"], 4)
        for i in (6, 7):
            self.assertEqual(report[str(1234567800 + i)]["peer_count"], 0)
            self.assertEqual(report[str(1234567800 + i)]["classification"], "nicht bewertbar")

    def test_accessory_bundles_are_devices_but_accessory_only_is_not(self):
        model = "iPhone 14"
        for title in ("Apple iPhone 14 128GB mit Hülle",
                      "iPhone 14 128GB inkl. Ladekabel",
                      "iPhone 14 128GB ohne Verpackung",
                      "Apple iPhone 14 128GB mit OVP"):
            with self.subTest(title=title):
                self.assertTrue(matches_model(title, model))
        for title in ("Hülle für iPhone 14", "iPhone 14 Hülle",
                      "iPhone 14 Ladekabel", "iPhone 14 Panzerglas"):
            with self.subTest(title=title):
                self.assertFalse(matches_model(title, model))
        offers = [normalize_offer(row(0, 100, title="iPhone 14 128GB mit Hülle"))]
        offers += [normalize_offer(row(i, 400, title=f"iPhone 14 128GB {i}"))
                   for i in range(1, 5)]
        result = assess_offers(offers, model)
        self.assertEqual(len(result), 5)
        self.assertEqual(next(r for r in result if r["id"] == str(1234567800))["peer_count"], 4)

    def test_symbolic_plus_editions_not_mixed_with_base(self):
        self.assertTrue(matches_model("Samsung Galaxy S23+ 128GB", "Galaxy S23+"))
        self.assertFalse(matches_model("Samsung Galaxy S23+ 128GB", "Galaxy S23"))
        self.assertFalse(matches_model("Samsung Galaxy S23 128GB", "Galaxy S23+"))
        self.assertTrue(matches_model("Samsung Galaxy S23 Plus 128GB", "Galaxy S23+"))
        offers = [normalize_offer(row(0, 100, title="Samsung Galaxy S23 128GB"))]
        offers += [normalize_offer(row(i, 400, title="Samsung Galaxy S23+ 128GB"))
                   for i in range(1, 6)]
        result = assess_offers(offers, "Galaxy S23")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["classification"], "nicht bewertbar")

    def test_galaxy_editions_are_separate(self):
        for suffix in ("Ultra", "Plus", "FE"):
            with self.subTest(suffix=suffix):
                self.assertFalse(matches_model(f"Samsung Galaxy S23 {suffix}", "Galaxy S23"))
        self.assertTrue(matches_model("Samsung Galaxy S23 Ultra 128GB", "Galaxy S23 Ultra"))
        offers = [normalize_offer(row(0, 100, title="Samsung Galaxy S23"))]
        offers += [normalize_offer(row(i, 900, title="Samsung Galaxy S23 Ultra"))
                   for i in range(1, 6)]
        result = assess_offers(offers, "Galaxy S23")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["classification"], "nicht bewertbar")

    def test_samsung_storage_variants_and_unknown_are_separate(self):
        offers = [normalize_offer(row(0, 100, title="Samsung S23 128GB"))]
        offers += [normalize_offer(row(i, 800, title="Samsung S23 512GB"))
                   for i in range(1, 6)]
        offers.append(normalize_offer(row(6, 900, title="Samsung S23")))
        report = {r["id"]: r for r in assess_offers(offers, "Samsung S23")}
        self.assertEqual(report[str(1234567800)]["peer_count"], 0)
        self.assertEqual(report[str(1234567806)]["peer_count"], 0)
        self.assertEqual(report[str(1234567801)]["peer_count"], 4)

    def test_10k_offers_prepare_variants_once_and_exclude_own_price(self):
        offers = [normalize_offer(row(i, i + 1)) for i in range(10_000)]
        with patch.object(bargain_hunter, "_variant_key",
                          wraps=bargain_hunter._variant_key) as variants:
            report = {r["id"]: r for r in assess_offers(offers, MODEL)}
        self.assertEqual(variants.call_count, 10_000)
        self.assertEqual(report[str(1234567800)]["peer_count"], 9999)
        self.assertEqual(report[str(1234567800)]["peer_median_eur"], "5001.00")
        self.assertEqual(report[str(1234577799)]["peer_median_eur"], "5000.00")

    def test_sorted_medians_match_reference_with_duplicate_ids_and_prices(self):
        import statistics

        offers = [normalize_offer(row(i // 2, price))
                  for i, price in enumerate((1, 3, 3, 3, 7, 9, 10, 11))]
        report = assess_offers(offers, MODEL)
        for result in report:
            peers = [o.comparison_price for o in offers if o.uid != result["id"]]
            self.assertEqual(result["peer_count"], len(peers))
            self.assertEqual(result["peer_median_eur"],
                             str(statistics.median(peers).quantize(Decimal("0.01"))))

    def test_nonfinite_discount_threshold_fails_explicitly(self):
        with self.assertRaises(DealInputError):
            assess_offers([], MODEL, discount_threshold=Decimal("NaN"))

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


    def test_malformed_csv_and_duplicate_columns_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.csv"
            for text in ('id,title,title,price,url\n1,A,B,3,x\n',
                         'id,title,price,url\n1,"unterbrochener Titel,3,x'):
                source.write_text(text, encoding="utf-8")
                with self.assertRaises(DealInputError):
                    load_offers(source)

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
