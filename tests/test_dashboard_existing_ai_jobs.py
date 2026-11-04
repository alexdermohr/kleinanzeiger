from __future__ import annotations

from datetime import datetime, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
from urllib.parse import quote
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from mark_api.dashboard import (
    DashboardGenerationAccess, DashboardWriteProxy, _DASHBOARD_HTML,
    _DASHBOARD_JS, create_server,
)
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.results import ReadResult
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
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/create",
            {**payload,"ad_ids":["4711"]})[0],400)
        code,first=self.request("POST","/api/portfolio/existing-ai/create",payload)
        self.assertEqual(code,200)
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/create",payload)[1]["job_id"],first["job_id"])
        code,page=self.request("GET",f"/api/portfolio/existing-ai/detail?job_id={first['job_id']}")
        self.assertEqual(code,200); self.assertEqual(page["items"][0]["original"]["title"],"Original")
        self.assertEqual(page["platform_writes"],0)
        # Exact job+ad ID lookups inherit the same local token boundary.
        endpoint=f"/api/portfolio/existing-ai/detail?job_id={first['job_id']}&ad_id=4711&limit=1"
        code,focused=self.request("GET",endpoint)
        self.assertEqual(code,200)
        self.assertEqual([x["ad_id"] for x in focused["items"]],["4711"])
        self.assertFalse(focused["has_more"])
        self.assertIsNone(focused["next_after"])
        self.assertEqual(self.request("GET",endpoint,token="bad")[0],403)
        self.assertEqual(self.request("GET",endpoint+"&after=0000")[0],400)
        self.assertEqual(self.request("GET",endpoint+"&ad_id=4711")[0],400)
        self.assertEqual(self.request("GET",endpoint.replace("ad_id=4711","ad_id="))[0],400)
        code,missing=self.request("GET",endpoint.replace("ad_id=4711","ad_id=9999"))
        self.assertEqual(code,200)
        self.assertEqual(missing["items"],[])

    def test_10000_item_http_all_selected_large_payload_and_overflow(self):
        # 9,999 synthetic originals plus the single setUp owner ad.
        # Large selection deliberately exceeds the old 512-KiB body bound.
        now = datetime.now(timezone.utc)
        ids = [f"item-{i:05d}-" + "x"*140 for i in range(9_999)]
        records = tuple(AdSnapshot(
            ad_id=ad_id, observed_at=now, source="kleinanzeigen-management",
            lifecycle_state=LifecycleState.ACTIVE,
            title=f"Lampe {i}", description="Belegte Originalbeschreibung",
        ) for i, ad_id in enumerate(ids))
        self.assertEqual(self.store.append_inventory_result(
            ReadResult.success_nonempty(records), observed_at=now,
            source="kleinanzeigen-management"), 9_999)
        self.start_server()
        base = {"mode":"all", "ad_ids":[], "instruction":"Nur belegte Fakten",
                "budget_cents":20, "estimate_cents":20, "request_key":"4"*64}
        code, all_job = self.request("POST", "/api/portfolio/existing-ai/create", base)
        self.assertEqual(code, 200)
        self.assertEqual(all_job["total"], 10_000)
        self.assertEqual(all_job["platform_writes"], 0)
        selected = {**base, "mode":"selected", "ad_ids":sorted(["4711"]+ids),
                    "request_key":"5"*64}
        raw_length = len(json.dumps(selected).encode("utf-8"))
        self.assertGreater(raw_length, 512*1024)
        self.assertLess(raw_length, 2*1024*1024)
        code, selected_job = self.request(
            "POST", "/api/portfolio/existing-ai/create", selected)
        self.assertEqual(code, 200)
        self.assertEqual(selected_job["total"], 10_000)
        self.assertEqual(selected_job["platform_writes"], 0)
        self.assertNotEqual(selected_job["job_id"], all_job["job_id"])
        code, page = self.request("GET",
            f"/api/portfolio/existing-ai/detail?job_id={selected_job['job_id']}")
        self.assertEqual(code, 200)
        self.assertEqual(len(page["items"]), 50)
        self.assertTrue(page["has_more"])
        # 160-character-ish stored IDs on the last page must still be
        # located immediately without visiting 200 cursor pages.
        target_id=quote(ids[-1],safe="")
        code,exact=self.request("GET",
            f"/api/portfolio/existing-ai/detail?job_id={selected_job['job_id']}&ad_id={target_id}&limit=1")
        self.assertEqual(code,200)
        self.assertEqual([x["ad_id"] for x in exact["items"]],[ids[-1]])
        self.assertEqual(exact["total"],10_000)
        self.assertFalse(exact["has_more"])
        self.assertEqual(exact["platform_writes"],0)
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id="overflow", observed_at=now, source="kleinanzeigen-management",
            lifecycle_state=LifecycleState.ACTIVE,
            title="Neu", description="Belegte Originalbeschreibung"))
        code, error = self.request("POST", "/api/portfolio/existing-ai/create",
                                  {**base,"request_key":"6"*64})
        self.assertEqual(code, 400)
        with self.store._connect() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM portfolio_existing_jobs").fetchone()[0], 2)

    def test_dashboard_contains_complete_existing_ad_workflow(self):
        for control in ("existing-ai-create", "existing-ai-all-items", "existing-ai-start", "existing-ai-pause",
                        "existing-ai-next-call", "existing-ai-batch", "existing-ai-stop",
                        "existing-ai-approve", "existing-ai-reject", "existing-ai-next",
                        "existing-ai-recovery", "existing-ai-recover",
                        "existing-ai-unknown-ad", "existing-ai-checked-cents",
                        "existing-ai-checked", "existing-ai-settle-unknown"):
            self.assertIn(f'id="{control}"', _DASHBOARD_HTML)
        self.assertNotIn("existing-ai-apply", _DASHBOARD_HTML)
        self.assertIn("Alle verifizierten Eigenanzeigen (max. 10.000)", _DASHBOARD_HTML)

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

    def test_100_synthetic_provider_calls_are_capped_before_the_101st(self):
        # No real provider: the session cap counts each accepted synthetic call.
        for index in range(100):
            self.store.append_ad_snapshot(AdSnapshot(
                ad_id=f"batch-{index:03d}", observed_at=datetime.now(timezone.utc),
                source="private-web-owner-inventory", lifecycle_state=LifecycleState.ACTIVE,
                title=f"Original {index}", description="Nur belegte Fakten"))
        calls=[]
        class Responses:
            def create(_self, **_kwargs):
                calls.append(1)
                return SimpleNamespace(status="completed",
                    output_text=json.dumps({"title":"Neu","description":"Nur belegte Fakten"}),
                    usage=SimpleNamespace(input_tokens=10,output_tokens=10))
        generator=SimpleNamespace(_provider=lambda:SimpleNamespace(responses=Responses()))
        self.start_server(DashboardGenerationAccess(
            ui_token="g"*32, generator_factory=lambda:generator, max_requests=100))
        payload={"mode":"all","ad_ids":[],"instruction":"Nur belegte Fakten",
                 "budget_cents":10_000,"estimate_cents":20,"request_key":"5"*64}
        code,job=self.request("POST","/api/portfolio/existing-ai/create",payload)
        self.assertEqual(code,200)
        self.assertEqual(job["total"],101)
        jid=job["job_id"]
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/state",
            {"job_id":jid,"action":"start"})[0],200)
        generation={"job_id":jid,"acknowledge_provider_cost":True}
        for _ in range(100):
            code,response=self.request("POST","/api/portfolio/existing-ai/generate-next",
                                       generation,generation=True)
            self.assertEqual(code,200,response)
            self.assertEqual(response["claimed"],True)
            self.assertEqual(response["progress"]["platform_writes"],0)
        self.assertEqual(len(calls),100)
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/generate-next",
            generation,generation=True)[0],429)
        self.assertEqual(len(calls),100)
        code,progress=self.request("GET",
            "/api/portfolio/existing-ai/detail?job_id="+jid)
        self.assertEqual(code,200)
        self.assertEqual(progress["counts"]["proposed"],100)
        self.assertEqual(progress["counts"]["queued"],1)
        self.assertEqual(progress["platform_writes"],0)

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


    @unittest.skipUnless(shutil.which("node"), "Node.js needed for dashboard interaction")
    def test_node_100_call_batch_budget_doubleclick_stop_and_unknown(self):
        harness=r"""
const vm=require("node:vm"),fs=require("node:fs"),assert=require("node:assert/strict");
class E {
 constructor(id=""){this.id=id;this.value="";this.checked=false;this.hidden=false;
  this.disabled=false;this.children=[];this.listeners={};this.textContent="";this.open=false;}
 addEventListener(n,f){(this.listeners[n]??=[]).push(f)}
 append(...x){this.children.push(...x)}
 replaceChildren(...x){this.children=[...x]}
 querySelectorAll(){return []}
 focus(){}
 scrollIntoView(){}
 click(){for(const f of this.listeners.click??[])f({target:this})}
}
const elements=new Map(),by=id=>{if(!elements.has(id))elements.set(id,new E(id));return elements.get(id)};
const id="a".repeat(32);
let calls=0,mode="normal",release=null,detailUrls=[];
const confirms=[];
const state={job_id:id,state:"running",platform_writes:0,total:100,
  budget_cents:140,estimate_cents:20,actual_cents:0,reserved_cents:0,
  counts:{queued:100,proposed:0,unknown:0,inflight:0},items:[],has_more:false,next_after:null};
const response=data=>({ok:true,status:200,json:async()=>data,text:async()=>JSON.stringify(data)});
const context={
 document:{getElementById:by,createElement:()=>new E(),querySelectorAll:()=>[]},
 console,URLSearchParams,Uint8Array,Blob,TextEncoder,setTimeout,clearTimeout,
 fetch:async(path,options={})=>{
  if(path.includes("/existing-ai/detail?")){detailUrls.push(path);return response({...state});}
  if(path.includes("/existing-ai/jobs?"))return response({jobs:[{job_id:id,created_at:"2026-10-29T18:00:00",total:100,state:"running"}],has_more:false});
  if(path==="/api/portfolio/existing-ai/generate-next"){
   calls++;
   if(mode==="hold")await new Promise(resolve=>{release=resolve});
   if(mode==="unknown"){
    state.state="needs_attention";state.counts.queued--;state.counts.unknown=1;
    return {ok:false,status:502,json:async()=>({error:"provider_outcome_unknown"})};
   }
   return response({claimed:true,ad_id:"ad-1",platform_writes:0});
  }
  return response({});
 },
 window:{location:{hash:"",pathname:"/",search:"",origin:"http://127.0.0.1"},
  history:{replaceState(){}},sessionStorage:{getItem(){return null},setItem(){}},
  confirm:message=>{confirms.push(message);return true},
  crypto:{getRandomValues:a=>a.fill(7)}},
 globalThis:null,navigator:{},crypto:{getRandomValues:a=>a.fill(7)}
};
context.globalThis=context;vm.createContext(context);
const source=fs.readFileSync(0,"utf8").replace(/consumeWriteToken\(\);\s*load\(\);\s*$/," ");
new vm.Script(source);vm.runInContext(source,context);
vm.runInContext('jobsAvailable=true;localWorkAvailable=true;writeUiAvailable=true;writeToken="t".repeat(32);generationUiAvailable=true;generationToken="g".repeat(32);existingAiActiveId="a".repeat(32);existingAiDisplayedId=existingAiActiveId',context);
by("existing-ai-batch-count").value="100";by("existing-ai-consent").checked=true;
(async()=>{
 // The exact task from the Radar must reach the intended item through
 // the shipped, real JS controller, independent of the job's first 50.
 state.items=[{ad_id:"item-09999",phase:"queued",original:{title:"Alt",description:"Belegt"},
   proposal:null,source_sha256:"a".repeat(64),actual_cents:0,error_code:null}];
 assert.equal(await vm.runInContext('loadExistingAiDetails(true,"item-09999")',context),true);
 assert.match(detailUrls.at(-1),/ad_id=item-09999/);
 assert.match(detailUrls.at(-1),/limit=1/);
 assert.equal(detailUrls.at(-1).includes("after="),false);
 assert.match(by("existing-ai-page").textContent,/Anzeige item-09999/);
 assert.equal(by("existing-ai-next").disabled,true);
 assert.equal(by("existing-ai-all-items").hidden,false);
 // One reversible click returns to the existing paginated job view.
 vm.runInContext('autopilotFocusedJobId=existingAiActiveId;autopilotFocusedAdId="item-09999"',context);
 by("existing-ai-all-items").click();
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(vm.runInContext("autopilotFocusedAdId",context),"");
 assert.equal(by("existing-ai-all-items").hidden,true);
 assert.equal(detailUrls.at(-1).includes("ad_id="),false);
 assert.match(by("existing-ai-page").textContent,/Seite 1/);
 state.items=[{...state.items[0],ad_id:"a different item"}];
 assert.equal(await vm.runInContext('loadExistingAiDetails(true,"item-09999")',context),false);
 assert.match(by("existing-ai-feedback").textContent,/nicht mehr im KI-Auftrag/);
 assert.equal(by("existing-ai-items").children.length,0);
 assert.equal(vm.runInContext("existingAiDisplayedId",context),"");
 state.items=[];
 vm.runInContext("existingAiDisplayedId=existingAiActiveId",context);
 // The persisted USD budget, NOT the editable text fields, permits only seven.
 by("existing-ai-budget").value="999999";
 await vm.runInContext("generateExistingAiBatch()",context);
 assert.equal(calls,7);
 assert.match(confirms[0],/Bis zu 7 einzelne KI-Anfragen/);
 assert.match(confirms[0],/1\.40 USD Budgetreservierungen/);
 assert.equal(by("existing-ai-consent").checked,false);
 assert.equal(vm.runInContext("existingAiBatchBusy",context),false);
 assert.equal(by("existing-ai-stop").disabled,true);
 // Two immediate clicks can never create two competing sequential pipelines.
 calls=0;state.budget_cents=100000;mode="hold";by("existing-ai-consent").checked=true;
 const first=vm.runInContext("generateExistingAiBatch()",context);
 const second=vm.runInContext("generateExistingAiBatch()",context);
 await second;
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(calls,1);
 assert.equal(vm.runInContext("existingAiBatchBusy",context),true);
 assert.equal(by("existing-ai-batch").disabled,true);
 by("existing-ai-stop").click();
 assert.ok(release);
 release();
 await first;
 assert.equal(calls,1);
 assert.equal(vm.runInContext("existingAiBatchBusy",context),false);
 assert.match(by("existing-ai-feedback").textContent,/gestoppt/);
 // Over-limit input must not reach confirmation or any provider endpoint.
 calls=0;mode="normal";by("existing-ai-consent").checked=true;
 by("existing-ai-batch-count").value="101";
 const confirmations=confirms.length;
 await vm.runInContext("generateExistingAiBatch()",context);
 assert.equal(calls,0);
 assert.equal(confirms.length,confirmations);
 assert.match(by("existing-ai-feedback").textContent,/1 bis 100/);
 // A transport-UNKNOWN stops the batch after exactly one attempted request.
 calls=0;mode="unknown";by("existing-ai-consent").checked=true;
 by("existing-ai-batch-count").value="100";
 by("existing-ai-settle-unknown").disabled=true;
 by("existing-ai-jobs-next").disabled=false;
 by("existing-ai-next").disabled=false;
 await vm.runInContext("generateExistingAiBatch()",context);
 assert.equal(calls,1);
 assert.match(by("existing-ai-feedback").textContent,/nicht blind wiederholen/);
 assert.equal(vm.runInContext("existingAiBatchBusy",context),false);
 assert.equal(by("existing-ai-stop").disabled,true);
 assert.equal(by("existing-ai-settle-unknown").disabled,false,
   "fresh UNKNOWN result must enable manual cost settlement");
 assert.equal(by("existing-ai-jobs-next").disabled,true,
   "refresh pagination state must not revert to stale enable");
 assert.equal(by("existing-ai-next").disabled,true,
   "refresh detail pagination state must not revert");
 console.log("100-call batch: budget, doubleclick, stop, invalid input, UNKNOWN PASS");
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        result=subprocess.run(
            [shutil.which("node") or "node", "-e", harness],
            input=_DASHBOARD_JS, text=True, encoding="utf-8",
            capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)



    @unittest.skipUnless(shutil.which("node"), "Node.js needed for dashboard interaction")
    def test_node_approved_existing_ai_local_owner_editor_handoff(self):
        """A locally approved existing-ad AI draft may enter only the exact unedited own form."""
        harness=r"""
const vm=require("node:vm"),fs=require("node:fs"),assert=require("node:assert/strict");
const full=fs.readFileSync(0,"utf8");
const first=full.indexOf("async function applyApprovedExistingAiToManage(");
const last=full.indexOf("async function copyApprovedExistingAi(",first);
assert.ok(first>0 && last>first,"owner-scoped local handoff function");
assert.match(full,/apply\.textContent="Im Anzeigeneditor lokal übernehmen"/);
assert.match(full,/apply\.addEventListener\("click",\(\)=>\{void applyApprovedExistingAiToManage\(item,job\.job_id\);\}\)/);
const source=full.slice(first,last);
const jobId="a".repeat(32),requests=[],confirmations=[];
const original={title:"Original",description:"Belegte Fakten."};
const proposal={title:"Besserer Titel",description:"Faktenklarer Text."};
const item={ad_id:"4711",phase:"approved",source_sha256:"sensitive-hash",
  original:{...original},proposal:{...proposal}};
const ad={ad_id:"4711",present:true,lifecycle_state:"active",source:"kleinanzeigen-management",
  observed_at:"2026-11-02T04:05:00Z",title:original.title,description:original.description};
const fields=new Map();
const byId=id=>{
  if(!fields.has(id))fields.set(id,{value:"",textContent:"",scrollIntoView(){}});
  return fields.get(id);
};
const ctx={
  byId,optionalById:byId,URLSearchParams,encodeURIComponent,
  existingAiActiveId:jobId,existingAiDisplayedId:jobId,existingAiBusy:false,
  existingAiBatchBusy:false,existingAiHandoffBusy:false,
  existingAiCursors:[""],existingAiPageIndex:0,
  autopilotFocusedJobId:"",autopilotFocusedAdId:"",
  pendingRecoveryBlocked:false,pendingWrites:new Map(),pendingForAd:()=>null,
  writeUiReady:()=>true,latestAdsById:new Map([["4711",ad]]),
  managedAdBaseline:{adId:"4711",titleKnown:true,descriptionKnown:true,
    title:original.title,description:original.description},
  VERIFIED_EXISTING_AD_SOURCES:new Set(["kleinanzeigen-management"]),
  resetGenerationExistingTarget:()=>{},
  window:{confirm:msg=>{confirmations.push(msg);return true}},
  _jobGet:async path=>{requests.push(["job",path]);return {
    job_id:jobId,platform_writes:0,items:[structuredClone(item)]}},
  getJson:async path=>{requests.push(["ad",path]);return {ad:{...ad}}}
};
vm.createContext(ctx);new vm.Script(source).runInContext(ctx);
const setForm=()=>{
  byId("manage-ad-id").value="4711";
  byId("manage-ad-select").value="4711";
  byId("manage-title").value=original.title;
  byId("manage-description").value=original.description;
};
const checkUnchanged=()=>{
  assert.equal(byId("manage-title").value,original.title);
  assert.equal(byId("manage-description").value,original.description);
};
const apply=changed=>ctx.applyApprovedExistingAiToManage(changed??item,jobId);
const reset=()=>{
  setForm();requests.length=0;confirmations.length=0;
  ctx.existingAiActiveId=jobId;
  ctx.existingAiDisplayedId=jobId;
  ctx.existingAiBusy=false;ctx.existingAiBatchBusy=false;
  ctx.existingAiHandoffBusy=false;ctx.pendingRecoveryBlocked=false;
  ctx.autopilotFocusedJobId="";ctx.autopilotFocusedAdId="";
  ctx.writeUiReady=()=>true;
  ctx.managedAdBaseline={adId:"4711",titleKnown:true,descriptionKnown:true,
    title:original.title,description:original.description};
  ctx.latestAdsById=new Map([["4711",ad]]);
  ctx._jobGet=async path=>{requests.push(["job",path]);return {
    job_id:jobId,platform_writes:0,items:[structuredClone(item)]}};
  ctx.getJson=async path=>{requests.push(["ad",path]);return {ad:{...ad}}};
  ctx.window.confirm=msg=>{confirmations.push(msg);return true};
};
(async()=>{
  reset();
  assert.equal(await apply(),true,"approved own ad goes into local editor");
  assert.equal(byId("manage-title").value,proposal.title);
  assert.equal(byId("manage-description").value,proposal.description);
  assert.equal(requests.length,2,"exactly two local read-only requests");
  assert.ok(requests[0][1].includes("/api/portfolio/existing-ai/detail?"));
  assert.equal(requests[1][1],"/api/catalog/ad?id=4711");
  assert.equal(confirmations.length,1,"explicit separate confirmation");
  assert.match(confirmations[0],/Keine Plattformänderung/);
  assert.match(byId("existing-ai-feedback").textContent,/lokalen Anzeigeneditor/);
  assert.equal(ctx.existingAiHandoffBusy,false);

  // Direct Radar handoff must revalidate the exact ad ID rather than
  // querying the first 50 items and rejecting an otherwise approved ad.
  reset();ctx.autopilotFocusedJobId=jobId;ctx.autopilotFocusedAdId="4711";
  ctx._jobGet=async path=>{requests.push(["job",path]);return {
    job_id:jobId,platform_writes:0,items:[structuredClone(item)],
    has_more:false,next_after:null}};
  assert.equal(await apply(),true,"focused item can enter unchanged own editor");
  assert.equal(requests.length,2);
  assert.match(requests[0][1],/ad_id=4711/);
  assert.match(requests[0][1],/limit=1/);
  assert.equal(requests[0][1].includes("after="),false);
  assert.equal(byId("manage-title").value,proposal.title);
  reset();ctx.autopilotFocusedJobId=jobId;ctx.autopilotFocusedAdId="9999";
  assert.equal(await apply(),false,"focused different ad must not be approved");
  assert.equal(requests.length,0);checkUnchanged();
  reset();ctx.autopilotFocusedJobId=jobId;ctx.autopilotFocusedAdId="4711";
  ctx._jobGet=async path=>{requests.push(["job",path]);return {
    job_id:jobId,platform_writes:0,items:[structuredClone(item)],
    has_more:true,next_after:"4711"}};
  assert.equal(await apply(),false,"inconsistent exact-item response fails closed");
  checkUnchanged();assert.equal(requests.length,1);
  reset();ctx.autopilotFocusedJobId=jobId;ctx.autopilotFocusedAdId="4711";
  ctx._jobGet=async path=>{requests.push(["job",path]);
    ctx.autopilotFocusedAdId="4712";
    return {job_id:jobId,platform_writes:0,items:[structuredClone(item)],
      has_more:false,next_after:null}};
  assert.equal(await apply(),false,"task focus drift during read invalidates handoff");
  checkUnchanged();assert.equal(requests.length,1);

  reset();
  assert.equal(await apply({...item,phase:"proposed"}),false);
  assert.equal(await apply({...item,ad_id:"4712"}),false);
  checkUnchanged();assert.equal(requests.length,0);
  assert.equal(confirmations.length,0);

  reset();ctx.writeUiReady=()=>false;
  assert.equal(await apply(),false);
  checkUnchanged();assert.equal(requests.length,0);

  reset();ctx.pendingRecoveryBlocked=true;
  assert.equal(await apply(),false);
  checkUnchanged();assert.equal(requests.length,0);

  reset();byId("manage-title").value="Manuelle Änderung";
  assert.equal(await apply(),false);
  assert.equal(byId("manage-title").value,"Manuelle Änderung");
  assert.equal(requests.length,0);

  reset();ctx._jobGet=async path=>{requests.push(["job",path]);return {
    job_id:jobId,platform_writes:0,items:[{...item,phase:"rejected"}]}};
  assert.equal(await apply(),false);
  checkUnchanged();assert.equal(requests.length,1);

  reset();ctx._jobGet=async path=>{requests.push(["job",path]);return {
    job_id:jobId,platform_writes:1,items:[item]}};
  assert.equal(await apply(),false);
  checkUnchanged();assert.equal(requests.length,1);

  reset();ctx.getJson=async path=>{requests.push(["ad",path]);return {ad:{...ad,title:"Changed"}}};
  assert.equal(await apply(),false);
  checkUnchanged();assert.equal(requests.length,2);

  reset();ctx.window.confirm=()=>{confirmations.push("race");
    byId("manage-description").value="Benutzereingabe";return true};
  assert.equal(await apply(),false);
  assert.equal(byId("manage-description").value,"Benutzereingabe");
  assert.equal(byId("manage-title").value,original.title);

  reset();ctx._jobGet=async path=>{requests.push(["job",path]);
    ctx.existingAiActiveId="b".repeat(32);
    return {job_id:jobId,platform_writes:0,items:[item]}};
  assert.equal(await apply(),false);
  checkUnchanged();assert.equal(requests.length,1);

  reset();ctx._jobGet=async path=>{requests.push(["job",path]);
    ctx.existingAiPageIndex=1;ctx.existingAiCursors=["","after-key"];
    return {job_id:jobId,platform_writes:0,items:[item]}};
  assert.equal(await apply(),false,"page change during local fetch must invalidate approval");
  checkUnchanged();assert.equal(requests.length,1);
  ctx.existingAiPageIndex=0;ctx.existingAiCursors=[""];

  reset();
  let release;ctx._jobGet=async path=>{requests.push(["job",path]);
    return await new Promise(resolve=>{release=resolve})};
  const firstCall=apply();await Promise.resolve();
  assert.equal(await apply(),false,"double click while in flight is rejected");
  release({job_id:jobId,platform_writes:0,items:[item]});
  assert.equal(await firstCall,true);
  assert.equal(ctx.existingAiHandoffBusy,false);
  assert.ok(requests.every(([method])=>method==="job"||method==="ad"));
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        result=subprocess.run(
            [shutil.which("node") or "node", "-e", harness],
            input=_DASHBOARD_JS,text=True,encoding="utf-8",
            capture_output=True,timeout=25,
        )
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js needed for dashboard interaction")
    def test_node_approved_existing_ai_clipboard_only_manual_handoff(self):
        harness=r"""
const vm=require("node:vm"), fs=require("node:fs"), assert=require("node:assert/strict");
class E {
  constructor(id="") {
    this.id=id; this.value=""; this.checked=false; this.hidden=false;
    this.disabled=false; this.children=[]; this.listeners={}; this.textContent="";
  }
  addEventListener(name,fn) {(this.listeners[name]??=[]).push(fn)}
  append(...children) {this.children.push(...children)}
  replaceChildren(...children) {this.children=[...children]}
  click() {for(const fn of this.listeners.click??[])fn({target:this})}
  focus() {} scrollIntoView() {} querySelectorAll() {return []}
}
const elements=new Map(), by=id=>{
  if(!elements.has(id))elements.set(id,new E(id));
  return elements.get(id);
};
const document={getElementById:by,createElement:()=>new E(),querySelectorAll:()=>[]};
const jobId="a".repeat(32), copied=[], requests=[];
const ad=(phase,id,proposal)=>({
  ad_id:id,phase,source_sha256:"sensitive-source-sha",
  original:{title:"ORIGINAL_SECRET",description:"OWNER_TOKEN_SHOULD_NOT_COPY"},
  proposal:proposal??{title:"Verbesserter Titel",description:"Belegte Details."}
});
const job={
  job_id:jobId,state:"review",total:5,platform_writes:0,
  budget_cents:200,estimate_cents:20,actual_cents:0,reserved_cents:0,
  counts:{approved:2,proposed:1,rejected:1,unknown:1,queued:0,inflight:0},
  has_more:false,next_after:null,
  items:[ad("approved","4711"),ad("proposed","ad-4712"),
    ad("rejected","ad-4713"),ad("unknown","ad-4714"),
    ad("approved","ad-4715",{title:" ",description:"Belegte Details."})]
};
const navigator={clipboard:{writeText:async value=>{copied.push(value)}}};
const response={ok:true,status:200,json:async()=>job,text:async()=>JSON.stringify(job)};
const context={document,navigator,console,URLSearchParams,Uint8Array,Blob,TextEncoder,
  setTimeout,clearTimeout,
  fetch:async path=>{requests.push(path);return response},
  window:{location:{hash:"",pathname:"/",search:"",origin:"http://127.0.0.1"},
    history:{replaceState(){}},sessionStorage:{getItem(){return null},setItem(){}},
    confirm:()=>true,crypto:{getRandomValues:a=>a.fill(7)}},
  globalThis:null,crypto:{getRandomValues:a=>a.fill(7)}};
context.globalThis=context;vm.createContext(context);
const source=fs.readFileSync(0,"utf8").replace(/consumeWriteToken\(\);\s*load\(\);\s*$/," ");
new vm.Script(source);vm.runInContext(source,context);
vm.runInContext('existingAiActiveId="a".repeat(32)',context);
const findAction=(row,label)=>row.children[2].children.find(child=>child.textContent===label);
const findCopy=row=>findAction(row,"Freigegebenen Vorschlag kopieren");
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  assert.equal(await vm.runInContext("loadExistingAiDetails()",context),true);
  const rows=by("existing-ai-items").children;
  assert.equal(rows.length,5);
  assert.ok(findCopy(rows[0]),"approved suggestion must be copyable");
  assert.ok(findAction(rows[0],"Im Anzeigeneditor lokal übernehmen"),
    "approved numeric own-ad suggestion offers explicit local handoff");
  assert.ok(findAction(rows[0],"Titel kopieren"));
  assert.ok(findAction(rows[0],"Beschreibung kopieren"));
  for(const idx of [1,2,3,4]){
    for(const label of ["Freigegebenen Vorschlag kopieren","Titel kopieren","Beschreibung kopieren"])
      assert.equal(findAction(rows[idx],label),undefined);
  }
  assert.equal(requests.length,1,"only one read to populate the table");
  findCopy(rows[0]).click();await settle();
  assert.deepEqual(copied,["Titel:\nVerbesserter Titel\n\nBeschreibung:\nBelegte Details."]);
  assert.equal(copied[0].includes("4711"),false);
  assert.equal(copied[0].includes("OWNER_TOKEN"),false);
  assert.match(by("existing-ai-feedback").textContent,/kopiert/i);
  assert.equal(requests.length,1,"clipboard action must never call platform API");
  findAction(rows[0],"Titel kopieren").click();await settle();
  findAction(rows[0],"Beschreibung kopieren").click();await settle();
  assert.deepEqual(copied.slice(1),["Verbesserter Titel","Belegte Details."]);
  assert.equal(requests.length,1,"single-field copy remains network-free");
  navigator.clipboard=null;
  findCopy(rows[0]).click();await settle();
  assert.match(by("existing-ai-feedback").textContent,/nicht verfügbar/);
  assert.equal(copied.length,3);
  navigator.clipboard={writeText:async()=>{throw new Error("SECRET_PRIVATE_TOKEN")}};
  findCopy(rows[0]).click();await settle();
  assert.match(by("existing-ai-feedback").textContent,/fehlgeschlagen/);
  assert.equal(by("existing-ai-feedback").textContent.includes("SECRET_PRIVATE_TOKEN"),false);
  assert.equal(copied.length,3);
  navigator.clipboard={writeText:async value=>{copied.push(value)}};
  vm.runInContext('existingAiActiveId="b".repeat(32)',context);
  findCopy(rows[0]).click();await settle();
  assert.match(by("existing-ai-feedback").textContent,/neu laden/);
  assert.equal(copied.length,3);
  assert.equal(requests.length,1);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        result=subprocess.run(
            [shutil.which("node") or "node", "-e", harness],
            input=_DASHBOARD_JS,text=True,encoding="utf-8",
            capture_output=True,timeout=25,
        )
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


    def _await_autonomous(self, job_id, predicate, *, seconds=4):
        deadline=monotonic()+seconds
        latest=None
        while monotonic()<deadline:
            latest=existing_job_details(self.store,job_id)
            if predicate(latest):
                return latest
            sleep(0.05)
        self.fail(f"autonomy did not reach expected persisted state: {latest}")

    def test_autonomy_needs_separate_cost_authorization_then_processes_and_stops(self):
        for i in range(2):
            self.store.append_ad_snapshot(AdSnapshot(
                ad_id=f"auto-{i}",observed_at=datetime.now(timezone.utc),
                source="private-web-owner-inventory",lifecycle_state=LifecycleState.ACTIVE,
                title=f"Original {i}",description="Nur belegte Fakten"))
        calls=[]
        class Responses:
            def create(_self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(status="completed",
                    output_text=json.dumps({"title":"Neu","description":"Nur belegte Fakten"}),
                    usage=SimpleNamespace(input_tokens=10,output_tokens=10))
        generator=SimpleNamespace(_provider=lambda:SimpleNamespace(responses=Responses()))
        access=DashboardGenerationAccess(ui_token="g"*32,generator_factory=lambda:generator,max_requests=1)
        self.start_server(access)
        code,job=self.request("POST","/api/portfolio/existing-ai/create",{
            "mode":"all","ad_ids":[],"instruction":"Sachlich verbessern",
            "budget_cents":200,"estimate_cents":20,"request_key":"a"*64})
        self.assertEqual(code,200,job)
        jid=job["job_id"]
        self.assertEqual(self.request("POST","/api/portfolio/existing-ai/state",
            {"job_id":jid,"action":"start"})[0],200)
        endpoint="/api/portfolio/existing-ai/autonomy"
        enable={"job_id":jid,"action":"enable","max_calls":2,"grant_key":"f"*64,"acknowledge_provider_cost":True}
        self.assertEqual(self.request("POST",endpoint,enable)[0],403)
        self.assertEqual(self.request("POST",endpoint,enable,token="bad",generation=True)[0],403)
        self.assertEqual(self.request("POST",endpoint,enable,origin="http://evil.invalid",
                                       generation=True)[0],403)
        self.assertEqual(self.request("POST",endpoint,{**enable,
            "acknowledge_provider_cost":False},generation=True)[0],400)
        self.assertEqual(calls,[])
        code,started=self.request("POST",endpoint,enable,generation=True)
        self.assertEqual(code,200,started)
        self.assertEqual(started["autonomy"]["max_calls"],2)
        # A duplicate current grant is a readback, not a fresh allowance.
        code,replay=self.request("POST",endpoint,enable,generation=True)
        self.assertEqual(code,200,replay)
        self.assertEqual(replay["autonomy"]["max_calls"],2)
        done=self._await_autonomous(jid,lambda p:p["counts"].get("proposed",0)==2)
        self.assertEqual(len(calls),2)
        code,replay=self.request("POST",endpoint,enable,generation=True)
        self.assertEqual(code,200,replay)
        self.assertEqual(replay["autonomy"]["used_calls"],2)
        self.assertEqual(done["counts"]["queued"],1)
        self.assertEqual(done["autonomy"]["used_calls"],2)
        self.assertEqual(done["autonomy"]["reason"],"call_limit_reached")
        self.assertFalse(done["autonomy"]["enabled"])
        self.assertEqual(done["platform_writes"],0)
        code,restart=self.request("POST",endpoint,{**enable,"max_calls":1,"grant_key":"e"*64},generation=True)
        self.assertEqual(code,200,restart)
        done=self._await_autonomous(jid,lambda p:p["state"]=="review")
        self.assertEqual(done["counts"]["proposed"],3)
        self.assertEqual(len(calls),3)
        self.assertEqual(done["autonomy"]["used_calls"],1)
        self.assertEqual(done["platform_writes"],0)

    def test_autonomy_stop_during_provider_excludes_second_call(self):
        self.store.append_ad_snapshot(AdSnapshot(ad_id="auto-second",
            observed_at=datetime.now(timezone.utc),source="private-web-owner-inventory",
            lifecycle_state=LifecycleState.ACTIVE,title="Zweite",description="Belegte Fakten"))
        entered,released=Event(),Event()
        self.addCleanup(released.set)
        calls=[]
        class Responses:
            def create(_self,**_kwargs):
                calls.append(1)
                entered.set()
                if not released.wait(3):
                    raise TimeoutError("test provider did not release")
                return SimpleNamespace(status="completed",
                    output_text=json.dumps({"title":"Neu","description":"Nur belegte Fakten"}),
                    usage=SimpleNamespace(input_tokens=10,output_tokens=10))
        generator=SimpleNamespace(_provider=lambda:SimpleNamespace(responses=Responses()))
        self.start_server(DashboardGenerationAccess(
            ui_token="g"*32,generator_factory=lambda:generator,max_requests=5))
        code,job=self.request("POST","/api/portfolio/existing-ai/create",{
            "mode":"all","ad_ids":[],"instruction":"Nur Fakten","budget_cents":200,
            "estimate_cents":20,"request_key":"b"*64})
        self.assertEqual(code,200,job)
        jid=job["job_id"]
        self.request("POST","/api/portfolio/existing-ai/state",
                     {"job_id":jid,"action":"start"})
        auto="/api/portfolio/existing-ai/autonomy"
        self.assertEqual(self.request("POST",auto,{"job_id":jid,"action":"enable",
            "max_calls":2,"grant_key":"c"*64,"acknowledge_provider_cost":True},generation=True)[0],200)
        self.assertTrue(entered.wait(3),"worker did not start synthetic call")
        try:
            code,stopped=self.request("POST",auto,{"job_id":jid,"action":"stop"})
            self.assertEqual(code,200,stopped)
            self.assertFalse(stopped["autonomy"]["enabled"])
        finally:
            released.set()
        done=self._await_autonomous(jid,lambda p:p["counts"].get("proposed",0)==1)
        self.assertEqual(done["counts"]["queued"],1)
        sleep(0.4)
        self.assertEqual(len(calls),1)
        self.assertEqual(done["autonomy"]["reason"],"stopped")

    def test_autonomous_unknown_is_quarantined_without_retry(self):
        self.store.append_ad_snapshot(AdSnapshot(ad_id="auto-unknown-second",
            observed_at=datetime.now(timezone.utc),source="private-web-owner-inventory",
            lifecycle_state=LifecycleState.ACTIVE,title="Zweite",description="Belegte Fakten"))
        calls=[]
        class Responses:
            def create(_self,**_kwargs):
                calls.append(1)
                raise TimeoutError("provider response lost")
        generator=SimpleNamespace(_provider=lambda:SimpleNamespace(responses=Responses()))
        self.start_server(DashboardGenerationAccess(ui_token="g"*32,
            generator_factory=lambda:generator,max_requests=5))
        code,job=self.request("POST","/api/portfolio/existing-ai/create",{
            "mode":"all","ad_ids":[],"instruction":"Nur Fakten","budget_cents":200,
            "estimate_cents":20,"request_key":"d"*64})
        self.assertEqual(code,200,job)
        jid=job["job_id"]
        self.request("POST","/api/portfolio/existing-ai/state",
                     {"job_id":jid,"action":"start"})
        auto="/api/portfolio/existing-ai/autonomy"
        self.assertEqual(self.request("POST",auto,{"job_id":jid,"action":"enable",
            "max_calls":3,"grant_key":"d"*64,"acknowledge_provider_cost":True},generation=True)[0],200)
        done=self._await_autonomous(jid,lambda p:p["state"]=="needs_attention" and
                                    not p["autonomy"]["enabled"])
        self.assertEqual(done["counts"]["unknown"],1)
        self.assertEqual(done["counts"]["queued"],1)
        self.assertEqual(done["reserved_cents"],20)
        self.assertEqual(done["autonomy"]["reason"],"provider_outcome_unknown")
        sleep(0.4)
        self.assertEqual(len(calls),1)
        self.assertEqual(done["platform_writes"],0)

    def test_autonomous_restart_recovers_lost_claim_before_provider(self):
        from mark_api.portfolio_existing_jobs import (
            configure_autonomy,consume_autonomy_call,
        )
        self.store.append_ad_snapshot(AdSnapshot(ad_id="auto-restart-next",
            observed_at=datetime.now(timezone.utc),source="private-web-owner-inventory",
            lifecycle_state=LifecycleState.ACTIVE,title="Zweite",description="Belegte Fakten"))
        # A persisted explicit permission with an unfinished provider call:
        # next process startup must quarantine rather than try again.
        from mark_api.portfolio_existing_jobs import create_job as create_existing
        jid=create_existing(self.store,["4711","auto-restart-next"],
            instruction="Nur Fakten",budget_cents=200,estimate_cents=20,
            request_key="f"*64)["job_id"]
        from mark_api.portfolio_existing_jobs import change_state as change_existing
        change_existing(self.store,jid,"start")
        configure_autonomy(self.store,jid,max_calls=3,grant_key="b"*64)
        consume_autonomy_call(self.store,jid)
        claim_next(self.store,jid)
        calls=[]
        generator=SimpleNamespace(_provider=lambda: calls.append(1))
        self.start_server(DashboardGenerationAccess(ui_token="g"*32,
            generator_factory=lambda:generator,max_requests=5))
        done=self._await_autonomous(jid,lambda p:p["state"]=="needs_attention" and
                                    not p["autonomy"]["enabled"])
        self.assertEqual(done["counts"]["unknown"],1)
        self.assertEqual(done["autonomy"]["used_calls"],1)
        self.assertEqual(calls,[])


if __name__ == "__main__": unittest.main()
