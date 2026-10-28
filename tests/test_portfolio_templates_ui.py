"""Real browser-JS contract: refresh and delayed save never retarget template edits.

All provider/API responses are synthetic; no network, AI or platform writes.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest

from mark_api.dashboard import _DASHBOARD_JS


class TemplateEditorUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node required for real-JS state test")
    def test_refresh_preserves_editor_revision_and_delayed_save_keeps_new_selection(self):
        harness = r"""
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const source=fs.readFileSync(0,"utf8");
const start=source.indexOf("async function loadWorkTemplates() {");
const end=source.indexOf("\nfunction renderSaleChoices() {",start);
assert.ok(start>=0 && end>start,"shipped template controller required");
class Element {
  constructor(id=""){this.id=id;this.value="";this.textContent="";
    this.hidden=false;this.disabled=false;this.open=false;
    this.children=[];this.listeners={};}
  addEventListener(k,fn){this.listeners[k]=fn;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=[...children];this.value="";}
}
const elements=new Map();
const document={
  getElementById(id){if(!elements.has(id))elements.set(id,new Element(id));
    return elements.get(id);},
  createElement(id){return new Element(id);}
};
const byId=id=>document.getElementById(id);
const window={confirm:()=>true};
let templates=[];
const messages=[];
const sent=[];
let fetchImpl=async()=>{throw Error("unexpected write")};
const fetch=(path,options)=>{
  assert.equal(path,"/api/portfolio/templates/mutate");
  const payload=JSON.parse(options.body);
  sent.push(payload);
  return fetchImpl(payload);
};
const _jobGet=async path=>{
  if(path==="/api/portfolio/templates")
    return {templates:structuredClone(templates)};
  if(path==="/api/portfolio/attention")
    return {proposals_to_review:0,unknown_ki_outcomes:0,
      manual_sales_missing_cost:0,old_local_reservations:0};
  throw Error("unexpected local GET "+path);
};
let sequence=0;
const context={document,byId,window,console,_jobGet,fetch,
  _jobHeaders:()=>({}),_newJobRequestKey:()=>String(++sequence).padStart(64,"0"),
  writeUiReady:()=>true};
vm.createContext(context);
vm.runInContext(`
let jobsAvailable=true,workTemplates=[],workTemplatePending=null,workTemplateBusy=false;
let workTemplateEditorBaseline=null,workTemplateSelectionGeneration=0;
let workTemplateLoadGeneration=0,workTemplateSelectionRequiresRecheck=false;
`+source.slice(start,end),context);
const choose=(id)=>{
  byId("work-template-select").value=id;
  byId("work-template-select").listeners.change();
};
const view=expr=>vm.runInContext(expr,context);
const A=(revision,name="Vorlage A")=>({
  template_id:"A",revision,name,instruction:"Instruktion A",idempotency_key:null
});

(async()=>{
  // A's rev 1 is loaded into the editor. A list refresh learns rev 2 but
  // must not silently use rev 2 with the untouched rev 1 form fields.
  templates=[A(1)];
  assert.equal(await view("loadWorkTemplates()"),true);
  choose("A");
  byId("work-template-name").value="Meine alte Bearbeitung A";
  byId("job-instruction").value="Veraltete Instruktion A";
  assert.equal(view("workTemplateEditorBaseline.revision"),1);
  templates=[A(2,"Extern geändert")];
  assert.equal(await view("loadWorkTemplates()"),true);
  assert.equal(view("workTemplateEditorBaseline.revision"),1);
  fetchImpl=async()=>({ok:false,status:409});
  await view("mutateWorkTemplate(false)");
  assert.equal(sent.length,1);
  assert.equal(sent[0].template_id,"A");
  assert.equal(sent[0].expected_revision,1,
    "stale editor must never borrow refreshed revision");
  assert.equal(sent[0].instruction,"Veraltete Instruktion A");
  assert.equal(byId("job-instruction").value,"Veraltete Instruktion A");
  assert.match(byId("work-template-feedback").textContent,/nicht bestätigt/);

  // Unknown failed write stays bound. A changed payload cannot silently
  // create a fresh idempotency key or overwrite a different revision.
  byId("job-instruction").value="nochmals geändert";
  await view("mutateWorkTemplate(false)");
  assert.equal(sent.length,1,"no second write while prior result is unsettled");

  // Independent fresh editor after a page restart.
  vm.runInContext("workTemplatePending=null;",context);
  assert.equal(await view("loadWorkTemplates()"),true);
  choose("A");
  byId("work-template-name").value="A gespeichert";
  byId("job-instruction").value="A update";
  let finishA;
  fetchImpl=()=>new Promise(resolve=>{finishA=resolve;});
  const inFlight=view("mutateWorkTemplate(false)");
  assert.equal(sent.length,2);
  assert.equal(sent[1].template_id,"A");
  assert.equal(sent[1].expected_revision,2);
  assert.equal(byId("work-template-select").disabled,true,
    "selection must be locked during an in-flight write");
  // Even a programmatic change arriving during the pending request may
  // not let A's completion reselect A and turn C fields into an A overwrite.
  choose("");
  byId("work-template-name").value="Neue Vorlage C";
  byId("job-instruction").value="Nur Inhalt C";
  templates=[A(3,"A gespeichert")];
  finishA({ok:true,json:async()=>({template_id:"A",platform_writes:0})});
  await inFlight;
  assert.equal(byId("work-template-select").value,"");
  assert.equal(byId("work-template-name").value,"Neue Vorlage C");
  assert.equal(byId("job-instruction").value,"Nur Inhalt C");
  assert.equal(byId("work-template-select").disabled,false);
  assert.equal(view("workTemplateEditorBaseline"),null);
  fetchImpl=async payload=>{
    assert.equal(payload.template_id,null,"C must never update A");
    assert.equal(payload.expected_revision,0);
    assert.equal(payload.name,"Neue Vorlage C");
    assert.equal(payload.instruction,"Nur Inhalt C");
    templates=[...templates,{template_id:"C",name:payload.name,
      instruction:payload.instruction,revision:1}];
    return {ok:true,json:async()=>({template_id:"C",platform_writes:0})};
  };
  await view("mutateWorkTemplate(false)");
  assert.equal(sent.length,3);
  assert.equal(sent[2].template_id,null);
  assert.equal(byId("work-template-select").value,"C");
  assert.equal(view("workTemplateEditorBaseline.revision"),1);
  console.log("TEMPLATE_EDITOR_CAS_SELECTION_PASS");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(
            [shutil.which("node") or "node", "-e", harness],
            input=_DASHBOARD_JS, text=True, encoding="utf-8",
            capture_output=True, check=False, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TEMPLATE_EDITOR_CAS_SELECTION_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
