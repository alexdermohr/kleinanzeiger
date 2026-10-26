"""Safety regression for confirmed multi-ad pause/activate UI actions.

No real session, account, network or platform operation is created.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest

from mark_api.dashboard import _DASHBOARD_HTML, _DASHBOARD_JS


class PortfolioBulkUiTests(unittest.TestCase):
    def test_visible_when_write_capability_is_present(self):
        for element in (
            'id="catalog-bulk-pause"', 'id="catalog-bulk-activate"',
            'id="catalog-bulk-stop"', 'id="catalog-bulk-progress"',
        ):
            self.assertIn(element, _DASHBOARD_HTML)
        self.assertIn('await verifyCatalogBulkAd(ad)', _DASHBOARD_JS)
        self.assertIn('if (!confirmedWriteResult(result))', _DASHBOARD_JS)
        self.assertIn('pendingWrites.size > 0', _DASHBOARD_JS)
        self.assertIn('window.confirm(', _DASHBOARD_JS)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for JS behavior")
    def test_bulk_confirmation_preview_gates_and_fail_closed(self):
        harness = r"""
const assert=require("node:assert/strict");
const vm=require("node:vm");
const fs=require("node:fs");
const script=fs.readFileSync(0,"utf8").replace(/\r\n/g,"\n");
const end=script.indexOf("\nconsumeWriteToken();\nload();");
assert.ok(end>0);
class Element {
  constructor(id="") {this.id=id;this.hidden=false;this.disabled=false;
    this.value="";this.textContent="";this.className="";this.children=[];
    this.listeners={};this.checked=false;this.title="";}
  addEventListener(k,fn){this.listeners[k]=fn;}
  replaceChildren(...children){this.children=[...children];}
  append(...children){this.children.push(...children);}
}
const elements=new Map();
const document={
  getElementById(id) {if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);},
  createElement(tag) {return new Element(tag);},
  querySelectorAll() {return [];}
};
let confirmCount=0, preview="", allowConfirm=true, writes=[], reads=[];
const window={
  addEventListener(){},
  confirm(msg) {assert.equal(writes.length,0,"no write may precede confirmation");
    confirmCount++;preview=msg;return allowConfirm;}
};
const context={document,window,console,URL,URLSearchParams,Date,fetch:()=>{throw Error("unexpected fetch")}};
vm.createContext(context);
vm.runInContext(script.slice(0,end),context);
vm.runInContext('writeUiAvailable=true;writeToken="t".repeat(32)',context);
const ad=(id,state="active",title="Anzeige "+id)=>({
  ad_id:id,title,present:true,lifecycle_state:state,
  observed_at:"2026-10-26T08:00:00+00:00",description:"Synthetic"
});
let inventory=[], reply=()=>({response:{status:200},payload:{operation_receipt:{outcome:"confirmed"}}});
window.read=(path)=>{
  assert.ok(path.startsWith("/api/catalog/ads?"),path);
  const qs=new URL(path,"http://127.0.0.1").searchParams;
  assert.equal(qs.get("status"),"all");
  assert.equal(qs.get("limit"),"100");
  reads.push(path);
  return {ads:inventory.filter(a=>a.ad_id===qs.get("q")),has_more:false,next_after:null};
};
window.write=async(scope,method,path,payload,opts)=>{
  assert.equal(confirmCount,1);
  assert.equal(method,"POST");
  assert.equal(payload,null);
  assert.equal(scope, "ad:"+opts.adId+":"+path.split("/").pop());
  assert.equal(path,"/api/write/ads/"+opts.adId+"/"+path.split("/").pop());
  writes.push(path);
  return reply(opts.adId);
};
vm.runInContext('getJson=async(path)=>window.read(path);runPlatformWrite=async(...args)=>window.write(...args)',context);
function prepare(ads) {
  inventory=ads;
  writes=[];reads=[];confirmCount=0;preview="";allowConfirm=true;
  reply=()=>({response:{status:200},payload:{operation_receipt:{outcome:"confirmed"}}});
  window.write=async(scope,method,path,payload,opts)=>{
    assert.equal(confirmCount,1);
    assert.equal(method,"POST");
    assert.equal(payload,null);
    assert.equal(scope,"ad:"+opts.adId+":"+path.split("/").pop());
    writes.push(path);
    return reply(opts.adId);
  };
  vm.runInContext('catalogSelectedIds.clear();catalogSelectedAds.clear();pendingWrites.clear();pendingRecoveryBlocked=false;writeToken="t".repeat(32);',context);
  window.fixture=ads;
  vm.runInContext('for (const a of window.fixture) {catalogSelectedIds.add(a.ad_id);catalogSelectedAds.set(a.ad_id,Object.freeze({...a}));} latestAdsById=new Map(window.fixture.map(a=>[a.ad_id,a]));renderCatalogSelection()',context);
}
const progress=()=>elements.get("catalog-bulk-progress").textContent;
const run=(action)=>vm.runInContext('runCatalogBulkAction('+JSON.stringify(action)+')',context);
(async()=>{
  prepare([ad("123"),ad("456")]);
  await run("pause");
  assert.equal(confirmCount,1);
  assert.equal(writes.length,2);
  assert.ok(preview.includes("123")&&preview.includes("456"));
  assert.ok(preview.includes("\n"),"confirmation must have separate lines");
  assert.equal(reads.length,2);
  assert.match(progress(),/2 von 2/);
  assert.equal(vm.runInContext("catalogSelectedIds.size",context),0);

  prepare([ad("123","paused"),ad("456","paused")]);
  await run("activate");
  assert.equal(writes.length,2);
  assert.ok(writes.every(p=>p.endsWith("/activate")));

  prepare([ad("123"),ad("456")]);
  allowConfirm=false;
  await run("pause");
  assert.equal(confirmCount,1);assert.equal(writes.length,0);assert.equal(reads.length,0);

  prepare([ad("123")]);
  vm.runInContext('writeToken=null',context);
  await run("pause");
  assert.equal(confirmCount,0);assert.equal(writes.length,0);

  prepare([ad("123")]);
  vm.runInContext('pendingWrites.set("pending",{scope:"pending"})',context);
  await run("pause");
  assert.equal(confirmCount,0);assert.equal(writes.length,0);

  prepare(Array.from({length:11},(_,i)=>ad(String(i+100))));
  await run("pause");
  assert.equal(confirmCount,0);assert.equal(writes.length,0);
  assert.match(progress(),/1 bis 10/);

  prepare([ad("123","paused")]);
  await run("pause");
  assert.equal(confirmCount,0);assert.equal(writes.length,0);

  prepare([ad("123"),ad("456")]);
  inventory=[ad("123"),ad("456","paused")];
  await run("pause");
  assert.equal(writes.length,1,"a changed ad may not be written");
  assert.match(progress(),/1 von 2/);
  assert.match(progress(),/verändert/);

  prepare([ad("123")]);
  window.read=()=>{throw Error("offline")};
  await run("pause");
  assert.equal(writes.length,0);
  assert.match(progress(),/offline/);
  window.read=(path)=>{
    reads.push(path);
    const q=new URL(path,"http://127.0.0.1").searchParams.get("q");
    return {ads:inventory.filter(a=>a.ad_id===q)};
  };

  prepare([ad("123"),ad("456")]);
  reply=()=>({response:{status:202},payload:{operation_receipt:{outcome:"ambiguous"}}});
  await run("pause");
  assert.equal(writes.length,1);
  assert.match(progress(),/unklar/);
  assert.equal(vm.runInContext("catalogSelectedIds.size",context),2);

  prepare([ad("123"),ad("456")]);
  window.write=async(scope,method,path)=>{
    writes.push(path);
    vm.runInContext('pendingWrites.set("unresolved",{scope:"unresolved"})',context);
    return {response:{status:200},payload:{operation_receipt:{outcome:"confirmed"}}};
  };
  await run("pause");
  assert.equal(writes.length,1,"pending ACK must stop all later writes");
  assert.match(progress(),/Recovery-ACK/);

  prepare([ad("123"),ad("456")]);
  window.write=async(scope,method,path)=>{
    writes.push(path);
    vm.runInContext("stopCatalogBulkAction()",context);
    return {response:{status:200},payload:{operation_receipt:{outcome:"confirmed"}}};
  };
  await run("pause");
  assert.equal(writes.length,1);
  assert.match(progress(),/gestoppt/);
  assert.equal(vm.runInContext("catalogBulkBusy",context),false);
  assert.equal(elements.get("catalog-bulk-stop").hidden,true);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result = subprocess.run(
            [shutil.which("node") or "node", "-e", harness],
            input=_DASHBOARD_JS, text=True, encoding="utf-8",
            capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
