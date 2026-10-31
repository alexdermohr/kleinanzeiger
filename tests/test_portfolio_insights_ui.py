"""Run the shipped JavaScript controller, including stale reads and uncertain local saves."""
from __future__ import annotations

import shutil
import subprocess
import unittest

from mark_api.dashboard import _DASHBOARD_JS
from mark_api.portfolio_insights_ui import INSIGHTS_JS


class PortfolioInsightsUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node required for shipped JavaScript")
    def test_local_only_controls_epoch_preview_identity_and_uncertain_save(self):
        harness = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const source = require("node:fs").readFileSync(0, "utf8");
class Element {
  constructor() { this.value=""; this.textContent=""; this.disabled=false; this.hidden=false; this.children=[]; this.listeners={}; }
  addEventListener(type, handler) { this.listeners[type]=handler; }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children=children; }
  scrollIntoView() {}
}
const elements = new Map();
const byId = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
const sent=[];
let ready=true, fetchImpl=async()=>{throw Error("unexpected fetch")}, key=0;
const context={console,URLSearchParams,Number,document:{createElement:()=>new Element()},
  byId,optionalById:byId,localUiReady:()=>ready,_jobHeaders:()=>({"X-Mark-Dashboard-Token":"local-token"}),
  _newJobRequestKey:()=>String(++key).padStart(64,"0"),
  fetch:async(path,options)=>{sent.push({path,options});return fetchImpl(path,options);}};
vm.createContext(context);
vm.runInContext("let jobsAvailable=true;\n"+source,context);
const run = expression => vm.runInContext(expression,context);
const success = value => ({ok:true,status:200,json:async()=>value});
const detail = (sku,revision=0) => ({sku,local_price_eur:50,rule:revision ? {
  revision,minimum_price_eur:40,reduction_step_eur:20,wait_days:0,reason:"Lokal",eligible_at:"2026-10-28T00:00:00Z"} : null});
(async()=>{
  // Local capability permits local work, without any platform Write capability.
  run("renderPortfolioInsights()");
  assert.equal(byId("price-rules-panel").hidden,false);
  ready=false;run("renderPortfolioInsights()");
  assert.equal(byId("price-rules-panel").hidden,true);
  assert.equal(await run("loadPriceRule('SKU-1')"),false);
  assert.equal(sent.length,0);
  ready=true;
  fetchImpl=async()=>success(detail("SKU-1"));
  assert.equal(await run("loadPriceRule('SKU-1')"),true);
  byId("price-rule-min").value="40"; byId("price-rule-step").value="20"; byId("price-rule-wait").value="7";
  run("previewPriceRule()");
  assert.match(byId("price-rule-preview").textContent,/50 € → 40 €/);
  assert.match(byId("price-rule-preview").textContent,/7 Tage nach Speichern/);
  assert.equal(byId("price-rule-save").disabled,false);
  byId("price-rule-min").value="51";run("previewPriceRule()");
  assert.equal(byId("price-rule-save").disabled,true);
  byId("price-rule-min").value="40";
  // A delayed result for one SKU cannot be displayed or saved as another SKU.
  let finishOld;
  fetchImpl=()=>new Promise(resolve=>{finishOld=resolve;});
  const delayed=run("loadPriceRule('SKU-1')");
  byId("price-rule-sku").value="SKU-2";
  byId("price-rule-sku").listeners.input();
  finishOld(success(detail("SKU-1",1)));
  assert.equal(await delayed,false);
  assert.equal(run("priceRuleTarget"),null);
  assert.equal(byId("price-rule-save").disabled,true);
  // Correct identity/revision, but an uncertain result must require rereading.
  fetchImpl=async()=>success(detail("SKU-2",3));
  assert.equal(await run("loadPriceRule('SKU-2')"),true);
  let finishSave;
  fetchImpl=()=>new Promise(resolve=>{finishSave=resolve;});
  const saving=run("mutatePriceRule('set')");
  const payload=JSON.parse(sent.at(-1).options.body);
  assert.equal(payload.sku,"SKU-2");assert.equal(payload.expected_revision,3);
  assert.equal(payload.minimum_price_eur,40);
  assert.equal(byId("price-rule-save").disabled,true);
  const before=sent.length;
  assert.equal(await run("mutatePriceRule('set')"),false);
  assert.equal(sent.length,before);
  finishSave({ok:false,status:503});
  assert.equal(await saving,false);
  assert.equal(run("priceRuleTarget"),null);
  assert.equal(await run("mutatePriceRule('set')"),false);
  assert.equal(sent.length,before);
  assert.match(byId("price-rule-status").textContent,/nicht blind wiederholen/);
  // Inventory pages remain pinned; a drift error discards the stale comparison.
  fetchImpl=async()=>success({items:[{ad_id:"10001",state:"changed",reason:"Text",
    previous:{title:"Alt",state:"active"},current:{title:"<img onerror=bad()>",state:"active",source:"owner",observed_at:"now"}}],
    state:"ready",epoch:"a".repeat(64),has_more:true,next_after:"10001",total:2,confirmed_at:"now",source:"owner"});
  assert.equal(await run("loadInventoryDiff()"),true);
  const row=byId("inventory-diff-body").children[0];
  assert.match(row.children[3].textContent,/<img onerror=bad\(\)>/);
  fetchImpl=async(path)=>{ assert.match(path,/after=10001/);assert.match(path,/epoch=a{64}/);return {ok:false,status:409};};
  assert.equal(await run("loadInventoryDiff(true)"),false);
  assert.equal(byId("inventory-diff-body").children.length,0);
  assert.equal(byId("inventory-diff-next").disabled,true);
  assert.equal(run("inventoryDiffCursor"),null);
  // A filter operates across all ad IDs, not just the first displayed page.
  byId("inventory-diff-filter").value="changed";
  fetchImpl=async(path)=>{
    assert.match(path,/state=changed/);
    return success({items:[{ad_id:"00030",state:"changed",reason:"Titel",
      previous:{title:"Alt",state:"active"},current:{title:"Neu",state:"active",source:"owner",observed_at:"now"}}],
      state:"ready",state_filter:"changed",epoch:"b".repeat(64),has_more:false,
      next_after:null,total:null,confirmed_at:"now",source:"owner"});
  };
  assert.equal(await byId("inventory-diff-filter").listeners.change(),true);
  assert.equal(byId("inventory-diff-body").children.length,1);
  assert.equal(byId("inventory-diff-body").children[0].children[0].textContent,"00030");
  assert.match(byId("inventory-diff-status").textContent,/global gefiltert/);
  // Changing a filter while a read is in flight automatically loads the new
  // selection. Neither an old response nor a second manual click is acceptable.
  let finishFiltered, filteredRequests=0;
  fetchImpl=path=>{
    filteredRequests+=1;
    if (filteredRequests===1) return new Promise(resolve=>{finishFiltered=resolve;});
    assert.match(path,/state=newly_observed/);
    return success({items:[{ad_id:"00031",state:"newly_observed",reason:"Neuer Eintrag",
      previous:{title:"Alt",state:"active"},current:null}],
      state:"ready",state_filter:"newly_observed",epoch:"d".repeat(64),
      has_more:false,next_after:null,total:null,confirmed_at:"now",source:"owner"});
  };
  const oldFilterLoad=run("loadInventoryDiff()");
  byId("inventory-diff-filter").value="unclear";
  assert.equal(byId("inventory-diff-filter").listeners.change(),false);
  byId("inventory-diff-filter").value="newly_observed";
  assert.equal(byId("inventory-diff-filter").listeners.change(),false);
  finishFiltered(success({items:[{ad_id:"STALE"}],state_filter:"changed",
    epoch:"c".repeat(64),has_more:false,next_after:null}));
  assert.equal(await oldFilterLoad,false);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(filteredRequests,2);
  assert.equal(byId("inventory-diff-body").children.length,1);
  assert.equal(byId("inventory-diff-body").children[0].children[0].textContent,"00031");
  assert.equal(run("inventoryDiffCursor"),null);
  // Autopilot: local read-only attention queue; never infer live inbox/sales.
  fetchImpl=async path=>{
    assert.match(path,/^\/api\/portfolio\/autopilot-attention\?/);
    if (path.includes("offset=0")) return success({
      items:[{kind:"ai_uncertain",priority:0,ad_id:"10001",
              reference:"a".repeat(32),evidence:"unknown"},
             {kind:"eml_unreviewed",priority:1,ad_id:"<img onerror=bad()>",
              reference:"chat_10001",evidence:"17"}],
      offset:0,has_more:true,next_offset:50,total:51,
      counts:{ai_uncertain:1,eml_unreviewed:1},platform_writes:0,
      provider_requests:0,platform_messages_read:false,
      automatic_execution_authorized:false
    });
    assert.match(path,/offset=50/);
    return success({
      items:[{kind:"manual_overdue",priority:2,ad_id:"10002",
              reference:"10002",evidence:"2026-10-25"}],
      offset:50,has_more:false,next_offset:null,total:51,
      counts:{ai_uncertain:1,eml_unreviewed:1},platform_writes:0,
      provider_requests:0,platform_messages_read:false,
      automatic_execution_authorized:false
    });
  };
  assert.equal(await byId("autopilot-load").listeners.click(),true);
  assert.equal(byId("autopilot-body").children.length,2);
  assert.match(byId("autopilot-body").children[1].children[1].textContent,/<img onerror=bad\(\)>/);
  assert.match(byId("autopilot-status").textContent,/51 lokale Prüfpunkte/);
  assert.equal(byId("autopilot-next").disabled,false);
  assert.equal(await byId("autopilot-next").listeners.click(),true);
  assert.equal(byId("autopilot-next").disabled,true);
  assert.equal(byId("autopilot-body").children.length,1);
  assert.match(byId("autopilot-status").textContent,/Seite 2/);
  fetchImpl=async()=>({ok:false,status:503});
  assert.equal(await run("loadAutopilotAttention()"),false);
  assert.equal(byId("autopilot-body").children.length,0);
  assert.equal(run("autopilotNextOffset"),null);
  assert.equal(byId("autopilot-next").disabled,true);
  assert.ok(sent.every(call=>call.path.startsWith("/api/portfolio/")));
  assert.ok(sent.every(call=>!call.path.includes("/api/write")));
  console.log("LOCAL_INSIGHTS_IDENTITY_EPOCH_NO_AUTOWRITE_PASS");
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        self.assertIn(INSIGHTS_JS, _DASHBOARD_JS)
        syntax = subprocess.run([shutil.which("node"), "--check", "-"],
                                input=_DASHBOARD_JS, text=True, encoding="utf-8",
                                capture_output=True, timeout=15)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        result = subprocess.run([shutil.which("node"), "-e", harness],
                                input=INSIGHTS_JS, text=True, encoding="utf-8",
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LOCAL_INSIGHTS_IDENTITY_EPOCH_NO_AUTOWRITE_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()