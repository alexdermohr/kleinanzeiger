"""Authenticated G1 local-only follow-up HTTP; no browser or provider writes."""
from __future__ import annotations
from datetime import datetime, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from mark_api.dashboard import DashboardWriteProxy, create_server
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.portfolio_followups import _schema
from mark_api.storage import SnapshotStore


class FollowupHttpTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix="kleinanzeiger-followup-http-")
        self.addCleanup(folder.cleanup)
        self.store = SnapshotStore(Path(folder.name)/"customer.sqlite", create_if_missing=True)
        self.proxy = DashboardWriteProxy(
            host="127.0.0.1", port=9999,
            bearer_token="b"*32, ui_token="w"*32,
        )
        self.server = create_server(self.store,port=0,write_proxy=self.proxy)
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join,3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.observe("10001")

    def observe(self, ad_id: str, source: str = "kleinanzeigen-management"):
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id=ad_id, title="Eigene Lampe", source=source,
            lifecycle_state=LifecycleState.ACTIVE,
            observed_at=datetime(2026,10,28,8,tzinfo=timezone.utc),
        ))

    def request(self, method: str, target: str, data=None, *,
                token=True, origin=True, content_type="application/json",
                extra=None):
        port=self.server.server_address[1]
        conn=HTTPConnection("127.0.0.1",port,timeout=10)
        headers=dict(extra or {})
        if token:
            headers.update({"X-Mark-Dashboard-Write":"1",
                            "X-Mark-Dashboard-Token":"w"*32})
        if origin:
            headers["Origin"]=f"http://127.0.0.1:{port}"
        if method=="POST":
            headers["Content-Type"]=content_type
        body=json.dumps(data,ensure_ascii=True).encode() if data is not None else None
        try:
            conn.request(method,target,body=body,headers=headers)
            response=conn.getresponse()
            return response.status,json.loads(response.read())
        finally:
            conn.close()

    def payload(self, **update):
        data={
            "action":"save", "ad_id":"10001","due_date":"2026-11-02",
            "note":"Manuell kontrollieren","expected_revision":0,
            "request_key":"a"*64,
        }
        data.update(update)
        return data

    def test_exact_auth_origin_replay_and_no_platform_write(self):
        self.assertEqual(self.request("GET","/api/portfolio/followups",token=False)[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/followups/mutate",
                                      self.payload(),token=False)[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/followups/mutate",
                                      self.payload(),origin=False)[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/followups/mutate",
                                      self.payload(),content_type="text/plain")[0],400)
        self.assertEqual(self.request("POST","/api/portfolio/followups/mutate?x=1",
                                      self.payload())[0],404)
        self.assertEqual(self.request("POST","/api/portfolio/followups/mutate",
                                      self.payload(),extra={"Idempotency-Key":"x"})[0],400)
        self.assertEqual(self.request("GET","/api/portfolio/followups")[1]["total"],0)
        code,response=self.request("POST","/api/portfolio/followups/mutate",self.payload())
        self.assertEqual(code,200,response)
        self.assertEqual(response["revision"],1)
        self.assertEqual(response["platform_writes"],0)
        self.assertIsNone(response["platform_expiry_at"])
        self.assertEqual(self.request("POST","/api/portfolio/followups/mutate",
                                      self.payload()),(200,response))
        self.assertEqual(self.request("POST","/api/portfolio/followups/mutate",
                                      self.payload(note="different"))[0],409)
        self.assertEqual(self.request("POST","/api/portfolio/followups/mutate",
                                      self.payload(request_key="b"*64))[0],409)
        page=self.request("GET","/api/portfolio/followups?limit=1")
        self.assertEqual(page[0],200)
        self.assertEqual(page[1]["total"],1)
        self.assertEqual(page[1]["platform_expiry_status"],"unknown_no_verified_observation")
        self.assertIsNone(page[1]["followups"][0]["platform_expiry_at"])
        self.assertEqual(page[1]["followups"][0]["title"],"Eigene Lampe")
        detail=self.request("GET","/api/portfolio/followups/detail?ad_id=10001")
        self.assertEqual(detail[1]["followup"]["revision"],1)
        self.assertEqual(detail[1]["followup"]["source"],"manual_user_followup")
        self.assertIsNone(self.request("GET","/api/portfolio/followups/detail?ad_id=10002")[1]["followup"])
        removed=self.request("POST","/api/portfolio/followups/mutate",
              self.payload(action="delete",due_date=None,note="",request_key="c"*64,expected_revision=1))
        self.assertEqual(removed[0],200)
        self.assertTrue(removed[1]["deleted"])
        self.assertEqual(self.request("GET","/api/portfolio/followups")[1]["total"],0)
        self.assertEqual(self.request("POST","/api/portfolio/followups/mutate",
              self.payload(action="delete",due_date=None,note="",request_key="c"*64,expected_revision=1)),removed)
        tombstone=self.request("GET","/api/portfolio/followups/detail?ad_id=10001")
        self.assertEqual(tombstone[0],200)
        self.assertIsNone(tombstone[1]["followup"])
        self.assertEqual(tombstone[1]["tombstone_revision"],2)
        self.assertEqual(self.request("POST","/api/portfolio/followups/mutate",
                         self.payload(request_key="d"*64,expected_revision=0))[0],409)
        recreated=self.request("POST","/api/portfolio/followups/mutate",
                         self.payload(request_key="e"*64,
                                      expected_revision=tombstone[1]["tombstone_revision"]))
        self.assertEqual(recreated[0],200)
        self.assertEqual(recreated[1]["revision"],3)
        self.assertEqual(self.request("GET","/api/portfolio/followups")[1]["total"],1)

    def test_untrusted_owner_date_and_bounded_query_denials(self):
        self.observe("999",source="local-catalog")
        self.assertEqual(self.request("POST","/api/portfolio/followups/mutate",
                                      self.payload(ad_id="999"))[0],400)
        for target in (
            "/api/portfolio/followups?limit=51",
            "/api/portfolio/followups?limit=1&limit=2",
            "/api/portfolio/followups?after=abc",
            "/api/portfolio/followups?x=1",
            "/api/portfolio/followups/detail?ad_id=10001&x=1",
            "/api/portfolio/followups/detail?ad_id=nope",
            "/api/portfolio/followups/detail",
        ):
            with self.subTest(target=target):
                self.assertEqual(self.request("GET",target)[0],400)
        self.assertEqual(self.request("GET","/api/portfolio/followups/detail?ad_id=10001",token=False)[0],403)

    def test_readonly_server_does_not_expose_owner_followups(self):
        other=create_server(self.store,port=0)
        thread=Thread(target=other.serve_forever,daemon=True)
        thread.start()
        try:
            conn=HTTPConnection("127.0.0.1",other.server_address[1],timeout=8)
            conn.request("GET","/api/portfolio/followups")
            response=conn.getresponse()
            self.assertEqual(response.status,404)
            conn.close()
        finally:
            other.shutdown()
            other.server_close()
            thread.join(3)


if __name__=="__main__":
    unittest.main()
