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
  setAttribute(name,value) { this[name]=value; }
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
vm.runInContext("let jobsAvailable=true; let localWorkAvailable=true; let writeUiAvailable=false; let existingAiActiveId='', existingAiDisplayedId='', existingAiBusy=false, existingAiBatchBusy=false, conversationBusy=false, followupBusy=false, catalogLoading=false, catalogBulkBusy=false, catalogRows=[], catalogGeneration=0, catalogPageIndex=0, catalogCursors=[''], catalogNextAfter=null, catalogFilters={search:'',status:'all'};\n"+source,context);
const run = expression => vm.runInContext(expression,context);
const success = value => ({ok:true,status:200,json:async()=>value});
const detail = (sku,revision=0) => ({sku,local_price_eur:50,rule:revision ? {
  revision,minimum_price_eur:40,reduction_step_eur:20,wait_days:0,reason:"Lokal",eligible_at:"2026-10-28T00:00:00Z"} : null});
(async()=>{
  // Local capability permits local work, without any platform Write capability.
  run("renderPortfolioInsights()");
  assert.equal(byId("price-rules-panel").hidden,false);
  assert.equal(byId("autopilot-panel").hidden,false);
  assert.equal(byId("nav-autopilot").hidden,false);
  // The unrelated jobs-list health probe may fail: local panels must remain usable.
  run("jobsAvailable=false; renderPortfolioInsights()");
  assert.equal(byId("price-rules-panel").hidden,false);
  assert.equal(byId("inventory-diff-panel").hidden,false);
  assert.equal(byId("autopilot-panel").hidden,false);
  // A real local price-rule GET still works even when job listing failed.
  fetchImpl=async()=>success(detail("SKU-local-only"));
  assert.equal(await run("loadPriceRule('SKU-local-only')"),true);
  assert.match(sent.at(-1).path,/price-rules\/detail\?sku=SKU-local-only/);
  sent.length=0;
  run("localWorkAvailable=false; renderPortfolioInsights()");
  assert.equal(byId("price-rules-panel").hidden,true);
  assert.equal(byId("inventory-diff-panel").hidden,true);
  assert.equal(await run("loadPriceRule('SKU-disabled')"),false);
  assert.equal(sent.length,0);
  // A write-enabled product session may have no separate local session token.
  // It must still reach local price/portfolio panels despite a failed jobs probe.
  run("writeUiAvailable=true; jobsAvailable=false; renderPortfolioInsights()");
  assert.equal(byId("price-rules-panel").hidden,false);
  assert.equal(byId("inventory-diff-panel").hidden,false);
  assert.equal(byId("autopilot-panel").hidden,false);
  fetchImpl=async()=>success(detail("SKU-write-only"));
  assert.equal(await run("loadPriceRule('SKU-write-only')"),true);
  assert.equal(sent.at(-1).path.includes("price-rules/detail?sku=SKU-write-only"),true);
  run("writeUiAvailable=false; renderPortfolioInsights()");
  assert.equal(byId("price-rules-panel").hidden,true);
  assert.equal(byId("inventory-diff-panel").hidden,true);
  sent.length=0;
  run("localWorkAvailable=true; jobsAvailable=true; renderPortfolioInsights()");
  ready=false;run("renderPortfolioInsights()");
  assert.equal(byId("price-rules-panel").hidden,true);
  assert.equal(byId("autopilot-panel").hidden,true);
  assert.equal(byId("nav-autopilot").hidden,true);
  assert.equal(await run("loadPriceRule('SKU-1')"),false);
  assert.equal(sent.length,0);
  ready=true;run("renderPortfolioInsights()");
  assert.equal(byId("autopilot-panel").hidden,false);
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
  // A failed first load must never be presented as a fresh comparison.
  const inventoryStart=sent.length;
  fetchImpl=async()=>({ok:false,status:503});
  assert.equal(await run("loadInventoryDiff()"),false);
  assert.equal(byId("inventory-diff-body").children.length,0);
  assert.match(byId("inventory-diff-status").textContent,/Kein aktueller Vergleich geladen.*neu laden/);
  assert.equal(byId("inventory-diff-load").disabled,false);
  // Inventory pages remain pinned; failed reads preserve the source evidence.
  const inventoryPage={items:[{ad_id:"10001",state:"changed",reason:"Text",
    previous:{title:"Alt",state:"active"},current:{title:"<img onerror=bad()>",state:"active",source:"owner",observed_at:"now"}}],
    state:"ready",epoch:"a".repeat(64),has_more:true,next_after:"10001",total:2,confirmed_at:"now",source:"owner"};
  fetchImpl=async()=>success(inventoryPage);
  assert.equal(await run("loadInventoryDiff()"),true);
  const row=byId("inventory-diff-body").children[0];
  assert.match(row.children[3].textContent,/<img onerror=bad\(\)>/);
  fetchImpl=async(path)=>{ assert.match(path,/after=10001/);assert.match(path,/epoch=a{64}/);return {ok:false,status:409};};
  assert.equal(await run("loadInventoryDiff(true)"),false);
  assert.equal(byId("inventory-diff-body").children.length,1);
  assert.equal(byId("inventory-diff-body").children[0],row);
  assert.match(byId("inventory-diff-status").textContent,/möglicherweise veraltet.*neu laden/);
  assert.equal(byId("inventory-diff-next").disabled,true);
  assert.equal(run("inventoryDiffCursor"),null);
  assert.equal(run("inventoryDiffEpoch"),null);
  // Reload failures, JSON failures, and a bad later row must be atomic too.
  const failures=[
    ()=>({ok:false,status:503}),
    ()=>{throw Error("offline");},
    ()=>({ok:true,json:async()=>{throw SyntaxError("bad JSON");}}),
    ()=>success({...inventoryPage,items:[inventoryPage.items[0],null]}),
    ()=>success({...inventoryPage,items:[inventoryPage.items[0],{...inventoryPage.items[0],current:{}}]}),
    ()=>success({...inventoryPage,has_more:true,next_after:{bad:true}}),
    ()=>success({...inventoryPage,epoch:"invalid"}),
    ()=>success({...inventoryPage,has_more:"yes"}),
  ];
  for (const [index,failure] of failures.entries()) {
    fetchImpl=async()=>success(inventoryPage);
    assert.equal(await run("loadInventoryDiff()"),true);
    const preserved=byId("inventory-diff-body").children[0];
    fetchImpl=async()=>failure();
    assert.equal(await run(index===0 ? "loadInventoryDiff(true)" : "loadInventoryDiff()"),false);
    assert.equal(byId("inventory-diff-body").children.length,1);
    assert.equal(byId("inventory-diff-body").children[0],preserved);
    assert.equal(run("inventoryDiffCursor"),null);
    assert.equal(run("inventoryDiffEpoch"),null);
    assert.equal(byId("inventory-diff-next").disabled,true);
    assert.equal(byId("inventory-diff-load").disabled,false);
    assert.equal(byId("inventory-diff-filter").disabled,false);
    assert.match(byId("inventory-diff-status").textContent,/möglicherweise veraltet.*neu laden/);
    assert.doesNotMatch(byId("inventory-diff-status").textContent,/verkauft|gelöscht|Nicht mehr im Bestand/i);
    const requestCount=sent.length;
    assert.equal(await run("loadInventoryDiff(true)"),false);
    assert.equal(sent.length,requestCount);
  }
  assert.ok(sent.slice(inventoryStart).every(call=>
    call.path.startsWith("/api/portfolio/inventory-diff?") && !call.options.method && !call.options.body));
  // An absent observation is not evidence of sale or deletion.
  fetchImpl=async()=>success({...inventoryPage,items:[{...inventoryPage.items[0],state:"unclear",
    current:{...inventoryPage.items[0].current,state:"absent"}}]});
  assert.equal(await run("loadInventoryDiff()"),true);
  assert.match(byId("inventory-diff-body").children[0].children[1].textContent,/kein Verkaufs- oder Löschbeleg/);
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
  // A change row must open the exact local listing without searching 50-hit
  // substring pages or triggering platform operations.
  const comparisonRow=byId("inventory-diff-body").children[0];
  const compareButton=comparisonRow.children[5].children[0];
  assert.equal(compareButton.textContent,"Anzeige prüfen");
  const compareSelections=[];
  context.getJson=async path=>{
    assert.equal(path,"/api/catalog/ad?id=00031");
    return {ad:{ad_id:"00031",title:"Exact local owner listing"}};
  };
  context.renderAds=ads=>compareSelections.push(ads);
  context.renderFollowupChoices=()=>{};
  context.renderSaleChoices=()=>{};
  context.renderCatalogNavigation=()=>{};
  assert.equal(await compareButton.listeners.click(),true);
  assert.equal(compareSelections.at(-1)[0].ad_id,"00031");
  assert.equal(byId("catalog-search").value,"00031");
  assert.match(byId("catalog-feedback").textContent,/Bestandsänderung nur anhand lokal/);
  context.getJson=async path=>{
    assert.equal(path,"/api/catalog/ad?id=00031");
    return {ad:{ad_id:"000310",title:"Wrong target"}};
  };
  assert.equal(await compareButton.listeners.click(),false);
  assert.equal(compareSelections.length,1);
  assert.match(byId("inventory-diff-status").textContent,/nicht eindeutig lokal bestätigt/);
  // A request resolving after the catalog changed cannot overwrite selection.
  let resolveCompare;
  context.getJson=()=>new Promise(resolve=>{resolveCompare=resolve;});
  const staleCompare=compareButton.listeners.click();
  run("catalogGeneration++");
  resolveCompare({ad:{ad_id:"00031"}});
  assert.equal(await staleCompare,false);
  assert.equal(compareSelections.length,1);
  assert.equal(await run("openInventoryDiffAd('INVALID')"),false);
  assert.equal(compareSelections.length,1);
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
  assert.equal(await byId("nav-autopilot").listeners.click(),true);
  assert.equal(byId("autopilot-body").children.length,2);
  // Radar task buttons must navigate to the exact existing local job, never call a provider.
  const selectedAi=[];
  const selectedFollowups=[];
  context.loadExistingAiDetails=async(reset,adId)=>{selectedAi.push([run("existingAiActiveId"),adId,reset]);run("existingAiDisplayedId=existingAiActiveId");return true;};
  context.selectFollowupAd=id=>selectedFollowups.push(id);
  let loadedConversations=0;
  context.loadConversations=async()=>{loadedConversations++;return true;};
  const firstTask=byId("autopilot-body").children[0];
  assert.equal(firstTask.children.length,5);
  assert.equal(await firstTask.children[4].children[0].listeners.click(),true);
  assert.equal(byId("existing-ai-disclosure").open,true);
  assert.equal(byId("existing-ai-choice").value,"a".repeat(32));
  assert.deepEqual(selectedAi[0],["a".repeat(32),"10001",true]);
  assert.equal(run("autopilotFocusedAdId"),"10001");
  assert.equal(await run("openAutopilotTask({kind:'ai_proposal',ad_id:'item-09999-alnum',reference:'a'.repeat(32)})"),true);
  assert.deepEqual(selectedAi.at(-1),["a".repeat(32),"item-09999-alnum",true]);
  const beforeInvalid=selectedAi.length;
  assert.equal(await run("openAutopilotTask({kind:'ai_uncertain',ad_id:'',reference:'a'.repeat(32)})"),false);
  assert.equal(selectedAi.length,beforeInvalid);
  assert.equal(run("autopilotFocusedAdId"),"");
  assert.equal(byId("autopilot-body").children[1].children[4].children.length,0,
    "malformed IDs must never become navigation actions");
  assert.equal(await run("openAutopilotTask({kind:'eml_unreviewed',ad_id:'10001',reference:'chat_10001'})"),true);
  assert.equal(byId("buyer-disclosure").open,true);
  assert.equal(byId("buyer-conversations-filter").value,"new");
  assert.match(byId("buyer-conversations-status").textContent,/chat_10001/);
  assert.equal(loadedConversations,1);
  // Existing EML fetch for another filter must finish under its own filter.
  // Opening a radar hint cannot retag the in-flight page and pagination.
  byId("buyer-conversations-filter").value="all";
  run("conversationBusy=true");
  assert.equal(await run("openAutopilotTask({kind:'eml_unreviewed',ad_id:'10001',reference:'chat_10001'})"),false);
  assert.equal(byId("buyer-conversations-filter").value,"all");
  assert.equal(loadedConversations,1);
  run("conversationBusy=false");
  assert.equal(await run("openAutopilotTask({kind:'eml_unreviewed',ad_id:'10001',reference:'chat_10001'})"),true);
  assert.equal(byId("buyer-conversations-filter").value,"new");
  assert.equal(loadedConversations,2);
  assert.equal(await byId("autopilot-load").listeners.click(),true);
  assert.equal(byId("autopilot-body").children.length,2);
  assert.match(byId("autopilot-body").children[1].children[1].textContent,/<img onerror=bad\(\)>/);
  assert.match(byId("autopilot-status").textContent,/51 lokale Prüfpunkte/);
  assert.equal(byId("autopilot-next").disabled,false);
  assert.equal(await byId("autopilot-next").listeners.click(),true);
  assert.equal(byId("autopilot-next").disabled,true);
  assert.equal(byId("autopilot-body").children.length,1);
  assert.match(byId("autopilot-status").textContent,/Seite 2/);
  assert.equal(await byId("autopilot-body").children[0].children[4].children[0].listeners.click(),true);
  assert.equal(byId("followup-pane").open,true);
  assert.deepEqual(selectedFollowups,["10002"]);
  // A concurrent local reminder mutation must never show a different
  // selected ad while falsely reporting navigation to the requested ad.
  run("followupBusy=true");
  byId("followup-pane").open=false;
  assert.equal(await run("openAutopilotTask({kind:'manual_due_soon',ad_id:'10003'})"),false);
  assert.deepEqual(selectedFollowups,["10002"]);
  assert.equal(byId("followup-pane").open,false);
  run("followupBusy=false");
  assert.equal(await run("openAutopilotTask({kind:'manual_due_soon',ad_id:'10003'})"),true);
  assert.deepEqual(selectedFollowups,["10002","10003"]);
  // A 14-day view stagnation hint navigates to the exact catalog item,
  // without a provider call, external write or a false "opened" response.
  fetchImpl=async()=>success({
    items:[{kind:"views_stagnant",priority:5,ad_id:"10077",
            reference:"2026-10-13 bis 2026-10-29",evidence:"Aufrufe 11 → 11"}],
    offset:0,has_more:false,next_offset:null,total:1,
    counts:{views_stagnant:1},platform_writes:0,provider_requests:0,
    platform_messages_read:false,automatic_execution_authorized:false
  });
  assert.equal(await run("loadAutopilotAttention()"),true);
  assert.match(byId("autopilot-status").textContent,/1 Anzeigen mit 14 Tagen/);
  assert.match(byId("autopilot-body").children[0].children[2].textContent,/14 Tage/);
  assert.equal(byId("autopilot-body").children[0].children[4].children[0].textContent,
    "Anzeige im Katalog prüfen");
  // 80 earlier list matches would exclude this exact ID from the first 50
  // substring hits. Only the indexed GET /api/catalog/ad?id= may be used.
  let exactReads=0;
  const rendered=[];
  context.searchCatalog=async()=>{throw Error("substring search forbidden here");};
  context.getJson=async path=>{
    exactReads++;
    assert.equal(path,"/api/catalog/ad?id=10077");
    return {ad:{ad_id:"10077",title:"Owner fixture from indexed lookup"}};
  };
  context.renderAds=ads=>rendered.push(ads);
  context.renderFollowupChoices=()=>{};
  context.renderSaleChoices=()=>{};
  context.renderCatalogNavigation=()=>{byId("catalog-page-label").textContent="Seite 1";};
  assert.equal(await byId("autopilot-body").children[0].children[4].children[0].listeners.click(),true);
  assert.equal(exactReads,1);
  assert.equal(rendered.length,1);
  assert.equal(rendered[0].length,1);
  assert.equal(rendered[0][0].ad_id,"10077");
  assert.equal(run("catalogRows[0].ad_id"),"10077");
  assert.equal(byId("catalog-page-label").textContent,"Einzelanzeige");
  assert.equal(byId("catalog-search").value,"10077");
  assert.match(byId("catalog-feedback").textContent,/Anzeige 10077 über exakte ID/);
  // Busy and stale current catalogue state must not be overwritten by a
  // read that returns after the user has changed catalogue generation.
  run("catalogBulkBusy=true");
  assert.equal(await run("openAutopilotTask({kind:'views_stagnant',ad_id:'10077'})"),false);
  assert.equal(exactReads,1);
  run("catalogBulkBusy=false");
  let resolveOldExact;
  context.getJson=()=>new Promise(resolve=>{resolveOldExact=resolve;});
  const oldExact=run("openAutopilotTask({kind:'views_stagnant',ad_id:'10077'})");
  run("catalogGeneration++");
  resolveOldExact({ad:{ad_id:"10077"}});
  assert.equal(await oldExact,false);
  assert.equal(rendered.length,1);
  context.getJson=async path=>{
    exactReads++;
    assert.equal(path,"/api/catalog/ad?id=10077");
    return {ad:{ad_id:"100770",title:"Wrong target"}};
  };
  assert.equal(await run("openAutopilotTask({kind:'views_stagnant',ad_id:'10077'})"),false);
  assert.match(byId("autopilot-status").textContent,/nicht eindeutig lokal bestätigt/);
  assert.equal(rendered.length,1);
  assert.equal(await run("openAutopilotTask({kind:'views_stagnant',ad_id:'invalid'})"),false);
  assert.equal(rendered.length,1);
  // A recently confirmed owner ad with no locally captured original text is
  // NOT proof that Kleinanzeigen lacks a description. Use the existing exact
  // local catalog GET and never trigger AI/provider or platform actions.
  fetchImpl=async path=>{
    assert.ok(path.startsWith("/api/portfolio/autopilot-attention?"));
    return success({
      items:[{kind:"owner_original_unread",priority:6,ad_id:"90077",
              reference:"2026-11-04",evidence:"Originaltext bislang lokal nicht belegt"}],
      offset:0,has_more:false,next_offset:null,total:1,
      counts:{owner_original_unread:1,views_stagnant:0},
      platform_writes:0,provider_requests:0,platform_messages_read:false,
      automatic_execution_authorized:false,
    });
  };
  assert.equal(await run("loadAutopilotAttention()"),true);
  assert.match(byId("autopilot-status").textContent,/1 eigenen Anzeigen mit lokal noch nicht erfasstem Originaltext/);
  const uncheckedRow=byId("autopilot-body").children[0];
  assert.match(uncheckedRow.children[2].textContent,/lokal noch nicht erfasst/);
  assert.equal(uncheckedRow.children[4].children[0].textContent,"Anzeige im Katalog prüfen");
  let unreadReads=0;
  context.getJson=async path=>{
    unreadReads++;
    assert.equal(path,"/api/catalog/ad?id=90077");
    return {ad:{ad_id:"90077",title:"Synthetische Eigenanzeige"}};
  };
  assert.equal(await uncheckedRow.children[4].children[0].listeners.click(),true);
  assert.equal(unreadReads,1);
  assert.equal(rendered.length,2);
  assert.equal(rendered.at(-1)[0].ad_id,"90077");
  assert.match(byId("catalog-feedback").textContent,/Originalbeschreibung bislang lokal nicht bestätigt/);
  assert.doesNotMatch(byId("catalog-feedback").textContent,/Aufrufzähler über zwei/);
  context.getJson=async path=>{
    assert.equal(path,"/api/catalog/ad?id=90077");
    return {ad:{ad_id:"900770",title:"Unpassende Anzeige"}};
  };
  assert.equal(await run("openAutopilotTask({kind:'owner_original_unread',ad_id:'90077'})"),false);
  assert.equal(rendered.length,2);
  assert.match(byId("autopilot-status").textContent,/nicht eindeutig lokal bestätigt/);
  ready=false;
  assert.equal(await run("openAutopilotTask({kind:'manual_overdue',ad_id:'10002'})"),false);
  assert.equal(selectedFollowups.length,2);
  ready=true;
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
