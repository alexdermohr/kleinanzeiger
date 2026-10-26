"""Authenticated P5 templates/attention routes, never platform or provider writes."""
from __future__ import annotations
from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from mark_api.dashboard import (
    DashboardWriteProxy,create_server,_DASHBOARD_HTML,_DASHBOARD_JS)
from mark_api.storage import SnapshotStore


class TemplateHttpTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory(prefix="kleinanzeiger-template-http-")
        self.addCleanup(tmp.cleanup)
        self.store=SnapshotStore(Path(tmp.name)/"kunden.sqlite",create_if_missing=True)
        proxy=DashboardWriteProxy(host="127.0.0.1",port=9999,
             bearer_token="b"*32,ui_token="w"*32)
        self.server=create_server(self.store,port=0,write_proxy=proxy)
        worker=Thread(target=self.server.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(worker.join,3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self,method,path,payload=None,*,auth=True,origin=True,
             content_type="application/json"):
        port=self.server.server_address[1]
        conn=HTTPConnection("127.0.0.1",port,timeout=12)
        headers={"X-Mark-Dashboard-Write":"1","X-Mark-Dashboard-Token":"w"*32} if auth else {}
        if origin:headers["Origin"]=f"http://127.0.0.1:{port}"
        if method=="POST":headers["Content-Type"]=content_type
        body=json.dumps(payload).encode("utf-8") if payload is not None else None
        try:
            conn.request(method,path,body=body,headers=headers)
            result=conn.getresponse()
            return result.status,json.loads(result.read())
        finally:
            conn.close()

    def request(self,**changes):
        payload={"template_id":None,"name":"Kurz",
                 "instruction":"Nur belegbare Angaben.",
                 "expected_revision":0,"delete":False,
                 "request_key":"a"*64}
        payload.update(changes)
        return payload

    def test_full_template_crud_with_exact_replay_and_local_counts(self):
        self.assertEqual(self.call("GET","/api/portfolio/templates")[1]["templates"],[])
        self.assertEqual(self.call("GET","/api/portfolio/attention")[1]["provider_calls"],0)
        url="/api/portfolio/templates/mutate"
        self.assertEqual(self.call("POST",url,self.request(),auth=False)[0],403)
        self.assertEqual(self.call("POST",url,self.request(),origin=False)[0],403)
        self.assertEqual(self.call("POST",url,self.request(),content_type="text/plain")[0],400)
        self.assertEqual(self.call("POST",url+"?new=1",self.request())[0],404)
        self.assertEqual(self.call("GET","/api/portfolio/templates",auth=False)[0],403)
        self.assertEqual(self.call("GET","/api/portfolio/attention",auth=False)[0],403)
        self.assertEqual(self.call("GET","/api/portfolio/templates?x=1")[0],400)
        code,saved=self.call("POST",url,self.request())
        self.assertEqual(code,200,saved)
        self.assertEqual(saved["revision"],1)
        self.assertEqual(saved["platform_writes"],0)
        self.assertEqual(self.call("POST",url,self.request())[1],saved)
        self.assertEqual(self.call("POST",url,self.request(
            name="different"))[0],409)
        template_id=saved["template_id"]
        code,edited=self.call("POST",url,self.request(
            template_id=template_id,expected_revision=1,
            instruction="Sachlicherer Titel",request_key="b"*64))
        self.assertEqual(code,200,edited)
        self.assertEqual(edited["revision"],2)
        self.assertEqual(self.call("POST",url,self.request(
            template_id=template_id,expected_revision=1,
            request_key="c"*64))[0],409)
        self.assertEqual(self.call("GET","/api/portfolio/templates")[1]["templates"][0]["revision"],2)
        code,removed=self.call("POST",url,self.request(
            template_id=template_id,expected_revision=2,
            name=None,instruction=None,delete=True,request_key="d"*64))
        self.assertEqual(code,200,removed)
        self.assertTrue(removed["deleted"])
        self.assertEqual(self.call("GET","/api/portfolio/templates")[1]["templates"],[])
        self.assertEqual(self.store.tracked_ad_ids(),())

    def test_absent_local_write_session_and_optional_ui(self):
        server=create_server(self.store,port=0)
        thread=Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            conn=HTTPConnection("127.0.0.1",server.server_address[1],timeout=9)
            conn.request("GET","/api/portfolio/templates")
            answer=conn.getresponse()
            self.assertEqual(answer.status,404)
            conn.close()
        finally:
            server.shutdown();server.server_close();thread.join(3)
        for marker in ('id="work-templates-disclosure"',
                       'id="work-template-select"',
                       'id="work-template-save"',
                       'id="work-template-delete"',
                       'id="work-attention"'):
            self.assertIn(marker,_DASHBOARD_HTML)
        self.assertIn('"/api/portfolio/templates/mutate"',_DASHBOARD_JS)
        self.assertIn('"/api/portfolio/attention"',_DASHBOARD_JS)


if __name__=="__main__":
    unittest.main()
