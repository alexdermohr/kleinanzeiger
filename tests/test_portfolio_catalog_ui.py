"""Browser-side portfolio pagination without loading or writing a real account."""
from __future__ import annotations

import shutil
import subprocess
import unittest

from mark_api.dashboard import _DASHBOARD_HTML, _DASHBOARD_JS


class PortfolioCatalogUiTests(unittest.TestCase):
    def test_search_select_and_page_controls_are_visible(self) -> None:
        for element in (
            'id="catalog-search"', 'id="catalog-status"', 'id="catalog-find"',
            'id="catalog-prev"', 'id="catalog-next"', 'id="catalog-clear"',
            'id="catalog-export"', 'id="catalog-selected-count"',
            'id="catalog-select-page"', 'id="catalog-deselect-page"',
        ):
            self.assertIn(element, _DASHBOARD_HTML)
        self.assertIn("getJson(\"/api/catalog/ads?\"", _DASHBOARD_JS)
        self.assertNotIn('getJson("/api/ads")', _DASHBOARD_JS)
        self.assertIn(
            'byId("catalog-select-page").addEventListener("click", () => changeCatalogVisiblePageSelection(true))',
            _DASHBOARD_JS,
        )
        self.assertIn(
            'byId("catalog-deselect-page").addEventListener("click", () => changeCatalogVisiblePageSelection(false))',
            _DASHBOARD_JS,
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js required for UI behavior")
    def test_pagination_filter_and_selection_no_platform_writes(self) -> None:
        harness = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(tag="") {
    this.tag=tag;this.children=[];this.value="";this.textContent="";
    this.hidden=false;this.checked=false;this.className="";this.listeners={};
  }
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  addEventListener(name,fn){this.listeners[name]=fn;}
  scrollIntoView(){}
  click(){this.clicked=true;}
}
const elements = new Map();
const document={
  getElementById(id){if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);},
  createElement(tag){return new Element(tag);},
  querySelectorAll(selector){
    if (selector!==".catalog-select input") return [];
    const found=[];
    const visit=(node)=>{
      for(const child of node.children||[]){
        if(child.className==="catalog-select")found.push(...child.children.filter(x=>x.tag==="input"));
        visit(child);
      }
    };
    visit(this.getElementById("ad-cards"));
    visit(this.getElementById("ads-body"));
    return found;
  },
};
const window={addEventListener(){}};
const sent=[];
let failNextOnce=false;
const ad=(id,title,state="active")=>({
  ad_id:id,title,description:"Details",present:state!=="absent",
  lifecycle_state:state,views:12,watch_count:1,reply_count:0,
  observed_at:"2026-10-26T08:00:00+00:00"
});
async function fetch(path){
  sent.push(path);
  assert.ok(path.startsWith("/api/catalog/ads?"),"no platform/write request");
  const u=new URL(path,"http://127.0.0.1:8765");
  assert.equal(u.searchParams.get("limit"),"50");
  const status=u.searchParams.get("status");
  const search=u.searchParams.get("q");
  const after=u.searchParams.get("after");
  if (after==="10001" && failNextOnce) {
    failNextOnce=false;
    throw new Error("synthetic transport failure");
  }
  let answer;
  if(search==="Massenauswahl" && status==="all"){
    answer={ads:Array.from({length:50},(_,i)=>ad(String(20000+i),"Artikel "+i)),
      has_more:false,next_after:null};
  }else if(search==="Tisch" && status==="paused"){
    answer={ads:[ad("10002","Tisch","paused")],has_more:false,next_after:null};
  }else if(after==="10001"){
    answer={ads:[ad("10002","Tisch","paused")],has_more:false,next_after:null};
  }else{
    answer={ads:[ad("10001","Lampe")],has_more:true,next_after:"10001"};
  }
  return {ok:true,json:async()=>answer};
}
const code=fs.readFileSync(0,"utf8").replace(/\r\n/g,"\n");
const end=code.indexOf("\nconsumeWriteToken();\nload();");
assert.ok(end>0);
const context={document,window,console,Date,URL,URLSearchParams,fetch};
vm.createContext(context);
vm.runInContext(code.slice(0,end),context);
vm.runInContext('writeUiAvailable=true; writeToken="x".repeat(32);',context);
elements.get("catalog-status").value="all";
(async()=>{
  assert.equal(await vm.runInContext("loadCatalogPage()",context),true);
  assert.equal(elements.get("ad-cards").children.length,1);
  assert.equal(elements.get("catalog-next").disabled,false);
  const card=elements.get("ad-cards").children[0];
  const chooser=card.children[1].children.find(c=>c.className==="catalog-select");
  assert.ok(chooser);
  const cb=chooser.children[0];
  assert.equal(elements.get("catalog-select-page").disabled,false);
  assert.equal(vm.runInContext("changeCatalogVisiblePageSelection(true)",context),true);
  assert.equal(cb.checked,true);
  assert.equal(elements.get("catalog-select-page").disabled,true);
  cb.checked=false;cb.listeners.change();
  assert.equal(elements.get("catalog-selected-count").textContent,"0 ausgewählt");
  assert.equal(vm.runInContext("changeCatalogVisiblePageSelection(true)",context),true);
  assert.equal(elements.get("catalog-selected-count").textContent,"1 ausgewählt");
  assert.equal(elements.get("catalog-selection-actions").hidden,false);
  assert.equal(elements.get("catalog-selection-hint").hidden,true);
  failNextOnce=true;
  assert.equal(await vm.runInContext("nextCatalogPage()",context),false);
  assert.equal(elements.get("catalog-page-label").textContent,"Seite 1");
  assert.equal(elements.get("ad-cards").children.length,1);
  assert.equal(elements.get("catalog-next").disabled,false);
  assert.equal(await vm.runInContext("nextCatalogPage()",context),true);
  assert.equal(elements.get("catalog-page-label").textContent,"Seite 2");
  assert.equal(elements.get("ad-cards").children.length,1);
  assert.equal(elements.get("catalog-selected-count").textContent,"1 ausgewählt");
  assert.equal(elements.get("catalog-next").disabled,true);
  assert.equal(vm.runInContext("changeCatalogVisiblePageSelection(true)",context),true);
  assert.equal(elements.get("catalog-selected-count").textContent,"2 ausgewählt");
  assert.equal(vm.runInContext("changeCatalogVisiblePageSelection(false)",context),true);
  assert.equal(elements.get("catalog-selected-count").textContent,"1 ausgewählt");
  assert.equal(await vm.runInContext("previousCatalogPage()",context),true);
  assert.equal(elements.get("catalog-page-label").textContent,"Seite 1");
  elements.get("catalog-search").value="Tisch";
  elements.get("catalog-status").value="paused";
  assert.equal(await vm.runInContext("searchCatalog()",context),true);
  assert.equal(elements.get("ad-cards").children[0].children[0].children[0].children[0].textContent,"Tisch");
  assert.equal(elements.get("catalog-page-label").textContent,"Seite 1");
  assert.ok(sent.some(path=>path.includes("q=Tisch")&&path.includes("status=paused")));
  assert.equal(elements.get("catalog-selected-count").textContent,"1 ausgewählt");
  elements.get("catalog-search").value="Massenauswahl";
  elements.get("catalog-status").value="all";
  assert.equal(await vm.runInContext("searchCatalog()",context),true);
  assert.equal(vm.runInContext("changeCatalogVisiblePageSelection(true)",context),true);
  assert.equal(elements.get("catalog-selected-count").textContent,"51 ausgewählt");
  assert.equal(elements.get("catalog-select-page").disabled,true);
  assert.equal(elements.get("catalog-selection-hint").hidden,false);
  assert.equal(elements.get("catalog-bulk-pause").disabled,true);
  assert.equal(elements.get("catalog-bulk-delete").disabled,true);
  assert.equal(elements.get("catalog-export").disabled,false);
  assert.equal(document.querySelectorAll(".catalog-select input").every(x=>x.checked),true);
  vm.runInContext("catalogLoading=true;renderCatalogSelection()",context);
  assert.equal(elements.get("catalog-deselect-page").disabled,true);
  assert.equal(vm.runInContext("changeCatalogVisiblePageSelection(false)",context),false);
  vm.runInContext("catalogLoading=false;catalogBulkBusy=true;renderCatalogSelection()",context);
  assert.equal(vm.runInContext("changeCatalogVisiblePageSelection(false)",context),false);
  vm.runInContext("catalogBulkBusy=false;renderCatalogSelection()",context);
  assert.equal(vm.runInContext("changeCatalogVisiblePageSelection(false)",context),true);
  assert.equal(elements.get("catalog-selected-count").textContent,"1 ausgewählt",
    "other-page selection must survive page deselection");
  assert.equal(document.querySelectorAll(".catalog-select input").every(x=>!x.checked),true);
  vm.runInContext("writeUiAvailable=false;renderCatalogSelection()",context);
  assert.equal(vm.runInContext("changeCatalogVisiblePageSelection(true)",context),true);
  assert.equal(elements.get("catalog-selected-count").textContent,"51 ausgewählt");
  assert.equal(elements.get("catalog-bulk-pause").hidden,true);
  assert.equal(elements.get("catalog-export").disabled,false);
  vm.runInContext("clearCatalogSelection()",context);
  assert.equal(elements.get("catalog-selected-count").textContent,"0 ausgewählt");
  assert.equal(elements.get("catalog-selection-actions").hidden,true);
  assert.equal(elements.get("catalog-export").disabled,true);
  vm.runInContext("writeUiAvailable=true;renderCatalogSelection()",context);
  vm.runInContext('for(let i=0;i<11;i++) catalogSelectedIds.add("selection"+i); renderCatalogSelection()',context);
  assert.equal(elements.get("catalog-selection-hint").hidden,false);
  assert.equal(elements.get("catalog-bulk-pause").disabled,true);
  assert.equal(elements.get("catalog-bulk-delete").disabled,true);
  assert.equal(elements.get("catalog-export").disabled,false);
  vm.runInContext("clearCatalogSelection()",context);
  assert.equal(elements.get("catalog-selection-actions").hidden,true);
  assert.ok(sent.every(path=>path.startsWith("/api/catalog/ads?")));
})().catch((error)=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(
            [shutil.which("node") or "node", "-e", harness],
            input=_DASHBOARD_JS, text=True, encoding="utf-8", capture_output=True,
            timeout=25, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()