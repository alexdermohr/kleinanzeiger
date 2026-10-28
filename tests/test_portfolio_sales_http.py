"""P3 authenticated sales UI HTTP, synthetic owner-only records, no platform writes."""
from __future__ import annotations

from datetime import datetime, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from mark_api.dashboard import DashboardWriteProxy, create_server, _DASHBOARD_HTML, _DASHBOARD_JS
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.storage import SnapshotStore


class PortfolioSalesHttpTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory(prefix="kleinanzeiger-sales-http-")
        self.addCleanup(temp.cleanup)
        self.store=SnapshotStore(Path(temp.name)/"customer.sqlite",create_if_missing=True)
        self.proxy=DashboardWriteProxy(
            host="127.0.0.1",port=9999,
            bearer_token="b"*32,ui_token="w"*32)
        self.server=create_server(self.store,port=0,write_proxy=self.proxy)
        worker=Thread(target=self.server.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(worker.join,3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.observe("10001")

    def observe(self,id,source="private-web-product-launcher"):
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id=id,title="Original Lampe",lifecycle_state=LifecycleState.ACTIVE,
            observed_at=datetime(2026,10,26,9,tzinfo=timezone.utc),source=source))

    def request(self,method,uri,data=None,*,token=True,origin=True,
                content_type="application/json"):
        port=self.server.server_address[1]
        conn=HTTPConnection("127.0.0.1",port,timeout=12)
        headers={}
        if token:
            headers.update({"X-Mark-Dashboard-Write":"1",
                            "X-Mark-Dashboard-Token":"w"*32})
        if origin:headers["Origin"]=f"http://127.0.0.1:{port}"
        if method=="POST":headers["Content-Type"]=content_type
        body=json.dumps(data,ensure_ascii=True).encode() if data is not None else None
        try:
            conn.request(method,uri,body=body,headers=headers)
            response=conn.getresponse()
            return response.status,json.loads(response.read())
        finally:
            conn.close()

    def change(self,**kwargs):
        row={"ad_id":"10001","state":"reserved","revenue_eur":None,
             "cost_eur":None,"location":"Regal 4",
             "note":"Bitte prüfen","request_key":"a"*64,"expected_revision":0}
        row.update(kwargs)
        return row

    def test_owner_only_and_exact_session_no_platform_effect(self):
        self.assertEqual(self.request("GET","/api/portfolio/sales",token=False)[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/sales/update",
                                      self.change(),token=False)[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/sales/update",
                                      self.change(),origin=False)[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/sales/update",
                                      self.change(),content_type="text/plain")[0],400)
        self.assertEqual(self.request("POST","/api/portfolio/sales/update?x=1",
                                      self.change())[0],404)
        self.assertEqual(self.request("POST","/api/portfolio/sales/update",
                                      self.change(ad_id="99999"))[0],400)
        self.assertEqual(self.request("GET","/api/portfolio/sales")[1]["sales"],[])
        code,item=self.request("POST","/api/portfolio/sales/update",self.change())
        self.assertEqual(code,200,item)
        self.assertEqual(item["platform_writes"],0)
        self.assertEqual(item["revision"],1)
        self.assertEqual(self.request("POST","/api/portfolio/sales/update",
                                      self.change())[1],item)
        self.assertEqual(self.request("POST","/api/portfolio/sales/update",
                                      self.change(note="different"))[0],409)
        self.assertEqual(self.request("POST","/api/portfolio/sales/update",
                                      self.change(request_key="b"*64))[0],409)
        detail=self.request("GET","/api/portfolio/sales/detail?ad_id=10001")
        self.assertEqual(detail[0],200)
        self.assertEqual(detail[1]["sale"]["revision"],1)
        self.assertEqual(detail[1]["sale"]["source"],"manual_local_entry")
        self.assertEqual(self.request("GET","/api/portfolio/sales/detail?ad_id=10002")[1]["sale"],None)
        self.assertEqual(self.request("GET","/api/portfolio/sales/detail?ad_id=nope")[0],400)
        self.assertEqual(self.request("GET","/api/portfolio/sales/detail?ad_id=10001&junk=1")[0],400)
        self.assertEqual(self.request("GET","/api/portfolio/sales/detail?ad_id=10001",token=False)[0],403)
        sales=self.request("GET","/api/portfolio/sales")[1]
        self.assertEqual(sales["total"],1)
        self.assertEqual(sales["sales"][0]["imported_inbound_messages"],0)
        self.assertEqual(self.store.tracked_ad_ids(),("10001",))

    def test_manually_sold_with_unknown_cost_and_no_silent_claim(self):
        self.request("POST","/api/portfolio/sales/update",self.change())
        code,result=self.request("POST","/api/portfolio/sales/update",
            self.change(request_key="b"*64,expected_revision=1,
                        state="sold",revenue_eur=180,cost_eur=None,
                        note="Erlös im Laden verbucht"))
        self.assertEqual(code,200,result)
        self.assertIsNone(result["profit_eur"])
        total=self.request("GET","/api/portfolio/sales/totals")[1]
        self.assertEqual(total["sold"],1)
        self.assertEqual(total["revenue_eur"],180)
        self.assertEqual(total["unknown_cost_count"],1)
        self.assertEqual(total["known_profit_eur"],0)
        self.assertEqual(total["known_profit_count"],0)
        self.assertEqual(self.request("GET","/api/portfolio/sales?limit=51")[0],400)
        self.assertEqual(self.request("GET","/api/portfolio/sales?limit=1&limit=2")[0],400)

    def test_local_sales_feature_not_available_without_write_session(self):
        other=create_server(self.store,port=0)
        thread=Thread(target=other.serve_forever,daemon=True)
        thread.start()
        try:
            conn=HTTPConnection("127.0.0.1",other.server_address[1],timeout=8)
            conn.request("GET","/api/portfolio/sales")
            res=conn.getresponse()
            self.assertEqual(res.status,404)
            conn.close()
        finally:
            other.shutdown()
            other.server_close()
            thread.join(3)

    def test_html_feature_and_js_api_present(self):
        for marker in ('id="sales-panel"','id="sale-ad-select"',
                       'id="sale-state"','id="sale-revenue"','id="sale-cost"',
                       'id="sale-save"','id="sales-body"','id="sale-next"'):
            self.assertIn(marker,_DASHBOARD_HTML)
        self.assertIn('"/api/portfolio/sales/update"',_DASHBOARD_JS)
        self.assertIn('function renderSaleChoices()',_DASHBOARD_JS)
        self.assertIn('Keine Plattformaktion',_DASHBOARD_JS)


if __name__=="__main__":
    unittest.main()
