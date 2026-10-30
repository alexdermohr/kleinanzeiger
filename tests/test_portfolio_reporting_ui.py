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

if __name__=="__main__":
    unittest.main()
