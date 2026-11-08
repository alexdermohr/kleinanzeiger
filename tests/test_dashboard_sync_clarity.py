"""Customer-facing sync evidence and contextual connection help, no platform access."""
from __future__ import annotations

import shutil
import subprocess
import unittest

from mark_api.dashboard import _DASHBOARD_HTML, _DASHBOARD_JS


class DashboardSyncClarityTests(unittest.TestCase):
    def test_prominent_status_exists_without_replacing_detailed_evidence(self):
        self.assertIn('id="last-confirmed-sync"', _DASHBOARD_HTML)
        self.assertIn('id="connection-help"', _DASHBOARD_HTML)
        self.assertIn('id="sync-status"', _DASHBOARD_HTML)
        self.assertLess(_DASHBOARD_HTML.index('id="last-confirmed-sync"'),
                        _DASHBOARD_HTML.index('id="sync-status"'))
        self.assertIn("last_successful_attempt", _DASHBOARD_JS)
        self.assertNotIn("document.cookie", _DASHBOARD_JS)
        self.assertIn('renderConnectionHelp(!writeUiAvailable && !localWorkAvailable', _DASHBOARD_JS)
        self.assertIn('id="offline-notice"', _DASHBOARD_HTML)

    @unittest.skipUnless(shutil.which("node"), "Node required for shipped JavaScript")
    def test_sync_time_tracks_last_confirmed_not_last_failed_attempt(self):
        source = "function ageDescription(" + _DASHBOARD_JS.split(
            "function ageDescription(", 1)[1].split("function metricCellWithEvidence(", 1)[0]
        node = r"""
const assert=require("node:assert/strict");
const vm=require("node:vm");
const source=require("node:fs").readFileSync(0,"utf8");
const elements=new Map();
const byId=id=>{
 if(!elements.has(id))elements.set(id,{textContent:"",className:""});
 return elements.get(id);
};
const ctx={byId,optionalById:byId,manualSyncAvailable:false,localReady:false};
ctx.localUiReady=()=>ctx.localReady;
vm.createContext(ctx);vm.runInContext(source,ctx);
const render=(sync,recovery)=>ctx.renderSyncStatus(sync,recovery);
render({state:"never_attempted",latest_attempt:null,last_successful_attempt:null},
  {unresolved_count:0});
assert.match(byId("last-confirmed-sync").textContent,/Noch kein erfolgreicher Kontoabgleich/);
assert.match(byId("last-confirmed-sync").textContent,/Kontoabgleich ist in dieser Sitzung nicht verfügbar/);
assert.doesNotMatch(byId("last-confirmed-sync").textContent,/Im Kleinanzeiger-Chrome anmelden/);
ctx.manualSyncAvailable=true;
render({state:"never_attempted",latest_attempt:null,last_successful_attempt:null},
  {unresolved_count:0});
assert.match(byId("last-confirmed-sync").textContent,/Kontoabgleich ist in dieser Sitzung nicht verfügbar/);
assert.doesNotMatch(byId("last-confirmed-sync").textContent,/Im Kleinanzeiger-Chrome anmelden/);
ctx.localReady=true;
render({state:"never_attempted",latest_attempt:null,last_successful_attempt:null},
  {unresolved_count:0});
assert.match(byId("last-confirmed-sync").textContent,/Im Kleinanzeiger-Chrome anmelden/);
ctx.manualSyncAvailable=false;
render(null, null);
assert.match(byId("last-confirmed-sync").textContent,/derzeit nicht abrufbar/);
const last={completed_at:"2026-11-07T11:45:00+01:00",source:"owner_inventory",snapshot_count:3};
render({state:"failed",latest_attempt:{started_at:"2026-11-07T12:30:00+01:00",
  completed_at:"2026-11-07T12:32:00+01:00",error_kind:"login_required",source:"owner_inventory"},
  last_successful_attempt:last},{unresolved_count:0});
const shown=byId("last-confirmed-sync").textContent;
assert.match(shown,/Letzter bestätigter Kontoabgleich:/);
assert.match(shown,/2026/);
assert.match(shown,/letzte Versuch ist fehlgeschlagen/);
assert.equal(byId("sync-overview").className,"section-label warning");
assert.match(byId("sync-status").textContent,/Letzter Sync fehlgeschlagen/);
render({state:"success_empty",latest_attempt:{...last,completed_at:"2026-11-07T12:45:00+01:00"},
 last_successful_attempt:{...last,completed_at:"2026-11-07T12:45:00+01:00"}},
 {unresolved_count:0});
assert.match(byId("sync-status").textContent,/bestätigter leerer Besitzerbestand/);
assert.doesNotMatch(byId("last-confirmed-sync").textContent,/letzte Versuch ist fehlgeschlagen/);
render({state:"failed",latest_attempt:{...last,error_kind:"read_failed"},
 last_successful_attempt:last},{unresolved_count:1,oldest_unresolved_attempt:{id:12}});
assert.match(byId("last-confirmed-sync").textContent,/Offene Abgleichversuche prüfen/);
render({state:"failed",latest_attempt:{...last,error_kind:"read_failed"},
 last_successful_attempt:{...last,completed_at:"invalid-date"}},{unresolved_count:0});
assert.match(byId("last-confirmed-sync").textContent,/Noch kein erfolgreicher Kontoabgleich/);
assert.doesNotMatch(byId("last-confirmed-sync").textContent,/Invalid Date/);
console.log("SYNC_FRESHNESS_DOM_PASS");
"""
        result = subprocess.run([shutil.which("node"), "-e", node], input=source,
            text=True, encoding="utf-8", capture_output=True, timeout=18)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SYNC_FRESHNESS_DOM_PASS", result.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node required for shipped JavaScript")
    def test_connection_help_matches_login_offline_and_read_only_conditions(self):
        source = "const connectionHelpMessages" + _DASHBOARD_JS.split(
            "const connectionHelpMessages", 1)[1].split("async function syncNow()", 1)[0]
        node = r"""
const assert=require("node:assert/strict");
const vm=require("node:vm");
const source=require("node:fs").readFileSync(0,"utf8");
const help={hidden:true,textContent:""};
const ctx={optionalById:id=>id==="connection-help"?help:null};
vm.createContext(ctx);vm.runInContext(source,ctx);
const check=(kind,word)=>{
 ctx.renderConnectionHelp(kind);
 assert.equal(help.hidden,false,kind);
 assert.match(help.textContent,new RegExp(word),kind);
};
check("owner_inventory_login_required","anmelden");
check("owner_inventory_connection_unavailable","Internet");
check("owner_inventory_browser_unavailable","Chrome");
check("read_only","Kleinanzeiger.cmd");
check("restart","erneut geprüft");
ctx.renderConnectionHelp(null);assert.equal(help.hidden,true);
ctx.renderConnectionHelp("__unrecognized__");assert.equal(help.hidden,true);
console.log("CONNECTION_GUIDANCE_DOM_PASS");
"""
        result = subprocess.run([shutil.which("node"), "-e", node], input=source,
            text=True, encoding="utf-8", capture_output=True, timeout=18)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CONNECTION_GUIDANCE_DOM_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
