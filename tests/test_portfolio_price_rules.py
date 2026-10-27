"""G5: no-autowrite price suggestions with strict floors, timing and keyset scale."""
from __future__ import annotations

import csv
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from mark_api.portfolio_drafts import _DDL as DRAFT_DDL, import_drafts, preview_import
from mark_api.portfolio_price_rules import (
    _DDL as RULE_DDL, PriceRuleError, delete_price_rule,
    price_suggestions_page, set_price_rule,
)
from mark_api.storage import SnapshotStore


_START = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)


class PriceRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="kleinanzeiger-g5-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "store.sqlite"
        self.store = SnapshotStore(self.db)

    def seed(self, sku="SKU-1", *, price=50):
        path = self.root / f"{sku}.csv"
        with path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(["sku", "title", "description", "category", "price_eur"])
            writer.writerow([sku, "Lampe", "Gebraucht und funktionsfähig", "Haus > Leuchten", price])
        preview = preview_import(self.store, path)
        self.assertEqual(preview["new"], 1)
        import_drafts(self.store, path, preview["sha256"])

    def set(self, *, sku="SKU-1", minimum=40, step=20, wait=7,
            revision=0, key="a" * 64, now=_START):
        return set_price_rule(
            self.store, sku=sku, minimum_price_eur=minimum,
            reduction_step_eur=step, wait_days=wait,
            expected_revision=revision, request_key=key, now=now,
        )

    def test_initial_list_is_pure_read_and_does_not_create_tables(self):
        self.assertEqual(price_suggestions_page(self.store)["total"], 0)
        with closing(sqlite3.connect(self.db)) as conn:
            for name in ("portfolio_price_rules", "portfolio_price_rule_requests"):
                self.assertIsNone(conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=?", (name,),
                ).fetchone())

    def test_clamp_to_minimum_cooldown_and_no_automatic_write(self):
        self.seed()
        stored = self.set()
        self.assertEqual((stored["local_price_eur_at_save"], stored["revision"]), (50, 1))
        self.assertFalse(stored["published"])
        self.assertEqual(stored["platform_writes"], 0)
        earlier = price_suggestions_page(self.store, now=_START + timedelta(days=6))
        waiting = earlier["rules"][0]
        self.assertEqual(waiting["state"], "waiting")
        self.assertIsNone(waiting["suggested_price_eur"])
        self.assertEqual(waiting["eligible_at"], (_START + timedelta(days=7)).isoformat())
        proposal = price_suggestions_page(self.store, now=_START + timedelta(days=8))
        first = proposal["rules"][0]
        self.assertEqual(first["state"], "suggested")
        self.assertEqual(first["suggested_price_eur"], 40)
        self.assertEqual(first["minimum_price_eur"], 40)
        self.assertEqual(first["local_price_eur"], 50)
        self.assertEqual(first["price_source"], "local_imported_draft")
        self.assertEqual(first["platform_writes"], 0)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT price_eur FROM portfolio_drafts WHERE sku='SKU-1'"
            ).fetchone()[0], 50)
        self.assertEqual(self.store.tracked_ad_ids(), ())
        self.assertEqual(self.store.sync_status()["state"], "never_attempted")

    def test_exact_floor_and_manual_price_below_floor_never_suggest_reduction(self):
        self.seed()
        self.set(minimum=50, wait=0)
        suggestion = price_suggestions_page(self.store, now=_START)["rules"][0]
        self.assertEqual(suggestion["state"], "at_minimum")
        self.assertIsNone(suggestion["suggested_price_eur"])
        with closing(self.store._connect()) as conn,conn:
            conn.execute("UPDATE portfolio_drafts SET price_eur=30 WHERE sku='SKU-1'")
        stale = price_suggestions_page(self.store, now=_START + timedelta(days=100))["rules"][0]
        self.assertEqual(stale["state"], "below_minimum")
        self.assertIsNone(stale["suggested_price_eur"])
        self.assertEqual(stale["local_price_eur"], 30)

    def test_revision_cas_exact_replay_and_new_wait_start(self):
        self.seed()
        first = self.set()
        self.assertEqual(first, self.set())
        with self.assertRaisesRegex(PriceRuleError, "anders verwendet"):
            self.set(step=5)
        with self.assertRaises(PriceRuleError):
            self.set(revision=1, key="b" * 64, minimum=60)
        changed = self.set(
            revision=1, minimum=35, step=5, wait=10,
            key="c" * 64, now=_START + timedelta(days=2),
        )
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(changed["minimum_price_eur"], 35)
        with self.assertRaises(PriceRuleError):
            self.set(revision=1, key="d" * 64)
        preview = price_suggestions_page(self.store, now=_START + timedelta(days=10))
        self.assertEqual(preview["rules"][0]["state"], "waiting")
        self.assertEqual(preview["rules"][0]["eligible_at"],
                         (_START + timedelta(days=12)).isoformat())
        self.assertEqual(price_suggestions_page(
            self.store, now=_START + timedelta(days=13)
        )["rules"][0]["suggested_price_eur"], 45)

    def test_delete_with_cas_and_replay_without_platform_effect(self):
        self.seed()
        self.set()
        with self.assertRaises(PriceRuleError):
            delete_price_rule(self.store, sku="SKU-1",
                              expected_revision=2, request_key="b" * 64)
        deleted = delete_price_rule(
            self.store, sku="SKU-1", expected_revision=1,
            request_key="c" * 64)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["platform_writes"], 0)
        self.assertEqual(delete_price_rule(
            self.store, sku="SKU-1", expected_revision=1,
            request_key="c" * 64), deleted)
        self.assertEqual(price_suggestions_page(self.store)["total"], 0)
        with self.assertRaises(PriceRuleError):
            delete_price_rule(self.store, sku="SKU-1",
                              expected_revision=1, request_key="d" * 64)

    def test_missing_local_draft_is_unknown_not_sold_or_deleted_platform_ad(self):
        self.seed()
        self.set(wait=0)
        with closing(self.store._connect()) as conn,conn:
            conn.execute("DELETE FROM portfolio_drafts WHERE sku='SKU-1'")
        item = price_suggestions_page(self.store, now=_START)["rules"][0]
        self.assertEqual(item["state"], "unknown_local_price")
        self.assertIsNone(item["local_price_eur"])
        self.assertIsNone(item["suggested_price_eur"])
        self.assertNotIn("sold", str(item))
        self.assertEqual(item["platform_writes"], 0)

    def test_delete_recreate_keeps_revision_monotone_and_rejects_stale_editor(self):
        self.seed()
        original = self.set(wait=0)
        self.assertEqual(original["revision"], 1)
        removed = delete_price_rule(
            self.store, sku="SKU-1", expected_revision=1, request_key="b" * 64)
        self.assertTrue(removed["deleted"])
        self.assertEqual(price_suggestions_page(self.store)["total"], 0)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT revision,deleted FROM portfolio_price_rules WHERE sku='SKU-1'"
            ).fetchone(), (2, 1))
        replacement = self.set(
            minimum=35, step=10, wait=0,
            key="c" * 64, revision=0, now=_START + timedelta(days=1))
        self.assertEqual(replacement["revision"], 3)
        with self.assertRaises(PriceRuleError):
            self.set(minimum=25, step=10, wait=0,
                     key="d" * 64, revision=1)
        with self.assertRaises(PriceRuleError):
            delete_price_rule(
                self.store, sku="SKU-1",
                expected_revision=1, request_key="e" * 64)
        proposed = price_suggestions_page(
            self.store, now=_START + timedelta(days=2))["rules"]
        self.assertEqual(len(proposed), 1)
        self.assertEqual(proposed[0]["minimum_price_eur"], 35)
        self.assertEqual(proposed[0]["suggested_price_eur"], 40)
        self.assertEqual(proposed[0]["revision"], 3)
        current = self.set(
            minimum=34, step=10, wait=0, revision=3, key="f" * 64)
        self.assertEqual(current["revision"], 4)
        delete_price_rule(self.store, sku="SKU-1",
                          expected_revision=4, request_key="0" * 64)
        newest = self.set(
            minimum=30, wait=0, revision=0, key="1" * 64)
        self.assertEqual(newest["revision"], 6)
        with self.assertRaises(PriceRuleError):
            self.set(minimum=25, wait=0, revision=3, key="2" * 64)
        self.assertEqual(self.store.tracked_ad_ids(), ())

    def test_deleted_tombstone_reactivation_respects_active_ten_thousand_limit(self):
        self.seed()
        with closing(self.store._connect()) as conn,conn:
            for statement in RULE_DDL.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute(
                "INSERT INTO portfolio_price_rules "
                "(sku,minimum_price_eur,reduction_step_eur,wait_days,revision,deleted,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                ("SKU-1", 40, 5, 0, 4, 1, _START.isoformat()),
            )
            conn.executemany(
                "INSERT INTO portfolio_price_rules "
                "(sku,minimum_price_eur,reduction_step_eur,wait_days,revision,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                ((f"FULL-{i:05d}", 15, 5, 0, 1, _START.isoformat())
                 for i in range(10_000)),
            )
        with self.assertRaisesRegex(PriceRuleError, "Maximal 10000"):
            self.set(minimum=35, wait=0, key="3"*64)
        with closing(self.store._connect()) as conn,conn:
            self.assertEqual(tuple(conn.execute(
                "SELECT revision,deleted FROM portfolio_price_rules WHERE sku='SKU-1'"
            ).fetchone()), (4, 1))
            conn.execute(
                "UPDATE portfolio_price_rules SET deleted=1, revision=2 "
                "WHERE sku='FULL-00000'")
        revived = self.set(minimum=35, wait=0, key="4"*64)
        self.assertEqual(revived["revision"], 5)
        self.assertEqual(price_suggestions_page(self.store)["total"], 10_000)

    def test_bad_values_and_unobserved_sku_are_rejected_atomically(self):
        self.seed()
        for minimum,step,wait,revision,key in [
            (51,5,0,0,"a"*64),
            (0,5,0,0,"a"*64),
            (40,0,0,0,"a"*64),
            (40,-1,0,0,"a"*64),
            (40,5,-1,0,"a"*64),
            (40,5,3651,0,"a"*64),
            (True,5,0,0,"a"*64),
            (40,True,0,0,"a"*64),
            (40,5,True,0,"a"*64),
            (40,5,0,True,"a"*64),
            (40,5,0,0,"z"*64),
        ]:
            with self.subTest((minimum,step,wait,revision,key)), self.assertRaises(PriceRuleError):
                self.set(minimum=minimum, step=step, wait=wait, revision=revision, key=key)
        with self.assertRaises(PriceRuleError):
            self.set(sku="MISSING", wait=0)
        with self.assertRaises(PriceRuleError):
            self.set(sku="../etc", wait=0)
        with self.assertRaises(ValueError):
            self.set(now=datetime(2026,10,27))
        self.assertEqual(price_suggestions_page(self.store)["total"], 0)

    def test_ten_thousand_rules_keyset_pagination_and_limit(self):
        # All synthetic; no provider, browser or customer records.
        with closing(self.store._connect()) as conn,conn:
            conn.execute(DRAFT_DDL)
            for statement in RULE_DDL.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.executemany(
                "INSERT INTO portfolio_drafts "
                "(sku,title,description,category_json,price_eur,images_json,"
                "source_sha256,source_name,created_at,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,'draft')",
                ((f"SKU-{i:05d}", "Test", "Lokal", '["Haus","Deko"]', 25,
                  "[]", "a"*64, "synthetic", _START.isoformat()) for i in range(10_000)),
            )
            conn.executemany(
                "INSERT INTO portfolio_price_rules "
                "(sku,minimum_price_eur,reduction_step_eur,wait_days,revision,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                ((f"SKU-{i:05d}", 20, 7, 0, 1, _START.isoformat())
                 for i in range(10_000)),
            )
        first = price_suggestions_page(self.store, limit=50, now=_START)
        self.assertEqual(first["total"], 10_000)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_after"], "SKU-00049")
        self.assertEqual(len(first["rules"]), 50)
        self.assertEqual(first["rules"][0]["suggested_price_eur"], 20)
        second = price_suggestions_page(
            self.store, after=first["next_after"], limit=50, now=_START)
        self.assertEqual(second["rules"][0]["sku"], "SKU-00050")
        final = price_suggestions_page(
            self.store, after="SKU-09949", now=_START)
        self.assertEqual(final["rules"][-1]["sku"], "SKU-09999")
        self.assertFalse(final["has_more"])
        self.assertIsNone(final["next_after"])
        self.assertEqual(self.store.tracked_ad_ids(), ())

    def test_corrupt_partial_schema_is_rejected_not_silently_repaired(self):
        with closing(self.store._connect()) as conn,conn:
            conn.execute("CREATE TABLE portfolio_price_rules (sku TEXT)")
        with self.assertRaises(sqlite3.DatabaseError):
            price_suggestions_page(self.store)


if __name__ == "__main__":
    unittest.main()