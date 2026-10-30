"""Read-only P4 portfolio reports with authenticated local loopback HTTP."""
from __future__ import annotations
from datetime import datetime,timezone
from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from mark_api.dashboard import DashboardWriteProxy,create_server,_DASHBOARD_HTML,_DASHBOARD_JS
from mark_api.domain import AdSnapshot,LifecycleState
from mark_api.storage import SnapshotStore
from mark_api.portfolio_existing_jobs import (
    create_job as create_existing_job,
    change_state as change_existing_job_state,
    claim_next as claim_existing,
    mark_unknown as mark_existing_unknown,
    settle_unknown as settle_existing_unknown,
)


class ReportingHttpTests(unittest.TestCase):
    def setUp(self):
        folder=TemporaryDirectory(prefix="kleinanzeiger-report-http-")
        self.addCleanup(folder.cleanup)
        self.store=SnapshotStore(Path(folder.name)/"report.sqlite",create_if_missing=True)
        self.store.append_ad_snapshot(AdSnapshot(
          ad_id="123456789",title="Lampe",
          lifecycle_state=LifecycleState.ACTIVE,
          observed_at=datetime(2026,10,26,9,tzinfo=timezone.utc),
          source="private-web-product-launcher"))
        self.proxy=DashboardWriteProxy(host="127.0.0.1",port=9999,
             bearer_token="b"*32,ui_token="w"*32)
        self.server=create_server(self.store,port=0,write_proxy=self.proxy)
        self.worker=Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join,3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def read(self,path,*,token=True):
        conn=HTTPConnection("127.0.0.1",self.server.server_address[1],timeout=10)
        headers={"X-Mark-Dashboard-Write":"1",
                 "X-Mark-Dashboard-Token":"w"*32} if token else {}
        try:
            conn.request("GET",path,headers=headers)
            r=conn.getresponse()
            return r.status,json.loads(r.read())
        finally:conn.close()

    def test_authenticated_periods_and_strict_unknown_query(self):
        self.assertEqual(self.read("/api/portfolio/report?days=7",token=False)[0],403)
        for days in (7,30,90):
            code,data=self.read(f"/api/portfolio/report?days={days}")
            self.assertEqual(code,200,data)
            self.assertEqual(len(data["imported_messages_per_day"]),days)
            self.assertEqual(data["own_ads_with_inventory_history"],1)
            self.assertEqual(data["imported_inbound_messages_window"],0)
            self.assertIsNone(data["imported_unique_buyers_window"])
            self.assertFalse(data["unique_buyer_count_supported"])
            self.assertTrue(all(row["imported_conversations"] == 0
                                for row in data["imported_messages_per_day"]))
            self.assertEqual(data["manual_sales_current_all_time"]["manually_sold"],0)
            self.assertIsNone(data["ai_usage_estimate_all_time"])
            self.assertFalse(data["conversion_rate_supported"])
            self.assertFalse(data["revenue_period_attributed"])
            self.assertEqual(data["platform_writes"],0)
        for bad in (
            "/api/portfolio/report",
            "/api/portfolio/report?days=8",
            "/api/portfolio/report?days=07",
            "/api/portfolio/report?days=7&days=30",
            "/api/portfolio/report?days=7&side=write",
            "/api/portfolio/report?days=%FF",
        ):
            with self.subTest(path=bad):
                self.assertEqual(self.read(bad)[0],400)

    def test_published_ad_verified_unknown_cost_is_separate_in_local_http_report(self):
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id="123456789", title="Belegter Titel", description="Belegte Fakten",
            lifecycle_state=LifecycleState.ACTIVE,
            observed_at=datetime(2026,10,29,10,tzinfo=timezone.utc),
            source="kleinanzeigen-management",
        ))
        jid = create_existing_job(
            self.store, ["123456789"], instruction="Nur belegte Informationen",
            request_key="d"*64, budget_cents=100, estimate_cents=20,
        )["job_id"]
        change_existing_job_state(self.store, jid, "start")
        pending = claim_existing(self.store, jid)
        mark_existing_unknown(self.store, jid, "123456789",
                              pending["claim_token"])
        self.assertEqual(self.read("/api/portfolio/report?days=7",token=False)[0],403)
        code, unverified = self.read("/api/portfolio/report?days=7")
        self.assertEqual(code,200)
        ai = unverified["ai_usage_estimate_all_time"]
        self.assertEqual(ai["usd_cent_reserved"],20)
        self.assertEqual(ai["unknown_outcomes"],1)
        self.assertEqual(ai["usd_cent_known_total"],0)
        settle_existing_unknown(self.store, jid, "123456789",
                                actual_cents=7, explicitly_checked=True)
        code, settled = self.read("/api/portfolio/report?days=7")
        self.assertEqual(code,200)
        cost=settled["ai_usage_estimate_all_time"]
        self.assertEqual(cost["usd_cent_estimate"],0)
        self.assertEqual(cost["usd_cent_checked_unknown"],7)
        self.assertEqual(cost["usd_cent_known_total"],7)
        self.assertEqual(cost["usd_cent_reserved"],0)
        self.assertEqual(cost["unknown_outcomes"],0)
        self.assertFalse(cost["invoice_verified"])
        self.assertEqual(cost["source_breakdown"][
            "published_owned_ad_jobs"]["checked"],7)
        self.assertIsNone(cost["source_breakdown"]["local_draft_jobs"])
        self.assertEqual(settled["platform_writes"],0)
        # The imported account facts and uncertain provider requests are never
        # cleared or retried by the reporting HTTP handler.
        with self.store._connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT phase FROM portfolio_existing_job_items WHERE job_id=?",
                (jid,),
            ).fetchone()["phase"],"rejected")

    def test_absent_write_session_denies_report(self):
        other=create_server(self.store,port=0)
        worker=Thread(target=other.serve_forever,daemon=True)
        worker.start()
        try:
            conn=HTTPConnection("127.0.0.1",other.server_address[1],timeout=10)
            conn.request("GET","/api/portfolio/report?days=7")
            r=conn.getresponse()
            self.assertEqual(r.status,404)
            conn.close()
        finally:
            other.shutdown()
            other.server_close()
            worker.join(3)

    def test_controlling_ui_and_no_billable_or_platform_commands(self):
        for text in ('id="report-panel"','id="report-days"',
                     'id="report-metric"','id="report-daily-metric-heading"',
                     'id="report-refresh"','id="report-download"',
                     'id="report-daily-body"'):
            self.assertIn(text,_DASHBOARD_HTML)
        for text in ('/api/portfolio/report?days=',
                     'known_surplus_eur','revenue_period_attributed',
                     'imported_conversations','unique_buyer_count_supported'):
            self.assertIn(text,_DASHBOARD_JS)


if __name__=="__main__":
    unittest.main()
