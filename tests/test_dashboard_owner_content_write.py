"""Bounded owner-editor original-text recovery and actual local Write API path.

Only synthetic local owner facts and in-memory platform effects are used.
No live account, paid provider or marketplace platform submit is made.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from mark_api.dashboard import DashboardWriteProxy, _DASHBOARD_HTML, _DASHBOARD_JS, create_server
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.private_web_runtime import OwnerInventorySyncFailure, PrivateWebWriteApiRuntime
from mark_api.results import ReadResult, ReadStatus
from mark_api.storage import SnapshotStore
from mark_api.write_api import WriteApiAccess, WriteCapability, create_write_api_server

from test_dashboard_full_lifecycle_e2e import (
    BACKEND_TOKEN, NOW, UI_TOKEN, SyntheticPlatform, service_for, request,
)


class OwnerOriginalContentRuntimeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = SnapshotStore(Path(tmp.name) / "catalog.sqlite")
        self.original = AdSnapshot(
            ad_id="314", source="kleinanzeigen-management", observed_at=NOW,
            lifecycle_state=LifecycleState.ACTIVE, title="Saved listing",
            description=None,
        )
        self.store.append_ad_snapshot(self.original)
        self.browser_calls = []
        self.editor = replace(
            self.original, source="kleinanzeigen-management+private-web",
            observed_at=NOW + timedelta(seconds=1), description="Original title and facts",
        )

        class BrowserContent:
            _cdp_session = SimpleNamespace(port=9222)
            def content_reader_for(inner, ad_id):
                self.browser_calls.append(ad_id)
                return SimpleNamespace(read_ads=lambda: ReadResult.success_nonempty((self.editor,)))
        self.runtime = object.__new__(PrivateWebWriteApiRuntime)
        self.runtime._operation_lock = Lock()
        self.runtime._stopping = False
        self.runtime._closed = False
        self.runtime._fence_witness = None
        self.runtime._media_runtime = None
        self.runtime._store = self.store
        self.runtime._content_runtime = BrowserContent()

    def test_exact_known_owned_ad_enriched_once_without_platform_submit(self):
        self.runtime.refresh_owned_ad_content("314")
        self.assertEqual(self.browser_calls, ["314"])
        observed = self.store.latest_ad_snapshot("314")
        self.assertEqual(observed.description, "Original title and facts")
        self.assertEqual(observed.source, "private-web-owner-inventory")
        self.assertEqual(observed.title, "Saved listing")

    def test_foreign_unknown_and_invalid_ids_do_not_contact_browser(self):
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id="499", source="user-supplied-local-catalog",
            observed_at=NOW, lifecycle_state=LifecycleState.ACTIVE,
            title="Foreign synthetic", description=None,
        ))
        for ad_id in ("499", "777", "三", "1; DROP TABLE", ""):
            with self.subTest(ad_id=ad_id), self.assertRaises((ValueError, OwnerInventorySyncFailure)):
                self.runtime.refresh_owned_ad_content(ad_id)
        self.assertEqual(self.browser_calls, [])
        self.assertIsNone(self.store.latest_ad_snapshot("777"))

    def test_busy_and_fenced_outcomes_do_not_open_editor_or_write_sqlite(self):
        self.runtime._operation_lock.acquire()
        try:
            with self.assertRaises(OwnerInventorySyncFailure) as captured:
                self.runtime.refresh_owned_ad_content("314")
            self.assertEqual(captured.exception.kind, "busy")
        finally:
            self.runtime._operation_lock.release()

        self.runtime._fence_witness = object()
        with self.assertRaises(OwnerInventorySyncFailure) as captured:
            self.runtime.refresh_owned_ad_content("314")
        self.assertEqual(captured.exception.kind, "recovery_required")
        self.runtime._fence_witness = None

        self.runtime._media_runtime = SimpleNamespace(reconciliation_required=True)
        with self.assertRaises(OwnerInventorySyncFailure):
            self.runtime.refresh_owned_ad_content("314")
        self.runtime._media_runtime = None
        self.assertEqual(self.browser_calls, [])
        self.assertEqual(len(self.store.ad_history("314")), 1)

    def test_unknown_result_does_not_fabricate_description_or_new_owner_fact(self):
        self.runtime._content_runtime.content_reader_for = lambda ad_id: SimpleNamespace(
            read_ads=lambda: ReadResult.failure(ReadStatus.TRANSPORT_ERROR, error="synthetic_disconnected")
        )
        with self.assertRaises(OwnerInventorySyncFailure) as captured:
            self.runtime.refresh_owned_ad_content("314")
        self.assertEqual(captured.exception.kind, "connection_unavailable")
        self.assertIsNone(self.store.latest_ad_snapshot("314").description)
        self.assertEqual(len(self.store.ad_history("314")), 1)

    def test_mismatched_editor_target_rejected_without_store_mutation(self):
        other = replace(self.editor, ad_id="999")
        self.runtime._content_runtime.content_reader_for = lambda ad_id: SimpleNamespace(
            read_ads=lambda: ReadResult.success_nonempty((other,))
        )
        with self.assertRaises(OwnerInventorySyncFailure) as captured:
            self.runtime.refresh_owned_ad_content("314")
        self.assertEqual(captured.exception.kind, "read_failed")
        self.assertEqual(len(self.store.ad_history("314")), 1)


class OwnerContentToWriteHttpTests(unittest.TestCase):
    def test_owner_original_read_then_actual_dashboard_proxy_patch_confirmation(self):
        with tempfile.TemporaryDirectory() as work:
            store = SnapshotStore(Path(work) / "catalog.sqlite")
            platform = SyntheticPlatform()
            store.append_ad_snapshot(replace(platform.ads["314"], description=None))
            seen = []

            def original_read_once(ad_id):
                seen.append(ad_id)
                if ad_id != "314":
                    raise OwnerInventorySyncFailure("read_failed")
                store.append_ad_snapshot(replace(
                    store.latest_ad_snapshot("314"),
                    source="private-web-owner-inventory",
                    observed_at=NOW + timedelta(seconds=1),
                    description="Saved owner description",
                ))

            access = WriteApiAccess(
                principal="synthetic-owner-content-and-write",
                bearer_token=BACKEND_TOKEN,
                capabilities=frozenset(WriteCapability) - {WriteCapability.CREATE_MEDIA},
                writes_enabled=True,
            )
            backend = create_write_api_server(service_for(platform, store), store, access, port=0)
            dashboard = create_server(
                store, port=0,
                write_proxy=DashboardWriteProxy(
                    host="127.0.0.1", port=backend.server_port,
                    bearer_token=BACKEND_TOKEN, ui_token=UI_TOKEN,
                    backend_principal=access.principal, local_reconciliation_enabled=True,
                ),
                original_read_once=original_read_once,
            )
            threads = []
            try:
                for server in (backend, dashboard):
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    threads.append(thread)
                base = f"http://127.0.0.1:{dashboard.server_port}"
                config = request(base, "/api/dashboard/config")[1]
                self.assertTrue(config["owner_content_available"])

                denied = request(base, "/api/dashboard/owner-content", method="POST",
                    payload={"ad_id": "314"}, authorized=False)
                self.assertEqual(denied[0], 403)
                self.assertEqual(seen, [])
                invalid = request(base, "/api/dashboard/owner-content", method="POST",
                    payload={"ad_id": "outside-catalog"})
                self.assertEqual(invalid[0], 400)
                self.assertEqual(seen, [])
                missing = request(base, "/api/dashboard/owner-content", method="POST",
                    payload={"ad_id": "777"})
                self.assertEqual(missing[0], 503)
                self.assertEqual(seen, ["777"])

                status, payload, _ = request(base, "/api/dashboard/owner-content", method="POST",
                    payload={"ad_id": "314"})
                self.assertEqual(status, 200, payload)
                self.assertEqual(payload["status"], "success")
                self.assertEqual(payload["ad"]["ad_id"], "314")
                self.assertEqual(payload["ad"]["description"], "Saved owner description")
                self.assertEqual(payload["ad"]["source"], "private-web-owner-inventory")
                self.assertEqual(platform.effects, [])
                self.assertEqual(seen, ["777", "314"])

                # The same real HTTP dashboard-Write proxy, core, synthetic owner
                # and SQLite store now handle a confirmed single content edit.
                code, edited, _ = request(base, "/api/write/ads/314", method="PATCH",
                    payload={"title": "New factual title", "description": "Verified real owner facts"}, key="one-edit-after-original-read")
                self.assertEqual(code, 200, edited)
                self.assertEqual(edited["operation_receipt"]["outcome"], "confirmed")
                self.assertEqual(platform.effects, ["update_content"])
                self.assertEqual(platform.ads["314"].title, "New factual title")
                self.assertEqual(platform.ads["314"].description, "Verified real owner facts")
                replay_code, replay, headers = request(base, "/api/write/ads/314", method="PATCH",
                    payload={"title": "New factual title", "description": "Verified real owner facts"}, key="one-edit-after-original-read")
                self.assertEqual(replay_code, 200)
                self.assertEqual(replay, edited)
                self.assertEqual(headers.get("Idempotency-Replayed"), "true")
                self.assertEqual(platform.effects, ["update_content"])
            finally:
                for server in (dashboard, backend):
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join(timeout=4)

    def test_original_reader_not_exposed_on_offline_or_anonymous_dashboard(self):
        with tempfile.TemporaryDirectory() as work:
            store = SnapshotStore(Path(work) / "store.sqlite")
            with self.assertRaises(ValueError):
                create_server(store, port=0, original_read_once=lambda ad_id: None)




class OwnerContentBrowserJavaScriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node required to run shipped JS")
    def test_exact_selection_and_form_preservation_when_read_completes(self):
        source = _DASHBOARD_JS.split("async function loadOriginalOwnedContent() {", 1)[1].split(
            "function rebaseManagedBaseline", 1,
        )[0]
        script = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0,'utf8');
const selected = {adId:'314',titleKnown:true,title:'Saved title',descriptionKnown:false,description:null};
const form = {
  'manage-ad-id':{value:'314'},'manage-title':{value:'Saved title'},
  'manage-description':{value:''}, 'manage-original-status':{textContent:''}
};
const rows = new Map();
let requests=[];
const env = {
  managedAdBaseline:selected, originalContentBusy:false, ownerContentAvailable:true, pendingRecoveryBlocked:false,
  pendingWrites:new Map(), writeToken:'session-token', latestAdsById:rows,
  VERIFIED_EXISTING_AD_SOURCES:new Set(['private-web-owner-inventory']),
  byId:id=>form[id], writeUiReady:()=>true,
  window:{location:{origin:'http://127.0.0.1:8765'}},
  renderOriginalContentAvailability:()=>{},
  renderGenerationAvailability:()=>{},
  populateManageForm:row=>{
    form['manage-title'].value=row.title;
    form['manage-description'].value=row.description;
    env.managedAdBaseline={adId:row.ad_id,title:row.title,description:row.description,
      titleKnown:true,descriptionKnown:true};
  },
  fetch:async(path,options)=>{
    requests.push({path,options});
    return {ok:true,json:async()=>({status:'success',ad:{
      ad_id:'314',title:'Saved title',description:'Original evidence',
      source:'private-web-owner-inventory',present:true,lifecycle_state:'active',
      observed_at:'2026-11-01T20:00:00+00:00',
    }})};
  }
};
vm.createContext(env);
vm.runInContext(source,env);
(async()=>{
  await env.loadOriginalOwnedContent();
  assert.equal(requests.length,1);
  assert.equal(requests[0].path,'/api/dashboard/owner-content');
  assert.equal(requests[0].options.method,'POST');
  assert.equal(requests[0].options.headers['Origin'],'http://127.0.0.1:8765');
  assert.equal(requests[0].options.headers['X-Mark-Dashboard-Token'],'session-token');
  assert.equal(JSON.parse(requests[0].options.body).ad_id,'314');
  assert.equal(form['manage-description'].value,'Original evidence');
  assert.equal(rows.get('314').description,'Original evidence');
  assert.equal(env.managedAdBaseline.descriptionKnown,true);
  assert.match(form['manage-original-status'].textContent,/geprüft/);

  // A local manual edit after dispatch must never be overwritten on completion.
  env.managedAdBaseline=selected;
  form['manage-description'].value='';
  const old = env.fetch;
  let release;
  env.fetch=async()=>new Promise(resolve=>{release=resolve;});
  const pending=env.loadOriginalOwnedContent();
  form['manage-title'].value='Unsaved manual title';
  release(await old('/api/dashboard/owner-content',{method:'POST'}));
  await pending;
  assert.equal(form['manage-title'].value,'Unsaved manual title');
  assert.equal(form['manage-description'].value,'');
  assert.match(form['manage-original-status'].textContent,/Keine Übernahme/);
  console.log('OWNER_ORIGINAL_JS_SELECTION_PROVEN');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        run = subprocess.run(
            [shutil.which("node"), "-e", script], input="async function loadOriginalOwnedContent() {" + source,
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("OWNER_ORIGINAL_JS_SELECTION_PROVEN", run.stdout)
