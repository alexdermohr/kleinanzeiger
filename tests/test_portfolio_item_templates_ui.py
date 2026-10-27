"""G2: Chrome-dashboard DOM simulation and no spontaneous publication."""
from __future__ import annotations

import shutil
import subprocess
import unittest

from mark_api.dashboard import _DASHBOARD_HTML, _DASHBOARD_JS


class ItemTemplateUITests(unittest.TestCase):
    def test_template_panel_is_integrated_but_separate_from_ai_work_templates(self):
        for identifier in (
            "item-templates-disclosure","item-template-source","item-template-name",
            "item-template-save","item-template-select","item-template-new-sku",
            "item-template-from","item-template-copy","item-template-delete",
            "item-template-refresh","item-template-feedback",
        ):
            self.assertIn('id="'+identifier+'"',_DASHBOARD_HTML)
        self.assertIn('id="work-templates-disclosure"',_DASHBOARD_HTML)
        self.assertIn('"/api/portfolio/item-templates/mutate"',_DASHBOARD_JS)
        self.assertIn('"/api/portfolio/item-templates"',_DASHBOARD_JS)
        self.assertIn('actions.append(templateButton);',_DASHBOARD_JS)
        self.assertIn('actions.append(copyButton);',_DASHBOARD_JS)
        self.assertNotIn('submitCreate()', _DASHBOARD_JS[
            _DASHBOARD_JS.index("// G2: local article templates;"):
            _DASHBOARD_JS.index("function renderDraftRows(items)")])

    @unittest.skipUnless(shutil.which("node"),"Node.js needed for UI DOM regression")
    def test_explicit_template_actions_no_platform_writes_and_idempotent_retry(self):
        harness=r"""
const assert=require("node:assert/strict");
const vm=require("node:vm");
const fs=require("node:fs");
class Element {
  constructor(tag=""){
    this.tag=tag;this.children=[];this._value="";this.textContent="";
    this.open=false;this.hidden=false;this.disabled=false;this.checked=false;
    this.listeners={};this.style={};this.files=[];
  }
  set value(x){this._value=x;}
  get value(){return this._value;}
  replaceChildren(...nodes){this.children=[...nodes];}
  append(...nodes){this.children.push(...nodes);}
  addEventListener(name,callback){this.listeners[name]=callback;}
  focus(){this.focused=true;}
  scrollIntoView(){}
  click(){}
}
const elements=new Map();
const document={
  getElementById(id){if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);},
  createElement(tag){return new Element(tag);},
  querySelectorAll(){return [];}
};
let keyCounter=1;
const window={addEventListener(){},confirm(){return true;},
  crypto:{getRandomValues(bytes){bytes.fill(keyCounter++);return bytes;}}};
const recorded=[];
const templates=[];
const copies=[];
let failNextCopy=false;
const successfulResponse=(data)=>({ok:true,status:200,statusText:"OK",json:async()=>data});
const failedResponse=(status)=>({ok:false,status,statusText:"fail",json:async()=>({error:"denied"})});
async function fetch(path,config={}){
 recorded.push({path,config});
 if(path==="/api/portfolio/item-templates" && !config.method)
   return successfulResponse({templates:templates.slice(),limit:100});
 if(path==="/api/portfolio/item-templates/mutate" && config.method==="POST"){
   assert.equal(config.headers["X-Mark-Dashboard-Token"],"w".repeat(32));
   const payload=JSON.parse(config.body);
   assert.match(payload.request_key,/^[0-9a-f]{64}$/);
   if(payload.action==="save"){
     const template={template_id:"a".repeat(32),name:payload.name,revision:1,
       title:"Lampe",description:"Gut",price_eur:35,images:[],published:false};
     templates.push(template);
     return successfulResponse({template_id:template.template_id,platform_writes:0,revision:1});
   }
   if(payload.action==="delete"){
     templates.length=0;
     return successfulResponse({template_id:payload.template_id,deleted:true,platform_writes:0});
   }
   if(payload.action==="duplicate" || payload.action==="from_template"){
     if(failNextCopy){failNextCopy=false;return failedResponse(503);}
     copies.push(payload.new_sku);
     return successfulResponse({sku:payload.new_sku,status:"draft",published:false,platform_writes:0});
   }
   throw new Error("unexpected mutation");
 }
 if(path.startsWith("/api/portfolio/drafts?")){
   const filter=new URLSearchParams(path.split("?")[1]).get("q");
   return successfulResponse({drafts:copies.filter(x=>!filter||x.includes(filter)).map(sku=>({
     sku,title:"Lampe",description:"Gut",category:"Haus > Lampen",price_eur:35,images:[]
   })),total:copies.length,has_more:false,next_after:null});
 }
 throw new Error("unexpected network path "+path);
}
const source=fs.readFileSync(0,"utf8");
const cutoff=source.indexOf("\nconsumeWriteToken();\nload();");
assert.ok(cutoff>0);
const context={document,window,console,fetch,Date,URLSearchParams,Uint8Array,Set,Map};
vm.createContext(context);
vm.runInContext(source.slice(0,cutoff),context);
const call=code=>vm.runInContext(code,context);
call('writeUiAvailable=true;writeToken="w".repeat(32);pendingRecoveryBlocked=false;');
const item={sku:"LOCAL-01",title:"Lampe",description:"Gut",category:"Haus > Lampen",
            price_eur:35,images:[]};
context.item=item;
(async()=>{
  const before=recorded.length;
  call('chooseItemTemplateSource(item,"save")');
  assert.equal(recorded.length,before,"selecting a row must never write");
  assert.equal(elements.get("item-template-source").value,"LOCAL-01");
  assert.equal(elements.get("item-template-name").value,"Lampe");
  assert.equal(elements.get("item-templates-disclosure").open,true);
  assert.equal(await call('itemTemplateCommand("save")'),true);
  assert.equal(templates.length,1);
  assert.equal(elements.get("item-template-select").value,"a".repeat(32));
  call('chooseItemTemplateSource(item,"copy")');
  elements.get("item-template-new-sku").value="NEW-001";
  failNextCopy=true;
  assert.equal(await call('itemTemplateCommand("duplicate")'),false);
  const first=recorded.filter(x=>x.config.method==="POST").at(-1);
  assert.match(elements.get("item-template-feedback").textContent,/Ergebnis unklar/);
  assert.equal(await call('itemTemplateCommand("duplicate")'),true);
  const second=recorded.filter(x=>x.config.method==="POST").at(-1);
  assert.equal(JSON.parse(first.config.body).request_key,JSON.parse(second.config.body).request_key,
               "same action after uncertain result reuses original key");
  assert.deepEqual(copies,["NEW-001"]);
  elements.get("item-template-select").value="a".repeat(32);
  elements.get("item-template-new-sku").value="NEW-002";
  assert.equal(await call('itemTemplateCommand("from_template")'),true);
  assert.deepEqual(copies,["NEW-001","NEW-002"]);
  assert.equal(await call('itemTemplateCommand("delete")'),true);
  assert.equal(templates.length,0);
  assert.equal(recorded.filter(x=>x.path.startsWith("/api/write/")).length,0);
  assert.equal(recorded.filter(x=>x.path.includes("generate")).length,0);
  assert.ok(recorded.every(x=>x.path.startsWith("/api/portfolio/")));
  console.log("G2 DOM explicit actions and idempotency PASS");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result=subprocess.run(
            ["node","-e",harness],input=_DASHBOARD_JS,text=True,
            capture_output=True,timeout=50,check=False,
        )
        self.assertEqual(result.returncode,0,
            result.stdout+"\n"+result.stderr)
        self.assertIn("idempotency PASS",result.stdout)


if __name__=="__main__":
    unittest.main()
