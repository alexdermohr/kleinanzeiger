"""G2: authenticated local article template endpoints never touch the platform."""
from __future__ import annotations

import csv
from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from mark_api.dashboard import DashboardWriteProxy, create_server
from mark_api.portfolio_drafts import drafts_page, import_drafts, preview_import
from mark_api.portfolio_item_templates import list_item_templates
from mark_api.storage import SnapshotStore


class ItemTemplateHttpTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory(prefix="kleinanzeiger-g2-http-")
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.store=SnapshotStore(self.root/"customer.sqlite",create_if_missing=True)
        source=self.root/"items.csv"
        with source.open("w",encoding="utf-8",newline="") as output:
            writer=csv.writer(output)
            writer.writerow(["sku","title","description","category","price_eur","images"])
            writer.writerow(["LOCAL-01","Lampe","Lampe in gutem Zustand",
                             "Haus > Leuchten","35","front.jpg;detail.png"])
        checked=preview_import(self.store,source)
        import_drafts(self.store,source,checked["sha256"])
        proxy=DashboardWriteProxy(host="127.0.0.1",port=9999,
                                  bearer_token="b"*32,ui_token="w"*32)
        self.server=create_server(self.store,port=0,write_proxy=proxy)
        thread=Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(thread.join,3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self,method,path,payload=None,*,auth=True,origin=True,
                content_type="application/json",additional_headers=None):
        port=self.server.server_address[1]
        headers={"X-Mark-Dashboard-Write":"1","X-Mark-Dashboard-Token":"w"*32} if auth else {}
        if origin:
            headers["Origin"]=f"http://127.0.0.1:{port}"
        if method=="POST":headers["Content-Type"]=content_type
        if additional_headers:headers.update(additional_headers)
        body=json.dumps(payload).encode("utf-8") if payload is not None else None
        connection=HTTPConnection("127.0.0.1",port,timeout=12)
        try:
            connection.request(method,path,body=body,headers=headers)
            response=connection.getresponse()
            return response.status,json.loads(response.read())
        finally:
            connection.close()

    def save(self,**overrides):
        request={"action":"save","source_sku":"LOCAL-01","name":"Lampe",
                 "template_id":None,"expected_revision":0,"request_key":"a"*64}
        request.update(overrides)
        return request

    def test_authenticated_crud_and_independent_local_drafts(self):
        endpoint="/api/portfolio/item-templates/mutate"
        self.assertEqual(self.request("GET","/api/portfolio/item-templates")[1]["templates"],[])
        status,created=self.request("POST",endpoint,self.save())
        self.assertEqual(status,200,created)
        self.assertEqual(created["platform_writes"],0)
        self.assertEqual(self.request("POST",endpoint,self.save())[1],created)
        template_id=created["template_id"]
        listed=self.request("GET","/api/portfolio/item-templates")[1]["templates"][0]
        self.assertEqual(listed["title"],"Lampe")
        self.assertNotIn("ad_id",listed)
        self.assertEqual(listed["images"],["front.jpg","detail.png"])
        clone={"action":"from_template","template_id":template_id,
               "new_sku":"COPY-01","request_key":"b"*64}
        status,result=self.request("POST",endpoint,clone)
        self.assertEqual(status,200,result)
        self.assertEqual(result["platform_writes"],0)
        self.assertFalse(result["published"])
        self.assertEqual(self.request("POST",endpoint,clone)[1],result)
        duplicated={"action":"duplicate","source_sku":"COPY-01",
                    "new_sku":"COPY-02","request_key":"c"*64}
        status,reply=self.request("POST",endpoint,duplicated)
        self.assertEqual(status,200,reply)
        self.assertEqual(reply["sku"],"COPY-02")
        self.assertEqual(drafts_page(self.store)["total"],3)
        self.assertEqual(self.store.tracked_ad_ids(),())
        self.assertEqual(self.store.sync_status()["state"],"never_attempted")
        drop={"action":"delete","template_id":template_id,
              "expected_revision":1,"request_key":"d"*64}
        self.assertEqual(self.request("POST",endpoint,drop)[0],200)
        self.assertEqual(self.request("POST",endpoint,drop)[0],200)
        self.assertEqual(list_item_templates(self.store)["templates"],[])
        self.assertEqual(drafts_page(self.store)["total"],3)

    def test_denied_sessions_origin_and_conflicting_request(self):
        url="/api/portfolio/item-templates/mutate"
        self.assertEqual(self.request("GET","/api/portfolio/item-templates",auth=False)[0],403)
        self.assertEqual(self.request("POST",url,self.save(),auth=False)[0],403)
        self.assertEqual(self.request("POST",url,self.save(),origin=False)[0],403)
        self.assertEqual(self.request("POST",url,self.save(),
            content_type="text/plain")[0],400)
        self.assertEqual(self.request("POST",url,self.save(),
            additional_headers={"Idempotency-Key":"a"*64})[0],400)
        self.assertEqual(self.request("POST",url+"?x=1",self.save())[0],404)
        self.assertEqual(self.request("GET","/api/portfolio/item-templates?x=1")[0],400)
        self.assertEqual(self.request("POST",url,self.save())[0],200)
        self.assertEqual(self.request("POST",url,self.save(name="Andere"))[0],409)
        self.assertEqual(self.request("POST",url,self.save(request_key="b"*64))[0],409)
        self.assertEqual(self.request("POST",url,{"action":"save","request_key":"c"*64})[0],400)
        self.assertEqual(self.request("POST",url,{"action":"x","request_key":"e"*64})[0],400)
        self.assertEqual(self.request("POST",url,{
            "action":"duplicate","source_sku":"LOCAL-01",
            "new_sku":"LOCAL-01","request_key":"f"*64})[0],409)
        self.assertEqual(drafts_page(self.store)["total"],1)

    def test_readonly_view_has_no_mutation_api(self):
        server=create_server(self.store,port=0)
        worker=Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            conn=HTTPConnection("127.0.0.1",server.server_address[1],timeout=9)
            conn.request("GET","/api/portfolio/item-templates")
            self.assertEqual(conn.getresponse().status,404)
            conn.close()
            conn=HTTPConnection("127.0.0.1",server.server_address[1],timeout=9)
            conn.request("POST","/api/portfolio/item-templates/mutate",
                         body=b"{}",headers={"Content-Type":"application/json"})
            self.assertEqual(conn.getresponse().status,404)
            conn.close()
        finally:
            server.shutdown();server.server_close();worker.join(3)


if __name__=="__main__":
    unittest.main()
