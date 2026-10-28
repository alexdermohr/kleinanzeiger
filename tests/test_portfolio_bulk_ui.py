"""Safety regression for confirmed multi-ad pause/activate/delete UI actions.

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
            'id="catalog-bulk-delete"', 'id="catalog-bulk-stop"', 'id="catalog-bulk-progress"',
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
  querySelectorAll(selector) {
    if(selector==="[data-write-control]")return [
      this.getElementById("manage-pause"),this.getElementById("create-submit")];
    return [];
  }
};
let confirmCount=0, promptCount=0, promptReply=null, challengeText="";
let preview="", allowConfirm=true, writes=[], reads=[];
const window={
  addEventListener(){},
  confirm(msg) {assert.equal(writes.length,0,"no write may precede confirmation");
    confirmCount++;preview=msg;return allowConfirm;},
  prompt(msg) {assert.equal(writes.length,0,"no write before typed consent");
    promptCount++;challengeText=msg;return promptReply;}
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
  assert.ok(path.startsWith("/api/catalog/ad?id="),path);
  const qs=new URL(path,"http://127.0.0.1").searchParams;
  reads.push(path);
  return {ad:inventory.find(a=>a.ad_id===qs.get("id"))??null};
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
  writes=[];reads=[];confirmCount=0;promptCount=0;promptReply=null;
  challengeText="";preview="";allowConfirm=true;
  reply=()=>({response:{status:200},payload:{operation_receipt:{outcome:"confirmed"}}});
  window.write=async(scope,method,path,payload,opts)=>{
    assert.equal(confirmCount,1);
    assert.equal(method,"POST");
    assert.equal(payload,null);
    assert.equal(scope,"ad:"+opts.adId+":"+path.split("/").pop());
    assert.equal(elements.get("manage-pause").disabled,true,
      "other write controls must be disabled throughout the bulk job");
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
  assert.equal(elements.get("manage-pause").disabled,false,
    "parallel write controls must be re-enabled after a settled batch");

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
    const id=new URL(path,"http://127.0.0.1").searchParams.get("id");
    return {ad:inventory.find(a=>a.ad_id===id)??null};
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

  // G6 deletion uses only the existing exact-ID DELETE Write API, after an
  // ordinary preview confirm AND a typed, count-bound destructive challenge.
  prepare([ad("123"),ad("456","paused")]);
  promptReply="LÖSCHEN 2";
  window.write=async(scope,method,path,payload,opts)=>{
    assert.equal(confirmCount,1);
    assert.equal(promptCount,1);
    assert.equal(method,"DELETE");
    assert.equal(payload,null);
    assert.equal(scope,"ad:"+opts.adId+":delete");
    assert.equal(path,"/api/write/ads/"+opts.adId);
    writes.push(path);
    return reply(opts.adId);
  };
  await run("delete");
  assert.equal(confirmCount,1);
  assert.equal(promptCount,1);
  assert.ok(challengeText.includes("LÖSCHEN 2"));
  assert.ok(preview.includes("123 · Anzeige 123 (aktiv -> gelöscht)"));
  assert.ok(preview.includes("456 · Anzeige 456 (pausiert -> gelöscht)"));
  assert.equal(writes.length,2);
  assert.deepEqual(writes,["/api/write/ads/123","/api/write/ads/456"]);
  assert.equal(reads.length,2);
  assert.match(progress(),/2 von 2/);
  assert.match(progress(),/IDs: 123, 456/);
  assert.equal(vm.runInContext("catalogSelectedIds.size",context),0);

  prepare([ad("789","active","Trick\n999999 · gelöscht\u202e")]);
  promptReply="LÖSCHEN 1";
  window.write=async(scope,method,path,payload,opts)=>{
    assert.equal(method,"DELETE");
    assert.equal(opts.adId,"789");
    writes.push(path);
    return reply(opts.adId);
  };
  await run("delete");
  assert.deepEqual(writes,["/api/write/ads/789"]);
  assert.ok(preview.includes("Trick 999999 · gelöscht"));
  assert.ok(!preview.includes("\n999999"),"untrusted title may not spoof an extra ID line");
  assert.ok(!preview.includes("\u202e"),"untrusted title may not reorder visible IDs");

  // Missing title is a real owner observation, not the literal name "Ohne Titel".
  // Exercise the actual catalog checkbox path rather than a test-only map.
  prepare([]);
  window.fixture=[ad("790","paused",null)];
  inventory=window.fixture;
  vm.runInContext('const nullLabel=catalogCheckbox(window.fixture[0]); const nullCheck=nullLabel.children[0]; nullCheck.checked=true; nullCheck.listeners.change()',context);
  assert.equal(vm.runInContext('catalogSelectedAds.get("790").title',context),null);
  promptReply="LÖSCHEN 1";
  window.write=async(scope,method,path,payload,opts)=>{
    assert.equal(method,"DELETE");assert.equal(opts.adId,"790");writes.push(path);
    return reply(opts.adId);
  };
  await run("delete");
  assert.deepEqual(writes,["/api/write/ads/790"]);
  assert.ok(preview.includes("790 · Ohne Titel (pausiert -> gelöscht)"));

  prepare([ad("791","active",null)]);
  inventory=[ad("791","active","Ohne Titel")]; // different original observation
  promptReply="LÖSCHEN 1";
  await run("delete");
  assert.equal(writes.length,0,"null->literal title drift must block before DELETE");
  assert.match(progress(),/verändert/);

  prepare([ad("123")]);
  promptReply="LÖSCHEN"; // missing the exact bound count
  await run("delete");
  assert.equal(confirmCount,1);
  assert.equal(promptCount,1);
  assert.equal(writes.length,0);
  assert.match(progress(),/abgebrochen/);

  prepare([ad("123")]);
  allowConfirm=false;
  await run("delete");
  assert.equal(confirmCount,1);
  assert.equal(promptCount,0);
  assert.equal(writes.length,0);

  prepare([ad("123","pending")]);
  promptReply="LÖSCHEN 1";
  await run("delete");
  assert.equal(confirmCount,0);
  assert.equal(promptCount,0);
  assert.equal(writes.length,0);

  prepare([ad("123"),ad("456")]);
  promptReply="LÖSCHEN 2";
  window.write=async(scope,method,path)=>{
    assert.equal(method,"DELETE");
    writes.push(path);
    return {response:{status:202},payload:{operation_receipt:{outcome:"ambiguous"}}};
  };
  await run("delete");
  assert.equal(writes.length,1,"unknown DELETE outcome must block later deletions");
  assert.match(progress(),/unklar/);
  assert.equal(vm.runInContext("catalogSelectedIds.size",context),2);

  prepare([ad("123"),ad("456")]);
  promptReply="LÖSCHEN 2";
  inventory=[ad("123","absent"),ad("456")];
  await run("delete");
  assert.equal(writes.length,0,"a changed owner listing must block deletion before the first write");
  assert.match(progress(),/verändert/);
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