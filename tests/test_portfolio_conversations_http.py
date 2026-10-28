"""G7 EML-only conversation triage HTTP: owner-authenticated and local-only."""
from __future__ import annotations

from datetime import datetime, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from mark_api.dashboard import DashboardWriteProxy, create_server
from mark_api.domain import AdSnapshot, InboundMessageEvent, LifecycleState
from mark_api.storage import SnapshotStore


NOW=datetime(2026,10,28,10,tzinfo=timezone.utc)


class ConversationHttpTests(unittest.TestCase):
    def setUp(self):
        folder=TemporaryDirectory(prefix="kleinanzeiger-g7-http-")
        self.addCleanup(folder.cleanup)
        self.store=SnapshotStore(Path(folder.name)/"customer.sqlite",create_if_missing=True)
        self.proxy=DashboardWriteProxy(
            host="127.0.0.1",port=9999,
            bearer_token="b"*32,ui_token="w"*32,
        )
        self.server=create_server(self.store,port=0,write_proxy=self.proxy)
        thread=Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(thread.join,3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id="10001",title="Eigenes Regal",
            source="kleinanzeigen-management",
            lifecycle_state=LifecycleState.ACTIVE,observed_at=NOW,
        ))
        self.store.append_inbound_message_events((InboundMessageEvent(
            ad_id="10001",conversation_id="chat_123",
            provider_message_id="message-123",observed_at=NOW,
            source="kleinanzeigen-email",
        ),))

    def request(self,method,target,data=None,*,token=True,origin=True,
                content_type="application/json",extra=None,server=None):
        active=server or self.server
        port=active.server_address[1]
        connection=HTTPConnection("127.0.0.1",port,timeout=8)
        headers=dict(extra or {})
        if token:
            headers.update({
                "X-Mark-Dashboard-Write":"1",
                "X-Mark-Dashboard-Token":"w"*32,
            })
        if origin:
            headers["Origin"]=f"http://127.0.0.1:{port}"
        if method=="POST":
            headers["Content-Type"]=content_type
        body=json.dumps(data,ensure_ascii=True).encode() if data is not None else None
        try:
            connection.request(method,target,body=body,headers=headers)
            response=connection.getresponse()
            return response.status,json.loads(response.read())
        finally:
            connection.close()

    def payload(self,**change):
        result={
            "ad_id":"10001", "conversation_id":"chat_123",
            "action":"review", "expected_revision":0,
            "request_key":"a"*64,
        }
        result.update(change)
        return result

    def test_exact_auth_and_local_review(self):
        self.assertEqual(self.request("GET","/api/portfolio/conversations",token=False)[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/conversations/review",
            self.payload(),token=False)[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/conversations/review",
            self.payload(),origin=False)[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/conversations/review",
            self.payload(),content_type="text/plain")[0],400)
        self.assertEqual(self.request("POST","/api/portfolio/conversations/review?x=1",
            self.payload())[0],404)
        self.assertEqual(self.request("POST","/api/portfolio/conversations/review",
            self.payload(),extra={"Idempotency-Key":"a"*64})[0],400)
        code,data=self.request("GET","/api/portfolio/conversations?limit=25&status=new")
        self.assertEqual(code,200,data)
        self.assertFalse(data["platform_messages_read"])
        self.assertEqual(data["conversations"][0]["title"],"Eigenes Regal")
        first=self.request("POST","/api/portfolio/conversations/review",self.payload())
        self.assertEqual(first[0],200,first)
        self.assertEqual(first[1]["platform_writes"],0)
        self.assertEqual(first[1]["revision"],1)
        self.assertEqual(self.request("POST","/api/portfolio/conversations/review",
            self.payload()),first)
        self.assertEqual(self.request("POST","/api/portfolio/conversations/review",
            self.payload(action="reopen"))[0],409)
        self.assertEqual(self.request("POST","/api/portfolio/conversations/review",
            self.payload(request_key="b"*64))[0],409)
        reviewed=self.request("GET","/api/portfolio/conversations?status=reviewed")
        self.assertEqual(reviewed[0],200)
        self.assertEqual(len(reviewed[1]["conversations"]),1)
        self.assertEqual(self.request("GET","/api/portfolio/conversations?status=new")[1]["conversations"],[])
        reopened=self.request("POST","/api/portfolio/conversations/review",
            self.payload(action="reopen",expected_revision=1,request_key="c"*64))
        self.assertEqual(reopened[0],200,reopened)
        self.assertEqual(reopened[1]["review_state"],"new_since_review")

    def test_strict_query_and_owner_checks(self):
        for url in (
            "/api/portfolio/conversations?limit=51",
            "/api/portfolio/conversations?limit=1&limit=2",
            "/api/portfolio/conversations?after=invalid",
            "/api/portfolio/conversations?after=10001/%%",
            "/api/portfolio/conversations?watermark=-1",
            "/api/portfolio/conversations?watermark=notanumber",
            "/api/portfolio/conversations?status=sent",
            "/api/portfolio/conversations?extra=1",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.request("GET",url)[0],400)
        self.assertEqual(self.request("POST","/api/portfolio/conversations/review",
            self.payload(conversation_id="fake"))[0],400)
        self.assertEqual(self.request("POST","/api/portfolio/conversations/review",
            self.payload(action="send"))[0],400)

    def test_readonly_dashboard_does_not_expose_local_triage(self):
        server=create_server(self.store,port=0)
        thread=Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            self.assertEqual(self.request("GET","/api/portfolio/conversations",
                server=server)[0],404)
            self.assertEqual(self.request("POST","/api/portfolio/conversations/review",
                self.payload(),server=server)[0],404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)


if __name__=="__main__":
    unittest.main()
