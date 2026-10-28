"""G2: separate local item templates, independent draft keys, no marketplace effects."""
from __future__ import annotations

import csv
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from mark_api.portfolio_drafts import drafts_page, import_drafts, preview_import
from mark_api.portfolio_item_templates import (
    ItemTemplateError, delete_item_template, draft_from_item_template,
    duplicate_local_draft, list_item_templates, save_item_template,
)
from mark_api.storage import SnapshotStore


class ItemTemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="kleinanzeiger-g2-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "product.sqlite"
        self.store = SnapshotStore(self.db)

    def seed(self, sku="LOCAL-A", title="Vintage Tisch Ø 80", image="front.jpg;back.png"):
        path = self.root / (sku + ".csv")
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["sku", "title", "description", "category", "price_eur", "images"])
            writer.writerow([sku, title, "Wenig benutzt, Abholung möglich",
                             "Haus > Möbel", "50", image])
        preview = preview_import(self.store, path)
        self.assertEqual(preview["new"], 1)
        import_drafts(self.store, path, preview["sha256"])

    def save(self, key="a" * 64, name="Esstische", source="LOCAL-A",
             template_id=None, revision=0):
        return save_item_template(
            self.store, request_key=key, name=name, source_sku=source,
            template_id=template_id, expected_revision=revision,
        )

    def test_listing_is_read_only_and_work_templates_are_untouched(self):
        self.assertEqual(list_item_templates(self.store),
                         {"templates": [], "limit": 100})
        with closing(sqlite3.connect(self.db)) as conn:
            for table in ("portfolio_item_templates", "portfolio_item_template_requests",
                          "portfolio_work_templates"):
                self.assertIsNone(conn.execute(
                    "SELECT name FROM sqlite_master WHERE name=?", (table,),
                ).fetchone())

    def test_template_capture_and_two_independent_drafts(self):
        self.seed()
        stored = self.save()
        self.assertEqual(stored["platform_writes"], 0)
        self.assertEqual(stored["revision"], 1)
        self.assertEqual(self.save(), stored)
        self.assertEqual(len(stored["template_id"]), 32)
        listings = list_item_templates(self.store)["templates"]
        self.assertEqual(len(listings), 1)
        source = listings[0]
        self.assertEqual(source["name"], "Esstische")
        self.assertEqual(source["title"], "Vintage Tisch Ø 80")
        self.assertEqual(source["images"], ["front.jpg", "back.png"])
        self.assertNotIn("sku", source)
        self.assertNotIn("ad_id", source)
        self.assertNotIn("write_capability", source)
        self.assertFalse(source["published"])

        first = draft_from_item_template(
            self.store, template_id=stored["template_id"], new_sku="NEW-1",
            request_key="b" * 64)
        second = draft_from_item_template(
            self.store, template_id=stored["template_id"], new_sku="NEW-2",
            request_key="c" * 64)
        self.assertEqual(first["status"], "draft")
        self.assertEqual(first["platform_writes"], 0)
        self.assertEqual(draft_from_item_template(
            self.store, template_id=stored["template_id"], new_sku="NEW-1",
            request_key="b" * 64), first)
        self.assertNotEqual(first["sku"], second["sku"])
        page = drafts_page(self.store)
        self.assertEqual(page["total"], 3)
        self.assertEqual([row["sku"] for row in page["drafts"]],
                         ["LOCAL-A", "NEW-1", "NEW-2"])
        for row in page["drafts"]:
            self.assertEqual(row["title"], "Vintage Tisch Ø 80")
            self.assertEqual(row["images"], ["front.jpg", "back.png"])
            self.assertFalse(row["published"])
            self.assertNotIn("ad_id", row)
        self.assertEqual(self.store.tracked_ad_ids(), ())
        self.assertEqual(self.store.sync_status()["state"], "never_attempted")
        with closing(sqlite3.connect(self.db)) as conn:
            sources = dict(conn.execute(
                "SELECT sku,source_name FROM portfolio_drafts"))
        self.assertEqual(sources["NEW-1"], "lokale-artikelvorlage")
        self.assertEqual(sources["NEW-2"], "lokale-artikelvorlage")

    def test_duplicate_without_saved_template_and_source_key_not_inherited(self):
        self.seed()
        answer = duplicate_local_draft(
            self.store, source_sku="LOCAL-A", new_sku="COPY-55",
            request_key="1" * 64)
        self.assertEqual(answer["platform_writes"], 0)
        self.assertFalse(answer["published"])
        self.assertEqual(drafts_page(self.store)["total"], 2)
        with closing(sqlite3.connect(self.db)) as conn:
            row = conn.execute(
                "SELECT sku,source_name FROM portfolio_drafts WHERE sku='COPY-55'",
            ).fetchone()
            self.assertEqual(row, ("COPY-55", "lokale-kopie"))

    def test_save_update_revision_and_deleted_template_cannot_clone(self):
        self.seed()
        old = self.save()
        self.seed(sku="LOCAL-B", title="Regal neu", image="other.webp")
        changed = self.save(
            key="b" * 64, source="LOCAL-B", name="Schränke",
            template_id=old["template_id"], revision=1)
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(len(list_item_templates(self.store)["templates"]), 1)
        self.assertEqual(list_item_templates(self.store)["templates"][0]["title"], "Regal neu")
        with self.assertRaises(ItemTemplateError):
            self.save(key="c" * 64, template_id=old["template_id"], revision=1)
        deletion = delete_item_template(
            self.store, template_id=old["template_id"], expected_revision=2,
            request_key="d" * 64)
        self.assertTrue(deletion["deleted"])
        self.assertEqual(delete_item_template(
            self.store, template_id=old["template_id"], expected_revision=2,
            request_key="d" * 64), deletion)
        with self.assertRaises(ItemTemplateError):
            draft_from_item_template(self.store, template_id=old["template_id"],
                                     new_sku="NEW", request_key="e" * 64)
        self.assertEqual(list_item_templates(self.store)["templates"], [])

    def test_duplicate_names_replay_conflict_and_atomic_reject(self):
        self.seed()
        saved = self.save()
        with self.assertRaises(ItemTemplateError):
            self.save(key="b" * 64, name="esstische")
        with self.assertRaises(ItemTemplateError):
            self.save(key="a" * 64, name="Other")
        with self.assertRaises(ItemTemplateError):
            draft_from_item_template(
                self.store, template_id=saved["template_id"],
                new_sku="LOCAL-A", request_key="c" * 64)
        with self.assertRaises(ItemTemplateError):
            duplicate_local_draft(
                self.store, source_sku="LOCAL-A", new_sku="LOCAL-A",
                request_key="c" * 64)
        with self.assertRaises(ItemTemplateError):
            duplicate_local_draft(
                self.store, source_sku="MISSING", new_sku="NEW-9",
                request_key="d" * 64)
        self.assertEqual(drafts_page(self.store)["total"], 1)
        self.assertEqual(len(list_item_templates(self.store)["templates"]), 1)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM portfolio_item_template_requests"
            ).fetchone()[0], 1)

    def test_invalid_inputs_fail_without_creating_a_template(self):
        self.seed()
        for name in ("", "x" * 61, "bad\x00name"):
            with self.subTest(name=name), self.assertRaises(ItemTemplateError):
                self.save(name=name)
        for key in ("invalid", "q" * 64, "", "a" * 65):
            with self.subTest(key=key), self.assertRaises(ItemTemplateError):
                self.save(key=key)
        for sku in ("", "foo/bar", "ä", " "):
            with self.subTest(sku=sku), self.assertRaises(ItemTemplateError):
                self.save(source=sku)
        with self.assertRaises(ItemTemplateError):
            self.save(source="ABSENT")
        self.assertEqual(list_item_templates(self.store)["templates"], [])
        self.assertEqual(drafts_page(self.store)["total"], 1)

    def test_large_local_catalog_does_not_expand_template_listing(self):
        self.seed()
        saved = self.save()
        for i in range(220):
            draft_from_item_template(
                self.store, template_id=saved["template_id"],
                new_sku=f"NEW-{i:04d}", request_key=f"{i + 1:064x}")
        self.assertEqual(drafts_page(self.store, limit=50)["total"], 221)
        self.assertEqual(len(list_item_templates(self.store)["templates"]), 1)
        self.assertEqual(self.store.tracked_ad_ids(), ())


if __name__ == "__main__":
    unittest.main()
