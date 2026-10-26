"""P3 sales ledger: idempotent local entries, bounded pages and known unknowns."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.portfolio_sales import SaleError, sale_detail, sales_page, sale_totals, update_manual_sale
from mark_api.storage import SnapshotStore, LOCAL_CATALOG_SOURCE


class SalesLedgerTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix="kleinanzeiger-sales-")
        self.addCleanup(folder.cleanup)
        self.db=Path(folder.name)/"kunden.sqlite"
        self.store=SnapshotStore(self.db,create_if_missing=True)

    def observe(self,ad_id,title="Lampe",source="private-web-product-launcher"):
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id=ad_id,title=title,lifecycle_state=LifecycleState.ACTIVE,
            observed_at=datetime(2026,10,26,9,tzinfo=timezone.utc),source=source))

    def payload(self,ad_id,revision=0,key="1"*64,**fields):
        result={"ad_id":ad_id,"state":"reserved",
                "revenue_eur":None,"cost_eur":None,
                "location":"Regal B","note":"Manuell vorgemerkt",
                "request_key":key,"expected_revision":revision}
        result.update(fields)
        return result

    def test_local_ledger_and_sale_gain_with_unknown_cost(self):
        self.observe("12345")
        first=update_manual_sale(self.store,self.payload("12345"))
        self.assertEqual(first["revision"],1)
        self.assertEqual(first["platform_writes"],0)
        self.assertEqual(update_manual_sale(self.store,self.payload("12345")),first)
        self.assertEqual(sale_totals(self.store)["reserved"],1)
        with self.assertRaises(SaleError):
            update_manual_sale(self.store,self.payload(
                "12345",key="2"*64,note="reused key changed"))
        with self.assertRaises(SaleError):
            update_manual_sale(self.store,self.payload("12345",key="3"*64))
        sold=update_manual_sale(self.store,self.payload(
            "12345",revision=1,key="4"*64,state="sold",
            revenue_eur=120,cost_eur=None,note="Kunde zahlte 120 Euro"))
        self.assertIsNone(sold["profit_eur"])
        totals=sale_totals(self.store)
        self.assertEqual(totals["revenue_eur"],120)
        self.assertEqual(totals["unknown_cost_count"],1)
        self.assertEqual(totals["known_cost_eur"],0)
        corrected=update_manual_sale(self.store,self.payload(
            "12345",revision=2,key="5"*64,state="sold",
            revenue_eur=120,cost_eur=60,note="Kosten nachgetragen"))
        self.assertEqual(corrected["profit_eur"],60)
        self.assertEqual(sale_totals(self.store)["known_cost_eur"],60)
        self.assertEqual(sale_totals(self.store)["known_profit_eur"],60)
        self.assertEqual(sale_totals(self.store)["known_profit_count"],1)
        page=sales_page(self.store)
        self.assertEqual(page["total"],1)
        self.assertEqual(page["sales"][0]["title"],"Lampe")
        self.assertEqual(page["sales"][0]["cost_eur"],60)
        self.assertEqual(sale_detail(self.store,"12345")["revision"],3)
        self.assertIsNone(sale_detail(self.store,"54321"))
        with self.assertRaises(SaleError):
            sale_detail(self.store,"12345 OR 1=1")
        with closing(self.store._connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM portfolio_sale_events").fetchone()[0],3)
        self.assertEqual(self.store.tracked_ad_ids(),("12345",))

    def test_email_only_or_untrusted_catalog_cannot_become_own_sale(self):
        self.observe("12345",source="user-supplied-local-catalog")
        self.observe("12346",source="local-catalog")
        self.observe("12348",source="owner")
        for id in ("12345","12346","12347","12348"):
            with self.subTest(id=id),self.assertRaises(SaleError):
                update_manual_sale(self.store,self.payload(id,key=(id[0]+id[-1])*32))
        self.assertEqual(sales_page(self.store)["total"],0)

    def test_invalid_payments_revision_and_user_strings_are_denied(self):
        self.observe("999")
        for i,changes in enumerate((
            {"revenue_eur":20},
            {"cost_eur":0},
            {"state":"sold"},
            {"state":"sold","revenue_eur":False},
            {"state":"sold","revenue_eur":10,"cost_eur":True},
            {"state":"sold","revenue_eur":-1},
            {"state":"SOLD"},
            {"ad_id":"999 OR 1=1"},
            {"location":"\u0000"},
            {"note":"\ud800"},
            {"expected_revision":True},
            {"request_key":"z"*64},
        )):
            with self.subTest(i=i),self.assertRaises(SaleError):
                attempted=self.payload("999")
                attempted.update(changes)
                update_manual_sale(self.store,attempted)
        self.assertEqual(sales_page(self.store)["total"],0)

    def test_binary_cursor_page_and_no_synthetic_platform_outcome(self):
        for n in range(102):
            id=f"{n+10000:05d}"
            self.observe(id,title="Artikel "+id)
            update_manual_sale(self.store,self.payload(id,key=f"{n+1:064x}"))
        a=sales_page(self.store,limit=50)
        self.assertEqual(a["total"],102)
        self.assertEqual(len(a["sales"]),50)
        self.assertEqual(a["sales"][0]["ad_id"],"10000")
        b=sales_page(self.store,after=a["next_after"])
        self.assertEqual(b["sales"][0]["ad_id"],"10050")
        c=sales_page(self.store,after=b["next_after"])
        self.assertEqual(len(c["sales"]),2)
        self.assertFalse(c["has_more"])
        self.assertEqual(sale_detail(self.store,"10101")["revision"],1)
        self.assertEqual(sale_detail(self.store,"10101")["location"],"Regal B")
        for bad in ({"after":"nope"},{"limit":51},{"limit":0},{"limit":True}):
            with self.assertRaises(SaleError):
                sales_page(self.store,**bad)
        dest=Path(self.db.parent)/"copy.sqlite"
        with closing(self.store._connect()) as conn,closing(sqlite3.connect(dest)) as backup:
            conn.backup(backup)
        restored=SnapshotStore(dest,create_if_missing=False,require_existing_schema=True)
        self.assertEqual(sales_page(restored,limit=1)["total"],102)


if __name__=="__main__":
    unittest.main()
