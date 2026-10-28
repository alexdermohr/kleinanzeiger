"""Real browser-state JS regression for the manual follow-up UI."""
from __future__ import annotations
import shutil
import subprocess
import unittest

from mark_api.dashboard import _DASHBOARD_HTML, _DASHBOARD_JS


class FollowupUiTests(unittest.TestCase):
    def test_compact_local_controls_and_unknown_expiry(self):
        for marker in (
            'id="followup-pane"', 'id="followup-ad-select"', 'id="followup-date"',
            'id="followup-note"', 'id="followup-save"', 'id="followup-delete"',
            'id="followup-body"', 'id="followup-prev"', 'id="followup-next"',
            'Ein Plattform-Ablaufdatum ist nicht bekannt',
        ):
            self.assertIn(marker, _DASHBOARD_HTML)
        self.assertIn('"/api/portfolio/followups/mutate"', _DASHBOARD_JS)
        self.assertIn('unknown_no_verified_observation', _DASHBOARD_JS)
        self.assertNotIn("innerHTML", _DASHBOARD_JS[
            _DASHBOARD_JS.index("function renderFollowupControls()"):
            _DASHBOARD_JS.index("function renderCatalogSelection()")
        ])

    @unittest.skipUnless(shutil.which("node"), "Node.js required for real JS")
    def test_real_js_races_crud_pagination_and_unknown_write(self):
        harness = r"""
const assert=require("node:assert/strict");
const vm=require("node:vm");
const fs=require("node:fs");
const source=fs.readFileSync(0,"utf8").replace(/\r\n/g,"\n");
const start=source.indexOf("function renderFollowupControls()");
const end=source.indexOf("\nfunction renderCatalogSelection()",start);
assert.ok(start>0&&end>start);
class Element {
  constructor(id="") {this.id=id;this.value="";this.disabled=false;this.hidden=false;
    this.textContent="";this.open=false;this.children=[];this.listeners={};}
  addEventListener(k,fn){this.listeners[k]=fn;}
  replaceChildren(...children){this.children=[...children];}
  append(...children){this.children.push(...children);}
}
const elements=new Map();
const document={
  getElementById(id) {
    if(!elements.has(id)) elements.set(id,new Element(id));
    return elements.get(id);
  },
  createElement(id){return new Element(id);}
};
const get=(id)=>document.getElementById(id);
let current={
  "123":{ad_id:"123",manual_followup_date:"2026-10-31",manual_note:"Prüfen",
    revision:1,source:"manual_user_followup",platform_expiry_at:null},
  "456":{ad_id:"456",manual_followup_date:"2026-11-04",manual_note:"Andere Anzeige",
    revision:3,source:"manual_user_followup",platform_expiry_at:null},
};
let tombstones={};
let postCalls=[],getCalls=[],failNextPost=false,rejectNextPost=false;
let confirmRemoval=true,delayedResolve=null;
let delayPage=false,releasePage=null;
const success=(body,status=200)=>({ok:status>=200&&status<300,status,async json(){return body;}});
const fetch=async(path,options)=>{
  if(!path.startsWith("/api/portfolio/followups")){
    throw Error("Unexpected marketplace/network path "+path);
  }
  if(path.startsWith("/api/portfolio/followups/detail?")){
    const id=new URL(path,"http://local.test").searchParams.get("ad_id");
    getCalls.push(id);
    if(id==="123" && delayedResolve){
      return new Promise(resolve=>{const done=delayedResolve;delayedResolve=null;done(()=>resolve(success({
        followup:current[id]||null,tombstone_revision:tombstones[id]||0,
        platform_expiry_status:"unknown_no_verified_observation"})));});
    }
    return success({followup:current[id]||null,tombstone_revision:tombstones[id]||0,
      platform_expiry_status:"unknown_no_verified_observation"});
  }
  if(path.startsWith("/api/portfolio/followups?")){
    const params=new URL(path,"http://local.test").searchParams;
    assert.equal(params.get("limit"),"50");
    const after=params.get("after")||"";
    const full=Array.from({length:102},(_,i)=>({
      ad_id:String(10000+i),manual_followup_date:"2026-11-05",
      manual_note:"",title:"Lokale Anzeige",
      state:"scheduled",platform_expiry_at:null,
    }));
    const filtered=full.filter(x=>x.ad_id>after);
    const rows=filtered.slice(0,50);
    const page={followups:rows,total:102,overdue:1,due_soon:2,
      has_more:filtered.length>50,
      next_after:filtered.length>50?rows.at(-1).ad_id:null,
      platform_expiry_status:"unknown_no_verified_observation",platform_writes:0};
    if(delayPage){
      delayPage=false;
      return new Promise(resolve=>{releasePage=()=>resolve(success(page));});
    }
    return success(page);
  }
  if(path==="/api/portfolio/followups/mutate"){
    assert.equal(options.method,"POST");
    assert.equal(options.headers["X-Mark-Dashboard-Token"],"token");
    assert.equal(options.headers["Content-Type"],"application/json");
    const data=JSON.parse(options.body);
    postCalls.push(data);
    if(rejectNextPost){
      rejectNextPost=false;return success({error:"invalid_followup"},400);
    }
    if(failNextPost){failNextPost=false;throw Error("lost local response");}
    const revision=data.expected_revision+1;
    if(data.action==="save"){
      delete tombstones[data.ad_id];
      current[data.ad_id]={ad_id:data.ad_id,manual_followup_date:data.due_date,
        manual_note:data.note,revision,source:"manual_user_followup",platform_expiry_at:null};
    }else{
      tombstones[data.ad_id]=revision;
      delete current[data.ad_id];
    }
    return success({ad_id:data.ad_id,revision,platform_writes:0,
      ...(data.action==="delete"?{deleted:true}:{})});
  }
  throw Error("Unrecognized request");
};
const window={
  confirm(){return confirmRemoval;},
};
const context={document,window,fetch,URL,URLSearchParams,encodeURIComponent,
  byId:(id)=>document.getElementById(id),
  optionalById:(id)=>document.getElementById(id),writeUiReady:()=>true,localUiReady:()=>true,
  _jobHeaders:()=>({"X-Mark-Dashboard-Token":"token"}),
  _newJobRequestKey:()=> "a".repeat(64),console,Number,String};
vm.createContext(context);
vm.runInContext([
 "let followupBusy=false;",
 "let followupActiveId='';",
 "let followupRevision=0;",
 "let followupHasLive=false;",
 "let followupDetailLoading=false;",
 "let followupDetailLoaded=false;",
 "let followupDetailsGeneration=0;",
 "let followupPageGeneration=0;",
 "let followupPageIndex=0;",
 "let followupPageCursors=[''];",
 "let followupNextAfter=null;",
 "let followupPending=null;",
 "let followupLoaded=false;",
 "let followupLoading=false;",
 "let catalogRows=[{ad_id:'123',title:'Erste Anzeige',present:true},",
 "{ad_id:'456',title:'Zweite Anzeige',present:true}];",
].join("\n"),context);
vm.runInContext(source.slice(start,end),context);
const run=(expr)=>vm.runInContext(expr,context);
(async()=>{
  run("renderFollowupChoices()");
  assert.equal(get("followup-ad-select").children.length,3);
  get("followup-ad-select").value="456";
  await run("loadSelectedFollowup()");
  assert.equal(get("followup-date").value,"2026-11-04");
  assert.equal(run("followupRevision"),3);

  // A slower first details request cannot re-enable another selected ad.
  delayedResolve=(finish)=>{window.finishA=finish;};
  get("followup-ad-select").value="123";
  const pendingA=run("loadSelectedFollowup()");
  assert.equal(get("followup-date").disabled,true,
    "edit input must remain disabled until current detail GET completes");
  assert.equal(get("followup-note").disabled,true);
  assert.equal(get("followup-save").disabled,true);
  get("followup-ad-select").value="456";
  await run("loadSelectedFollowup()");
  window.finishA();
  await pendingA;
  assert.equal(get("followup-date").value,"2026-11-04");
  assert.equal(run("followupRevision"),3);

  get("followup-ad-select").value="123";
  await run("loadSelectedFollowup()");
  assert.equal(get("followup-date").disabled,false);
  assert.equal(get("followup-save").disabled,false);
  get("followup-date").value="2026-11-15";
  get("followup-note").value="Fotos und Titel prüfen";
  await run('mutateSelectedFollowup("save")');
  assert.equal(postCalls.length,1);
  assert.equal(postCalls[0].action,"save");
  assert.equal(postCalls[0].ad_id,"123");
  assert.equal(postCalls[0].expected_revision,1);
  assert.equal(postCalls[0].request_key,"a".repeat(64));
  assert.equal(get("followup-date").value,"2026-11-15");
  assert.equal(run("followupRevision"),2);
  assert.match(get("followup-summary").textContent,/102 eigene Wiedervorlagen/);
  assert.equal(get("followup-body").children.length,50);
  assert.equal(get("followup-body").children[0].children[1].textContent,"Lokale Anzeige");
  assert.equal(run("followupPageIndex"),0);
  await run("changeFollowupPage(1)");
  assert.equal(run("followupPageIndex"),1);
  assert.equal(get("followup-body").children.length,50);
  await run("changeFollowupPage(1)");
  assert.equal(run("followupPageIndex"),2);
  assert.equal(get("followup-body").children.length,2);
  assert.equal(get("followup-next").disabled,true);

  delayPage=true;
  const inFlight=run("loadFollowups(true)");
  assert.equal(run("followupLoading"),true);
  await run("changeFollowupPage(1)");
  assert.equal(run("followupPageIndex"),0,
    "in-flight page read must fence concurrent navigation");
  releasePage();
  await inFlight;
  assert.equal(run("followupLoading"),false);
  assert.equal(run("followupPageIndex"),0);
  assert.equal(get("followup-next").disabled,false);

  // Unknown local HTTP response is never auto-retried with a new key.
  get("followup-date").value="2026-11-17";
  failNextPost=true;
  await run('mutateSelectedFollowup("save")');
  assert.equal(postCalls.length,2);
  assert.ok(run("followupPending")!==null);
  assert.match(get("followup-feedback").textContent,/Kein automatischer Retry/);
  await run('mutateSelectedFollowup("save")');
  assert.equal(postCalls.length,3);
  assert.equal(postCalls[1].request_key,postCalls[2].request_key);
  assert.equal(run("followupPending"),null);

  confirmRemoval=false;
  await run('mutateSelectedFollowup("delete")');
  assert.equal(postCalls.length,3);
  confirmRemoval=true;
  await run('mutateSelectedFollowup("delete")');
  assert.equal(postCalls.length,4);
  assert.equal(postCalls[3].action,"delete");
  assert.equal(postCalls[3].ad_id,"123");
  assert.equal(postCalls[3].due_date,null);
  assert.equal(postCalls[3].note,"");
  assert.equal(run("followupRevision"),4,
    "deleted follow-up tombstone revision must be preserved for CAS");
  assert.equal(get("followup-delete").disabled,true,
    "do not offer deletion of an already deleted reminder");

  // A definitive 400 never persists an UNKNOWN intent lock; the user must
  // be able to correct the invalid date without restarting Kleinanzeiger.
  get("followup-date").value="2101-01-01";
  rejectNextPost=true;
  await run('mutateSelectedFollowup("save")');
  assert.equal(postCalls.length,5);
  assert.equal(postCalls[4].expected_revision,4);
  assert.equal(run("followupPending"),null);
  assert.match(get("followup-feedback").textContent,/korrigieren/);
  get("followup-date").value="2026-11-20";
  await run('mutateSelectedFollowup("save")');
  assert.equal(postCalls.length,6);
  assert.equal(postCalls[5].expected_revision,4);
  assert.equal(run("followupRevision"),5);
  console.log("FOLLOWUP_UI_PASS");
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        completed = subprocess.run(
            [shutil.which("node") or "node", "-e", harness],
            input=_DASHBOARD_JS, text=True, encoding="utf-8", capture_output=True,
            timeout=35, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("FOLLOWUP_UI_PASS", completed.stdout)


if __name__ == "__main__":
    unittest.main()
