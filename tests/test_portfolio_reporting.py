"""P4 local reports distinguish imported messages, manually entered sales and AI estimates."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime,timezone,timedelta
from pathlib import Path
import json
import sqlite3
import tempfile
import time
import unittest

from mark_api.domain import AdSnapshot, InboundMessageEvent, LifecycleState
from mark_api.portfolio_reporting import PortfolioReportError, report
from mark_api.portfolio_sales import update_manual_sale
from mark_api.portfolio_jobs import create_job, change_state, process_one
from mark_api.portfolio_drafts import _DDL
from mark_api.storage import SnapshotStore

NOW=datetime(2026,10,26,12,0,tzinfo=timezone.utc)

class PortfolioReportingTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="kleinanzeiger-report-")
        self.addCleanup(tmp.cleanup)
        self.store=SnapshotStore(Path(tmp.name)/"report.sqlite",create_if_missing=True)

    def ad(self,id,source="private-web-product-launcher"):
        self.store.append_ad_snapshot(AdSnapshot(
          ad_id=id,title="Lampe",lifecycle_state=LifecycleState.ACTIVE,
          observed_at=NOW-timedelta(days=14),source=source))

    def message(self,id,conv,mid,at):
        self.store.append_inbound_message_events([InboundMessageEvent(
            ad_id=id,conversation_id=conv,provider_message_id=mid,
            observed_at=at,source="test-synthetic-email")])

    def sale(self,id,n,revenue,cost):
        return update_manual_sale(self.store,{
          "ad_id":id,"state":"sold","revenue_eur":revenue,
          "cost_eur":cost,"location":"Regal A","note":"Manuell",
          "request_key":f"{n:064x}","expected_revision":0})

    def test_timestamps_source_ownership_separate_manual_sales_and_ai(self):
        self.ad("10001")
        self.ad("10002",source="private-web-periodic-readonly")
        self.ad("10003",source="local-catalog")
        self.message("10001","conv-a","m-1",datetime(2026,10,26,0,20,tzinfo=timezone.utc))
        self.message("10001","conv-a","m-2",
            datetime(2026,10,25,23,40,tzinfo=timezone(timedelta(hours=-2))))
        self.message("10002","conv-b","m-3",datetime(2026,10,24,17,tzinfo=timezone.utc))
        self.message("10002","conv-c","m-4",datetime(2026,10,19,11,tzinfo=timezone.utc))
        self.message("10003","conv-d","m-5",datetime(2026,10,26,1,tzinfo=timezone.utc))
        self.sale("10001",1,300,200)
        self.sale("10002",2,50,None)
        weekly=report(self.store,days=7,now=NOW)
        self.assertEqual(weekly["own_ads_with_inventory_history"],2)
        self.assertEqual(weekly["imported_inbound_messages_window"],3)
        self.assertEqual(weekly["imported_conversations_window"],2)
        self.assertEqual(weekly["start_day_utc"],"2026-10-20")
        self.assertEqual(weekly["end_day_utc"],"2026-10-26")
        self.assertEqual(len(weekly["imported_messages_per_day"]),7)
        self.assertEqual(weekly["imported_messages_per_day"][-1]["imported_messages"],2)
        self.assertEqual(weekly["manual_sales_current_all_time"]["revenue_eur"],350)
        self.assertEqual(weekly["manual_sales_current_all_time"]["known_cost_eur"],200)
        self.assertEqual(weekly["manual_sales_current_all_time"]["known_surplus_eur"],100)
        self.assertEqual(weekly["manual_sales_current_all_time"]["unknown_cost_count"],1)
        self.assertIsNone(weekly["ai_usage_estimate_all_time"])
        self.assertFalse(weekly["revenue_period_attributed"])
        self.assertFalse(weekly["conversion_rate_supported"])
        self.assertEqual(weekly["platform_writes"],0)
        monthly=report(self.store,days=30,now=NOW)
        self.assertEqual(monthly["imported_inbound_messages_window"],4)
        self.assertEqual(len(report(self.store,days=90,now=NOW)["imported_messages_per_day"]),90)
        # Now attach a synthetic local P2 job with metered estimate, no API.
        with closing(self.store._connect()) as conn,conn:
            conn.execute(_DDL)
            conn.execute(
              "INSERT INTO portfolio_drafts "
              "(sku,title,description,category_json,price_eur,images_json,"
              "source_sha256,source_name,created_at,status) "
              "VALUES(?,?,?,?,?,?,?,?,?,'draft')",
              ("S-001","Leuchte","Guter Zustand",'["Haus","Licht"]',29,
               '[]','0'*64,"test.csv",NOW.isoformat()))
        j=create_job(self.store,["S-001"],instruction="Präzisieren",
          request_key="e"*64,budget_cents=100,estimate_cents=20)
        change_state(self.store,j["job_id"],"start")
        process_one(self.store,j["job_id"],lambda original,instruction:
          ({"title":"Gute Leuchte","description":"Guter Zustand"},1))
        estimate=report(self.store,days=7,now=NOW)["ai_usage_estimate_all_time"]
        self.assertEqual(estimate["usd_cent_estimate"],1)
        self.assertEqual(estimate["unknown_outcomes"],0)
        self.assertFalse(estimate["invoice_verified"])

    def test_empty_data_and_invalid_window_and_clock(self):
        empty=report(self.store,days=7,now=NOW)
        self.assertEqual(empty["own_ads_with_inventory_history"],0)
        self.assertEqual(empty["manual_sales_current_all_time"]["manually_sold"],0)
        self.assertIsNone(empty["ai_usage_estimate_all_time"])
        with closing(self.store._connect()) as conn:
            tables={r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
        self.assertNotIn("portfolio_sale_states",tables)
        self.assertNotIn("portfolio_jobs",tables)
        for invalid in (True,0,1,31,"7",7.0,None):
            with self.subTest(invalid=invalid),self.assertRaises(PortfolioReportError):
                report(self.store,days=invalid,now=NOW)
        with self.assertRaises(PortfolioReportError):
            report(self.store,days=7,now=datetime(2026,10,26))

    def test_10000_synthetic_owner_ads_report_remains_constant_size(self):
        rows=[
            (str(1_000_000_000+i),NOW.isoformat(),
             "private-web-product-launcher","active",
             "Artikel "+str(i),None,None,None,None,None)
            for i in range(10000)
        ]
        with closing(self.store._connect()) as conn,conn:
            conn.executemany(
                "INSERT INTO ad_snapshots "
                "(ad_id,observed_at,source,lifecycle_state,title,description,"
                "views,watch_count,reply_count,metric_source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",rows)
        started=time.monotonic()
        data=report(self.store,days=90,now=NOW)
        self.assertEqual(data["own_ads_with_inventory_history"],10000)
        self.assertEqual(data["imported_inbound_messages_window"],0)
        self.assertEqual(len(data["imported_messages_per_day"]),90)
        self.assertLess(len(json.dumps(data,ensure_ascii=False)),16_000)
        self.assertLess(time.monotonic()-started,10,"10k report should stay bounded")

    def test_corrupt_email_date_fails_closed_instead_of_missing_count(self):
        self.ad("10001")
        self.message("10001","conv","m-1",NOW)
        with closing(self.store._connect()) as conn,conn:
            conn.execute("UPDATE inbound_message_events SET observed_at='broken' ")
        with self.assertRaises(sqlite3.DatabaseError):
            report(self.store,days=7,now=NOW)

if __name__=="__main__":
    unittest.main()
