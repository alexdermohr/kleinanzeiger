"""G3: local 1,000-SKU photo matching, no network or platform mutation."""
from __future__ import annotations
import shutil
import subprocess
import unittest
from mark_api.dashboard import _DASHBOARD_HTML, _DASHBOARD_JS


class PortfolioDraftPhotoUiTests(unittest.TestCase):
    def test_ui_has_optional_photo_folder_and_no_implicit_submit(self) -> None:
        for item in ('id="draft-photo-folder"', 'webkitdirectory', 'id="draft-photo-clear"',
                     'id="draft-photo-preview"', 'id="draft-photo-folder-status"',
                     'id="draft-photo-thumbnails"'):
            self.assertIn(item, _DASHBOARD_HTML)
        self.assertIn("verifyDraftImageSignature", _DASHBOARD_JS)
        self.assertIn("chosenDraftImages", _DASHBOARD_JS)
        self.assertIn("Dateiübernahme", _DASHBOARD_JS)

    @unittest.skipUnless(shutil.which("node"), "Node.js needed for actual dashboard JS")
    def test_one_thousand_skus_match_content_validation_conflicts_and_no_writes(self) -> None:
        harness = r"""
const assert=require("node:assert/strict");
const vm=require("node:vm");
const fs=require("node:fs");
class Element {
  constructor(tag="") {
    this.tag=tag;this.children=[];this._value="";this.files=[];
    this.textContent="";this.hidden=false;this.disabled=false;this.checked=false;
    this.open=false;this.className="";this.listeners={};this.style={};
  }
  set value(v) {this._value=v;if(v==="" && this.type==="file")this.files=[];}
  get value(){return this._value;}
  replaceChildren(...nodes){this.children=[...nodes];}
  append(...nodes){this.children.push(...nodes);}
  addEventListener(k,f){this.listeners[k]=f;}
  scrollIntoView(){}
  click(){}
}
const elements=new Map();
const document={
 getElementById(id){if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);},
 createElement(tag){return new Element(tag);},
 querySelectorAll(){return [];}
};
let confirms=0;
const window={addEventListener(){},confirm(){confirms++;return true;}};
let writes=0;
async function fetch(){writes++;throw new Error("unexpected server call");}
let opened=0,revoked=0;
const URL={createObjectURL(){opened++;return "blob:synthetic-"+opened;},revokeObjectURL(){revoked++;}};
class DataTransfer {
  constructor(){this._files=[];this.items={add:(f)=>{this._files.push(f);}};}
  get files(){return [...this._files];}
}
const SIGNATURES={
 jpg:[255,216,255,224,0,0,0,0,0,0,0,0,0,0,0,0],
 png:[137,80,78,71,13,10,26,10,0,0,0,0,0,0,0,0],
 webp:[82,73,70,70,8,0,0,0,87,69,66,80,0,0,0,0]
};
function file(name,folder,kind=null){
 const ext=name.split(".").pop().toLowerCase();
 const signature=kind||({jpeg:"jpg",jpg:"jpg",png:"png",webp:"webp"}[ext]);
 const type=({jpg:"image/jpeg",jpeg:"image/jpeg",png:"image/png",webp:"image/webp"}[ext]);
 const content=Uint8Array.from(SIGNATURES[signature]||[]);
 return {name,webkitRelativePath:folder+"/"+name,type,size:content.length,
   slice:()=>({arrayBuffer:async()=>content.buffer.slice(0)})};
}
const source=fs.readFileSync(0,"utf8");
const end=source.indexOf("\nconsumeWriteToken();\nload();");
assert.ok(end>0,"dashboard should be cut before network boot");
const ctx={document,window,console,URL,URLSearchParams,DataTransfer,fetch,Date};
vm.createContext(ctx);vm.runInContext(source.slice(0,end),ctx);
vm.runInContext('writeUiAvailable=true;writeToken="x".repeat(32);',ctx);
const folder=elements.get("draft-photo-folder");
folder.type="file";
const media=elements.get("create-media");
media.type="file";
const photoFiles=[];
for(let i=0;i<1000;i++){
 const sku=String(i).padStart(4,"0");
 photoFiles.push(file("front-"+sku+".jpg","Selected/SKU-"+sku));
 photoFiles.push(file("detail-"+sku+".png","Selected/SKU-"+sku));
}
const call=(code)=>vm.runInContext(code,ctx);
const item={sku:"SKU-0999",title:"Lampe aus Katalog",description:"Bekannte Details",
 category:"Haus > Licht",price_eur:29,images:["detail-0999.png","front-0999.jpg"]};
ctx.testItem=item;
(async()=>{
 folder.files=photoFiles;call("loadDraftPhotoFolder()");
 assert.equal(call("draftPhotoIndex.size"),2000);
 assert.equal(call("draftPhotoFolderChosen"),true);
 assert.match(elements.get("draft-photo-folder-status").textContent,/2000 Bilder/);
 assert.equal(await call("useLocalDraft(testItem)"),true);
 assert.deepEqual(media.files.map(x=>x.name),item.images);
 assert.equal(elements.get("create-title").value,item.title);
 assert.equal(elements.get("create-category").value,item.category);
 assert.equal(elements.get("create-price").value,"29");
 assert.equal(opened,2);
 assert.equal(elements.get("draft-photo-preview").hidden,false);
 assert.equal(writes,0,"index and previews must make no network calls");
 assert.equal(confirms,1,"only draft-form-overwrite confirmation, not publish");
 const keep=elements.get("create-title").value;
 // A duplicate filename, even in another SKU folder, must block *before* form mutation.
 folder.files=[file("front-0999.jpg","Selected/A"),file("front-0999.jpg","Selected/B"),
               file("detail-0999.png","Selected/C")];
 call("loadDraftPhotoFolder()");
 assert.equal(media.files.length,0,"changing folder must discard prior auto-applied photos");
 assert.equal(await call("useLocalDraft(testItem)"),false);
 assert.equal(elements.get("create-title").value,keep);
 assert.match(elements.get("draft-feedback").textContent,/Mehrdeutig/);
 // Missing image also blocks.
 folder.files=[file("front-0999.jpg","Selected/Only")];call("loadDraftPhotoFolder()");
 assert.equal(await call("useLocalDraft(testItem)"),false);
 assert.match(elements.get("draft-feedback").textContent,/Fehlend: detail-0999.png/);
 // Disguised PNG data as JPEG must fail format sniffing.
 folder.files=[file("front-0999.jpg","Selected/A","png"),file("detail-0999.png","Selected/B")];
 call("loadDraftPhotoFolder()");
 assert.equal(await call("useLocalDraft(testItem)"),false);
 assert.match(elements.get("draft-feedback").textContent,/Inhalt passt nicht/);
 // Unsafe traversal is excluded and cannot accidentally match a draft.
 folder.files=[file("front-0999.jpg","Selected/../Outside"),file("detail-0999.png","Selected")];
 call("loadDraftPhotoFolder()");
 assert.equal(await call("useLocalDraft(testItem)"),false);
 assert.match(elements.get("draft-feedback").textContent,/Fehlend: front-0999.jpg/);
 // Unsupported MIME rejected on folder indexing, not guessed by extension.
 folder.files=[{...file("front-0999.jpg","Selected"),type:"image/png"},file("detail-0999.png","Selected")];
 call("loadDraftPhotoFolder()");
 assert.equal(await call("useLocalDraft(testItem)"),false);
 // Ordinary text-only import remains possible without folder selection.
 call("clearDraftPhotoFolder()");
 assert.equal(await call("useLocalDraft(testItem)"),true);
 assert.equal(media.files.length,0);
 assert.match(elements.get("draft-feedback").textContent,/Bilder selbst auswählen/);
 // No publish, provider or platform requests in the entire flow.
 assert.equal(writes,0);
 assert.ok(revoked>=2,"preview URLs must be released on selection change");
 folder.files=Array.from({length:12001},(_,i)=>file("img-"+i+".jpg","Selected"));
 call("loadDraftPhotoFolder()");
 assert.equal(call("draftPhotoFolderChosen"),false);
 assert.match(elements.get("draft-photo-folder-status").textContent,/12.000/);
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
"""
        result = subprocess.run([shutil.which("node") or "node", "--jitless", "-e", harness],
                                input=_DASHBOARD_JS, capture_output=True, text=True,
                                encoding="utf-8", timeout=40, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
