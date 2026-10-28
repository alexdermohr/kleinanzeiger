"""P2 durable, bounded offline queue contracts. No provider/network calls."""
from __future__ import annotations

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import sqlite3
import tempfile
import unittest

from mark_api.portfolio_drafts import _DDL
from mark_api.portfolio_jobs import (
    PortfolioJobError, apply_approved_local, change_state, claim_next, create_job,
    details, finish_claim, list_jobs, mark_unknown, process_one,
    recover_interrupted, review_items, settle_unknown,
)
from mark_api.storage import SnapshotStore


class PortfolioJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/"ads.sqlite"
        self.store=SnapshotStore(self.db,create_if_missing=True)

    def seed(self, count: int) -> list[str]:
        keys=[f"SKU-{i:05d}" for i in range(count)]
        with closing(self.store._connect()) as conn,conn:
            conn.execute(_DDL)
            conn.executemany(
                "INSERT INTO portfolio_drafts "
                "(sku,title,description,category_json,price_eur,images_json,"
                "source_sha256,source_name,created_at,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,'draft')",
                ((key,f"Titel {key}","Eigene Fakten","[\"Haus\",\"Dekor\"]",19,
                  "[]","0"*64,"test.csv","2026-10-26T00:00:00Z")
                 for key in keys))
        return keys

    def create(self, keys: list[str], *, k: int=1, budget: int=10000,
               estimate: int=2) -> dict:
        return create_job(self.store,keys,instruction="Titel verbessern ohne Fakten zu erfinden",
                          request_key=f"{k:064x}",budget_cents=budget,
                          estimate_cents=estimate)

    @staticmethod
    def mock_model(original: dict, instruction: str) -> tuple[dict,int]:
        assert "ohne Fakten" in instruction
        return ({"title":"Verbessert "+original["title"],
                 "description":original["description"]+"; lesbarer"},1)

    def test_one_thousand_selection_restart_and_idempotent_progress(self) -> None:
        keys=self.seed(1000)
        job=self.create(keys)
        self.assertEqual(job["total"],1000)
        self.assertEqual(len(details(self.store,job["job_id"])["items"]),50)
        self.assertTrue(details(self.store,job["job_id"])["has_more"])
        self.assertEqual(job["job_id"],self.create(keys)["job_id"])
        with self.assertRaises(PortfolioJobError):
            self.create(keys[:-1])
        change_state(self.store,job["job_id"],"start")
        change_state(self.store,job["job_id"],"pause")
        with self.assertRaises(PortfolioJobError):
            claim_next(self.store,job["job_id"])
        change_state(self.store,job["job_id"],"resume")
        claimed=claim_next(self.store,job["job_id"])
        self.assertEqual(claimed["reserved_cents"],2)
        # A killed caller never repeats a billable request during restart.
        fresh=SnapshotStore(self.db,create_if_missing=False,require_existing_schema=True)
        recovered=recover_interrupted(fresh,job["job_id"])
        self.assertEqual(recovered["state"],"needs_attention")
        self.assertEqual(recovered["counts"]["unknown"],1)
        with self.assertRaises(PortfolioJobError):
            change_state(fresh,job["job_id"],"resume")
        with self.assertRaises(PortfolioJobError):
            finish_claim(fresh,job["job_id"],claimed["sku"],claimed["claim_token"],
                         {"title":"Already gone","description":"Original"},actual_cents=0)
        with self.assertRaises(PortfolioJobError):
            settle_unknown(fresh,job["job_id"],claimed["sku"],
                           actual_cents=1,explicitly_checked=False)
        self.assertEqual(settle_unknown(fresh,job["job_id"],claimed["sku"],
                         actual_cents=1,explicitly_checked=True)["state"],"paused")
        change_state(fresh,job["job_id"],"resume")
        next_one=process_one(fresh,job["job_id"],self.mock_model)
        self.assertEqual(next_one["sku"],keys[1])
        self.assertEqual(next_one["progress"]["actual_cents"],2)
        self.assertEqual(next_one["progress"]["reserved_cents"],0)
        self.assertEqual(list_jobs(fresh)[0]["job_id"],job["job_id"])

    def test_budget_quota_and_no_accidental_retry(self) -> None:
        keys=self.seed(10)
        job=self.create(keys,budget=3,estimate=2)
        jid=job["job_id"]
        change_state(self.store,jid,"start")
        self.assertTrue(process_one(self.store,jid,self.mock_model)["claimed"])
        self.assertTrue(process_one(self.store,jid,self.mock_model)["claimed"])
        self.assertFalse(process_one(self.store,jid,self.mock_model)["claimed"])
        info=details(self.store,jid)
        self.assertEqual(info["state"],"budget_exhausted")
        self.assertEqual(info["counts"]["queued"],8)
        self.assertEqual(info["actual_cents"],2)
        self.assertEqual(info["platform_writes"],0)
        change_state(self.store,jid,"resume")
        self.assertIsNone(claim_next(self.store,jid))
        self.assertEqual(details(self.store,jid)["state"],"budget_exhausted")

    def test_generator_exception_stops_and_preserves_unknown(self) -> None:
        keys=self.seed(3)
        jid=self.create(keys)["job_id"]
        change_state(self.store,jid,"start")
        called=[]
        def fails(original, instruction):
            called.append(original["sku"])
            raise RuntimeError("provider timeout after possible charge")
        with self.assertRaises(RuntimeError):
            process_one(self.store,jid,fails)
        self.assertEqual(called,[keys[0]])
        info=details(self.store,jid)
        self.assertEqual(info["state"],"needs_attention")
        self.assertEqual(info["reserved_cents"],2)
        self.assertEqual(info["items"][0]["phase"],"unknown")
        with self.assertRaises(PortfolioJobError):
            change_state(self.store,jid,"resume")
        settle_unknown(self.store,jid,keys[0],actual_cents=2,explicitly_checked=True)
        self.assertEqual(details(self.store,jid)["actual_cents"],2)

    def test_settled_unknown_without_proposal_cannot_be_approved_or_block_valid_draft(self) -> None:
        keys = self.seed(2)
        jid = self.create(keys)["job_id"]
        change_state(self.store, jid, "start")
        claimed = claim_next(self.store, jid)
        self.assertEqual(claimed["sku"], keys[0])
        mark_unknown(self.store, jid, keys[0], claimed["claim_token"])
        settle_unknown(self.store, jid, keys[0],
                       actual_cents=1, explicitly_checked=True)
        change_state(self.store, jid, "resume")
        produced = process_one(self.store, jid, self.mock_model)
        self.assertEqual(produced["sku"], keys[1])

        # The settled UNKNOWN has no proposal_json. Re-approving it together
        # with a valid suggestion previously broke the entire draft apply.
        with self.assertRaises(PortfolioJobError):
            review_items(self.store, jid, keys, approve=True)
        current = details(self.store, jid)
        self.assertEqual(current["counts"]["rejected"], 1)
        self.assertEqual(current["counts"]["proposed"], 1)
        self.assertEqual(current["counts"].get("approved", 0), 0)

        review_items(self.store, jid, [keys[1]], approve=True)
        self.assertEqual(apply_approved_local(self.store, jid)["applied"], 1)
        with closing(self.store._connect()) as conn:
            titles = dict(conn.execute(
                "SELECT sku, title FROM portfolio_drafts ORDER BY sku COLLATE BINARY"
            ).fetchall())
        self.assertEqual(titles[keys[0]], "Titel " + keys[0])
        self.assertEqual(titles[keys[1]], "Verbessert Titel " + keys[1])
        self.assertEqual(details(self.store, jid)["actual_cents"], 2)

    def test_atomic_review_and_local_application_with_stale_source(self) -> None:
        keys=self.seed(3)
        jid=self.create(keys)["job_id"]
        change_state(self.store,jid,"start")
        for _ in range(2):
            process_one(self.store,jid,self.mock_model)
        with self.assertRaises(PortfolioJobError):
            review_items(self.store,jid,[keys[0],keys[2]],approve=True)
        self.assertEqual(details(self.store,jid)["counts"]["proposed"],2)
        review_items(self.store,jid,keys[:2],approve=True)
        # Concurrent manual change on second draft: no partial bulk update.
        with closing(self.store._connect()) as conn,conn:
            conn.execute("UPDATE portfolio_drafts SET description='Extern geaendert' WHERE sku=?",
                         (keys[1],))
        with self.assertRaises(PortfolioJobError):
            apply_approved_local(self.store,jid)
        with closing(self.store._connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT title FROM portfolio_drafts WHERE sku=?",
                (keys[0],)).fetchone()[0],"Titel "+keys[0])
            conn.execute("UPDATE portfolio_drafts SET description='Eigene Fakten' "
                         "WHERE sku=?",(keys[1],))
            conn.commit()
        applied=apply_approved_local(self.store,jid)
        self.assertEqual(applied,{"job_id":jid,"applied":2,"platform_writes":0})
        self.assertEqual(apply_approved_local(self.store,jid)["applied"],0)
        self.assertEqual(details(self.store,jid)["counts"]["applied_local"],2)

    def test_legacy_approved_entry_without_proposal_fails_atomically(self) -> None:
        keys = self.seed(2)
        jid = self.create(keys)["job_id"]
        change_state(self.store, jid, "start")
        for _ in keys:
            process_one(self.store, jid, self.mock_model)
        review_items(self.store, jid, keys, approve=True)
        with closing(self.store._connect()) as conn, conn:
            conn.execute(
                "UPDATE portfolio_job_items SET proposal_json=NULL "
                "WHERE job_id=? AND sku=? COLLATE BINARY",
                (jid, keys[0]),
            )
        # A pre-existing legacy approved row must never leave a partial draft
        # change or crash with an unrelated JSON TypeError.
        with self.assertRaises(PortfolioJobError):
            apply_approved_local(self.store, jid)
        with closing(self.store._connect()) as conn:
            titles = dict(conn.execute(
                "SELECT sku, title FROM portfolio_drafts ORDER BY sku COLLATE BINARY"
            ).fetchall())
        self.assertEqual(titles[keys[0]], "Titel " + keys[0])
        self.assertEqual(titles[keys[1]], "Titel " + keys[1])
        review_items(self.store, jid, [keys[0]], approve=False)
        self.assertEqual(apply_approved_local(self.store, jid)["applied"], 1)
        self.assertEqual(details(self.store, jid)["counts"]["rejected"], 1)

    def test_validation_and_cancel_prevents_mutation(self) -> None:
        keys=self.seed(100)
        with self.assertRaises(PortfolioJobError):
            self.create(keys+["MISSING"])
        with self.assertRaises(PortfolioJobError):
            self.create([keys[0],keys[0]])
        self.assertFalse(list_jobs(self.store))
        jid=self.create(keys)["job_id"]
        change_state(self.store,jid,"cancel")
        with self.assertRaises(PortfolioJobError):
            change_state(self.store,jid,"start")
        with self.assertRaises(PortfolioJobError):
            apply_approved_local(self.store,jid)

    def test_parallel_claims_are_unique(self) -> None:
        keys=self.seed(100)
        jid=self.create(keys,budget=1000)["job_id"]
        change_state(self.store,jid,"start")
        def try_claim(_):
            try:
                return claim_next(self.store,jid)
            except PortfolioJobError:
                return None
        with ThreadPoolExecutor(max_workers=6) as pool:
            claims=list(pool.map(try_claim,range(30)))
        self.assertEqual(len([c for c in claims if c is not None]),1)
        self.assertEqual(details(self.store,jid)["counts"]["inflight"],1)
        self.assertEqual(details(self.store,jid)["reserved_cents"],2)
        with self.assertRaises(PortfolioJobError):
            claim_next(self.store,jid)

    def test_five_thousand_page_size_and_sqlite_backup_roundtrip(self) -> None:
        keys=self.seed(5000)
        job=self.create(keys,budget=20000)
        self.assertEqual(job["total"],5000)
        first=details(self.store,job["job_id"],limit=50)
        self.assertTrue(first["has_more"])
        second=details(self.store,job["job_id"],after=first["next_after"])
        self.assertEqual(first["items"][-1]["sku"],keys[49])
        self.assertEqual(second["items"][0]["sku"],keys[50])
        dest=Path(self.temp.name)/"backup.sqlite"
        with closing(self.store._connect()) as source, closing(sqlite3.connect(dest)) as backup:
            source.backup(backup)
        restored=SnapshotStore(dest,create_if_missing=False,require_existing_schema=True)
        self.assertEqual(details(restored,job["job_id"])["total"],5000)
        self.assertEqual(list_jobs(restored)[0]["job_id"],job["job_id"])

    def test_cli_uses_only_existing_database_and_replay_safe_requests(self) -> None:
        keys=self.seed(15)
        choice=Path(self.temp.name)/"selected-skus.txt"
        choice.write_text("\n".join(keys)+"\n",encoding="utf-8")
        def call(*argv: str, db: Path | None=None) -> tuple[int,dict|None]:
            args=[sys.executable,"-m","mark_api.portfolio_jobs_cli","--db",
                  str(db or self.db),*argv]
            proc=subprocess.run(args,capture_output=True,text=True,timeout=30)
            return proc.returncode,(json.loads(proc.stdout) if proc.stdout else None)
        missing=Path(self.temp.name)/"missing.sqlite"
        code,_=call("list",db=missing)
        self.assertEqual(code,2)
        self.assertFalse(missing.exists(),"CLI must not create a replacement DB")
        cmd=["create","--skus-file",str(choice),"--instruction","Nur Fakten",
             "--budget-cents","100","--estimate-cents","2"]
        code,first=call(*cmd)
        self.assertEqual(code,0)
        self.assertEqual(first["total"],15)
        code,replayed=call(*cmd)
        self.assertEqual(code,0)
        self.assertEqual(first["job_id"],replayed["job_id"])
        code,listed=call("list")
        self.assertEqual(code,0)
        self.assertEqual(listed["jobs"][0]["job_id"],first["job_id"])
        jid=first["job_id"]
        self.assertEqual(call("start",jid)[1]["state"],"running")
        self.assertEqual(call("pause",jid)[1]["state"],"paused")
        self.assertEqual(call("resume",jid)[1]["state"],"running")
        self.assertEqual(call("cancel",jid)[1]["state"],"cancelled")
        self.assertEqual(call("detail",jid)[1]["platform_writes"],0)

if __name__=="__main__":
    unittest.main()