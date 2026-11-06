"""Report CSV browser contract: BOM, real row breaks, no untrusted formula cells."""
from __future__ import annotations
import shutil
import subprocess
import unittest
from mark_api.dashboard import _DASHBOARD_JS

class PortfolioReportingUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node runtime needed")
    def test_unicode_csv_export_and_untrusted_day_rejection(self):
        script=r"""
const vm=require("node:vm"),fs=require("node:fs"),assert=require("node:assert/strict");
const input=fs.readFileSync(0,"utf8");
new vm.Script(input);
const start=input.indexOf("function downloadPortfolioReportCsv() {");
const end=input.indexOf('byId("report-refresh").addEventListener',start);
assert.ok(start>0 && end>start);
const code=input.slice(start,end);
let saved=null,clicks=0,appends=0,metric="messages";
class BlobCapture{
 constructor(parts,opts){saved=parts.join("");assert.equal(opts.type,"text/csv;charset=utf-8");}
}
const ctx={
 reportData:{window_days:7,imported_messages_per_day:[
   {day_utc:"2026-10-25",imported_messages:0,imported_conversations:0},
   {day_utc:"2026-10-26",imported_messages:3,imported_conversations:2}]},
 byId:(id)=>({value:id==="report-metric"?metric:"7"}),
 Blob:BlobCapture,
 URL:{createObjectURL(){return "blob:test";},revokeObjectURL(){}},
 document:{createElement(){
   return {click(){clicks++;},remove(){},href:"",download:""};
 },body:{append(){appends++;}}},
 Number, String
};
vm.runInNewContext(code+";downloadPortfolioReportCsv()",ctx);
assert.ok(saved.startsWith("\ufefftag_utc,importierte_eingangsnachrichten\r\n"));
assert.ok(saved.includes("2026-10-26,3\r\n"));
assert.equal(clicks,1);assert.equal(appends,1);
metric="conversations";
saved=null;
vm.runInNewContext(code+";downloadPortfolioReportCsv()",ctx);
assert.ok(saved.startsWith("\ufefftag_utc,unterschiedliche_gespraeche_je_tag\r\n"));
assert.ok(saved.includes("2026-10-26,2\r\n"));
assert.equal(clicks,2);
saved=null;
ctx.reportData.imported_messages_per_day=[{day_utc:"=HYPERLINK",imported_messages:1}];
vm.runInNewContext(code+";downloadPortfolioReportCsv()",ctx);
assert.equal(clicks,2,"formulas must abort export");
assert.equal(saved,null);
"""
        node=shutil.which("node") or "node"
        proc=subprocess.run([node,"-e",script],input=_DASHBOARD_JS,
            text=True, encoding="utf-8",capture_output=True,timeout=30)
        self.assertEqual(proc.returncode,0,proc.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node runtime needed")
    def test_previous_window_label_is_source_bound_and_fail_closed(self):
        script=r"""
const vm=require("node:vm"),fs=require("node:fs"),assert=require("node:assert/strict");
const input=fs.readFileSync(0,"utf8");
new vm.Script(input);
const start=input.indexOf("function portfolioImportedTrendText(data) {");
const end=input.indexOf("async function loadPortfolioReport()",start);
assert.ok(start>0 && end>start);
const code=input.slice(start,end);
const value={start_day_utc:"2026-10-20",end_day_utc:"2026-10-26",
 imported_inbound_messages_window:4,imported_conversations_window:2,
 imported_previous_window:{start_day_utc:"2026-10-13",end_day_utc:"2026-10-19",
   imported_inbound_messages:2,imported_conversations:1,
   delta_imported_inbound_messages:2,delta_imported_conversations:1,
   source:"local_eml_only",platform_inbox_complete:false}};
function render(data){return vm.runInNewContext(code+";portfolioImportedTrendText(data)",{data});}
const label=render(value);
assert.match(label,/2026-10-13 bis 2026-10-19/);
assert.match(label,/Änderung \+2/);
assert.match(label,/Änderung \+1/);
assert.match(label,/beweisen keine ausbleibende Plattformnachfrage/);
const prior=value.imported_previous_window;
for(const bad of [
 {...prior,delta_imported_inbound_messages:3},
 {...prior,imported_inbound_messages:-2},
 {...prior,imported_conversations:"1"},
 {...prior,source:"platform_inbox"},
 {...prior,platform_inbox_complete:true},
 {...prior,end_day_utc:"2026-10-20"},
 {...prior,start_day_utc:"=HYPERLINK"},
]) {
 assert.throws(()=>render({...value,imported_previous_window:bad}),
   /Ungültiger|Widersprüchlicher/);
}
assert.throws(()=>render({...value,imported_previous_window:null}),/Ungültiger/);
const empty={...value,imported_inbound_messages_window:0,imported_conversations_window:0,
 imported_previous_window:{...prior,imported_inbound_messages:0,imported_conversations:0,
 delta_imported_inbound_messages:0,delta_imported_conversations:0}};
assert.match(render(empty),/fehlende E-Mails/);
"""
        proc=subprocess.run([shutil.which("node") or "node","-e",script],
            input=_DASHBOARD_JS,text=True,encoding="utf-8",
            capture_output=True,timeout=30)
        self.assertEqual(proc.returncode,0,proc.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node runtime needed")
    def test_cost_summary_separates_checked_unknown_and_rejects_inconsistent_totals(self):
        script=r"""
const vm=require("node:vm"),fs=require("node:fs"),assert=require("node:assert/strict");
const input=fs.readFileSync(0,"utf8");
new vm.Script(input);
const start=input.indexOf("function portfolioAiCostText(cost) {");
const end=input.indexOf("async function loadPortfolioReport()",start);
assert.ok(start>0 && end>start);
const code=input.slice(start,end);
function render(cost) {
  return vm.runInNewContext(code+";portfolioAiCostText(input)",{input:cost});
}
const row={usd_cent_estimate:120,usd_cent_checked_unknown:8,
  usd_cent_known_total:128,usd_cent_reserved:25,
  unknown_outcomes:1,inflight_outcomes:0,invoice_verified:false};
const label=render(row);
for (const expected of ["1.20 USD", "0.08 USD", "1.28 USD", "0.25 USD",
                        "UNKNOWN manuell geprüft", "keine bestätigte Anbieterrechnung"]) {
  assert.ok(label.includes(expected),label);
}
assert.ok(render(null).includes("Noch keine lokalen KI-Auftragskosten"));
for (const invalid of [
  {...row,usd_cent_known_total:120},
  {...row,usd_cent_checked_unknown:-1},
  {...row,usd_cent_reserved:"25"},
  {...row,invoice_verified:true},
  {...row,usd_cent_known_total:undefined}
]) {
  assert.throws(()=>render(invalid),/Ungültiger KI-Kostenbericht/);
}
"""
        proc=subprocess.run([shutil.which("node") or "node","-e",script],
            input=_DASHBOARD_JS,text=True,encoding="utf-8",
            capture_output=True,timeout=30)
        self.assertEqual(proc.returncode,0,proc.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node runtime needed")
    def test_top_ad_report_is_bounded_dom_only_and_rejects_false_buyers(self):
        script=r"""
const vm=require("node:vm"),fs=require("node:fs"),assert=require("node:assert/strict");
const input=fs.readFileSync(0,"utf8");
new vm.Script(input);
const start=input.indexOf("function renderPortfolioReportTopAds(data) {");
const end=input.indexOf("function portfolioAiCostText(cost) {",start);
assert.ok(start>0 && end>start);
const code=input.slice(start,end);
function element(tag) {
  return {tag,textContent:"",children:[],append(node){this.children.push(node);},
    replaceChildren(...nodes){this.children=nodes;}};
}
const nodes={"report-top-ads-body":element("tbody"),
             "report-top-ads-note":element("p")};
const ctx={
  document:{createElement:element},
  byId:id=>nodes[id],
  data:{imported_conversations_window:2,imported_inbound_messages_window:3,
    imported_top_ads_window:[
      {ad_id:"101",imported_conversations:1,imported_inbound_messages:2},
      {ad_id:"102",imported_conversations:1,imported_inbound_messages:1}]}
};
function run(){return vm.runInNewContext(code+";renderPortfolioReportTopAds(data)",ctx);}
run();
assert.equal(nodes["report-top-ads-body"].children.length,2);
assert.deepEqual(nodes["report-top-ads-body"].children[0].children.map(c=>c.textContent),
                 ["101","1","2"]);
assert.ok(nodes["report-top-ads-note"].textContent.includes("nicht nach Käufern"));
for (const rows of [
  [{ad_id:"<img src=x>",imported_conversations:1,imported_inbound_messages:1}],
  [{ad_id:"101",imported_conversations:3,imported_inbound_messages:3}],
  [{ad_id:"101",imported_conversations:1,imported_inbound_messages:0}],
  Array(11).fill({ad_id:"101",imported_conversations:1,imported_inbound_messages:1})
]) {
  ctx.data={...ctx.data,imported_top_ads_window:rows};
  assert.throws(run,/Ungültige|Unklare/);
}
ctx.data={...ctx.data,imported_top_ads_window:[]};
run();
assert.equal(nodes["report-top-ads-body"].children.length,0);
assert.ok(nodes["report-top-ads-note"].textContent.includes("kein Null-Nachfragebeleg"));
"""
        proc=subprocess.run([shutil.which("node") or "node","-e",script],
                            input=_DASHBOARD_JS,text=True,encoding="utf-8",
                            capture_output=True,timeout=30)
        self.assertEqual(proc.returncode,0,proc.stderr)

if __name__=="__main__":
    unittest.main()
