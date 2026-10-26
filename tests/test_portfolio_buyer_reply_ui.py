"""Synthetic buyer-reply assistant browser safety and consent regression.

No real buyer messages, paid provider calls, or Kleinanzeigen writes.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest

from mark_api.dashboard import _DASHBOARD_HTML, _DASHBOARD_JS


class BuyerReplyUiTests(unittest.TestCase):
    def test_customer_has_a_separate_manual_reply_workspace(self):
        for marker in (
            'id="nav-buyer"', 'id="buyer-panel"', 'id="buyer-disclosure"',
            'id="buyer-ad-id"', 'id="buyer-ad-title"',
            'id="buyer-inquiry"', 'id="buyer-facts"', 'id="buyer-consent"',
            'id="buyer-reply"', 'id="buyer-generate"', 'id="buyer-manual"',
            'id="buyer-copy"',
        ):
            self.assertIn(marker, _DASHBOARD_HTML)
        self.assertIn('mode: "reply", prompt, acknowledge_provider_cost: true', _DASHBOARD_JS)
        self.assertIn('"/api/catalog/ad?id="', _DASHBOARD_JS)
        self.assertNotIn('"/api/write/messages"', _DASHBOARD_JS)
        self.assertNotIn('"/api/write/replies"', _DASHBOARD_JS)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for real JS")
    def test_local_template_paid_consent_stale_rejection_copy_and_no_send(self):
        harness = r"""
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const script=fs.readFileSync(0,"utf8").replace(/\r\n/g,"\n");
const end=script.indexOf("\nconsumeWriteToken();\nload();");
assert.ok(end>0);
class Element {
  constructor(id) {this.id=id;this.value="";this.checked=false;
    this.textContent="";this.hidden=false;this.disabled=false;
    this.listeners={};this.children=[];this.open=false;}
  addEventListener(name,cb) {this.listeners[name]=cb;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=[...items];}
  scrollIntoView(){}
  select(){this.selected=true;}
}
const elements=new Map();
const document={
  getElementById(id) {if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);},
  createElement(tag) {return new Element(tag);},
  querySelectorAll() {return [];}
};
const sent=[],copies=[];
let currentAd=null, failProvider=false, holdProvider=false, pendingProviderResolve=null,invalidProvider=false;
const fetch=async(path,opts)=>{
  sent.push({path,opts});
  if(path.startsWith("/api/catalog/ad?id=")) {
    assert.equal(opts?.method,undefined);
    assert.equal(new URL(path,"http://127.0.0.1").searchParams.get("id"),currentAd.ad_id);
    return {ok:true,json:async()=>({ad:currentAd})};
  }
  if(path==="/api/dashboard/generate") {
    assert.equal(opts.method,"POST");
    assert.equal(opts.credentials,"omit");
    assert.equal(opts.mode,"same-origin");
    assert.equal(opts.headers["X-Mark-Generation"],"1");
    assert.equal(opts.headers["X-Mark-Generation-Token"],"s".repeat(32));
    const body=JSON.parse(opts.body);
    assert.equal(body.mode,"reply");
    assert.equal(body.acknowledge_provider_cost,true);
    assert.ok(body.prompt.includes(currentAd.ad_id));
    assert.ok(body.prompt.includes("Nachricht"));
    if(holdProvider) return new Promise(resolve=>pendingProviderResolve=resolve);
    if(failProvider) return {ok:false,status:502};
    if(invalidProvider)return {ok:true,status:200,json:async()=>({status:"draft",sent:true,reply:"forged"})};
    return {ok:true,status:200,json:async()=>({status:"draft",sent:false,reply:"Guten Tag, vielen Dank für Ihre Anfrage."})};
  }
  throw Error("unexpected network request "+path);
};
const window={addEventListener(){}};
const navigator={clipboard:{writeText:async(text)=>copies.push(text)}};
const context={document,window,navigator,fetch,console,Date,URL,URLSearchParams};
vm.createContext(context);
vm.runInContext(script.slice(0,end),context);
vm.runInContext('generationUiAvailable=true; generationToken="s".repeat(32)',context);
const byId=(id)=>elements.get(id);
const ad=(id="123",title="Gebrauchtes Fahrrad")=>({
  ad_id:id,title,present:true,lifecycle_state:"active",
  observed_at:"2026-10-26T08:00:00+00:00"
});
function selectAd(target) {
  currentAd=target;window.target=target;
  vm.runInContext("openBuyerReplyForAd(window.target)",context);
}
const generate=()=>vm.runInContext("requestBuyerReply()",context);
const copy=()=>vm.runInContext("copyBuyerReply()",context);
const countPosts=()=>sent.filter(x=>x.path==="/api/dashboard/generate").length;
const assertOnlyReadsAndDrafts=()=>{
  assert.ok(sent.every(x=>x.path.startsWith("/api/catalog/ad?id=")
    || x.path==="/api/dashboard/generate"), "must never send a platform message");
};
(async()=>{
  assert.equal(byId("buyer-generate").listeners.click!==undefined,true);
  selectAd(ad());
  assert.equal(byId("buyer-ad-id").value,"123");
  assert.equal(byId("buyer-ad-title").value,"Gebrauchtes Fahrrad");
  assert.equal(byId("buyer-disclosure").open,true);
  assert.equal(byId("buyer-reply").value,"");
  vm.runInContext("manualBuyerReply()",context);
  assert.match(byId("buyer-reply").value,/Guten Tag/);
  assert.equal(sent.length,0,"local canned draft uses no network");
  await copy();
  assert.equal(copies.length,1);
  assert.equal(copies[0],byId("buyer-reply").value);

  byId("buyer-inquiry").value="Ist ein Versand möglich?";
  byId("buyer-inquiry").listeners.input();
  assert.equal(byId("buyer-reply").value,"","new input retires the old draft");
  byId("buyer-facts").value="Kein Versand zugesagt.";
  byId("buyer-facts").listeners.input();
  await generate();
  assert.equal(sent.length,0,"no cost consent means no network");
  assert.match(byId("buyer-status").textContent,/gesondert bestätigen/);

  byId("buyer-consent").checked=true;
  await generate();
  assert.equal(countPosts(),1);
  assert.equal(sent.filter(x=>x.path.startsWith("/api/catalog/")).length,1);
  assert.equal(byId("buyer-consent").checked,false);
  assert.equal(byId("buyer-reply").value,"Guten Tag, vielen Dank für Ihre Anfrage.");
  assert.equal(byId("buyer-copy").disabled,false);
  assertOnlyReadsAndDrafts();

  byId("buyer-inquiry").value="Kann ich heute abholen?";
  byId("buyer-inquiry").listeners.input();
  assert.equal(byId("buyer-reply").value,"");
  // An observed local owner change must stop the paid request.
  currentAd={...currentAd,title:"Geändert"};
  byId("buyer-consent").checked=true;
  await generate();
  assert.equal(countPosts(),1,"a stale ad may never reach a paid provider");
  assert.match(byId("buyer-status").textContent,/verändert/);

  selectAd(ad("456","Lampe"));
  byId("buyer-inquiry").value="Kann ich die Lampe ansehen?";
  byId("buyer-inquiry").listeners.input();
  byId("buyer-consent").checked=true;
  failProvider=true;
  await generate();
  assert.equal(countPosts(),2);
  assert.equal(byId("buyer-reply").value,"");
  assert.equal(byId("buyer-consent").checked,false);
  assert.match(byId("buyer-status").textContent,/Kein automatischer Neuversuch/);
  await generate();
  assert.equal(countPosts(),2,"the unknown provider outcome is not retried");
  failProvider=false;

  byId("buyer-consent").checked=true;
  invalidProvider=true;
  await generate();
  assert.equal(countPosts(),3);
  assert.equal(byId("buyer-reply").value,"","never show a forged sent result");
  invalidProvider=false;

  byId("buyer-consent").checked=true;
  holdProvider=true;
  const pending=generate();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(byId("buyer-inquiry").disabled,true);
  assert.equal(byId("buyer-generate").disabled,true);
  assert.equal(countPosts(),4);
  window.other=ad("999","Nicht die aktuelle Anzeige");
  vm.runInContext("openBuyerReplyForAd(window.other)",context);
  assert.equal(byId("buyer-ad-id").value,"456","cannot switch ad during billable request");
  await generate();
  assert.equal(countPosts(),4,"no second request while busy");
  assert.ok(pendingProviderResolve);
  pendingProviderResolve({ok:true,status:200,json:async()=>({
    status:"draft",sent:false,reply:"Guten Tag! Ich prüfe den Artikel."
  })});
  await pending;
  holdProvider=false;
  assert.equal(byId("buyer-inquiry").disabled,false);
  assert.equal(byId("buyer-reply").value,"Guten Tag! Ich prüfe den Artikel.");

  selectAd(ad("321","Vase"));
  vm.runInContext("generationUiAvailable=false; generationToken=null",context);
  vm.runInContext("buyerReplyAvailability()",context);
  assert.equal(byId("buyer-generate").disabled,true);
  vm.runInContext("manualBuyerReply()",context);
  assert.match(byId("buyer-reply").value,/Vase/);
  assert.equal(countPosts(),4,"manual drafting works without AI provider");

  window.invalid={...ad("876"),present:false};
  vm.runInContext("openBuyerReplyForAd(window.invalid)",context);
  assert.equal(byId("buyer-ad-id").value,"321","absent ad is not a valid target");
  assertOnlyReadsAndDrafts();
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(
            [shutil.which("node") or "node", "-e", harness],
            input=_DASHBOARD_JS, text=True, encoding="utf-8",
            capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__ == "__main__":
    unittest.main()
