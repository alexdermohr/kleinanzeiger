"""P5 local templates and attention counts use no platform or provider."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime,timezone,timedelta
from pathlib import Path
import tempfile
import unittest
from mark_api.domain import AdSnapshot,LifecycleState
from mark_api.portfolio_templates import (
    WorkTemplateError,attention_counts,list_templates,mutate_template)
from mark_api.portfolio_sales import update_manual_sale
from mark_api.portfolio_jobs import create_job,change_state,process_one
from mark_api.portfolio_drafts import _DDL
from mark_api.storage import SnapshotStore

class TemplatesTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store=SnapshotStore(Path(tmp.name)/"state.sqlite",create_if_missing=True)

    def payload(self, key="a"*64, template_id=None, revision=0,
                name="Neutral", instruction="Nur belegte Fakten", delete=False):
        return {
          "request_key":key,"template_id":template_id,"expected_revision":revision,
          "name":name if not delete else None,
          "instruction":instruction if not delete else None,
          "delete":delete,
        }

    def test_replay_update_conflict_delete(self):
        request=self.payload()
        first=mutate_template(self.store,request)
        self.assertEqual(first["revision"],1)
        self.assertEqual(mutate_template(self.store,request),first)
        self.assertEqual(first["platform_writes"],0)
        self.assertEqual(len(list_templates(self.store)["templates"]),1)
        with self.assertRaises(WorkTemplateError):
            mutate_template(self.store,self.payload(instruction="Anders"))
        with self.assertRaises(WorkTemplateError):
            mutate_template(self.store,self.payload(key="b"*64,name="neutral"))
        edited=self.payload(key="c"*64,template_id=first["template_id"],
                            revision=1,name="Sammler",instruction="Sachlicher Titel")
        result=mutate_template(self.store,edited)
        self.assertEqual(result["revision"],2)
        self.assertEqual(mutate_template(self.store,edited),result)
        with self.assertRaises(WorkTemplateError):
            mutate_template(self.store,self.payload(key="d"*64,
                template_id=first["template_id"],revision=1))
        delete=self.payload(key="e"*64,template_id=first["template_id"],
                            revision=2,delete=True)
        self.assertTrue(mutate_template(self.store,delete)["deleted"])
        self.assertTrue(mutate_template(self.store,delete)["deleted"])
        self.assertEqual(list_templates(self.store)["templates"],[])
        with closing(self.store._connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM portfolio_template_requests").fetchone()[0],3)

    def test_max_100_templates_and_invalid_text(self):
        self.assertEqual(list_templates(self.store)["templates"],[])
        for i in range(100):
            mutate_template(self.store,self.payload(
                key=f"{i+1:064x}",name=f"Profil {i:03d}"))
        self.assertEqual(len(list_templates(self.store)["templates"]),100)
        with self.assertRaises(WorkTemplateError):
            mutate_template(self.store,self.payload(key="f"*64,name="Overflow"))
        for value in ("","\u0000","x"*1501):
            with self.assertRaises(WorkTemplateError):
                mutate_template(self.store,self.payload(
                    key="e"*64,instruction=value))
        with self.assertRaises(WorkTemplateError):
            mutate_template(self.store,self.payload(key="z"*64))
        with self.assertRaises(WorkTemplateError):
            mutate_template(self.store,self.payload(key="e"*64,revision=True))

    def test_passive_alerts_without_provider_or_platform_writes(self):
        for n in (1,2):
            self.store.append_ad_snapshot(AdSnapshot(
              ad_id=str(n),title="Lampe",lifecycle_state=LifecycleState.ACTIVE,
              source="private-web-product-launcher",
              observed_at=datetime(2026,10,26,12,tzinfo=timezone.utc)))
        def sale(ad_id,kind,key):
            return update_manual_sale(self.store,{
                "ad_id":ad_id,"state":kind,
                "revenue_eur":30 if kind=="sold" else None,
                "cost_eur":None,"location":"Regal","note":"",
                "expected_revision":0,"request_key":key})
        sale("1","sold","a"*64)
        sale("2","reserved","b"*64)
        with closing(self.store._connect()) as conn,conn:
            conn.execute("UPDATE portfolio_sale_states SET updated_at=? "
                         "WHERE ad_id='2'",
                         ((datetime.now(timezone.utc)-timedelta(days=20)).isoformat(),))
            conn.execute(_DDL)
            conn.executemany(
                "INSERT INTO portfolio_drafts "
                "(sku,title,description,category_json,price_eur,images_json,"
                "source_sha256,source_name,created_at,status) "
                "VALUES(?,?,?,?,?,?,?,?,?,'draft')",
                [(f"SKU-{n}","Lampe","Zustand gut",'["Haus","Licht"]',
                  30,"[]","0"*64,"test.csv","2026-10-26T00:00:00+00:00")
                 for n in range(2)])
        job=create_job(self.store,["SKU-0","SKU-1"],instruction="Verbessern",
                       request_key="c"*64,budget_cents=200,estimate_cents=20)
        change_state(self.store,job["job_id"],"start")
        process_one(self.store,job["job_id"],lambda old,instr:
            ({"title":"Bessere Lampe","description":"Zustand gut"},1))
        state=attention_counts(self.store)
        self.assertEqual(state["proposals_to_review"],1)
        self.assertEqual(state["unknown_ki_outcomes"],0)
        self.assertEqual(state["manual_sales_missing_cost"],1)
        self.assertEqual(state["old_local_reservations"],1)
        self.assertEqual(state["provider_calls"],0)
        self.assertEqual(state["platform_writes"],0)
        # Cancellation does not rewrite prior item phases. Their proposals
        # are no longer reviewable and must disappear from the attention badge.
        change_state(self.store, job["job_id"], "cancel")
        cancelled=attention_counts(self.store)
        self.assertEqual(cancelled["proposals_to_review"],0)
        self.assertEqual(cancelled["unknown_ki_outcomes"],0)
        self.assertEqual(cancelled["manual_sales_missing_cost"],1)
        self.assertEqual(cancelled["old_local_reservations"],1)
        self.assertEqual(cancelled["platform_writes"],0)

if __name__=="__main__":
    unittest.main()