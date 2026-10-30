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
from mark_api.portfolio_existing_jobs import (
    create_job as create_existing_job,
    change_state as change_existing_job_state,
    claim_next as claim_existing,
    mark_unknown as mark_existing_unknown,
    process_one as process_existing,
    settle_unknown as settle_existing_unknown,
    review_items as review_existing_items,
    _schema as existing_jobs_schema,
)
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

    def own_for_ai(self, ad_id):
        # The actual published-ad AI gate requires a non-empty, evidenced
        # original title and description, not merely a historical ad ID.
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id=ad_id, title="Belegter Artikel",
            description="Faktenbasierte Originalbeschreibung",
            lifecycle_state=LifecycleState.ACTIVE,
            observed_at=NOW,
            source="kleinanzeigen-management",
        ))

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
        self.assertIsNone(weekly["imported_unique_buyers_window"])
        self.assertFalse(weekly["unique_buyer_count_supported"])
        self.assertEqual(weekly["imported_messages_per_day"][-1]["imported_conversations"],1)
        self.assertEqual(weekly["imported_messages_per_day"][-3]["imported_conversations"],1)
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

    def test_published_ad_costs_include_checked_unknown_and_unsettled_reserves(self):
        # Each generator below is a local stub, not an OpenAI/provider call.
        for ad_id in ("10001", "10002", "10003"):
            self.own_for_ai(ad_id)
        first = create_existing_job(
            self.store, ["10001"], instruction="Nur Belegtes",
            request_key="1"*64, budget_cents=80, estimate_cents=20,
        )["job_id"]
        change_existing_job_state(self.store, first, "start")
        claim = claim_existing(self.store, first)
        mark_existing_unknown(self.store, first, "10001", claim["claim_token"])
        unresolved = report(self.store, days=7, now=NOW)["ai_usage_estimate_all_time"]
        self.assertEqual(unresolved["usd_cent_estimate"], 0)
        self.assertEqual(unresolved["usd_cent_reserved"], 20)
        self.assertEqual(unresolved["unknown_outcomes"], 1)
        self.assertEqual(unresolved["source_breakdown"]["local_draft_jobs"], None)
        self.assertEqual(unresolved["source_breakdown"][
            "published_owned_ad_jobs"]["unknown"], 1)
        self.assertFalse(unresolved["invoice_verified"])
        settle_existing_unknown(self.store, first, "10001",
                                actual_cents=8, explicitly_checked=True)
        # The existing local review API can transition an already settled,
        # proposal-free rejected item to approved. That approval never
        # rewrites history or constitutes platform publication. Accounting
        # must preserve checked charge and never fail with HTTP 503.
        review_existing_items(self.store, first, ["10001"], approve=True)
        second = create_existing_job(
            self.store, ["10002"], instruction="Tatsachen pruefen",
            request_key="2"*64, budget_cents=80, estimate_cents=20,
        )["job_id"]
        change_existing_job_state(self.store, second, "start")
        process_existing(self.store, second, lambda *_: (
            {"title": "Belegter Vorschlag", "description": "Aus vorhandenen Fakten"}, 5
        ))
        third = create_existing_job(
            self.store, ["10003"], instruction="Nur Fakten",
            request_key="3"*64, budget_cents=80, estimate_cents=20,
        )["job_id"]
        change_existing_job_state(self.store, third, "start")
        claim_existing(self.store, third)
        data = report(self.store, days=30, now=NOW)
        costs = data["ai_usage_estimate_all_time"]
        self.assertEqual(costs["usd_cent_estimate"], 5)
        self.assertEqual(costs["usd_cent_checked_unknown"], 8)
        self.assertEqual(costs["usd_cent_known_total"], 13)
        self.assertEqual(costs["usd_cent_reserved"], 20)
        self.assertEqual(costs["unknown_outcomes"], 0)
        self.assertEqual(costs["inflight_outcomes"], 1)
        self.assertEqual(costs["source_breakdown"][
            "published_owned_ad_jobs"]["items"], 3)
        self.assertEqual(costs["source_breakdown"][
            "published_owned_ad_jobs"]["checked"], 8)
        self.assertFalse(costs["invoice_verified"])
        self.assertEqual(data["platform_writes"], 0)
        # Running this report never makes a cost settlement or resumes work.
        self.assertEqual(report(self.store, days=30, now=NOW), data)
        with closing(self.store._connect()) as conn:
            reserved = conn.execute(
                "SELECT phase,reserved_cents FROM portfolio_existing_job_items "
                "WHERE job_id=? AND ad_id='10003'", (third,),
            ).fetchone()
            self.assertEqual((reserved["phase"], reserved["reserved_cents"]),
                             ("inflight", 20))

    def test_existing_and_draft_ai_costs_are_combined_exactly_once(self):
        self.own_for_ai("10001")
        eid = create_existing_job(
            self.store, ["10001"], instruction="Belegter Originaltext",
            request_key="a"*64, budget_cents=80, estimate_cents=20,
        )["job_id"]
        change_existing_job_state(self.store, eid, "start")
        process_existing(self.store, eid, lambda *_: (
            {"title":"Sorgfältig geprüft","description":"Belegte Daten"},5))
        with closing(self.store._connect()) as conn,conn:
            conn.execute(_DDL)
            conn.execute(
                "INSERT INTO portfolio_drafts "
                "(sku,title,description,category_json,price_eur,images_json,"
                "source_sha256,source_name,created_at,status) "
                "VALUES(?,?,?,?,?,?,?,?,?,'draft')",
                ("S-001","Leuchte","Original", '["Haus","Licht"]',29,
                 "[]","0"*64,"test.csv",NOW.isoformat()),
            )
        did = create_job(
            self.store,["S-001"],instruction="Nur Fakten",
            request_key="b"*64,budget_cents=80,estimate_cents=20,
        )["job_id"]
        change_state(self.store,did,"start")
        process_one(self.store,did,lambda *_: (
            {"title":"Belegte Leuchte","description":"Original"},3))
        data=report(self.store,days=7,now=NOW)
        cost=data["ai_usage_estimate_all_time"]
        self.assertEqual(cost["usd_cent_estimate"],8)
        self.assertEqual(cost["usd_cent_checked_unknown"],0)
        self.assertEqual(cost["usd_cent_known_total"],8)
        self.assertEqual(cost["source_breakdown"][
            "local_draft_jobs"]["token_estimate"],3)
        self.assertEqual(cost["source_breakdown"][
            "published_owned_ad_jobs"]["token_estimate"],5)
        self.assertEqual(data["platform_writes"],0)

    def test_existing_ad_cost_corruption_fails_closed_instead_of_omitting_cost(self):
        self.own_for_ai("10001")
        jid = create_existing_job(
            self.store, ["10001"], instruction="Belege",
            request_key="9"*64, budget_cents=20, estimate_cents=20,
        )["job_id"]
        with closing(self.store._connect()) as conn,conn:
            conn.execute(
                "UPDATE portfolio_existing_job_items SET actual_cents=-4 WHERE job_id=?",
                (jid,),
            )
        with self.assertRaises(sqlite3.DatabaseError):
            report(self.store, days=7, now=NOW)

    def test_ten_thousand_published_ad_ai_items_use_constant_size_report(self):
        with closing(self.store._connect()) as conn,conn:
            self.assertTrue(existing_jobs_schema(conn, create=True))
            conn.execute(
                "INSERT INTO portfolio_existing_jobs "
                "(job_id,request_key,request_sha256,instruction,state,"
                "budget_cents,estimate_cents,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                ("a"*32,"b"*64,"c"*64,"Fakten","paused",100000,20,
                 NOW.isoformat(),NOW.isoformat()),
            )
            conn.executemany(
                "INSERT INTO portfolio_existing_job_items "
                "(job_id,ad_id,source_json,source_sha256,phase,actual_cents,updated_at)"
                " VALUES(?,?, '{}', ?, 'proposed', 1, ?)",
                (("a"*32,str(1000000000+i),"f"*64,NOW.isoformat())
                 for i in range(10000)),
            )
        began=time.monotonic()
        data=report(self.store,days=90,now=NOW)
        usage=data["ai_usage_estimate_all_time"]
        self.assertEqual(usage["usd_cent_estimate"], 10000)
        self.assertEqual(usage["usd_cent_checked_unknown"], 0)
        self.assertEqual(usage["usd_cent_known_total"], 10000)
        self.assertEqual(usage["source_breakdown"]["published_owned_ad_jobs"]["items"],10000)
        self.assertEqual(data["platform_writes"], 0)
        self.assertLess(len(json.dumps(data)),16000)
        self.assertLess(time.monotonic()-began,10)

    def test_conversation_per_day_is_not_an_unverified_buyer_count(self):
        self.ad("10001")
        self.message("10001","conv-a","m-1",NOW-timedelta(days=1))
        self.message("10001","conv-a","m-2",NOW)
        self.message("10001","conv-a","m-3",NOW)
        self.message("10001","conv-b","m-4",NOW)
        weekly=report(self.store,days=7,now=NOW)
        self.assertEqual(weekly["imported_inbound_messages_window"],4)
        self.assertEqual(weekly["imported_conversations_window"],2)
        self.assertEqual(weekly["imported_messages_per_day"][-2]["imported_conversations"],1)
        self.assertEqual(weekly["imported_messages_per_day"][-1]["imported_conversations"],2)
        self.assertEqual(sum(item["imported_conversations"]
                         for item in weekly["imported_messages_per_day"]),3)
        self.assertIsNone(weekly["imported_unique_buyers_window"])

    def test_empty_data_and_invalid_window_and_clock(self):
        empty=report(self.store,days=7,now=NOW)
        self.assertEqual(empty["own_ads_with_inventory_history"],0)
        self.assertIsNone(empty["imported_unique_buyers_window"])
        self.assertFalse(empty["unique_buyer_count_supported"])
        self.assertTrue(all(row["imported_conversations"] == 0
                            for row in empty["imported_messages_per_day"]))
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
        self.assertTrue(all(row["imported_conversations"] == 0
                            for row in data["imported_messages_per_day"]))
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
