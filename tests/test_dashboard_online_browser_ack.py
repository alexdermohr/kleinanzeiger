"""Browser regression for durable two-phase dashboard ACK of confirmed writes.

Uses a real isolated headless Chrome tab, real dashboard/write HTTP handlers,
a SQLite file and an in-memory synthetic platform. No marketplace call.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from mark_api.storage import SnapshotStore
from test_dashboard_full_lifecycle_e2e import SyntheticPlatform, servers, UI_TOKEN
from test_offline_browser_e2e import _chrome, _stop_browser, _wait_for_cdp_page

CHROME_NODE_SCRIPT = r"""
const assert = require("node:assert/strict");
const ws = new WebSocket(process.argv[1]);
const calls = new Map();
let seq = 0;
const jsErrors = [];
ws.addEventListener("message",event=>{
 const msg = JSON.parse(String(event.data));
 if(msg.id && calls.has(msg.id)){
  const {resolve,reject} = calls.get(msg.id); calls.delete(msg.id);
  msg.error ? reject(Error(msg.error.message)) : resolve(msg.result);
 }
 if(msg.method==="Runtime.exceptionThrown"){
  jsErrors.push(msg.params.exceptionDetails.text);
 }
});
const command=(method,params={})=>new Promise((resolve,reject)=>{
 const id=++seq; calls.set(id,{resolve,reject});
 ws.send(JSON.stringify({id,method,params}));
});
const evaluate=async expression=>{
 const result=await command("Runtime.evaluate",{expression,awaitPromise:true,returnByValue:true});
 if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));
 return result.result.value;
};
const eventually=async expression=>{
 for(let i=0;i<180;i++){
  if(await evaluate(expression))return;
  await new Promise(resolve=>setTimeout(resolve,150));
 }
 let state=null;
 try{
  state=await evaluate("JSON.stringify({status:document.getElementById('write-status').textContent,ids:[...latestAdsById.keys()],pending:[...pendingWrites].map(([k,v])=>({scope:k,key:v.key,acknowledgeOnly:v.acknowledgeOnly})),createValue:document.getElementById('create-title').value})");
 }catch(err){state="diagnostic failure: "+String(err);}
 throw Error("Chrome dashboard write did not finalize: "+expression+"; state="+state);
};
(async()=>{
 await new Promise((resolve,reject)=>{
  ws.addEventListener("open",resolve,{once:true});
  ws.addEventListener("error",reject,{once:true});
 });
 await command("Page.enable");
 await command("Runtime.enable");
 await command("Page.navigate",{url:process.argv[2]});
 await eventually("typeof writeUiReady==='function' && writeUiReady() && !document.getElementById('write-panel').hidden && document.getElementById('ads-body').textContent.includes('Original')");
 assert.equal(await evaluate("location.hash"),"");
 assert.equal(await evaluate("document.getElementById('create-submit').disabled"),false);
 await evaluate("document.getElementById('create-disclosure').open=true");
 await evaluate("(()=>{const fields={'create-category':'Freizeit > Sammeln','create-title':'Synthetic Chrome ACK','create-description':'Real UI, no real marketplace','create-price':'10'};for(const [id,value] of Object.entries(fields)){const e=document.getElementById(id);e.value=value;e.dispatchEvent(new Event('input',{bubbles:true}));}return true})()");
 assert.equal(await evaluate("document.getElementById('create-form').checkValidity()"),true);
 await evaluate("document.getElementById('create-submit').click()");
 await eventually("latestAdsById.has('315') && latestAdsById.get('315').title==='Synthetic Chrome ACK' && pendingWrites.size===0 && document.getElementById('create-title').value===''");
 assert.deepEqual(jsErrors,[]);
 console.log("REAL_CHROME_TWO_PHASE_WRITE_ACK_PASS");
 await command("Browser.close");
})().catch(error=>{console.error("REAL_CHROME_TWO_PHASE_WRITE_ACK_FAIL",error);ws.close();process.exitCode=1;});
"""


@unittest.skipUnless(_chrome() and shutil.which("node"), "isolated Chrome and Node required")
class BrowserWriteAckLifecycleTests(unittest.TestCase):
    def test_confirmed_create_clears_durable_two_phase_ack_after_actual_button_click(self):
        with tempfile.TemporaryDirectory(prefix="kleinanzeiger-online-write-ack-") as directory:
            root = Path(directory)
            store = SnapshotStore(root / "test.sqlite")
            platform = SyntheticPlatform()
            store.append_ad_snapshot(platform.ads["314"])
            browser = None
            output = (root / "chrome.log").open("wb")
            try:
                with servers(platform, store) as base:
                    opener = build_opener(ProxyHandler({}))
                    page = None
                    failures = []
                    for attempt in range(2):
                        profile = root / ("chrome-profile-" + str(attempt))
                        args = [
                            _chrome(), "--headless=new", "--no-first-run",
                            "--no-default-browser-check", "--disable-background-networking",
                            "--disable-sync", "--disable-component-update", "--disable-default-apps",
                            "--disable-extensions", "--metrics-recording-only",
                            "--remote-debugging-address=127.0.0.1",
                            "--remote-debugging-port=0",
                            "--user-data-dir=" + str(profile),
                            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1",
                            "about:blank",
                        ]
                        browser = subprocess.Popen(
                            args, stdout=output, stderr=output,
                            start_new_session=os.name != "nt",
                            creationflags=(
                                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                            ),
                        )
                        page, error = _wait_for_cdp_page(profile, browser, opener)
                        if page is not None:
                            break
                        failures.append(error)
                        _stop_browser(browser)
                    self.assertIsNotNone(page, f"Chrome CDP unavailable: {failures}")
                    result = subprocess.run(
                        [
                            shutil.which("node"), "-e", CHROME_NODE_SCRIPT,
                            page["webSocketDebuggerUrl"], base + "/#write_token=" + UI_TOKEN,
                        ],
                        capture_output=True, text=True, encoding="utf-8", timeout=70,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("REAL_CHROME_TWO_PHASE_WRITE_ACK_PASS", result.stdout)
                    self.assertEqual(platform.effects, ["create"])
                    self.assertEqual(platform.backend_requests, [("POST", "/api/write/ads")])
                    self.assertEqual(store.dashboard_pending_writes(), ())
                    self.assertEqual(platform.ads["315"].title, "Synthetic Chrome ACK")
            finally:
                _stop_browser(browser)
                output.close()
