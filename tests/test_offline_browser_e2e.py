"""Real headless Chrome -> shipped dashboard -> HTTP -> SQLite, using synthetic inventory only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import time
import unittest
from urllib.request import ProxyHandler, build_opener

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.launcher import build_product_launcher
from mark_api.portfolio_drafts import import_drafts, preview_import
from mark_api.portfolio_price_rules import price_rule_detail
from mark_api.results import ReadResult, ReadStatus
from mark_api.storage import SnapshotStore


def _chrome():
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / "Google/Chrome/Application/chrome.exe"
            if candidate.is_file():
                return str(candidate)
    return None



def _stop_browser(process):
    """End only the isolated test Chrome process group."""
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _wait_for_cdp_page(profile, process, opener, *, timeout_seconds=25):
    """A port file can precede HTTP readiness; neither event proves the other."""
    port_file = profile / "DevToolsActivePort"
    until = time.monotonic() + timeout_seconds
    last_error = "port file not created"
    while time.monotonic() < until:
        if process.poll() is not None:
            return None, f"Chrome exited with code {process.returncode}"
        if port_file.is_file():
            try:
                port = int(port_file.read_text(encoding="ascii").splitlines()[0])
                if not 1 <= port <= 65535:
                    raise ValueError("invalid DevTools port")
                with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
                    pages = json.load(response)
                for page in pages:
                    if (page.get("type") == "page" and page.get("url") == "about:blank"
                            and page.get("webSocketDebuggerUrl")):
                        return page, None
                last_error = "Chrome has no ready about:blank page target"
            except (OSError, ValueError, LookupError, TypeError) as error:
                last_error = type(error).__name__
        time.sleep(0.1)
    return None, last_error


class ChromeCdpReadinessTests(unittest.TestCase):
    def test_port_file_is_not_http_readiness_and_transient_timeout_retries(self):
        from io import BytesIO

        class Running:
            returncode = None
            def poll(self):
                return None

        class DelayedEndpoint:
            calls = 0
            def open(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError("not ready")
                return BytesIO(json.dumps([{
                    "type": "page", "url": "about:blank", "webSocketDebuggerUrl": "ws://localhost/test",
                }]).encode("utf-8"))

        with TemporaryDirectory() as directory:
            profile = Path(directory)
            (profile / "DevToolsActivePort").write_text("9223\n", encoding="ascii")
            endpoint = DelayedEndpoint()
            page, error = _wait_for_cdp_page(profile, Running(), endpoint, timeout_seconds=1)
            self.assertIsNone(error)
            self.assertEqual(page["type"], "page")
            self.assertEqual(endpoint.calls, 2)

    def test_exited_browser_is_not_mistaken_for_success(self):
        class Exited:
            returncode = 22
            def poll(self):
                return 22

        with TemporaryDirectory() as directory:
            page, error = _wait_for_cdp_page(Path(directory), Exited(), None, timeout_seconds=1)
        self.assertIsNone(page)
        self.assertIn("22", error)


@unittest.skipUnless(_chrome() and shutil.which("node"), "Chrome and Node required for real browser acceptance")
class OfflineBrowserAcceptanceTests(unittest.TestCase):
    def test_cached_work_price_rule_diff_and_explicit_reconnection_through_real_browser(self):
        with TemporaryDirectory(prefix="kleinanzeiger-browser-offline-") as directory:
            root = Path(directory)
            store = SnapshotStore(root / "customer.sqlite")
            old = datetime.now(timezone.utc) - timedelta(hours=1)
            snapshot = AdSnapshot(ad_id="10001", title="Gespeicherte Lampe", source="kleinanzeigen-management",
                                  observed_at=old, lifecycle_state=LifecycleState.ACTIVE)
            attempt = store.begin_sync_attempt(source="private-web-product-launcher", started_at=old)
            store.append_inventory_result(ReadResult.success_nonempty((snapshot,)),
                                          observed_at=old, source="private-web-product-launcher",
                                          attempt_id=attempt, completed_at=old)
            articles = root / "articles.csv"
            articles.write_text("sku,title,description,category,price_eur\nSKU-1,Lampe,Gebraucht,Haus > Leuchten,50\n", encoding="utf-8")
            preview = preview_import(store, articles)
            import_drafts(store, articles, preview["sha256"])
            reads = []

            class Inventory:
                def read_inventory(self, *, stop_requested=None):
                    reads.append(True)
                    if len(reads) == 1:
                        return ReadResult.failure(ReadStatus.TRANSPORT_ERROR)
                    fresh = AdSnapshot(ad_id="10001", title="Frisch bestaetigte Lampe",
                                       source="kleinanzeigen-management", observed_at=datetime.now(timezone.utc),
                                       lifecycle_state=LifecycleState.ACTIVE)
                    return ReadResult.success_nonempty((fresh,))

                def close(self):
                    pass

            def denied_writer(**_kwargs):
                raise AssertionError("offline customer journey must never construct a platform writer")

            runtime = build_product_launcher(
                db_path=store.path, cdp_port=9222, dashboard_port=0, write_port=0,
                allow_offline=True, runtime_factory=lambda **kw: Inventory(),
                write_runtime_factory=denied_writer,
            )
            thread = Thread(target=runtime.serve_forever, daemon=True)
            thread.start()
            log = (root / "chrome.log").open("wb")
            process = None
            try:
                opener = build_opener(ProxyHandler({}))
                page = None
                failures = []
                # A busy CI host can exit Chrome before binding DevTools or
                # expose DevToolsActivePort before /json/list actually responds.
                # Retry only the browser bootstrap, never the product writes.
                for launch_attempt in range(2):
                    profile = root / f"isolated-browser-{launch_attempt}"
                    flags = [
                        _chrome(), "--headless=new", "--no-first-run", "--no-default-browser-check",
                        "--disable-background-networking", "--disable-sync", "--disable-component-update",
                        "--disable-default-apps", "--disable-extensions", "--metrics-recording-only",
                        "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                        "--user-data-dir=" + str(profile),
                        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1",
                        "about:blank",
                    ]
                    process = subprocess.Popen(
                        flags, stdout=log, stderr=log,
                        start_new_session=os.name != "nt",
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                    )
                    page, error = _wait_for_cdp_page(profile, process, opener)
                    if page is not None:
                        break
                    failures.append(error)
                    _stop_browser(process)
                self.assertIsNotNone(page, f"isolated Chrome DevTools never became ready: {failures}")
                harness = r'''
const assert = require("node:assert/strict");
const ws = new WebSocket(process.argv[1]);
let sequence=0; const pending=new Map(); const errors=[];
ws.addEventListener("message", event=>{
  const message=JSON.parse(String(event.data));
  if (message.id && pending.has(message.id)) {
    const {resolve,reject}=pending.get(message.id); pending.delete(message.id);
    message.error ? reject(Error(message.error.message)) : resolve(message.result);
  }
  if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails.text);
});
const command=(method,params={})=>new Promise((resolve,reject)=>{
  const id=++sequence;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));
});
const evaluate=async expression=>{
  const answer=await command("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});
  if (answer.exceptionDetails) throw Error(JSON.stringify(answer.exceptionDetails));
  return answer.result.value;
};
const eventually=async expression=>{
  for(let i=0;i<150;i++) {
    if(await evaluate(expression)) return;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw Error("Timed out waiting for "+expression);
};
(async()=>{
  await new Promise((resolve,reject)=>{ws.addEventListener("open",resolve,{once:true});ws.addEventListener("error",reject,{once:true});});
  await command("Page.enable");await command("Runtime.enable");
  await command("Page.navigate",{url:process.argv[2]});
  await eventually("document.getElementById('offline-notice') && !document.getElementById('offline-notice').hidden && typeof jobsAvailable !== 'undefined' && jobsAvailable");
  assert.equal(await evaluate("writeUiReady()"),false);
  assert.equal(await evaluate("localUiReady()"),true);
  assert.equal(await evaluate("document.getElementById('manage-save').disabled"),true);
  assert.equal(await evaluate("document.getElementById('create-submit').disabled"),true);
  await eventually("document.getElementById('ads-body').textContent.includes('Gespeicherte Lampe')");
  await evaluate("document.getElementById('drafts-disclosure').open=true; document.getElementById('draft-find').click()");
  await eventually("[...document.querySelectorAll('#drafts-body button')].some(button=>button.textContent==='Preisregel')");
  await evaluate("[...document.querySelectorAll('#drafts-body button')].find(button=>button.textContent==='Preisregel').click()");
  await eventually("document.getElementById('price-rule-original').textContent.includes('50 €')");
  await evaluate("document.getElementById('price-rule-min').value='40'; document.getElementById('price-rule-step').value='20'; document.getElementById('price-rule-wait').value='0'; document.getElementById('price-rule-min').dispatchEvent(new Event('input',{bubbles:true}))");
  assert.match(await evaluate("document.getElementById('price-rule-preview').textContent"),/50 € → 40 €/);
  assert.equal(await evaluate("document.getElementById('price-rule-save').disabled"),false);
  await evaluate("document.getElementById('price-rule-save').click()");
  await eventually("document.getElementById('price-rule-body').textContent.includes('40 €')");
  await evaluate("document.getElementById('inventory-diff-load').click()");
  await eventually("document.getElementById('inventory-diff-status').textContent.includes('Kein aktueller')");
  assert.equal(await evaluate("document.getElementById('sync-now').disabled"),false);
  await evaluate("document.getElementById('sync-now').click()");
  await eventually("document.getElementById('ads-body').textContent.includes('Frisch bestaetigte Lampe')");
  assert.equal(await evaluate("writeUiReady()"),false);
  assert.equal(await evaluate("document.getElementById('manage-save').disabled"),true);
  await evaluate("document.getElementById('inventory-diff-load').click()");
  await eventually("document.getElementById('inventory-diff-body').textContent.includes('Geändert')");
  assert.equal(await evaluate("document.getElementById('offline-notice').hidden"),false);
  assert.deepEqual(errors,[]);
  await command("Browser.close");
  console.log("REAL_BROWSER_OFFLINE_LOCAL_RULE_DIFF_RESYNC_NO_PLATFORM_WRITE_PASS");
})().catch(error=>{console.error(error);ws.close();process.exitCode=1;});
'''
                result = subprocess.run([shutil.which("node"), "-e", harness,
                                         page["webSocketDebuggerUrl"], runtime.dashboard_url],
                                        capture_output=True, text=True, encoding="utf-8", timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("REAL_BROWSER_OFFLINE_LOCAL_RULE_DIFF_RESYNC_NO_PLATFORM_WRITE_PASS", result.stdout)
                self.assertEqual(len(reads), 2, "no automatic inventory retries")
                self.assertEqual(len(store.ad_history("10001")), 2)
                self.assertFalse(runtime.writes_enabled)
                detail = price_rule_detail(store, sku="SKU-1")
                self.assertEqual(detail["local_price_eur"], 50)
                self.assertEqual(detail["rule"]["suggested_price_eur"], 40)
                self.assertEqual(detail["rule"]["platform_writes"], 0)
            finally:
                _stop_browser(process)
                log.close()
                runtime.close()
                thread.join(5)


if __name__ == "__main__":
    unittest.main()