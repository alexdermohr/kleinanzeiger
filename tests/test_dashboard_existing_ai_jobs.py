from __future__ import annotations

from datetime import datetime, timezone
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
from unittest.mock import patch

from mark_api.dashboard import (
    DashboardGenerationAccess, DashboardWriteProxy, _DASHBOARD_HTML,
    _DASHBOARD_JS, create_server,
)
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.storage import SnapshotStore
from mark_api.portfolio_existing_jobs import claim_next, details as existing_job_details


class ExistingAiDashboardTests(unittest.TestCase):
    TOKEN="t"*32
    def setUp(self):
        self.tmp=TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=SnapshotStore(Path(self.tmp.name)/"x.sqlite",create_if_missing=True)
        self.store.append_ad_snapshot(AdSnapshot(ad_id="4711",observed_at=datetime.now(timezone.utc),
            source="kleinanzeigen-management",lifecycle_state=LifecycleState.ACTIVE,
            title="Original",description="Nur belegte Fakten"))

    def start_server(self, generation=None):
        proxy=DashboardWriteProxy(host="127.0.0.1",port=8999,bearer_token="b"*32,ui_token=self.TOKEN)
        self.server=create_server(self.store,port=0,write_proxy=proxy,
                                  generation_access=generation)
        self.thread=Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.thread.join,2); self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)

    def request(self, method, path, payload=None, *, token=TOKEN, origin="auto",
                generation=False):
        conn=HTTPConnection("127.0.0.1",self.server.server_address[1],timeout=5)
        headers={"X-Mark-Dashboard-Write":"1","X-Mark-Dashboard-Token":token}
        if origin=="auto": origin=f"http://127.0.0.1:{self.server.server_address[1]}"
        if origin is not None: headers["Origin"]=origin
        if generation:
            headers["X-Mark-Generation"]="1"
            headers["X-Mark-Generation-Token"]="g"*32
        body=None
        if method=="POST": headers["Content-Type"]="application/json"; body=json.dumps(payload).encode()
        conn.request(method,path,body=body,headers=headers); response=conn.getresponse()
        result=response.status,json.loads(response.read()); conn.close(); return result

    def test_same_origin_owner_session_and_double_click(self):
        self.start_server()
        payload={"mode":"all","ad_ids":[],"instruction":"Sachlich verbessern",
                 "budget_cents":200,"estimate_cents":20,"request_key":"e"*64}
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/create",payload,token="bad")[0],403)
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/create",payload,origin="http://evil.test")[0],403)
        code,first=self.request("POST","/api/portfolio/existing-ai/create",payload)
        self.assertEqual(code,200)
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/create",payload)[1]["job_id"],first["job_id"])
        code,page=self.request("GET",f"/api/portfolio/existing-ai/detail?job_id={first['job_id']}")
        self.assertEqual(code,200); self.assertEqual(page["items"][0]["original"]["title"],"Original")
        self.assertEqual(page["platform_writes"],0)

    def test_dashboard_contains_complete_existing_ad_workflow(self):
        for control in ("existing-ai-create", "existing-ai-start", "existing-ai-pause",
                        "existing-ai-next-call", "existing-ai-batch", "existing-ai-stop",
                        "existing-ai-approve", "existing-ai-reject", "existing-ai-next",
                        "existing-ai-recovery", "existing-ai-recover",
                        "existing-ai-unknown-ad", "existing-ai-checked-cents",
                        "existing-ai-checked", "existing-ai-settle-unknown"):
            self.assertIn(f'id="{control}"', _DASHBOARD_HTML)
        self.assertNotIn("existing-ai-apply", _DASHBOARD_HTML)

    def test_interrupted_existing_ai_uses_same_origin_checked_local_settlement(self):
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id="4712", observed_at=datetime.now(timezone.utc),
            source="private-web-owner-inventory",
            lifecycle_state=LifecycleState.ACTIVE,
            title="Zweite Anzeige", description="Nur bekannte Fakten",
        ))
        access=DashboardGenerationAccess(
            ui_token="g"*32,generator_factory=lambda:None,
        )
        self.start_server(access)
        payload={"mode":"selected","ad_ids":["4711","4712"],
                 "instruction":"Faktengetreu optimieren",
                 "budget_cents":70,"estimate_cents":20,
                 "request_key":"f"*64}
        code,job=self.request("POST","/api/portfolio/existing-ai/create",payload)
        self.assertEqual(code,200)
        jid=job["job_id"]
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/state",
            {"job_id":jid,"action":"start"})[0],200)
        claim=claim_next(self.store,jid)
        self.assertEqual(claim["ad_id"],"4711")
        uri="/api/portfolio/existing-ai/recover"
        # Other billable requests exclude accounting changes even though
        # these local endpoints require no provider token themselves.
        self.assertTrue(access._lock.acquire(blocking=False))
        try:
            self.assertEqual(self.request("POST",uri,{"job_id":jid})[0],409)
            self.assertEqual(existing_job_details(self.store,jid)["counts"]["inflight"],1)
        finally:
            access._lock.release()
        self.assertEqual(self.request("POST",uri,{"job_id":jid},token="wrong")[0],403)
        self.assertEqual(self.request("POST",uri,{"job_id":jid},
            origin="http://evil.invalid")[0],403)
        self.assertEqual(self.request("POST",uri,{"job_id":jid,"extra":True})[0],400)
        code,recovered=self.request("POST",uri,{"job_id":jid})
        self.assertEqual(code,200)
        self.assertEqual(recovered["state"],"needs_attention")
        self.assertEqual(recovered["counts"]["unknown"],1)
        self.assertEqual(recovered["reserved_cents"],20)
        self.assertEqual(self.request("POST",uri,{"job_id":jid})[1]["counts"],recovered["counts"])
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/state",
            {"job_id":jid,"action":"resume"})[0],400)
        settle="/api/portfolio/existing-ai/settle-unknown"
        missing={"job_id":jid,"ad_id":"4711","actual_cents":7,
                 "explicitly_checked":False}
        self.assertEqual(self.request("POST",settle,missing)[0],400)
        self.assertEqual(self.request("POST",settle,{**missing,
            "explicitly_checked":True,"actual_cents":True})[0],400)
        self.assertEqual(self.request("POST",settle,{**missing,
            "explicitly_checked":True,"ad_id":"4712"})[0],400)
        self.assertEqual(self.request("POST",settle,{**missing,
            "explicitly_checked":True},token="wrong")[0],403)
        self.assertTrue(access._lock.acquire(blocking=False))
        try:
            self.assertEqual(self.request("POST",settle,
                {**missing,"explicitly_checked":True})[0],409)
        finally:
            access._lock.release()
        code,cleared=self.request("POST",settle,
            {**missing,"explicitly_checked":True})
        self.assertEqual(code,200)
        self.assertEqual(cleared["platform_writes"],0)
        self.assertEqual(cleared["state"],"paused")
        self.assertEqual(cleared["actual_cents"],7)
        self.assertEqual(cleared["reserved_cents"],0)
        self.assertEqual(cleared["counts"]["queued"],1)
        self.assertEqual(cleared["counts"]["rejected"],1)
        self.assertEqual(self.request("POST",settle,
            {**missing,"explicitly_checked":True})[0],400)
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/state",
            {"job_id":jid,"action":"resume"})[0],200)

    def test_generation_requires_second_token_and_provider_cost_floor(self):
        calls=[]
        class Responses:
            def create(_self,**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(status="completed",
                    output_text=json.dumps({"title":"Neu","description":"Nur belegte Fakten"}),
                    usage=SimpleNamespace(input_tokens=10,output_tokens=10))
        generator=SimpleNamespace(_provider=lambda:SimpleNamespace(responses=Responses()))
        access=DashboardGenerationAccess(ui_token="g"*32,generator_factory=lambda:generator)
        self.start_server(access)
        low={"mode":"all","ad_ids":[],"instruction":"Sachlich", "budget_cents":20,
             "estimate_cents":19,"request_key":"4"*64}
        code,job=self.request("POST","/api/portfolio/existing-ai/create",low)
        self.assertEqual(code,200)
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/state",
            {"job_id":job["job_id"],"action":"start"})[0],200)
        payload={"job_id":job["job_id"],"acknowledge_provider_cost":True}
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/generate-next",payload,
                                      generation=True)[0],400)
        self.assertEqual(calls,[])

        # A separate adequately reserved job reaches the independent generation
        # authorization check, but still never calls the provider without it.
        good={**low,"budget_cents":40,"estimate_cents":20,"request_key":"5"*64}
        code,job=self.request("POST","/api/portfolio/existing-ai/create",good)
        self.assertEqual(code,200)
        self.request("POST","/api/portfolio/existing-ai/state",
                     {"job_id":job["job_id"],"action":"start"})
        payload={"job_id":job["job_id"],"acknowledge_provider_cost":True}
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/generate-next",payload)[0],403)
        self.assertEqual(calls,[])

    def test_two_confirmed_ten_call_batches_use_configured_global_limit(self):
        for index in range(20):
            self.store.append_ad_snapshot(AdSnapshot(
                ad_id=f"extra-{index:02d}", observed_at=datetime.now(timezone.utc),
                source="private-web-owner-inventory",
                lifecycle_state=LifecycleState.ACTIVE, title=f"Original {index}",
                description="Nur belegte Fakten"))
        calls=[]
        class Responses:
            def create(_self, **_kwargs):
                calls.append(1)
                return SimpleNamespace(status="completed",
                    output_text=json.dumps({"title":"Neu","description":"Nur belegte Fakten"}),
                    usage=SimpleNamespace(input_tokens=10,output_tokens=10))
        generator=SimpleNamespace(_provider=lambda:SimpleNamespace(responses=Responses()))
        self.start_server(DashboardGenerationAccess(
            ui_token="g"*32, generator_factory=lambda:generator, max_requests=20))
        create={"mode":"all","ad_ids":[],"instruction":"Sachlich",
                "budget_cents":10000,"estimate_cents":20,"request_key":"6"*64}
        code,job=self.request("POST","/api/portfolio/existing-ai/create",create)
        self.assertEqual(code,200)
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/state",
            {"job_id":job["job_id"],"action":"start"})[0],200)
        payload={"job_id":job["job_id"],"acknowledge_provider_cost":True}
        for _batch in range(2):
            for _call in range(10):
                self.assertEqual(self.request("POST","/api/portfolio/existing-ai/generate-next",
                    payload,generation=True)[0],200)
        self.assertEqual(len(calls),20)
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/generate-next",
            payload,generation=True)[0],429)
        self.assertEqual(len(calls),20)

    def test_storage_failures_are_sanitized_503_for_reads_create_and_review(self):
        self.start_server()
        with patch("mark_api.dashboard.list_existing_jobs",
                   side_effect=sqlite3.OperationalError("secret list path")):
            self.assertEqual(self.request("GET","/api/portfolio/existing-ai/jobs")[0],503)
        with patch("mark_api.dashboard.existing_job_details",
                   side_effect=OSError("secret detail path")):
            self.assertEqual(self.request("GET","/api/portfolio/existing-ai/detail?job_id="+"a"*32)[0],503)
        create={"mode":"selected","ad_ids":["4711"],"instruction":"Sachlich",
                "budget_cents":200,"estimate_cents":20,"request_key":"7"*64}
        with patch("mark_api.dashboard.create_existing_job",
                   side_effect=sqlite3.OperationalError("secret create path")):
            code,body=self.request("POST","/api/portfolio/existing-ai/create",create)
            self.assertEqual((code,body),(503,{"error":"database_unavailable"}))
        review={"job_id":"a"*32,"ad_ids":["4711"],"approve":True}
        with patch("mark_api.dashboard.review_existing_items",
                   side_effect=OSError("secret review path")):
            code,body=self.request("POST","/api/portfolio/existing-ai/review",review)
            self.assertEqual((code,body),(503,{"error":"database_unavailable"}))

    @unittest.skipUnless(shutil.which("node"), "Node.js needed for dashboard interaction")
    def test_node_click_creates_selected_catalog_job_and_parse(self):
        harness=r'''
const vm=require("node:vm"),fs=require("node:fs"),assert=require("node:assert/strict");
class E { constructor(id=""){this.id=id;this.value="";this.checked=false;this.hidden=false;this.disabled=false;this.children=[];this.listeners={};this.textContent="";this.open=false;}
 addEventListener(n,f){(this.listeners[n]??=[]).push(f)} append(...x){this.children.push(...x)} replaceChildren(...x){this.children=[...x]} querySelectorAll(){return []} focus(){} scrollIntoView(){} click(){for(const f of this.listeners.click??[])f({target:this});} }
const elements=new Map(),by=id=>{if(!elements.has(id))elements.set(id,new E(id));return elements.get(id)};
const document={getElementById:by,createElement:()=>new E(),querySelectorAll:()=>[]};
const calls=[];
const context={document,console,URLSearchParams,Uint8Array,Blob,TextEncoder,setTimeout,clearTimeout,
 fetch:async(path,options={})=>{calls.push({path,options});return {ok:true,status:200,json:async()=>({job_id:"a".repeat(32),total:1,platform_writes:0,jobs:[]}),text:async()=>"{}"}},
 window:{location:{hash:"",pathname:"/",search:"",origin:"http://127.0.0.1"},history:{replaceState(){}},sessionStorage:{getItem(){return null},setItem(){}},confirm:()=>true,crypto:{getRandomValues:a=>a.fill(7)}},
 globalThis:null,navigator:{},crypto:{getRandomValues:a=>a.fill(7)}};context.globalThis=context;vm.createContext(context);
let source=fs.readFileSync(0,"utf8").replace(/consumeWriteToken\(\);\s*load\(\);\s*$/," ");
new vm.Script(source);vm.runInContext(source,context);
vm.runInContext('jobsAvailable=true;localWorkAvailable=true;writeUiAvailable=true;writeToken="t".repeat(32);catalogSelectedIds.add("4711")',context);
by("existing-ai-instruction").value="Sachlich";by("existing-ai-budget").value="2.00";by("existing-ai-estimate").value="0.20";
by("existing-ai-create").click();
vm.runInContext('existingAiActiveId="a".repeat(32);existingAiDisplayedId=existingAiActiveId;existingAiNextAfter="4711";existingAiSelected.add("4711")',context);
by("existing-ai-next").click();
assert.equal(vm.runInContext('existingAiPageIndex',context),1);
assert.equal(vm.runInContext('existingAiSelected.size',context),0);
vm.runInContext('existingAiBusy=false;existingAiDisplayedId="b".repeat(32);existingAiSelected.add("4711")',context);
by("existing-ai-approve").click();
setTimeout(()=>{const call=calls.find(x=>x.path==="/api/portfolio/existing-ai/create");assert.ok(call);const body=JSON.parse(call.options.body);assert.deepEqual(body.ad_ids,["4711"]);assert.equal(body.mode,"selected");assert.equal(body.budget_cents,200);assert.match(body.request_key,/^[0-9a-f]{64}$/);assert.equal(calls.some(x=>x.path==="/api/portfolio/existing-ai/review"),false);},0);
'''
        result=subprocess.run([shutil.which("node") or "node","-e",harness],
            input=_DASHBOARD_JS,text=True,encoding="utf-8",capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__ == "__main__": unittest.main()
