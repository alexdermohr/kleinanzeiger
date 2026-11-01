"""Same-origin P2 job contract: local drafts only, no paid or platform request."""
from __future__ import annotations

from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from mark_api.dashboard import (
    DashboardGenerationAccess, DashboardWriteProxy, _DASHBOARD_HTML,
    _DASHBOARD_JS, create_server,
)
from mark_api.generation import AdvertisementGenerator
from mark_api.portfolio_provider import (
    PROVIDER_MODEL, PROVIDER_OUTPUT_TOKEN_LIMIT, _usage_cost_cents,
    PortfolioProviderError, generate_metered_proposal,
)
from mark_api.portfolio_drafts import _DDL
from mark_api.portfolio_jobs import details, process_one
from mark_api.storage import SnapshotStore


class FakeResponses:
    def __init__(self):
        self.calls=[]
        self.raise_error=False
        self.missing_usage=False

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_error:
            raise TimeoutError("synthetic ambiguous provider response")
        return SimpleNamespace(
            status="completed",
            usage=None if self.missing_usage else SimpleNamespace(
                input_tokens=500,output_tokens=120),
            output_text=json.dumps({
                "title":"Optimierter Titel",
                "description":"Originalbeschreibung",
            }),
        )


class PortfolioJobsHttpTests(unittest.TestCase):
    TOKEN = "t" * 32
    GENERATION_TOKEN = "g" * 32

    def setUp(self) -> None:
        temp = TemporaryDirectory(prefix="kleinanzeiger-p2-http-")
        self.addCleanup(temp.cleanup)
        self.database = Path(temp.name) / "kunden.sqlite"
        self.store = SnapshotStore(self.database, create_if_missing=True)
        self.proxy = DashboardWriteProxy(
            host="127.0.0.1", port=8999, bearer_token="b" * 32,
            ui_token=self.TOKEN,
        )
        self.fake_responses=FakeResponses()
        self.generation=DashboardGenerationAccess(
            ui_token=self.GENERATION_TOKEN, max_requests=1,
            generator_factory=lambda: AdvertisementGenerator(
                client=SimpleNamespace(responses=self.fake_responses)),
        )
        self.server = create_server(
            self.store,port=0,write_proxy=self.proxy,
            generation_access=self.generation,
        )
        self.worker = Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def fill(self, count: int) -> list[str]:
        skus = [f"SKU-{i:05d}" for i in range(count)]
        with closing(self.store._connect()) as conn, conn:
            conn.execute(_DDL)
            conn.executemany(
                "INSERT INTO portfolio_drafts "
                "(sku,title,description,category_json,price_eur,images_json,"
                "source_sha256,source_name,created_at,status) "
                "VALUES(?,?,?,?,?,?,?,?,?,'draft')",
                ((sku, "Lampe " + sku, "Originalbeschreibung", '["Haus","Licht"]',
                  29, '[]', '0'*64, "local.csv", "2026-10-26T00:00:00+00:00")
                 for sku in skus),
            )
        return skus

    def request(self, method: str, route: str, data: object | None = None,
                *, token: str | None = TOKEN, origin: str | None = "auto",
                raw: bytes | None = None,
                content_type: str = "application/json",
                headers_extra: dict[str,str] | None = None) -> tuple[int, dict]:
        port = self.server.server_address[1]
        conn = HTTPConnection("127.0.0.1", port, timeout=15)
        headers = {}
        if token is not None:
            headers["X-Mark-Dashboard-Write"] = "1"
            headers["X-Mark-Dashboard-Token"] = token
        if origin == "auto":
            origin = f"http://127.0.0.1:{port}"
        if origin is not None:
            headers["Origin"] = origin
        if headers_extra is not None:
            headers.update(headers_extra)
        if method == "POST":
            headers["Content-Type"] = content_type
            payload = raw if raw is not None else json.dumps(data).encode("utf-8")
        else:
            payload = None
        try:
            conn.request(method, route, body=payload, headers=headers)
            response = conn.getresponse()
            body = response.read()
            return response.status, json.loads(body.decode("utf-8"))
        finally:
            conn.close()

    def create(self, **overrides) -> tuple[int, dict]:
        payload = {
            "mode": "all", "skus": [], "search": "",
            "instruction": "Nur belegte Fakten hervorheben",
            "budget_cents": 2500, "estimate_cents": 2,
            "request_key": "f"*64,
        }
        payload.update(overrides)
        return self.request("POST", "/api/portfolio/jobs/create", payload)

    def test_1000_all_drafts_pagination_and_persistent_local_approval(self):
        skus = self.fill(1000)
        self.assertEqual(self.request("GET", "/api/portfolio/jobs")[0], 200)
        code, response = self.create()
        self.assertEqual(code, 200)
        self.assertEqual(response["total"], 1000)
        jid = response["job_id"]
        self.assertEqual(self.create()[1]["job_id"], jid)
        code, info = self.request("GET", f"/api/portfolio/jobs/detail?job_id={jid}")
        self.assertEqual(code, 200)
        self.assertEqual(len(info["items"]), 50)
        self.assertTrue(info["has_more"])
        next_page = self.request(
            "GET", f"/api/portfolio/jobs/detail?job_id={jid}&after={info['next_after']}"
        )[1]
        self.assertEqual(next_page["items"][0]["sku"], skus[50])
        self.assertEqual(self.request("POST", "/api/portfolio/jobs/state",
                                     {"job_id": jid, "action": "start"})[0], 200)
        self.assertEqual(details(self.store,jid)["state"], "running")
        self.assertEqual(details(self.store,jid)["counts"]["queued"], 1000)
        def mock(original, instruction):
            return {"title":"Optimierter Titel", "description":"Originalbeschreibung"}, 1
        process_one(self.store,jid,mock)
        self.assertEqual(self.request("POST", "/api/portfolio/jobs/review",
            {"job_id":jid,"skus":[skus[0]],"approve":True})[0],200)
        code, changed = self.request("POST", "/api/portfolio/jobs/apply-local", {"job_id":jid})
        self.assertEqual(code,200)
        self.assertEqual(changed["applied"],1)
        self.assertEqual(changed["platform_writes"],0)
        with closing(self.store._connect()) as conn:
            title = conn.execute(
                "SELECT title FROM portfolio_drafts WHERE sku=?", (skus[0],)
            ).fetchone()[0]
            self.assertEqual(title,"Optimierter Titel")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ad_snapshots").fetchone()[0],0)
        self.assertEqual(self.request("POST", "/api/portfolio/jobs/apply-local",
                                     {"job_id":jid})[1]["applied"],0)

    def test_5000_all_and_filtered_selection_not_truncated(self):
        skus=self.fill(5000)
        code,result=self.create(search="SKU-04",request_key="e"*64)
        self.assertEqual(code,200)
        self.assertEqual(result["total"],1000)
        code,result=self.create(request_key="d"*64)
        self.assertEqual(code,200)
        self.assertEqual(result["total"],5000)
        self.assertEqual(self.request("GET","/api/portfolio/jobs?limit=1")[1]["jobs"][0]["total"],5000)
        self.assertEqual(self.create(request_key="c"*64,mode="selected",
                                     skus=skus[:500],search="")[1]["total"],500)
        # Up-front rejection of 5001 matches rather than partial job creation.
        with closing(self.store._connect()) as conn,conn:
            conn.execute(
                "INSERT INTO portfolio_drafts "
                "(sku,title,description,category_json,price_eur,images_json,"
                "source_sha256,source_name,created_at,status) "
                "VALUES(?,?,?,?,?,?,?,?,?,'draft')",
                ("SKU-05000","Lampe 5000","Originalbeschreibung",
                 '["Haus","Licht"]',29,'[]','0'*64,"local.csv","2026-10-26T00:00:00+00:00"),
            )
        code,error=self.create(request_key="b"*64)
        self.assertEqual(code,400)
        self.assertEqual(error["error"],"invalid_portfolio_job_command")

    def test_session_origin_input_idempotence_and_no_side_effect(self):
        self.fill(2)
        self.assertEqual(self.request("GET","/api/portfolio/jobs",token=None)[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/jobs/create",{},
                                     token=None)[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/jobs/create",{},
                                     origin="http://evil.example")[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/jobs/create",{},
                                     origin=None)[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/jobs/create",{},
                                     token="bad")[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/jobs/create",{},
                                     content_type="text/plain")[0],400)
        # Windows must receive the HTTP 400 even when the rejected POST
        # carries enough unread bytes to trigger a TCP abort on close.
        self.assertEqual(self.request("POST","/api/portfolio/jobs/create",
                                     raw=b"{}"+b" "*65536,
                                     content_type="text/plain")[0],400)
        self.assertEqual(self.request("POST","/api/portfolio/jobs/create",{},
                                     raw=b'{"mode":"all","mode":"all"}')[0],400)
        self.assertEqual(self.request("POST","/api/portfolio/jobs/create?unsafe=1",{})[0],404)
        self.assertEqual(self.request("GET","/api/portfolio/jobs?limit=100")[0],400)
        self.assertEqual(self.create(request_key="not-sha256")[0],400)
        self.assertEqual(self.request("GET","/api/portfolio/jobs")[1]["jobs"],[])
        code,first=self.create()
        self.assertEqual(code,200)
        self.assertEqual(self.create(instruction="Anders")[0],400)
        self.assertEqual(len(self.request("GET","/api/portfolio/jobs")[1]["jobs"]),1)
        self.assertEqual(first["platform_writes"],0)

    def test_provider_requires_both_sessions_and_explicit_cost_consent(self):
        self.fill(3)
        code, job = self.create(estimate_cents=20)
        self.assertEqual(code,200)
        jid=job["job_id"]
        self.assertEqual(self.request("POST","/api/portfolio/jobs/state",
            {"job_id":jid,"action":"start"})[0],200)
        path="/api/portfolio/jobs/generate-next"
        consent={"job_id":jid,"acknowledge_provider_cost":True}
        generation_headers={
            "X-Mark-Generation":"1",
            "X-Mark-Generation-Token":self.GENERATION_TOKEN,
        }
        self.assertEqual(self.request("POST",path,consent)[0],403)
        self.assertEqual(self.request("POST",path,consent,
            headers_extra={"X-Mark-Generation":"1",
                           "X-Mark-Generation-Token":"wrong"})[0],403)
        self.assertEqual(self.request("POST",path,{"job_id":jid,
            "acknowledge_provider_cost":False},
            headers_extra=generation_headers)[0],400)
        self.assertEqual(self.request("POST",path,consent,token=None,
            headers_extra=generation_headers)[0],403)
        self.assertEqual(self.request("POST",path,consent,origin="http://evil.test",
            headers_extra=generation_headers)[0],403)
        self.assertEqual(self.fake_responses.calls,[])
        code, answer=self.request("POST",path,consent,
            headers_extra=generation_headers)
        self.assertEqual(code,200,answer)
        self.assertTrue(answer["claimed"])
        self.assertEqual(len(self.fake_responses.calls),1)
        sent=self.fake_responses.calls[0]
        self.assertEqual(sent["model"],PROVIDER_MODEL)
        self.assertEqual(sent["max_output_tokens"],PROVIDER_OUTPUT_TOKEN_LIMIT)
        self.assertEqual(sent["store"],False)
        self.assertEqual(sent["reasoning"],{"effort":"none"})
        self.assertNotIn("images_json",sent["input"])
        self.assertEqual(details(self.store,jid)["counts"]["proposed"],1)
        self.assertEqual(self.request("POST",path,consent,
            headers_extra=generation_headers)[0],429)
        self.assertEqual(len(self.fake_responses.calls),1)
        self.assertEqual(self.store.tracked_ad_ids(),())

    def test_provider_unknown_quarantined_and_not_retried(self):
        self.fill(3)
        self.fake_responses.raise_error=True
        jid=self.create(request_key="2"*64,estimate_cents=20)[1]["job_id"]
        self.request("POST","/api/portfolio/jobs/state",
            {"job_id":jid,"action":"start"})
        consent={"job_id":jid,"acknowledge_provider_cost":True}
        headers={"X-Mark-Generation":"1",
                 "X-Mark-Generation-Token":self.GENERATION_TOKEN}
        code, result=self.request("POST","/api/portfolio/jobs/generate-next",
                                 consent,headers_extra=headers)
        self.assertEqual(code,502,result)
        self.assertEqual(result["error"],"provider_outcome_unknown")
        self.assertEqual(result["automatic_retry_authorized"],False)
        progress=details(self.store,jid)
        self.assertEqual(progress["state"],"needs_attention")
        self.assertEqual(progress["counts"]["unknown"],1)
        self.assertEqual(progress["reserved_cents"],20)
        self.assertEqual(self.request("POST","/api/portfolio/jobs/generate-next",
                         consent,headers_extra=headers)[0],429)
        self.assertEqual(len(self.fake_responses.calls),1)

    def test_provider_usage_cost_is_bounded_estimate(self):
        self.assertEqual(_usage_cost_cents(SimpleNamespace(
            input_tokens=500,output_tokens=120)),(500,120,1))
        with self.assertRaises(PortfolioProviderError):
            _usage_cost_cents(SimpleNamespace(input_tokens=False,output_tokens=3))
        with self.assertRaises(PortfolioProviderError):
            _usage_cost_cents(SimpleNamespace(input_tokens=20,output_tokens=1201))
        self.fill(1)
        original={"title":"Lampe","description":"Originalbeschreibung",
                  "category_json":'["Haus","Licht"]',"price_eur":29}
        generator=AdvertisementGenerator(client=SimpleNamespace(
            responses=self.fake_responses))
        proposal, charge=generate_metered_proposal(generator,original,"Titel verbessern")
        self.assertEqual(proposal["title"],"Optimierter Titel")
        self.assertEqual(charge,1)
        self.fake_responses.missing_usage=True
        with self.assertRaises(PortfolioProviderError):
            generate_metered_proposal(generator,original,"Titel verbessern")

    def test_web_ui_has_bound_local_actions_and_parses(self):
        for marker in ("id=\"jobs-panel\"", "id=\"job-all-matching\"",
                       "id=\"job-create\"", "id=\"job-items-body\"",
                       "id=\"job-approve\"", "id=\"job-reject\"", "id=\"job-apply-local\"",
                       "id=\"nav-jobs\""):
            self.assertIn(marker,_DASHBOARD_HTML)
        for marker in ("getRandomValues","/api/portfolio/jobs/",
                       "Keine Plattformänderung","loadJobDetails",
                       "renderJobSelection","writeUiReady()"):
            self.assertIn(marker,_DASHBOARD_JS)
        if shutil.which("node"):
            result=subprocess.run(
                [shutil.which("node"), "-e",
                 "new(require('node:vm').Script)(require('node:fs').readFileSync(0,'utf8'))"],
                input=_DASHBOARD_JS, text=True, encoding="utf-8", capture_output=True, timeout=15,
            )
            self.assertEqual(result.returncode,0,result.stderr)


if __name__=="__main__":
    unittest.main()
