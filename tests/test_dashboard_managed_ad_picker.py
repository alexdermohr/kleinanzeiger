"""Ensure the Windows product dashboard can select synced ads without typing IDs."""
from __future__ import annotations

import shutil
import subprocess
import unittest

from mark_api.dashboard import _DASHBOARD_HTML, _DASHBOARD_JS


class DashboardManagedAdPickerTests(unittest.TestCase):
    def test_selection_is_explicit_and_ids_remain_readonly(self) -> None:
        self.assertIn('<select id="manage-ad-select" data-write-control>', _DASHBOARD_HTML)
        self.assertIn('<input id="manage-ad-id" type="text" readonly>', _DASHBOARD_HTML)
        self.assertIn('Vorhandene Anzeige auswählen', _DASHBOARD_HTML)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for browser-JS behavior")
    def test_picker_renders_live_ads_and_binds_only_known_ids(self) -> None:
        harness = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");

class FakeNode {
  constructor(tag = "") {
    this.tag = tag;
    this.children = [];
    this.value = "";
    this.textContent = "";
    this.listeners = {};
    this.hidden = false;
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  addEventListener(event, callback) { this.listeners[event] = callback; }
  scrollIntoView() {}
}
const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeNode(id));
    return elements.get(id);
  },
  createElement: (tag) => new FakeNode(tag),
  querySelectorAll: () => [],
};
const window = {addEventListener() {}};
const source = fs.readFileSync(0, "utf8").replace(/\r\n/g, "\n");
const end = source.indexOf("\nconsumeWriteToken();\nload();");
assert.ok(end > 0, "Dashboard setup marker must exist");

const context = {document, window, console, Date, URL, URLSearchParams};
vm.createContext(context);
vm.runInContext(source.slice(0, end), context);
vm.runInContext('writeUiAvailable = true; writeToken = "z".repeat(32)', context);
const testAds = [
  {ad_id: "123", title: "Aktive Anzeige", description: "Gebraucht", lifecycle_state: "active", present: true},
  {ad_id: "456", title: "Entfernte Anzeige", description: "Nicht aktuell", lifecycle_state: "absent", present: false}
];
context.testAds = testAds;
vm.runInContext("renderAds(testAds)", context);
const picker = elements.get("manage-ad-select");
assert.equal(picker.children.length, 2);
assert.equal(picker.children[0].value, "");
assert.equal(picker.children[1].value, "123");
assert.match(picker.children[1].textContent, /Aktive Anzeige.*123/);
assert.equal(elements.get("manage-ad-id").value, "");

picker.value = "123";
picker.listeners.change();
assert.equal(elements.get("manage-ad-id").value, "123");
assert.equal(elements.get("manage-title").value, "Aktive Anzeige");
assert.equal(elements.get("manage-description").value, "Gebraucht");
assert.equal(elements.get("manage-state").textContent, "active");

picker.value = "456";
picker.listeners.change();
assert.equal(elements.get("manage-ad-id").value, "");
assert.equal(picker.value, "");

picker.value = "123";
picker.listeners.change();
vm.runInContext("renderAds(testAds)", context);
assert.equal(picker.value, "123");
assert.equal(elements.get("manage-ad-id").value, "123");

testAds[0].present = false;
vm.runInContext("renderAds(testAds)", context);
assert.equal(picker.value, "");
assert.equal(elements.get("manage-ad-id").value, "");
assert.equal(elements.get("manage-title").value, "");

vm.runInContext('pendingWrites.set("ad:456:update", {adId: "456", scope: "ad:456:update"})', context);
vm.runInContext("renderAds(testAds)", context);
assert.equal(picker.children.length, 2);
assert.equal(picker.children[1].value, "456");
picker.value = "456";
picker.listeners.change();
assert.equal(elements.get("manage-ad-id").value, "456");

vm.runInContext('pendingWrites.clear()', context);
vm.runInContext("renderAds(testAds)", context);
assert.equal(picker.value, "");
assert.equal(elements.get("manage-ad-id").value, "");
"""
        result = subprocess.run(
            [shutil.which("node") or "node", "-e", harness],
            input=_DASHBOARD_JS,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()