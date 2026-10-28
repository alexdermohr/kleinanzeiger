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
let saved=null,clicks=0,appends=0;
class BlobCapture{
 constructor(parts,opts){saved=parts.join("");assert.equal(opts.type,"text/csv;charset=utf-8");}
}
const ctx={
 reportData:{window_days:7,imported_messages_per_day:[
   {day_utc:"2026-10-25",imported_messages:0},
   {day_utc:"2026-10-26",imported_messages:3}]},
 byId:()=>({value:"7"}),
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
saved=null;
ctx.reportData.imported_messages_per_day=[{day_utc:"=HYPERLINK",imported_messages:1}];
vm.runInNewContext(code+";downloadPortfolioReportCsv()",ctx);
assert.equal(clicks,1,"formulas must abort export");
assert.equal(saved,null);
"""
        node=shutil.which("node") or "node"
        proc=subprocess.run([node,"-e",script],input=_DASHBOARD_JS,
            text=True, encoding="utf-8",capture_output=True,timeout=30)
        self.assertEqual(proc.returncode,0,proc.stderr)

if __name__=="__main__":
    unittest.main()
