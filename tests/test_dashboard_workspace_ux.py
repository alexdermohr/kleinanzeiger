"""User-facing dashboard layout and card selection, without external writes."""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest

from mark_api.dashboard import _DASHBOARD_HTML, _DASHBOARD_CSS, _DASHBOARD_JS


class DashboardWorkspaceUXTests(unittest.TestCase):
    def test_central_workspace_and_optional_sections(self) -> None:
        html = _DASHBOARD_HTML
        self.assertIn('id="workspace" class="workspace"', html)
        self.assertLess(html.index('id="ad-cards"'), html.index('id="manage-form"'))
        self.assertLess(html.index('id="manage-form"'), html.index('id="generation-panel"'))
        self.assertLess(html.index('id="manage-form"'), html.index('id="analytics-panel"'))
        self.assertIn('<details id="create-disclosure"', html)
        self.assertIn('<details id="generation-disclosure"', html)
        self.assertIn('<details id="analytics-disclosure"', html)
        self.assertIn('href="#create-disclosure"', html)
        self.assertIn('href="#analytics-panel"', html)
        self.assertIn('href="#generation-panel"', html)
        self.assertNotIn("Schreibaktionen laufen ausschließlich über die bestehende lokale Write API", html)
        self.assertNotIn("Diese standalone Dashboard-Instanz ist read-only", html)
        self.assertIn('id="write-status"', html)
        self.assertIn('id="sync-status"', html)
        self.assertIn('id="generation-pricing-note"', html)
        self.assertIn('id="generation-consent"', html)
        self.assertIn('id="manage-delete"', html)
        self.assertIn('id="manage-ad-id" type="text" readonly', html)
        self.assertIn('id="create-media"', html)
        self.assertIn('id="groups-chart"', html)
        self.assertIn('id="ranking-chart"', html)
        self.assertIn(':focus-visible', _DASHBOARD_CSS)
        self.assertIn('@media (max-width: 640px)', _DASHBOARD_CSS)
        self.assertIn('.workspace {', _DASHBOARD_CSS)
        ids = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)), "HTML IDs must remain unique")

    def test_focused_navigation_and_contextual_actions(self) -> None:
        html = _DASHBOARD_HTML
        navigation = html.split('<nav class="quick-nav"', 1)[1].split("</nav>", 1)[0]
        primary = navigation.split('<details id="nav-more"', 1)[0]
        self.assertEqual(primary.count("<a "), 5)
        for label in ("Übersicht", "Meine Anzeigen", "Artikel &amp; KI",
                      "Interessenten", "Auswertungen"):
            self.assertIn(label, primary)
        self.assertIn('id="nav-more"', navigation)
        for link in ('id="nav-create"', 'id="nav-jobs"', 'id="nav-sales"',
                     'id="nav-report"', 'id="nav-generation"'):
            self.assertIn(link, navigation.split('<details id="nav-more"', 1)[1])
        self.assertIn('id="catalog-selection-actions"', html)
        self.assertIn('aria-label="Aktionen für ausgewählte Anzeigen" hidden', html)
        self.assertIn('id="catalog-selection-hint"', html)
        self.assertIn('actions.hidden = count === 0 && !catalogBulkBusy', _DASHBOARD_JS)
        self.assertIn('hint.hidden = count <= 10 || !writeUiAvailable', _DASHBOARD_JS)
        self.assertIn('.nav-more-links { position: absolute;', _DASHBOARD_CSS)
        self.assertIn('.catalog-selection-actions:not([hidden]) { position: sticky;', _DASHBOARD_CSS)
        self.assertNotIn('.quick-nav a:first-child', _DASHBOARD_CSS)

    @unittest.skipUnless(shutil.which("node"), "Node.js needed for browser-JS behavior")
    def test_cards_bind_only_loaded_own_ads_and_keep_selection_safe(self) -> None:
        harness = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Node {
  constructor(tag="") {
    this.tag=tag; this.children=[]; this.value=""; this.textContent="";
    this.hidden=false; this.className=""; this.listeners={};
  }
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children=[...nodes];}
  addEventListener(name,callback) {this.listeners[name]=callback;}
  scrollIntoView() {}
}
const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id,new Node(id));
    return elements.get(id);
  },
  createElement(tag) {return new Node(tag);},
  querySelectorAll() {return [];},
};
const window = {addEventListener(){}};
const code = fs.readFileSync(0,"utf8").replace(/\r\n/g,"\n");
const end = code.indexOf("\nconsumeWriteToken();\nload();");
assert.ok(end > 0);
const context={document,window,console,Date,URL,URLSearchParams};
vm.createContext(context);
vm.runInContext(code.slice(0,end),context);
vm.runInContext('writeUiAvailable = true; writeToken = "z".repeat(32);',context);
const ads = [
 {ad_id:"12",title:"Anzeigentitel",description:"Beschreibung",lifecycle_state:"active",present:true,views:19},
 {ad_id:"13",title:"Alte Anzeige",description:"alt",lifecycle_state:"absent",present:false,views:2},
 {ad_id:"14",title:"Weiterer Artikel",description:"Zwei",lifecycle_state:"paused",present:true,views:7}
];
context.ads=ads;
vm.runInContext("renderAds(ads)",context);
const cards=elements.get("ad-cards");
assert.equal(cards.children.length,3);
assert.equal(cards.children[0].children[0].children[0].children[0].textContent,"Anzeigentitel");
assert.equal(cards.children[2].children[0].children[0].children[0].textContent,"Alte Anzeige");
const firstAction=cards.children[0].children[1].children.find(c=>c.tag==="button");
assert.equal(firstAction.textContent,"Bearbeiten");
firstAction.listeners.click();
assert.equal(elements.get("manage-ad-id").value,"12");
assert.equal(elements.get("manage-title").value,"Anzeigentitel");
assert.equal(cards.children[0].className,"ad-card selected");
assert.equal(cards.children[1].className,"ad-card");
const oldAction=cards.children[2].children[1].children.find(c=>c.tag==="button");
assert.equal(oldAction,undefined);
context.ads=[ads[1],ads[2]];
vm.runInContext("renderAds(ads)",context);
assert.equal(elements.get("manage-ad-id").value,"");
assert.equal(elements.get("manage-ad-select").value,"");
assert.equal(elements.get("ad-cards").children.length,2);
context.ads=[];
vm.runInContext("renderAds(ads)",context);
assert.equal(elements.get("ad-cards-empty").hidden,false);
assert.equal(elements.get("manage-ad-id").value,"");
vm.runInContext("writeUiAvailable = false; renderWriteAvailability()",context);
assert.equal(elements.get("nav-create").hidden,true);
context.ads=[ads[0]];
vm.runInContext("renderAds(ads)",context);
assert.equal(cards.children[0].children[1].children.filter(c=>c.tag==="button").length,0);
"""
        result = subprocess.run(
            [shutil.which("node") or "node", "-e", harness],
            input=_DASHBOARD_JS, text=True, capture_output=True, timeout=20, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
