from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.portfolio_existing_jobs import (
    ExistingJobError, change_state, claim_next, create_job, details,
    eligible_ad_ids, finish_claim, process_one, review_items,
)
from mark_api.storage import SnapshotStore


class ExistingPortfolioJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=SnapshotStore(Path(self.tmp.name)/"store.sqlite",create_if_missing=True)
        self.now=datetime(2026,10,29,tzinfo=timezone.utc)

    def add(self, ad_id, *, source="kleinanzeigen-management", state=LifecycleState.ACTIVE,
            title=None, observed=None):
        self.store.append_ad_snapshot(AdSnapshot(ad_id=ad_id,observed_at=observed or self.now,
            source=source,lifecycle_state=state,title=title or "Lampe "+ad_id,
            description="Belegte Originalbeschreibung"))

    def test_1000_frozen_owned_ads_pagination_idempotence_budget_and_approval(self):
        ids=[f"{i:05d}" for i in range(1000)]
        for ad_id in ids: self.add(ad_id)
        self.assertEqual(eligible_ad_ids(self.store),ids)
        args=dict(instruction="Sachlich optimieren",request_key="a"*64,
                  budget_cents=20,estimate_cents=20)
        job=create_job(self.store,ids,**args); jid=job["job_id"]
        self.assertEqual(create_job(self.store,ids,**args)["job_id"],jid)
        first=details(self.store,jid); self.assertEqual(len(first["items"]),50)
        self.assertEqual(details(self.store,jid,after=first["next_after"])["items"][0]["ad_id"],ids[50])
        change_state(self.store,jid,"start")
        process_one(self.store,jid,lambda original,instruction:({"title":"Besser","description":original["description"]},7))
        self.assertEqual(details(self.store,jid)["actual_cents"],7)
        self.assertFalse(process_one(self.store,jid,lambda *_: self.fail("over budget"))["claimed"])
        self.assertEqual(details(self.store,jid)["state"],"budget_exhausted")
        review_items(self.store,jid,[ids[0]],approve=True)
        self.assertEqual(details(self.store,jid)["counts"]["approved"],1)

    def test_foreign_unknown_changed_and_ambiguous_call_fail_closed(self):
        self.add("owned")
        self.add("foreign",source="user-supplied-local-catalog")
        self.add("unknown",state=LifecycleState.UNKNOWN)
        self.assertEqual(eligible_ad_ids(self.store),["owned"])
        with self.assertRaises(ExistingJobError):
            create_job(self.store,["foreign"],instruction="x",request_key="b"*64,budget_cents=20,estimate_cents=20)
        jid=create_job(self.store,["owned"],instruction="x",request_key="c"*64,budget_cents=40,estimate_cents=20)["job_id"]
        change_state(self.store,jid,"start")
        with self.assertRaises(RuntimeError):
            process_one(self.store,jid,lambda *_:(_ for _ in ()).throw(RuntimeError("timeout")))
        self.assertEqual(details(self.store,jid)["state"],"needs_attention")
        with self.assertRaises(ExistingJobError): change_state(self.store,jid,"resume")

        jid=create_job(self.store,["owned"],instruction="y",request_key="d"*64,budget_cents=20,estimate_cents=20)["job_id"]
        change_state(self.store,jid,"start")
        process_one(self.store,jid,lambda o,i:({"title":"Neu","description":o["description"]},1))
        self.add("owned",title="Extern geändert",observed=self.now+timedelta(seconds=1))
        with self.assertRaises(ExistingJobError): review_items(self.store,jid,["owned"],approve=True)

    def test_all_accepts_exactly_all_four_verified_sources(self):
        verified = (
            "kleinanzeigen-management", "private-web-product-launcher",
            "private-web-owner-inventory", "private-web-periodic-readonly",
        )
        for index, source in enumerate(verified):
            self.add(f"owned-{index}", source=source)
        self.add("foreign", source="private-web-similar-but-unverified")
        self.assertEqual(eligible_ad_ids(self.store),
                         [f"owned-{index}" for index in range(4)])

    def test_utc_order_malformed_timestamp_and_changed_source_before_cost(self):
        self.add("keeper")
        self.add("offset", observed=datetime.fromisoformat("2026-10-29T10:30:00+02:00"))
        self.add("offset", source="user-supplied-local-catalog",
                 observed=datetime.fromisoformat("2026-10-29T09:00:00+00:00"))
        self.assertNotIn("offset", eligible_ad_ids(self.store))
        with self.assertRaises(ExistingJobError):
            create_job(self.store,["offset"],instruction="x",request_key="1"*64,
                       budget_cents=20,estimate_cents=20)

        self.add("bad-time")
        with self.store._connect() as conn:
            conn.execute("UPDATE ad_snapshots SET observed_at='not-a-time' WHERE ad_id='bad-time'")
        with self.assertRaisesRegex(ExistingJobError, "timestamp"):
            eligible_ad_ids(self.store)

        self.add("changed")
        jid=create_job(self.store,["changed"],instruction="x",request_key="2"*64,
                       budget_cents=20,estimate_cents=20)["job_id"]
        change_state(self.store,jid,"start")
        self.add("changed",source="user-supplied-local-catalog",
                 observed=self.now+timedelta(seconds=1))
        called=False
        def provider(*_args):
            nonlocal called; called=True
            return {"title":"Nie","description":"Nie"},1
        with self.assertRaises(ExistingJobError): process_one(self.store,jid,provider)
        self.assertFalse(called)
        self.assertEqual(details(self.store,jid)["state"],"needs_attention")
        self.assertEqual(details(self.store,jid)["items"][0]["error_code"],"source_changed")

    def test_schema_drift_and_provider_output_limits_fail_closed(self):
        self.add("bounded")
        jid=create_job(self.store,["bounded"],instruction="x",request_key="3"*64,
                       budget_cents=100,estimate_cents=20)["job_id"]
        change_state(self.store,jid,"start")
        claim=claim_next(self.store,jid)
        with self.assertRaises(ExistingJobError):
            finish_claim(self.store,jid,"bounded",claim["claim_token"],
                         {"title":"x"*66,"description":"ok"},actual_cents=1)
        with self.store._connect() as conn:
            conn.execute("ALTER TABLE portfolio_existing_jobs ADD COLUMN drift TEXT")
        with self.assertRaises(Exception): details(self.store,jid)


if __name__ == "__main__": unittest.main()
