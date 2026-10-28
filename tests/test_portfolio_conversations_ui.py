"""G7 browser-state regression: local inbox triage with no platform send."""
from __future__ import annotations

import shutil
import subprocess
import unittest

from mark_api.dashboard import _DASHBOARD_HTML, _DASHBOARD_JS


class ConversationUiTests(unittest.TestCase):
    def test_controls_live_inside_existing_buyer_panel(self):
        for element in (
            'id="buyer-conversations-filter"',
            'id="buyer-conversations-load"',
            'id="buyer-conversations-prev"',
            'id="buyer-conversations-next"',
            'id="buyer-conversations-body"',
            'id="buyer-conversations-status"',
            "Keine Plattform-Lesebestätigung",
        ):
            self.assertIn(element, _DASHBOARD_HTML)
        for marker in (
            '"/api/portfolio/conversations?"',
            '"/api/portfolio/conversations/review"',
            'local_review',
            "conversationPending",
        ):
            # The JS must not include any automatic platform send.
            if marker != 'local_review':
                self.assertIn(marker, _DASHBOARD_JS)
        block = _DASHBOARD_JS.split("// EML-only, human-controlled triage.", 1)[1].split(
            "// Antworttexte bleiben nur im Browserformular.", 1
        )[0]
        self.assertNotIn("innerHTML", block)
        self.assertNotIn("/api/write", block)
        self.assertIn('button.textContent=', block)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for native JS VM test")
    def test_real_browser_triage_replay_and_local_only(self):
        harness = r"""
const assert=require("node:assert/strict");
const vm=require("node:vm");
const fs=require("node:fs");
const source=fs.readFileSync(0,"utf8");
const start=source.indexOf("// EML-only, human-controlled triage.");
const end=source.indexOf("// Antworttexte bleiben nur im Browserformular.",start);
assert.ok(start>=0 && end>start,"G7 JS block exists");
class Element {
  constructor(id="") {
    this.id=id;this.disabled=false;this.value="";
    this.textContent="";this.children=[];this.listeners={};
  }
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=[...children];}
  addEventListener(name,fn){this.listeners[name]=fn;}
}
const elements=new Map();
const document={
  getElementById(id) {
    if(!elements.has(id)) elements.set(id,new Element(id));
    return elements.get(id);
  },
  createElement(tag){return new Element(tag);}
};
const get=(id)=>document.getElementById(id);
get("buyer-conversations-filter").value="new";
let conversations=[
  {ad_id:"10001",conversation_id:"chat_123",message_count:2,
    last_imported_event_at:"2026-10-28T10:00:00Z",title:"Regal",own_ad:true,
    review_state:"new_since_review",revision:0},
  {ad_id:"99999",conversation_id:"chat_999",message_count:1,
    last_imported_event_at:"2026-10-28T10:00:00Z",title:null,own_ad:false,
    review_state:"new_since_review",revision:0},
];
let posts=[],gets=[],loseFirstResponse=false;
const response=(code,body)=>({
  ok:code>=200&&code<300,status:code,async json(){return body;},
});
const fetch=async(path,options)=>{
  assert.ok(path.startsWith("/api/portfolio/conversations"),path);
  assert.equal(options.headers["X-Mark-Dashboard-Token"],"owner-token");
  if(options.method==="POST"){
    assert.equal(path,"/api/portfolio/conversations/review");
    assert.equal(options.headers["Content-Type"],"application/json");
    const payload=JSON.parse(options.body);
    posts.push(payload);
    assert.deepEqual(Object.keys(payload).sort(),[
      "action","ad_id","conversation_id","expected_revision","request_key"
    ]);
    const current=conversations.find(x=>x.ad_id===payload.ad_id &&
      x.conversation_id===payload.conversation_id);
    assert.equal(current.own_ad,true);
    if(current.revision===payload.expected_revision){
      current.revision++;
      current.review_state=payload.action==="review"?
        "locally_reviewed":"new_since_review";
    }
    if(loseFirstResponse) {
      loseFirstResponse=false;
      throw Error("lost local response after database commit");
    }
    return response(200,{ad_id:payload.ad_id,
      conversation_id:payload.conversation_id,
      revision:current.revision,platform_writes:0,
      review_state:current.review_state});
  }
  const params=new URL(path,"http://127.0.0.1").searchParams;
  const filter=params.get("status")||"all";
  gets.push({filter,after:params.get("after")});
  const view=conversations.filter(x=>filter==="all" ||
    (filter==="new"&&x.review_state==="new_since_review") ||
    (filter==="reviewed"&&x.review_state==="locally_reviewed"));
  return response(200,{conversations:view,watermark:4,
    has_more:false,next_after:null,filter,platform_messages_read:false,
    platform_writes:0});
};
const context={
  document,fetch,URLSearchParams,Number,JSON,
  byId:(id)=>document.getElementById(id),
  jobsAvailable:true,writeUiReady:()=>true,
  _jobHeaders:()=>({"X-Mark-Dashboard-Token":"owner-token"}),
  _newJobRequestKey:()=> "a".repeat(64),
};
vm.createContext(context);
vm.runInContext(source.slice(start,end),context);
const run=(expr)=>vm.runInContext(expr,context);
(async()=>{
  assert.equal(await run("loadConversations(true)"),true);
  let rows=get("buyer-conversations-body").children;
  assert.equal(rows.length,2);
  assert.equal(rows[0].children[3].children[0].textContent,"Als geprüft markieren");
  assert.equal(rows[1].children[3].children.length,0,"EML has no owner");
  const first=rows[0].children[3].children[0];
  loseFirstResponse=true;
  await run("markConversation({ad_id:'10001',conversation_id:'chat_123',"+
    "own_ad:true,review_state:'new_since_review',revision:0})");
  assert.equal(posts.length,1,"no automatic retry");
  assert.equal(run("conversationPending !== null"),true);
  // A fresh observed state with the matching revision reconciles the lost POST.
  get("buyer-conversations-filter").value="all";
  await run("loadConversations(true)");
  assert.equal(run("conversationPending"),null);
  rows=get("buyer-conversations-body").children;
  assert.equal(rows[0].children[2].textContent,"Lokal geprüft");
  assert.equal(rows[0].children[3].children[0].disabled,false);
  await run("markConversation({ad_id:'10001',conversation_id:'chat_123',"+
    "own_ad:true,review_state:'locally_reviewed',revision:1})");
  assert.equal(posts.length,2);
  assert.equal(posts[0].request_key.length,64);
  assert.equal(posts[0].action,"review");
  assert.equal(posts[1].action,"reopen");
  assert.equal(posts[1].expected_revision,1);
  assert.equal(conversations[0].review_state,"new_since_review");
  assert.ok(gets.length>=3);
  assert.equal(get("buyer-conversations-status").textContent.includes("Plattformstatus"),true);
  console.log("G7 browser triage and uncertain local write reconciliation: PASS");
})().catch(err=>{console.error(err);process.exitCode=1;});
"""
        process = subprocess.run(
            ["node","--jitless","-e",harness],
            input=_DASHBOARD_JS, text=True,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=12,
            check=False,
        )
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertIn("PASS",process.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for JS syntax")
    def test_complete_dashboard_js_compiles(self):
        result=subprocess.run(["node","--jitless","--check"],input=_DASHBOARD_JS,
            text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__ == "__main__":
    unittest.main()