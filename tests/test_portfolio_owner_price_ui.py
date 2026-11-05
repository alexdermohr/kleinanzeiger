"""Execute shipped own-listing price UI with real JavaScript event handlers."""
from __future__ import annotations

import shutil
import subprocess
import unittest

from mark_api.portfolio_insights_ui import INSIGHTS_HTML, INSIGHTS_JS


class OwnerPriceUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node required for actual UI controller")
    def test_owner_price_details_preview_replay_unknown_and_list(self):
        for name in ("owner-price-ad-id", "owner-price-manual", "owner-price-save",
                     "owner-price-body", "owner-price-next", "owner-price-open"):
            self.assertIn('id="' + name + '"', INSIGHTS_HTML)
        harness = r'''
const assert=require("node:assert/strict"),vm=require("node:vm"),fs=require("node:fs");
const source=fs.readFileSync(0,"utf8");
class Element {
  constructor(){this.value="";this.textContent="";this.disabled=false;this.hidden=false;this.children=[];this.listeners={};}
  addEventListener(kind,fn){this.listeners[kind]=fn;}
  appendChild(child){this.children.push(child);}
  replaceChildren(...items){this.children=items;}
  scrollIntoView(){}
}
const elements=new Map();
const byId=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const sent=[];let ready=true, key=0, impl=async()=>{throw Error("fetch unexpected");};
const context={console,URLSearchParams,Number,document:{createElement:()=>new Element()},
  byId,optionalById:byId,localUiReady:()=>ready,_jobHeaders:()=>({"X-Mark-Dashboard-Token":"local"}),
  _newJobRequestKey:()=>String(++key).padStart(64,"0"),
  fetch:async(path,options)=>{sent.push({path,options});return impl(path,options);}};
vm.createContext(context);
vm.runInContext("let jobsAvailable=true;"+source,context);
const run=expression=>vm.runInContext(expression,context);
const success=value=>({ok:true,status:200,json:async()=>value});
const owner=(confirmed=true)=>({confirmed,state:confirmed?"active":"paused",observed_at:"2026-11-05T18:00:00Z",source:"kleinanzeigen-management"});
const detail=(id,confirmed=true,revision=0)=>({ad_id:id,owner:owner(confirmed),platform_writes:0,
  rule:revision?{ad_id:id,revision,manual_price_eur:70,minimum_price_eur:50,
    reduction_step_eur:10,wait_days:7,eligible_at:"2026-11-12T18:00:00Z",reason:"Lokal"}:null});
(async()=>{
  // Deny any new action without a locally authenticated session or active owner.
  ready=false;
  assert.equal(await run("loadOwnerPriceRule('10001')"),false);
  assert.equal(sent.length,0);
  ready=true;
  impl=async()=>success(detail("10001",false));
  assert.equal(await run("loadOwnerPriceRule('10001')"),true);
  byId("owner-price-manual").value="70";
  byId("owner-price-min").value="50";
  byId("owner-price-step").value="10";
  byId("owner-price-wait").value="7";
  run("previewOwnerPriceRule()");
  assert.equal(byId("owner-price-save").disabled,true);
  assert.match(byId("owner-price-original").textContent,/nicht aktuell/);
  impl=async()=>success(detail("10001"));
  assert.equal(await run("loadOwnerPriceRule('10001')"),true);
  byId("owner-price-manual").value="70";
  byId("owner-price-min").value="50";
  byId("owner-price-step").value="10";
  byId("owner-price-wait").value="7";
  run("previewOwnerPriceRule()");
  assert.equal(byId("owner-price-save").disabled,false);
  assert.match(byId("owner-price-preview").textContent,/70 € → 60 €/);
  assert.match(byId("owner-price-preview").textContent,/nicht von der Plattform verifiziert/);
  byId("owner-price-min").value="71";run("previewOwnerPriceRule()");
  assert.equal(byId("owner-price-save").disabled,true);
  byId("owner-price-min").value="50";run("previewOwnerPriceRule()");
  // Stale async response for another ad may not become editable.
  let finishRead;impl=()=>new Promise(done=>{finishRead=done;});
  const pending=run("loadOwnerPriceRule('10001')");
  byId("owner-price-ad-id").value="10002";
  byId("owner-price-ad-id").listeners.input();
  finishRead(success(detail("10001")));
  assert.equal(await pending,false);
  assert.equal(run("ownerPriceTarget"),null);
  assert.equal(byId("owner-price-save").disabled,true);
  // An ambiguous local save cannot be retried or presented as a successful platform action.
  impl=async()=>success(detail("10001",true,3));
  assert.equal(await run("loadOwnerPriceRule('10001')"),true);
  let finishSave;impl=()=>new Promise(done=>{finishSave=done;});
  const save=run("mutateOwnerPriceRule('set')");
  const request=sent.at(-1);
  assert.equal(request.path,"/api/portfolio/owner-price-rules/mutate");
  const payload=JSON.parse(request.options.body);
  assert.equal(payload.ad_id,"10001");
  assert.equal(payload.expected_revision,3);
  assert.equal(payload.manual_price_eur,70);
  assert.equal(payload.minimum_price_eur,50);
  assert.equal(payload.reduction_step_eur,10);
  assert.equal(payload.wait_days,7);
  assert.equal(byId("owner-price-save").disabled,true);
  const before=sent.length;
  assert.equal(await run("mutateOwnerPriceRule('set')"),false);
  assert.equal(sent.length,before);
  finishSave({ok:false,status:503});
  assert.equal(await save,false);
  assert.equal(run("ownerPriceTarget"),null);
  assert.match(byId("owner-price-status").textContent,/nicht blind wiederholen/);
  assert.equal(await run("mutateOwnerPriceRule('set')"),false);
  assert.equal(sent.length,before);
  // Local page uses bounded keyset and textContent for arbitrary labels.
  impl=async()=>success({platform_writes:0,total:2,has_more:true,next_after:"10002",rules:[
    {ad_id:"10001",manual_price_eur:70,minimum_price_eur:50,reduction_step_eur:10,
     wait_days:7,suggested_price_eur:60,reason:"<img onerror=bad()>",
     price_source:"manually_entered_not_platform_verified",platform_writes:0}]});
  assert.equal(await run("loadOwnerPriceRules()"),true);
  const row=byId("owner-price-body").children[0];
  assert.match(row.children[5].textContent,/<img onerror=bad\(\)>/);
  assert.equal(byId("owner-price-next").disabled,false);
  impl=async(path)=>{assert.match(path,/after=10002/);return {ok:false,status:503};};
  assert.equal(await run("loadOwnerPriceRules(true)"),false);
  assert.equal(run("ownerPriceCursor"),null);
  assert.equal(byId("owner-price-next").disabled,true);
  assert.match(byId("owner-price-status").textContent,/Keine automatische Preisänderung/);
  console.log("OWNER_PRICE_UI_PASS",sent.length);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        process = subprocess.run(
            ["node", "-e", harness], input=INSIGHTS_JS, text=True,
            capture_output=True, timeout=45, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn("OWNER_PRICE_UI_PASS", process.stdout)


if __name__ == "__main__":
    unittest.main()
