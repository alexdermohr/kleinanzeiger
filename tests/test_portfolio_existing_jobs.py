from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.portfolio_existing_jobs import (
    ExistingJobError, change_state, claim_next, create_job, details,
    eligible_ad_ids, finish_claim, process_one, recover_interrupted, review_items,
    settle_unknown,
)
from mark_api.results import ReadResult
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

    def test_10000_verified_owned_ads_job_and_10001_rejection_are_atomic(self):
        # A single synthetic observation transaction avoids 10,000 setup commits.
        ids = [f"{i:05d}" for i in range(10_000)]
        records = tuple(AdSnapshot(
            ad_id=ad_id, observed_at=self.now,
            source="kleinanzeigen-management", lifecycle_state=LifecycleState.ACTIVE,
            title="Lampe " + ad_id, description="Belegte Originalbeschreibung",
        ) for ad_id in ids)
        self.assertEqual(self.store.append_inventory_result(
            ReadResult.success_nonempty(records), source="kleinanzeigen-management",
            observed_at=self.now), 10_000)
        self.assertEqual(eligible_ad_ids(self.store), ids)
        args = dict(instruction="Nur belegte Angaben", request_key="7"*64,
                    budget_cents=20, estimate_cents=20)
        job = create_job(self.store, ids, **args)
        jid = job["job_id"]
        self.assertEqual(job["total"], 10_000)
        self.assertEqual(job["platform_writes"], 0)
        self.assertEqual(create_job(self.store, ids, **args)["job_id"], jid)
        page = details(self.store, jid)
        self.assertEqual(len(page["items"]), 50)
        self.assertTrue(page["has_more"])
        self.assertEqual(details(self.store, jid, after=page["next_after"])["items"][0]["ad_id"], ids[50])
        change_state(self.store, jid, "start")
        first = claim_next(self.store, jid)
        self.assertEqual(first["ad_id"], ids[0])
        pending = recover_interrupted(self.store, jid)
        self.assertEqual(pending["counts"]["unknown"], 1)
        self.assertEqual(pending["reserved_cents"], 20)
        with self.assertRaises(ExistingJobError):
            change_state(self.store, jid, "resume")
        settled = settle_unknown(self.store, jid, ids[0], actual_cents=6,
                                 explicitly_checked=True)
        self.assertEqual(settled["state"], "paused")
        self.assertEqual(settled["counts"]["queued"], 9_999)
        self.assertEqual(settled["actual_cents"], 6)
        self.assertEqual(settled["platform_writes"], 0)
        self.add("overflow")
        with self.assertRaisesRegex(ExistingJobError, "more than 10000"):
            eligible_ad_ids(self.store)
        with self.assertRaises(ExistingJobError):
            create_job(self.store, ids + ["overflow"], **{**args, "request_key":"6"*64})
        self.assertEqual(details(self.store, jid)["total"], 10_000)
        with self.store._connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM portfolio_existing_jobs").fetchone()[0], 1)

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

    def test_interrupted_paid_claim_quarantined_and_manually_settled(self):
        for ad_id in ("10501", "10502"):
            self.add(ad_id)
        job = create_job(self.store, ["10501", "10502"], instruction="Nur Fakten",
                         request_key="9"*64,budget_cents=60,estimate_cents=20)
        jid = job["job_id"]
        change_state(self.store, jid, "start")
        started = claim_next(self.store, jid)
        self.assertEqual(started["ad_id"], "10501")
        self.assertEqual(details(self.store,jid)["reserved_cents"], 20)
        with self.assertRaises(ExistingJobError):
            claim_next(self.store, jid)
        recovered = recover_interrupted(self.store,jid)
        self.assertEqual(recovered["state"], "needs_attention")
        self.assertEqual(recovered["counts"]["unknown"],1)
        self.assertEqual(recovered["reserved_cents"],20)
        self.assertEqual(recover_interrupted(self.store,jid),recovered)
        with self.assertRaises(ExistingJobError):
            change_state(self.store,jid,"resume")
        for invalid in (False, None, 0, "yes"):
            with self.subTest(checked=invalid), self.assertRaises(ExistingJobError):
                settle_unknown(self.store,jid,"10501",actual_cents=7,
                               explicitly_checked=invalid)
        for invalid in (-1, True, 1.5, 100_000_001):
            with self.subTest(cents=invalid), self.assertRaises(ExistingJobError):
                settle_unknown(self.store,jid,"10501",actual_cents=invalid,
                               explicitly_checked=True)
        with self.assertRaises(ExistingJobError):
            settle_unknown(self.store,jid,"10502",actual_cents=7,
                           explicitly_checked=True)
        resolved = settle_unknown(self.store,jid,"10501",actual_cents=7,
                                  explicitly_checked=True)
        self.assertEqual(resolved["state"],"paused")
        self.assertEqual(resolved["counts"]["rejected"],1)
        self.assertEqual(resolved["counts"]["queued"],1)
        self.assertEqual(resolved["reserved_cents"],0)
        self.assertEqual(resolved["actual_cents"],7)
        self.assertEqual(details(self.store,jid)["items"][0]["error_code"],
                         "manually_settled")
        with self.assertRaises(ExistingJobError):
            finish_claim(self.store,jid,"10501",started["claim_token"],
                         {"title":"Outdated","description":"Unknown"},actual_cents=1)
        with self.assertRaises(ExistingJobError):
            settle_unknown(self.store,jid,"10501",actual_cents=7,
                           explicitly_checked=True)
        change_state(self.store,jid,"resume")
        seen=[]
        response=process_one(self.store,jid,lambda original,instruction:(
            seen.append((original["ad_id"],instruction))
            or {"title":"Freigabe", "description":original["description"]},4))
        self.assertTrue(response["claimed"])
        self.assertEqual(seen,[("10502","Nur Fakten")])
        self.assertEqual(details(self.store,jid)["actual_cents"],11)
        self.assertEqual(details(self.store,jid)["counts"]["rejected"],1)
        self.assertEqual(details(self.store,jid)["platform_writes"],0)
        # A checked UNKNOWN is deliberately proposal-free; a user may not
        # later turn that discarded call into an accepted AI suggestion.
        with self.assertRaisesRegex(ExistingJobError, "no provider proposal"):
            review_items(self.store,jid,["10501"],approve=True)
        with self.assertRaisesRegex(ExistingJobError, "no provider proposal"):
            review_items(self.store,jid,["10501","10502"],approve=True)
        after=details(self.store,jid)
        self.assertEqual(after["actual_cents"],11)
        self.assertEqual(after["counts"]["rejected"],1)
        self.assertEqual(after["counts"]["proposed"],1)
        self.assertEqual(after["platform_writes"],0)
        # Existing genuine proposals remain individually reviewable.
        approved=review_items(self.store,jid,["10502"],approve=True)
        self.assertEqual(approved["counts"]["approved"],1)
        self.assertEqual(approved["counts"]["rejected"],1)
        self.assertEqual(approved["actual_cents"],11)

    def test_existing_unknown_requires_manual_settlement_even_without_restart(self):
        self.add("10503")
        jid=create_job(self.store,["10503"],instruction="x",
            request_key="8"*64,budget_cents=25,estimate_cents=20)["job_id"]
        change_state(self.store,jid,"start")
        with self.assertRaises(RuntimeError):
            process_one(self.store,jid,lambda *_: (_ for _ in ()).throw(
                RuntimeError("provider request outcome unknown")))
        before=details(self.store,jid)
        self.assertEqual(before["state"],"needs_attention")
        again = recover_interrupted(self.store,jid)
        self.assertEqual(again["counts"], before["counts"])
        self.assertEqual(again["state"], before["state"])
        self.assertEqual(again["reserved_cents"], before["reserved_cents"])
        self.assertEqual(settle_unknown(self.store,jid,"10503",actual_cents=0,
                                       explicitly_checked=True)["actual_cents"],0)

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
