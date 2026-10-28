"""Real JS interaction contracts for P2 without provider costs or platform writes."""
from __future__ import annotations

import shutil
import subprocess
import unittest

from mark_api.dashboard import _DASHBOARD_JS


class PortfolioJobsUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node required for JS interaction")
    def test_selection_idempotence_review_and_local_apply(self) -> None:
        harness = r"""
const assert=require("node:assert/strict");
const vm=require("node:vm");
const fs=require("node:fs");
class Element {
  constructor(tag=""){
    this.tag=tag;this.children=[];this.value="";this.textContent="";
    this.hidden=false;this.checked=false;this.className="";this.listeners={};
    this.disabled=false;this.open=false;this.title="";
  }
  get lastChild(){return this.children[this.children.length-1];}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  addEventListener(event,callback){this.listeners[event]=callback;}
  querySelectorAll(selector){
    assert.equal(selector,"input[type=checkbox]");
    const walk=n=>n.children.flatMap(c=>[
      ...(c.tag==="input"&&c.type==="checkbox"?[c]:[]),...walk(c)
    ]);
    return walk(this);
  }
  scrollIntoView(){}
}
const elements=new Map();
const document={
  getElementById(id){if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);},
  createElement(tag){return new Element(tag);},
  querySelectorAll(){return [];}
};
const confirmation=[];
const window={addEventListener(){},confirm(text){confirmation.push(text);return true;},
  crypto:{getRandomValues(array){for(let i=0;i<array.length;i++)array[i]=i;return array;}}};
const requests=[];
let locallyApplied=false;
let jobSwitchRace=false;
let pendingOldJobReply=null;
let rejectFirstCreate=true;
let jobs=[];
let batchCalls=0;
let failThirdBatchCall=true;
let stopDuringNextBatch=false;
const skus=["SKU-00001","SKU-00002"];
async function fetch(path,options={}){
  assert.ok(path.startsWith("/api/portfolio/"),"unexpected platform/backend action "+path);
  const method=options.method||"GET";
  const body=options.body?JSON.parse(options.body):null;
  requests.push({path,method,body});
  if(path.startsWith("/api/portfolio/drafts?")){
    const cursor=new URL(path,"http://127.0.0.1:8765").searchParams.get("after");
    const isSecond=!!cursor;
    return {ok:true,json:async()=>({drafts:[{sku:isSecond?skus[1]:skus[0],
      title:locallyApplied?"Lokal verbessert":"Original",description:"Fakten",category:"Haus > Licht",price_eur:29}],
      has_more:!isSecond,next_after:isSecond?null:skus[0],total:2})};
  }
  if(path==="/api/portfolio/jobs/create"){
    assert.equal(method,"POST");
    assert.equal(body.mode,"selected");
    assert.deepEqual(body.skus,skus);
    assert.equal(body.budget_cents,200);
    assert.equal(body.estimate_cents,2);
    if(rejectFirstCreate){
      rejectFirstCreate=false;
      throw new Error("synthetic ambiguous network reply");
    }
    jobs=[{job_id:"a".repeat(32),created_at:"2026-10-26T00:00:00Z",
      state:"review",total:2}];
    return {ok:true,json:async()=>({job_id:jobs[0].job_id,total:2})};
  }
  if(path==="/api/portfolio/jobs?limit=50"){
    assert.ok(options.headers["X-Mark-Dashboard-Token"]);
    return {ok:true,json:async()=>({jobs})};
  }
  if(path.startsWith("/api/portfolio/jobs/detail?")){
    if(jobSwitchRace){
      const id=new URL(path,"http://127.0.0.1:8765").searchParams.get("job_id");
      if(id==="a".repeat(32)){
        return new Promise(resolve=>{pendingOldJobReply=resolve;});
      }
      if(id==="b".repeat(32)) throw new Error("synthetic B detail timeout");
    }
    return {ok:true,json:async()=>({
      ...jobs[0], counts:{proposed:1,queued:1,approved:0,unknown:0},
      actual_cents:1,reserved_cents:0,budget_cents:200,items:[
        {sku:skus[0],original:{title:"Original",description:"Fakten"},
         proposal:{title:"Besser",description:"Verbessert"},phase:"proposed"}],
      has_more:false,next_after:null
    })};
  }
  if(path==="/api/portfolio/jobs/generate-next"){
    assert.equal(method,"POST");
    assert.equal(body.acknowledge_provider_cost,true);
    assert.equal(body.job_id,"a".repeat(32));
    assert.ok(options.headers["X-Mark-Generation-Token"]);
    batchCalls++;
    if(stopDuringNextBatch){
      stopDuringNextBatch=false;
      document.getElementById("job-batch-stop").listeners.click();
    }
    const failed=failThirdBatchCall && batchCalls===3;
    return {ok:!failed,status:failed?502:200,json:async()=>({claimed:!failed})};
  }
  if(path==="/api/portfolio/jobs/review"||path==="/api/portfolio/jobs/apply-local"){
    assert.equal(method,"POST");
    assert.equal(body.job_id,"a".repeat(32));
    if(path.endsWith("/review")){
      assert.deepEqual(body.skus,[skus[0]]);
      assert.equal(body.approve,true);
    }
    if(path.endsWith("/apply-local")) locallyApplied=true;
    return {ok:true,json:async()=>({job_id:body.job_id,applied:1,platform_writes:0})};
  }
  throw new Error("unexpected request "+path);
}
const code=fs.readFileSync(0,"utf8").replace(/\r\n/g,"\n");
const end=code.indexOf("\nconsumeWriteToken();\nload();");
assert.ok(end>0);
const context={document,window,URL,URLSearchParams,fetch,console,Date};
vm.createContext(context);
vm.runInContext(code.slice(0,end),context);
vm.runInContext('writeUiAvailable=true; jobsAvailable=true; writeToken="x".repeat(32);',context);
const at=id=>document.getElementById(id);
(async()=>{
  vm.runInContext('generationUiAvailable=true; jobsAvailable=false; renderJobsAvailability()',context);
  assert.equal(at('job-provider-control').hidden,true,'provider controls must hide before job probe succeeds');
  vm.runInContext('jobsAvailable=true; renderJobsAvailability()',context);
  assert.equal(at('job-provider-control').hidden,false,'provider controls must be shown after successful job probe');
  assert.equal(await vm.runInContext("loadDraftsPage()",context),true);
  let cb=at("drafts-body").children[0].children[0].children[0];
  cb.checked=true;cb.listeners.change();
  assert.equal(at("job-selected-count").textContent,"1 Entwürfe einzeln ausgewählt");
  assert.equal(await vm.runInContext("nextDraftsPage()",context),true);
  cb=at("drafts-body").children[0].children[0].children[0];
  cb.checked=true;cb.listeners.change();
  assert.equal(at("job-selected-count").textContent,"2 Entwürfe einzeln ausgewählt");
  at("job-instruction").value="Nur belegbare Fakten optimieren";
  at("job-budget-eur").value="2.00";
  at("job-estimate-eur").value="0.02";
  await vm.runInContext("createJob()",context);
  assert.ok(at("jobs-feedback").textContent.includes("synthetic ambiguous"));
  await vm.runInContext("createJob()",context);
  const createCalls=requests.filter(r=>r.path.endsWith("/create"));
  assert.equal(createCalls.length,2);
  assert.equal(createCalls[0].body.request_key,createCalls[1].body.request_key);
  assert.equal(at("job-choice").value,"a".repeat(32));
  assert.equal(at("job-items-body").children.length,1);
  const reviewCheckbox=at("job-items-body").children[0].children[0].children[0];
  reviewCheckbox.checked=true;reviewCheckbox.listeners.change();
  await vm.runInContext('jobAction("review",{skus:["SKU-00001"],approve:true})',context);
  at("drafts-disclosure").open=true;
  const draftFetchesBeforeApply=requests.filter(r=>r.path.startsWith("/api/portfolio/drafts?")).length;
  await vm.runInContext('jobAction("apply-local",{})',context);
  assert.equal(requests.filter(r=>r.path.startsWith("/api/portfolio/drafts?")).length,
               draftFetchesBeforeApply+1,"successful local apply must refresh displayed draft rows");
  assert.equal(at("drafts-body").children[0].children[2].textContent,
               "Lokal verbessert","create-form source must reflect applied changes");
  at("job-clear-selection").listeners.click();
  assert.equal(at("job-selected-count").textContent,"0 Entwürfe einzeln ausgewählt");
  assert.equal(at("drafts-body").children.length,1,"clear selection must not erase page");
  assert.equal(at("drafts-body").children[0].children[0].children[0].checked,false);
  vm.runInContext('generationUiAvailable=true; generationToken="g".repeat(32);',context);
  at("job-estimate-eur").value="0.20";
  at("job-batch-count").value="4";
  at("job-provider-consent").checked=true;
  await vm.runInContext("generateJobBatch()",context);
  assert.equal(batchCalls,3,"unknown outcome must halt remaining billable calls");
  assert.equal(at("job-generate-batch").disabled,false);
  assert.equal(at("job-batch-stop").disabled,true);
  assert.ok(at("job-provider-status").textContent.includes("angehalten"));
  failThirdBatchCall=false;
  at("job-batch-count").value="2";
  at("job-provider-consent").checked=true;
  await vm.runInContext("generateJobBatch()",context);
  assert.equal(batchCalls,5);
  assert.ok(at("job-provider-status").textContent.includes("gespeichert"));
  stopDuringNextBatch=true;
  at("job-batch-count").value="4";
  at("job-provider-consent").checked=true;
  await vm.runInContext("generateJobBatch()",context);
  assert.equal(batchCalls,6,"stop must prevent all subsequent calls");
  assert.ok(at("job-provider-status").textContent.includes("gestoppt"));
  assert.ok(requests.every(r=>r.path.startsWith("/api/portfolio/")));

  // A is still being loaded when the user changes to B. Both have the
  // same SKU; a stale A checkbox must never authorize B's unseen proposal.
  jobSwitchRace=true;
  const oldId="a".repeat(32),newId="b".repeat(32);
  vm.runInContext('jobActiveId="a".repeat(32)',context);
  const pendingA=vm.runInContext("loadJobDetails(true)",context);
  assert.ok(pendingOldJobReply,"first detail request must be pending");
  at("job-choice").value=newId;
  at("job-choice").listeners.change();
  await new Promise(resolve=>setImmediate(resolve));
  pendingOldJobReply({ok:true,json:async()=>({
    job_id:oldId,items:[{sku:skus[0],phase:"proposed",
      proposal:{title:"Old job A",description:"Do not approve"},original:{title:"Old"}}],
    total:1,has_more:false,counts:{proposed:1},
    actual_cents:0,reserved_cents:0,budget_cents:20,state:"review"
  })});
  await pendingA;
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(vm.runInContext("jobActiveId",context),newId);
  assert.equal(at("job-items-body").children.length,0,
               "old job A proposals cannot remain actionable for B");
  const priorApprovals=requests.filter(r=>r.path.endsWith("/review")).length;
  vm.runInContext('jobReviewSelected.add("SKU-00001");jobReview(true)',context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(requests.filter(r=>r.path.endsWith("/review")).length,priorApprovals,
               "same SKU must not approve unverified job B");
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result = subprocess.run(
            [shutil.which("node") or "node", "-e", harness],
            input=_DASHBOARD_JS, text=True, encoding="utf-8",
            capture_output=True, check=False, timeout=25,
        )
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=="__main__":
    unittest.main()