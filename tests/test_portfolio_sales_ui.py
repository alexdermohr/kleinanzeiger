"""P3 native browser regression: one ad's save cannot unlock another ad."""
from __future__ import annotations

import shutil
import subprocess
import unittest

from mark_api.dashboard import _DASHBOARD_JS


class SalesSelectionUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node required for browser-state test")
    def test_async_save_does_not_reenable_unverified_other_listing(self) -> None:
        harness = r"""
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");

const source=fs.readFileSync(0,"utf8");
const start=source.indexOf("async function loadSelectedSale() {");
// P3 runs independently; downstream P4 adds reporting handlers before nav-drafts.
const draftEnd=source.indexOf("\nconst draftNavigation =",start);
const reportEnd=source.indexOf("\nasync function loadPortfolioReport() {",start);
const end=reportEnd>start && reportEnd<draftEnd ? reportEnd : draftEnd;
assert.ok(start>=0 && end>start,"the shipped sales functions must exist");
const elements=new Map();
class Element {
  constructor(tag=""){this.tag=tag;this.value="";this.textContent="";
    this.disabled=false;this.children=[];this.listeners={};}
  append(...parts){this.children.push(...parts);}
  replaceChildren(...parts){this.children=[...parts];}
}
const document={
  getElementById(id){if(!elements.has(id))elements.set(id,new Element(id));
    return elements.get(id);},
  createElement(tag){return new Element(tag);}
};
const byId=id=>document.getElementById(id);
const sent=[];
let settleA;
function fetch(path,options={}){
  assert.equal(path,"/api/portfolio/sales/update");
  const body=JSON.parse(options.body);
  sent.push(body);
  return new Promise(resolve=>{settleA=resolve;});
}
async function _jobGet(path){
  if(path.startsWith("/api/portfolio/sales/detail?"))
    throw new Error("synthetic B detail unavailable");
  if(path.startsWith("/api/portfolio/sales?"))
    return {sales:[],has_more:false,next_after:null,total:0};
  if(path==="/api/portfolio/sales/totals")
    return {sold:0,reserved:0,revenue_eur:0,known_cost_eur:0,
            known_profit_eur:0,known_profit_count:0,unknown_cost_count:0};
  throw new Error("unexpected local read "+path);
}
const context={document,byId,fetch,_jobGet,_jobHeaders:()=>({}),
  _newJobRequestKey:()=> "a".repeat(64),
  writeUiReady:()=>true,localUiReady:()=>true,URLSearchParams,
  window:{confirm:()=>true},console};
vm.createContext(context);
vm.runInContext(`
let salesAvailable=true,saleSaveBusy=false,saleLoadSeq=0;
let saleVerifiedId="",salePending=null;
let saleEntries=new Map([["123",{ad_id:"123",state:"reserved",
  revenue_eur:null,cost_eur:null,revision:2,location:"Regal A",note:"A data"}]]);
let salesCursors=[""],salesPage=0,salesNextAfter=null;
`+source.slice(start,end),context);

(async()=>{
  byId("sale-ad-select").value="123";
  vm.runInContext("fillSaleForm()",context);
  vm.runInContext('saleVerifiedId="123"',context);
  byId("sale-save").disabled=false;
  const oldPending=vm.runInContext("saveSaleState()",context);
  assert.equal(sent.length,1);
  assert.equal(sent[0].ad_id,"123");
  byId("sale-ad-select").value="456";
  await vm.runInContext("loadSelectedSale()",context);
  assert.equal(byId("sale-save").disabled,true,
    "unverified B must be locked before old A returns");
  settleA({ok:true,json:async()=>({ad_id:"123",platform_writes:0,revision:3})});
  await oldPending;
  assert.equal(byId("sale-save").disabled,true,
    "completion of A may not unlock B after B verification fails");
  vm.runInContext("saveSaleState()",context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(sent.length,1,"B must never be saved with A's stale values");
  console.log("SALE_SELECTION_LOCK_PASS");
})().catch(err=>{console.error(err);process.exitCode=1;});
"""
        run = subprocess.run(
            [shutil.which("node") or "node", "-e", harness],
            input=_DASHBOARD_JS, text=True, encoding="utf-8",
            capture_output=True, check=False, timeout=25,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("SALE_SELECTION_LOCK_PASS", run.stdout)


if __name__ == "__main__":
    unittest.main()