"""P1: source-provenanced drafts; 1000-row atomic CSV/Excel, zero marketplace effects."""
from __future__ import annotations

import base64
import csv
from contextlib import closing
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mark_api.portfolio_drafts import (
    DraftImportError, drafts_page, export_drafts, import_drafts, parse_drafts,
    preview_import,
)
from mark_api.portfolio_import_cli import main
from mark_api.storage import SnapshotStore


class ProductDraftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory(prefix="kleinanzeiger-p1-synthetic-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "ads.sqlite"
        self.store = SnapshotStore(self.db)

    def make_csv(self, count: int = 2, *, title: str = "Bücherregal") -> Path:
        file = self.root / "artikel.csv"
        with file.open("w", encoding="utf-8-sig", newline="") as output:
            out = csv.writer(output)
            out.writerow(["Artikelnummer", "Titel", "Beschreibung", "Kategorie",
                          "Preis", "Bilder"])
            for n in range(count):
                out.writerow([
                    f"SKU-{n:05d}", title + " " + str(n),
                    "Gebraucht, guter Zustand. \"Nord\"",
                    "Haus & Garten > Möbel", "49,00",
                    "vorn.jpg;detail.png",
                ])
        return file

    def test_thousand_rows_dryrun_atomic_commit_repeat_export_and_page(self) -> None:
        source = self.make_csv(1000)
        checked = preview_import(self.store, source)
        self.assertEqual((checked["total"], checked["new"], checked["conflict"]), (1000, 1000, 0))
        self.assertEqual(checked["mapping"]["sku"], "Artikelnummer")
        with closing(sqlite3.connect(self.db)) as raw:
            self.assertIsNone(raw.execute(
                "SELECT name FROM sqlite_master WHERE name='portfolio_drafts'"
            ).fetchone(), "dry-run must not mutate schema or data")
        done = import_drafts(self.store, source, checked["sha256"])
        self.assertEqual((done["inserted"], done["published"]), (1000, 0))
        self.assertEqual(drafts_page(self.store)["total"], 1000)
        first = drafts_page(self.store, limit=50)
        self.assertEqual(len(first["drafts"]), 50)
        self.assertEqual(first["drafts"][0]["status"], "draft")
        second = drafts_page(self.store, after=first["next_after"])
        self.assertEqual(second["drafts"][0]["sku"], "SKU-00050")
        self.assertEqual(drafts_page(self.store, search="999")["total"], 1)
        self.assertEqual(import_drafts(self.store, source, checked["sha256"])["unchanged"], 1000)
        self.assertEqual(preview_import(self.store, source)["new"], 0)
        exported = self.root / "export.csv"
        self.assertEqual(export_drafts(self.store, exported)["rows"], 1000)
        with exported.open("r", encoding="utf-8-sig", newline="") as inp:
            self.assertEqual(sum(1 for _ in csv.DictReader(inp)), 1000)
        exported_preview = preview_import(self.store, exported)
        self.assertEqual(exported_preview["unchanged"], 1000)
        self.assertEqual(exported_preview["new"], 0)
        # User-imported SKU is not a Kleinanzeigen ad ID or an owner assertion.
        self.assertEqual(self.store.tracked_ad_ids(), ())
        self.assertEqual(self.store.sync_status()["state"], "never_attempted")

    def test_german_excel_semicolon_csv_and_decimal_comma(self) -> None:
        filename = self.root / "deutscher-export.csv"
        with filename.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output, delimiter=";")
            writer.writerow(["Artikelnummer","Titel","Beschreibung","Kategorie","Preis","Bilder"])
            writer.writerow(["VASE-001", 'Vase "Łódź"', "Sehr guter Zustand",
                             "Haus & Garten > Dekoration", "29,00", "vorn.jpg;detail.png"])
        preview = preview_import(self.store, filename)
        self.assertEqual((preview["new"], preview["total"]), (1, 1))
        import_drafts(self.store, filename, preview["sha256"])
        self.assertEqual(drafts_page(self.store)["drafts"][0]["price_eur"], 29)
        self.assertEqual(drafts_page(self.store)["drafts"][0]["images"],
                         ["vorn.jpg", "detail.png"])

    def test_windows_crlf_and_formula_safe_csv_round_trip_is_reversible(self) -> None:
        source = self.root / "windows-lines.csv"
        original_title = "+ Kleiderständer"
        original_desc = "- guter Zustand\r\nAbholung möglich"
        with source.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output, lineterminator="\r\n")
            writer.writerow(["sku", "title", "description", "category", "price_eur"])
            writer.writerow(["ART-0001", original_title, original_desc,
                             "-Haus > Möbel", "30"])
        preview = preview_import(self.store, source)
        self.assertEqual(preview["total"], 1)
        import_drafts(self.store, source, preview["sha256"])
        draft = drafts_page(self.store)["drafts"][0]
        self.assertEqual(draft["description"], "- guter Zustand\nAbholung möglich")
        self.assertEqual(draft["title"], original_title)
        exported = self.root / "safe-out.csv"
        export_drafts(self.store, exported)
        with exported.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(rows[0]["title"].startswith("'+"))
        self.assertTrue(rows[0]["description"].startswith("'-"))
        self.assertEqual(preview_import(self.store, exported)["unchanged"], 1)
        # A second file with the original literal apostrophe must be preserved.
        quoted = self.root / "quoted.csv"
        with quoted.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["sku","title","description","category","price_eur"])
            writer.writerow(["ART-0002", "'=Originaltitel", "Beschreibung",
                             "Haus > Deko", "20"])
        check = preview_import(self.store, quoted)
        import_drafts(self.store, quoted, check["sha256"])
        second_export = self.root / "second.csv"
        export_drafts(self.store, second_export)
        self.assertEqual(preview_import(self.store, second_export)["unchanged"], 2)

    def test_conflicts_are_all_or_nothing_even_when_after_valid_new_rows(self) -> None:
        source = self.make_csv(2)
        digest = preview_import(self.store, source)["sha256"]
        self.assertEqual(import_drafts(self.store, source, digest)["inserted"], 2)
        with source.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(["sku", "title", "description", "category", "price_eur"])
            writer.writerow(["NEW-00001", "Neuer Artikel", "Neu", "Haus > Deko", "3"])
            writer.writerow(["SKU-00000", "Andere Beschreibung", "Neu", "Haus > Deko", "3"])
        attempt = preview_import(self.store, source)
        self.assertEqual((attempt["new"], attempt["conflict"]), (1, 1))
        with self.assertRaisesRegex(DraftImportError, "anderem Inhalt"):
            import_drafts(self.store, source, attempt["sha256"])
        self.assertEqual(drafts_page(self.store)["total"], 2)
        self.assertEqual(drafts_page(self.store, search="NEW-00001")["drafts"], [])

    def test_wrong_digest_and_bad_input_cannot_create_table(self) -> None:
        source = self.make_csv(1)
        digest = preview_import(self.store, source)["sha256"]
        with self.assertRaisesRegex(DraftImportError, "geaendert"):
            import_drafts(self.store, source, "0"*64)
        self.assertEqual(drafts_page(self.store)["total"], 0)
        with source.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(["sku", "title", "description", "category", "price_eur"])
            writer.writerow(["A-1", "A", "Ok", "Haus > Deko", "5"])
            writer.writerow(["A-1", "B", "Ok", "Haus > Deko", "5"])
        from mark_api.portfolio_drafts import DraftBatchError
        with self.assertRaises(DraftBatchError) as caught:
            preview_import(self.store, source)
        self.assertIn("Doppelte", caught.exception.issues[0]["message"])
        self.assertEqual(drafts_page(self.store)["total"], 0)

    def test_invalid_rows_have_row_field_and_never_publish(self) -> None:
        source = self.root / "broken.csv"
        source.write_text("sku,title,description,category,price_eur\n"
                          "SKU-0,Lampe,Beschreibung,Haus > Licht,12\n"
                          "SKU-1,Lampe,Text,Haus > Licht,12,50\n",
                          encoding="utf-8")
        # wrong delimiter creates extra column only at the data row
        with self.assertRaises(DraftImportError):
            preview_import(self.store, source)
        self.assertEqual(drafts_page(self.store)["total"], 0)

    def test_multiple_invalid_rows_report_exact_locations_without_partial_commit(self) -> None:
        from mark_api.portfolio_drafts import DraftBatchError
        path = self.root / "bad-rows.csv"
        path.write_text(
            "sku,title,description,category,price_eur\n"
            "BAD-01,Lampe,Test,Haus > Licht,49\n"
            "BAD-02,Lampe,Test,Haus > Licht,nicht-euro\n"
            "BAD-03,Lampe,Test,einzeln,7\n"
            "BAD-04,Lampe,Test,Haus > Licht,0\n",
            encoding="utf-8",
        )
        with self.assertRaises(DraftBatchError) as caught:
            preview_import(self.store, path)
        self.assertEqual([issue["row"] for issue in caught.exception.issues], [3, 4, 5])
        self.assertEqual(drafts_page(self.store)["total"], 0)

    def test_csv_formula_like_or_wrong_bytes_cannot_claim_owner(self) -> None:
        source = self.make_csv(3, title='Łódź "Nord"')
        preview = preview_import(self.store, source)
        import_drafts(self.store, source, preview["sha256"])
        one = drafts_page(self.store)["drafts"][0]
        self.assertEqual(one["title"], 'Łódź "Nord" 0')
        self.assertEqual(one["images"], ["vorn.jpg", "detail.png"])
        self.assertEqual(one["published"], False)
        self.assertEqual(self.store.tracked_ad_ids(), ())

    def test_disallow_import_over_missing_database_or_denied_container(self) -> None:
        source = self.make_csv(2)
        missing = self.root / "does-not-exist.sqlite"
        from contextlib import redirect_stdout
        import io
        capture = io.StringIO()
        with redirect_stdout(capture):
            self.assertEqual(main(["--db", str(missing), "--mode", "preview", "--file", str(source)]), 2)
        self.assertFalse(missing.exists())
        from mark_api import container_draft
        with patch.object(container_draft, "_MARKER", self.root / "UNATTESTED_DRAFT"):
            (self.root / "UNATTESTED_DRAFT").write_text("draft", encoding="utf-8")
            with redirect_stdout(capture):
                self.assertEqual(main(["--db", str(self.db), "--mode", "preview", "--file", str(source)]), 2)

    def test_xlsx_with_real_workbook_if_openpyxl_installed(self) -> None:
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest("openpyxl optional Excel dependency")
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["SKU", "Titel", "Beschreibung", "Kategorie", "Preis", "Bilder"])
        for n in range(1000):
            sheet.append([f"XL-{n:05d}", f"Vase {n}", "Sehr gut",
                          "Wohnen > Deko", 29, "vase.jpg"])
        path = self.root / "artikel.xlsx"
        workbook.save(path)
        check = preview_import(self.store, path)
        self.assertEqual((check["total"], check["new"]), (1000, 1000))
        self.assertEqual(import_drafts(self.store, path, check["sha256"])["inserted"], 1000)
        self.assertEqual(drafts_page(self.store)["total"], 1000)
        # Formulas cannot be imported as new product facts or calculations.
        sheet["B2"] = "=1+1"
        workbook.save(path)
        with self.assertRaisesRegex(DraftImportError, "Formeln"):
            preview_import(self.store, path)


if __name__ == "__main__":
    unittest.main()