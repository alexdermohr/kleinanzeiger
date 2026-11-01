"""One-click owner inventory sync without platform writes or hidden retries."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

from mark_api.dashboard import (
    DashboardWriteProxy, _DASHBOARD_HTML, _DASHBOARD_JS, create_server,
)
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.private_web_runtime import (
    OwnerInventorySyncFailure, PrivateWebRuntimeSetupError, PrivateWebWriteApiRuntime,
)
from mark_api.results import ReadResult, ReadStatus
from mark_api.storage import SnapshotStore



class DashboardManualSyncJavaScriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node required for shipped JavaScript")
    def test_offline_sync_updates_local_view_without_unlocking_platform_writes(self):
        # Execute the shipped controller with only transport and local reload mocked.
        readiness = _DASHBOARD_JS.split("function writeUiReady() {", 1)[1].split(
            "function validPendingEntry", 1)[0]
        controller = _DASHBOARD_JS.split("const syncFailureHints =", 1)[1].split(
            "function renderJobsAvailability", 1)[0]
        source = "function writeUiReady() {" + readiness + "const syncFailureHints =" + controller
        harness = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const source = require("node:fs").readFileSync(0, "utf8");
const status = {textContent:""}, button = {disabled:false};
const requests = [];
let response = {ok:true,json:async()=>({status:"success",current_ads:7})};
let reloaded = true, reloads = 0;
const context = {
  manualSyncAvailable:true, syncInFlight:false, localWorkAvailable:true,
  writeUiAvailable:false, writeToken:"fixture-local-token-0123456789",
  pendingRecoveryBlocked:false,
  window:{location:{origin:"http://127.0.0.1:12345"}},
  byId:id=>id === "status" ? status : button,
  fetch:async(path,options)=>{requests.push({path,options});return response;},
  load:async()=>{reloads++;return reloaded;},
};
vm.createContext(context);
vm.runInContext(source, context);
const run = expression => vm.runInContext(expression, context);
(async()=>{
  assert.equal(run("writeUiReady()"),false);
  await run("syncNow()");
  assert.equal(reloads,1);
  assert.match(status.textContent,/7 Anzeigen.*Lokale Anzeige aktualisiert/);
  assert.match(status.textContent,/Plattformänderungen bleiben bis zum kontrollierten Neustart.*gesperrt/);
  assert.equal(run("writeUiReady()"),false);
  assert.equal(context.writeUiAvailable,false);
  assert.equal(context.localWorkAvailable,true);
  assert.equal(button.disabled,false);
  // A failed local reload must never claim an updated view.
  reloaded=false;
  await run("syncNow()");
  assert.match(status.textContent,/lokale Ansicht konnte nicht neu geladen werden/);
  assert.doesNotMatch(status.textContent,/Lokale Anzeige aktualisiert/);
  // A failed inventory read keeps the failure message and performs no reload.
  response={ok:false,json:async()=>({error:"owner_inventory_read_failed"})};
  await run("syncNow()");
  assert.equal(reloads,2);
  assert.match(status.textContent,/Abgleich fehlgeschlagen.*Vorhandene Anzeigen bleiben gespeichert/);
  assert.equal(run("writeUiReady()"),false);
  assert.equal(requests.length,3);
  assert.ok(requests.every(r=>r.path === "/api/dashboard/sync" && r.options.method === "POST"));
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which("node"), "-e", harness],
                                input=source, text=True, encoding="utf-8",
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class DashboardManualSyncHttpTests(unittest.TestCase):
    TOKEN = "fixture-user-session-token-0123456789"
    BACKEND = "server-only-token-0123456789"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SnapshotStore(Path(self.tmp.name) / "items.sqlite")
        self.calls = []
        proxy = DashboardWriteProxy(
            host="127.0.0.1", port=19080,
            bearer_token=self.BACKEND, ui_token=self.TOKEN,
        )
        self.server = create_server(
            self.store, port=0, write_proxy=proxy,
            sync_once=lambda: self.calls.append("once") or 1,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"

    def _request(self, method="POST", path="/api/dashboard/sync",
                 headers=None, body=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            connection.close()

    def auth(self):
        return {
            "Origin": self.origin,
            "X-Mark-Dashboard-Write": "1",
            "X-Mark-Dashboard-Token": self.TOKEN,
            "Sec-Fetch-Site": "same-origin",
        }

    def test_ui_distinguishes_local_reload_from_real_sync(self):
        self.assertIn('id="sync-now"', _DASHBOARD_HTML)
        self.assertIn('id="reload"', _DASHBOARD_HTML)
        self.assertIn("Ansicht neu laden", _DASHBOARD_HTML)
        self.assertIn('fetch("/api/dashboard/sync"', _DASHBOARD_JS)
        self.assertIn("owner_inventory_busy", _DASHBOARD_JS)
        self.assertIn("owner_inventory_login_required", _DASHBOARD_JS)
        self.assertIn("owner_inventory_recovery_required", _DASHBOARD_JS)

        code, body = self._request("GET", "/api/dashboard/config")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {
            "write_ui_available": True, "manual_sync_available": True,
            "portfolio_jobs_available": True,
        })

    def test_only_explicit_local_token_bound_post_triggers_one_sync(self):
        for headers, body in (
            ({}, None),
            ({**self.auth(), "X-Mark-Dashboard-Token": "invalid"}, None),
            ({**self.auth(), "Origin": "http://elsewhere.invalid"}, None),
            ({**self.auth(), "Sec-Fetch-Site": "cross-site"}, None),
            ({**self.auth(), "Authorization": "Bearer other"}, None),
        ):
            with self.subTest(headers=headers):
                status, _ = self._request(headers=headers, body=body)
                self.assertEqual(status, 403)
                self.assertEqual(self.calls, [])

        status, _ = self._request("GET", "/api/dashboard/sync", self.auth())
        self.assertEqual(status, 404)
        status, _ = self._request(headers={
            **self.auth(), "Idempotency-Key": "unsupported",
        })
        self.assertEqual(status, 400)
        status, _ = self._request(
            path="/api/dashboard/sync?extra=1", headers=self.auth(),
        )
        self.assertEqual(status, 404)
        status, _ = self._request(
            headers={**self.auth(), "Content-Type": "application/json"},
            body=b"{}",
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])

        status, body = self._request(headers=self.auth())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "success", "current_ads": 1})
        self.assertEqual(self.calls, ["once"])

    def test_classified_failures_are_sanitized_and_not_retried(self):
        for kind, expected_status in (
            ("busy", 409), ("recovery_required", 409),
            ("login_required", 503), ("browser_unavailable", 503),
            ("connection_unavailable", 503), ("storage_unavailable", 503),
            ("read_failed", 503),
        ):
            with self.subTest(kind=kind):
                calls = []
                def fails():
                    calls.append(1)
                    raise OwnerInventorySyncFailure(kind)
                proxy = DashboardWriteProxy(
                    host="127.0.0.1", port=19080,
                    bearer_token=self.BACKEND, ui_token=self.TOKEN,
                )
                server = create_server(self.store, port=0, write_proxy=proxy, sync_once=fails)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    port = server.server_address[1]
                    connection = HTTPConnection("127.0.0.1", port, timeout=5)
                    try:
                        connection.request("POST", "/api/dashboard/sync",
                            headers={**self.auth(), "Origin": f"http://127.0.0.1:{port}"})
                        response = connection.getresponse()
                        payload = response.read().decode("utf-8")
                        self.assertEqual(response.status, expected_status)
                        self.assertEqual(json.loads(payload), {"error": "owner_inventory_" + kind})
                        self.assertEqual(calls, [1])
                    finally:
                        connection.close()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=3)

    def test_sync_is_not_a_standalone_or_arbitrary_untrusted_callback(self):
        with self.assertRaises(ValueError):
            create_server(self.store, port=0, sync_once=lambda: 0)
        with self.assertRaises(ValueError):
            create_server(
                self.store, port=0, write_proxy=DashboardWriteProxy(
                    host="127.0.0.1", port=19080,
                    bearer_token=self.BACKEND, ui_token=self.TOKEN,
                ), sync_once="not-callable",
            )

    def test_sync_error_is_sanitized_and_not_retried(self):
        calls = []
        def fails():
            calls.append(1)
            raise RuntimeError("browser cookie secret")
        # Only substitute the closure's callable in this test through
        # a newly created server; don't create an accessible POST fallback.
        proxy = DashboardWriteProxy(
            host="127.0.0.1", port=19081,
            bearer_token=self.BACKEND, ui_token=self.TOKEN,
        )
        other = create_server(self.store, port=0, write_proxy=proxy, sync_once=fails)
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        try:
            port = other.server_address[1]
            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request(
                    "POST", "/api/dashboard/sync", headers={
                        **self.auth(), "Origin": f"http://127.0.0.1:{port}",
                    },
                )
                response = connection.getresponse()
                body = response.read()
                self.assertEqual(response.status, 503)
                self.assertNotIn(b"cookie secret", body)
                self.assertEqual(calls, [1])
            finally:
                connection.close()
        finally:
            other.shutdown()
            other.server_close()
            thread.join(timeout=3)


class OwnerManualSyncRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SnapshotStore(Path(self.tmp.name) / "snapshots.sqlite")

    def runtime(self, result):
        class Reader:
            _cdp_session = SimpleNamespace(port=19555, timeout_seconds=5.0)
            def __init__(self):
                self.calls = 0
            def read_inventory(self):
                self.calls += 1
                return result
        reader = Reader()
        writer = PrivateWebWriteApiRuntime(
            server=SimpleNamespace(server_address=("127.0.0.1", 19082)),
            store=self.store, content_runtime=reader,
            confirmation_runtime=None, media_runtime=None,
            mark_service=None, media_service=None, media_handle_store=None,
            operation_lock=RLock(),
        )
        return writer, reader

    def test_confirmed_owner_read_is_persisted_once(self):
        ad = AdSnapshot(
            ad_id="12345678", title="Testanzeige",
            observed_at=datetime.now(timezone.utc),
            source="synthetic-owner", lifecycle_state=LifecycleState.ACTIVE,
            views=11,
        )
        runtime, reader = self.runtime(ReadResult.success_nonempty((ad,)))
        self.assertEqual(runtime.sync_owner_inventory(), 1)
        self.assertEqual(reader.calls, 1)
        self.assertEqual(self.store.tracked_ad_ids(), ("12345678",))
        self.assertEqual(self.store.latest_sync_attempt().outcome, "success_nonempty")
        self.assertEqual(self.store.sync_recovery_status()["unresolved_count"], 0)

    def test_failed_read_keeps_old_data_and_fails_closed(self):
        ad = AdSnapshot(
            ad_id="12345678", observed_at=datetime.now(timezone.utc),
            source="synthetic-owner", lifecycle_state=LifecycleState.ACTIVE,
        )
        self.store.append_ad_snapshot(ad)
        runtime, reader = self.runtime(
            ReadResult.failure(ReadStatus.TRANSPORT_ERROR),
        )
        with self.assertRaises(OwnerInventorySyncFailure) as caught:
            runtime.sync_owner_inventory()
        self.assertEqual(caught.exception.kind, "connection_unavailable")
        self.assertEqual(reader.calls, 1)
        self.assertEqual(self.store.tracked_ad_ids(), ("12345678",))
        self.assertNotIn(
            self.store.latest_sync_attempt().outcome,
            ("success_empty", "success_nonempty"),
        )

    def test_unauthenticated_read_is_distinguished_from_transport_errors(self):
        runtime, reader = self.runtime(ReadResult.failure(ReadStatus.UNAUTHENTICATED))
        with self.assertRaises(OwnerInventorySyncFailure) as caught:
            runtime.sync_owner_inventory()
        self.assertEqual(caught.exception.kind, "login_required")
        self.assertEqual(reader.calls, 1)

    def test_existing_unknown_sync_blocks_another_platform_read(self):
        self.store.begin_sync_attempt(
            source="prior-unknown", started_at=datetime.now(timezone.utc),
        )
        runtime, reader = self.runtime(ReadResult.success_empty(()))
        with self.assertRaisesRegex(PrivateWebRuntimeSetupError, "reconciliation"):
            runtime.sync_owner_inventory()
        self.assertEqual(reader.calls, 0)

    def test_busy_writer_prevents_competing_owner_cdp_read(self):
        runtime, reader = self.runtime(ReadResult.success_empty(()))
        holding = threading.Event()
        release = threading.Event()

        def hold_write_lock():
            with runtime._operation_lock:
                holding.set()
                release.wait(timeout=5)

        worker = threading.Thread(target=hold_write_lock, daemon=True)
        worker.start()
        try:
            self.assertTrue(holding.wait(timeout=2))
            with self.assertRaisesRegex(PrivateWebRuntimeSetupError, "busy"):
                runtime.sync_owner_inventory()
            self.assertEqual(reader.calls, 0)
            self.assertEqual(self.store.sync_status()["state"], "never_attempted")
        finally:
            release.set()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
