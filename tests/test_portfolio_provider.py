"""Metered P2 provider contracts: NO real OpenAI network traffic or ads."""
from __future__ import annotations

from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
import unittest

from mark_api.dashboard import DashboardGenerationAccess, DashboardWriteProxy, create_server
from mark_api.generation import AdvertisementGenerator
from mark_api.portfolio_drafts import _DDL
from mark_api.portfolio_jobs import (
    change_state, create_job, details, PortfolioJobError,
)
from mark_api.portfolio_provider import (
    PROVIDER_MODEL, PROVIDER_REQUIRED_RESERVE_CENTS, PROVIDER_OUTPUT_TOKEN_LIMIT,
    PortfolioProviderError, _prompt, _usage_cost_cents, generate_metered_proposal,
)
from mark_api.storage import SnapshotStore


ORIGINAL = {
    "sku": "SKU-001", "title": "Echte Lampe", "description": "Guter Zustand",
    "category_json": '["Haus","Licht"]', "price_eur": 29,
    "images_json": '["secret-path.jpg"]', "status": "draft",
}


class FakeResponses:
    def __init__(self, *, fail=False, status="completed", usage=True):
        self.calls=[]
        self.fail=fail
        self.status=status
        self.has_usage=usage

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("synthetic upstream broken after send")
        tokens=(SimpleNamespace(input_tokens=120,output_tokens=40)
                if self.has_usage else None)
        return SimpleNamespace(
            status=self.status,
            output_text=json.dumps({
                "title":"Prägnanter Titel","description":"Guter Zustand",
            }),
            usage=tokens,
        )


class PortfolioProviderTests(unittest.TestCase):
    def fake(self, **kwargs):
        responses=FakeResponses(**kwargs)
        generator=AdvertisementGenerator(client=SimpleNamespace(responses=responses))
        return responses,generator

    def test_metering_explicit_model_output_bounds_and_no_media(self):
        responses,generator=self.fake()
        proposal,cents=generate_metered_proposal(generator,ORIGINAL,"Nur Fakten")
        self.assertEqual(proposal["title"],"Prägnanter Titel")
        self.assertEqual(cents,1)
        self.assertEqual(len(responses.calls),1)
        call=responses.calls[0]
        self.assertEqual(call["model"],PROVIDER_MODEL)
        self.assertEqual(call["max_output_tokens"],PROVIDER_OUTPUT_TOKEN_LIMIT)
        self.assertEqual(call["service_tier"],"default")
        self.assertEqual(call["store"],False)
        self.assertEqual(call["reasoning"],{"effort":"none"})
        self.assertNotIn("secret-path.jpg",call["input"])
        self.assertNotIn("SKU-001",call["input"])
        self.assertIn("Guter Zustand",call["input"])
        self.assertEqual(_usage_cost_cents(SimpleNamespace(
            input_tokens=24000,output_tokens=1200))[2],13)
        self.assertGreaterEqual(PROVIDER_REQUIRED_RESERVE_CENTS,13)

    def test_invalid_response_or_usage_rejected_without_retry(self):
        for kwargs in ({"fail":True},{"status":"incomplete"},{"usage":False}):
            with self.subTest(kwargs=kwargs):
                responses,generator=self.fake(**kwargs)
                with self.assertRaises(PortfolioProviderError):
                    generate_metered_proposal(generator,ORIGINAL,"Nur belegbar")
                self.assertEqual(len(responses.calls),1)
        for usage in (
            SimpleNamespace(input_tokens=True,output_tokens=2),
            SimpleNamespace(input_tokens=24001,output_tokens=2),
            SimpleNamespace(input_tokens=1,output_tokens=1201),
            SimpleNamespace(input_tokens=-1,output_tokens=1),
        ):
            with self.assertRaises(PortfolioProviderError):
                _usage_cost_cents(usage)
        with self.assertRaises(PortfolioProviderError):
            _prompt({**ORIGINAL,"category_json":'["invalid"]'},"Anweisung")

    def test_no_silent_paid_recall_after_provider_exception(self):
        with TemporaryDirectory() as root:
            store=SnapshotStore(Path(root)/"kunden.sqlite",create_if_missing=True)
            with closing(store._connect()) as conn,conn:
                conn.execute(_DDL)
                conn.execute(
                    "INSERT INTO portfolio_drafts "
                    "(sku,title,description,category_json,price_eur,images_json,"
                    "source_sha256,source_name,created_at,status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,'draft')",
                    ("SKU-001",ORIGINAL["title"],ORIGINAL["description"],
                     ORIGINAL["category_json"],29,"[]","0"*64,"import.csv",
                     "2026-10-26T00:00:00+00:00"),
                )
            job=create_job(
                store,["SKU-001"],instruction="Nur bekannte Fakten",
                request_key="1"*64,budget_cents=40,
                estimate_cents=PROVIDER_REQUIRED_RESERVE_CENTS,
            )
            jid=job["job_id"]
            change_state(store,jid,"start")
            responses,generator=self.fake(fail=True)
            session=DashboardGenerationAccess(
                ui_token="g"*32,generator_factory=lambda:generator,
            )
            with self.assertRaises(PortfolioProviderError):
                session.generate_one_portfolio_proposal(store,jid)
            status=details(store,jid)
            self.assertEqual(status["state"],"needs_attention")
            self.assertEqual(status["counts"]["unknown"],1)
            self.assertEqual(status["reserved_cents"],PROVIDER_REQUIRED_RESERVE_CENTS)
            self.assertEqual(len(responses.calls),1)
            with self.assertRaises(PortfolioJobError):
                change_state(store,jid,"resume")
            with self.assertRaises(PortfolioJobError):
                session.generate_one_portfolio_proposal(store,jid)
            self.assertEqual(len(responses.calls),1,"retry forbidden")

    def test_native_http_requires_both_session_tokens_and_explicit_consent(self):
        with TemporaryDirectory() as root:
            store=SnapshotStore(Path(root)/"kunden.sqlite",create_if_missing=True)
            with closing(store._connect()) as conn,conn:
                conn.execute(_DDL)
                conn.execute(
                    "INSERT INTO portfolio_drafts "
                    "(sku,title,description,category_json,price_eur,images_json,"
                    "source_sha256,source_name,created_at,status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,'draft')",
                    ("SKU-001",ORIGINAL["title"],ORIGINAL["description"],
                     ORIGINAL["category_json"],29,"[]","0"*64,"import.csv",
                     "2026-10-26T00:00:00+00:00"),
                )
            jid=create_job(store,["SKU-001"],instruction="Nur bekannte Fakten",
                           request_key="2"*64,budget_cents=40,
                           estimate_cents=PROVIDER_REQUIRED_RESERVE_CENTS)["job_id"]
            change_state(store,jid,"start")
            responses,generator=self.fake()
            session=DashboardGenerationAccess(
                ui_token="g"*32,generator_factory=lambda:generator,
            )
            proxy=DashboardWriteProxy(host="127.0.0.1",port=9999,
                                       bearer_token="b"*32,ui_token="w"*32)
            server=create_server(store,port=0,write_proxy=proxy,
                                 generation_access=session)
            worker=Thread(target=server.serve_forever,daemon=True)
            worker.start()
            try:
                port=server.server_address[1]
                def call(payload,headers=None):
                    conn=HTTPConnection("127.0.0.1",port,timeout=15)
                    base={
                        "Content-Type":"application/json",
                        "Origin":f"http://127.0.0.1:{port}",
                        "X-Mark-Dashboard-Write":"1",
                        "X-Mark-Dashboard-Token":"w"*32,
                    }
                    if headers:base.update(headers)
                    try:
                        conn.request("POST","/api/portfolio/jobs/generate-next",
                                     json.dumps(payload),headers=base)
                        reply=conn.getresponse()
                        return reply.status,json.loads(reply.read().decode())
                    finally:
                        conn.close()
                consent={"job_id":jid,"acknowledge_provider_cost":True}
                self.assertEqual(call(consent)[0],403)
                tokens={"X-Mark-Generation":"1",
                        "X-Mark-Generation-Token":"g"*32}
                self.assertEqual(call({**consent,"acknowledge_provider_cost":False},tokens)[0],400)
                self.assertEqual(len(responses.calls),0)
                code,data=call(consent,tokens)
                self.assertEqual(code,200)
                self.assertTrue(data["claimed"])
                self.assertEqual(details(store,jid)["counts"]["proposed"],1)
                self.assertEqual(call(consent,tokens)[0],400,"no duplicate charge")
                self.assertEqual(len(responses.calls),1)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)


if __name__=="__main__":
    unittest.main()
