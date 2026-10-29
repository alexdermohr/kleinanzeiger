"""P0 customer startup -> real HTTP -> local work -> explicit recovery, no platform writes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from mark_api.dashboard import DashboardWriteProxy, create_server
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.launcher import build_product_launcher, ProductLauncherError, EmailImportReport
from mark_api.results import ReadResult, ReadStatus
from mark_api.storage import SnapshotStore
from mark_api.write_api import acquire_write_api_store_lock


class Inventory:
    def __init__(self, result, *, close_error=False):
        self.result = result
        self.close_error = close_error
        self.closed = False

    def read_inventory(self, *, stop_requested=None):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self):
        if self.close_error:
            raise RuntimeError("reader close uncertain")
        self.closed = True


class ProductOfflineWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="kleinanzeiger-offline-")
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "customer.sqlite"
        self.store = SnapshotStore(self.path)
        self.now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        self.snapshot = AdSnapshot(
            ad_id="10001", title="Bestätigte Lampe", source="private-web-product-launcher",
            observed_at=self.now, lifecycle_state=LifecycleState.ACTIVE,
        )
        attempt = self.store.begin_sync_attempt(source="private-web-product-launcher", started_at=self.now)
        self.store.append_inventory_result(
            ReadResult.success_nonempty((self.snapshot,)), observed_at=self.now,
            source="private-web-product-launcher", attempt_id=attempt, completed_at=self.now,
        )
        self.failure = ReadResult.failure(ReadStatus.TRANSPORT_ERROR, error="cdp_connect")
        self.readers = []
        self.result = self.failure
        self.writer = Mock(side_effect=AssertionError("offline must never construct a platform writer"))
        self.runtime = None

    def factory(self, **_kwargs):
        reader = Inventory(self.result)
        self.readers.append(reader)
        return reader

    def start(self, **overrides):
        options = dict(
            db_path=self.path, cdp_port=9222, dashboard_port=0, write_port=0,
            runtime_factory=self.factory, write_runtime_factory=self.writer,
            clock=lambda: self.now + timedelta(hours=1), allow_offline=True,
        )
        options.update(overrides)
        runtime = build_product_launcher(**options)
        self.runtime = runtime
        worker = Thread(target=runtime.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 3)
        self.addCleanup(runtime.close)
        self.port = runtime.server_address[1]
        self.token = parse_qs(urlsplit(runtime.dashboard_url).fragment).get("write_token", ["not-an-issued-local-token"])[0]
        return runtime

    def request(self, method, path, data=None, *, token=True, origin=True, extra=None):
        headers = {}
        if token:
            headers.update({"X-Mark-Dashboard-Write": "1", "X-Mark-Dashboard-Token": self.token})
        if origin:
            headers["Origin"] = f"http://127.0.0.1:{self.port}"
        if data is not None:
            headers["Content-Type"] = "application/json"
        headers.update(extra or {})
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=json.dumps(data) if data is not None else None, headers=headers)
            result = connection.getresponse()
            body = result.read()
            return result.status, json.loads(body) if body else None
        finally:
            connection.close()

    def test_transport_failure_opens_cached_workspace_without_any_writer(self):
        runtime = self.start()
        self.assertTrue(runtime.offline_mode)
        self.assertFalse(runtime.writes_enabled)
        self.writer.assert_not_called()
        self.assertTrue(self.readers[0].closed)
        self.assertEqual(self.store.ad_history("10001"), (self.snapshot,))
        status, config = self.request("GET", "/api/dashboard/config")
        self.assertEqual(status, 200)
        self.assertEqual(config, {"write_ui_available": False, "offline_mode": True,
                                 "local_work_available": True, "manual_sync_available": True,
                                 "portfolio_jobs_available": True})
        summary = self.request("GET", "/api/summary")[1]
        self.assertEqual(summary["current_ads"], 1)
        self.assertEqual(summary["sync_status"]["state"], "failed")
        self.assertEqual(summary["sync_status"]["last_successful_attempt"]["source"], "private-web-product-launcher")
        self.assertEqual(self.request("GET", "/api/portfolio/jobs?limit=1")[0], 200)
        with self.assertRaises(ProductLauncherError):
            _ = runtime.write_server_address
        for method, path in (("POST", "/api/write/ads"), ("PATCH", "/api/write/ads/10001"),
                             ("DELETE", "/api/write/ads/10001"),
                             ("POST", "/api/dashboard/pending-writes/ack")):
            self.assertGreaterEqual(self.request(method, path, {})[0], 400)
        self.writer.assert_not_called()

    def test_local_reservation_is_usable_idempotent_and_origin_bound(self):
        self.start()
        sale = {"ad_id": "10001", "state": "reserved", "revenue_eur": None,
                "cost_eur": None, "location": "Regal 4", "note": "Offline notiert",
                "request_key": "a" * 64, "expected_revision": 0}
        path = "/api/portfolio/sales/update"
        self.assertEqual(self.request("POST", path, sale, token=False)[0], 403)
        self.assertEqual(self.request("POST", path, sale, origin=False)[0], 403)
        status, result = self.request("POST", path, sale)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["platform_writes"], 0)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(self.request("POST", path, sale)[1], result)
        self.assertEqual(self.request("POST", path, {**sale, "note": "different"})[0], 409)
        self.assertEqual(self.store.ad_history("10001"), (self.snapshot,))
        self.writer.assert_not_called()

    def test_explicit_resync_requires_exact_local_session_and_never_retries_itself(self):
        self.start()
        for extra in ({"Origin": "http://foreign.invalid"},
                      {"Sec-Fetch-Site": "cross-site"},
                      {"Authorization": "Bearer bogus"},
                      {"X-Mark-Dashboard-Token": "wrong"}):
            self.assertEqual(self.request("POST", "/api/dashboard/sync", extra=extra)[0], 403)
        self.assertEqual(len(self.readers), 1)
        self.assertEqual(self.request("POST", "/api/dashboard/sync")[0], 503)
        self.assertEqual(len(self.readers), 2)
        self.assertEqual(self.store.ad_history("10001"), (self.snapshot,))
        fresh = AdSnapshot(ad_id="10001", title="Aktueller Eigenbestand", source="private-web-product-launcher",
                           observed_at=self.now + timedelta(hours=1), lifecycle_state=LifecycleState.ACTIVE)
        self.result = ReadResult.success_nonempty((fresh,))
        self.assertEqual(self.request("POST", "/api/dashboard/sync"),
                         (200, {"status": "success", "current_ads": 1}))
        self.assertEqual(len(self.readers), 3)
        self.assertEqual(self.store.ad_history("10001")[-1], fresh)
        self.assertFalse(self.request("GET", "/api/dashboard/config")[1]["write_ui_available"])
        self.writer.assert_not_called()

    def test_explicit_resync_classifies_safe_read_failures_once(self):
        self.start()
        for result, error in (
            (ReadResult.failure(ReadStatus.TRANSPORT_ERROR, error="cookie=secret"),
             "owner_inventory_connection_unavailable"),
            (ReadResult.failure(ReadStatus.UNAUTHENTICATED, http_status=401),
             "owner_inventory_login_required"),
            (ReadResult.failure(ReadStatus.PARSE_ERROR, error="corrupt /private/path"),
             "owner_inventory_read_failed"),
        ):
            with self.subTest(error=error):
                before = len(self.readers)
                self.result = result
                self.assertEqual(self.request("POST", "/api/dashboard/sync"),
                                 (503, {"error": error}))
                self.assertEqual(len(self.readers), before + 1)
                self.assertEqual(self.store.ad_history("10001"), (self.snapshot,))
        self.writer.assert_not_called()

    def test_explicit_resync_classifies_busy_recovery_and_storage_without_read(self):
        runtime = self.start()
        session = runtime._inventory_runtime
        before = len(self.readers)
        self.assertTrue(session._operation_lock.acquire(blocking=False))
        try:
            self.assertEqual(self.request("POST", "/api/dashboard/sync"),
                             (409, {"error": "owner_inventory_busy"}))
        finally:
            session._operation_lock.release()
        self.assertEqual(len(self.readers), before)

        prior = self.store.begin_sync_attempt(source="unfinished", started_at=self.now)
        self.assertEqual(self.request("POST", "/api/dashboard/sync"),
                         (409, {"error": "owner_inventory_recovery_required"}))
        self.assertEqual(len(self.readers), before)
        self.store.fail_sync_attempt(prior, source="unfinished",
                                     error_kind="parse_error",
                                     completed_at=self.now)

        with patch.object(SnapshotStore, "sync_recovery_status",
                          side_effect=OSError("database /private/path unavailable")):
            self.assertEqual(self.request("POST", "/api/dashboard/sync"),
                             (503, {"error": "owner_inventory_storage_unavailable"}))
        self.assertEqual(len(self.readers), before)
        self.assertEqual(self.store.ad_history("10001"), (self.snapshot,))
        self.writer.assert_not_called()

    def test_explicit_resync_classifies_runtime_start_failure_without_retry(self):
        factory = Mock(side_effect=RuntimeError("cookie secret in /private/path"))
        self.start(runtime_factory=factory)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(self.request("POST", "/api/dashboard/sync"),
                         (503, {"error": "owner_inventory_browser_unavailable"}))
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(self.store.ad_history("10001"), (self.snapshot,))
        self.writer.assert_not_called()

    def test_only_confirmed_empty_inventory_can_mark_absent(self):
        self.start()
        self.assertEqual(self.store.ad_history("10001")[-1].lifecycle_state, LifecycleState.ACTIVE)
        self.result = ReadResult.success_empty(())
        self.assertEqual(self.request("POST", "/api/dashboard/sync")[1]["current_ads"], 0)
        self.assertEqual(self.store.ad_history("10001")[-1].lifecycle_state, LifecycleState.ABSENT)
        self.assertFalse(self.runtime.writes_enabled)

    def test_close_releases_db_lease_and_new_offline_start_preserves_history(self):
        runtime = self.start()
        with self.assertRaises(RuntimeError):
            acquire_write_api_store_lock(self.store)
        runtime.close()
        lease = acquire_write_api_store_lock(self.store)
        lease.close()
        self.start()
        self.assertEqual(self.store.ad_history("10001"), (self.snapshot,))
        self.writer.assert_not_called()

    def test_factory_failure_falls_back_but_no_read_is_automatically_retried(self):
        factory = Mock(side_effect=RuntimeError("browser unavailable; cookie secret"))
        self.start(runtime_factory=factory)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(self.store.ad_history("10001"), (self.snapshot,))
        self.assertNotIn("cookie secret", json.dumps(self.request("GET", "/api/summary")[1]))

    def test_strict_builder_and_independent_witness_never_fall_back(self):
        for options in ({"allow_offline": False}, {"allow_offline": True, "fence_witness": object()}):
            with self.subTest(options=options), self.assertRaises(ProductLauncherError):
                build_product_launcher(db_path=self.path, cdp_port=9222, dashboard_port=0,
                                       runtime_factory=self.factory, **options)
        self.writer.assert_not_called()

    def test_missing_database_and_corrupt_database_are_not_offline_success(self):
        for name, content in (("missing.sqlite", None), ("corrupt.sqlite", b"not sqlite")):
            path = self.path.with_name(name)
            if content is not None:
                path.write_bytes(content)
            with self.subTest(name=name), self.assertRaises(ProductLauncherError):
                build_product_launcher(db_path=path, cdp_port=9222,
                                       runtime_factory=self.factory, allow_offline=True)
            self.assertEqual(path.read_bytes() if path.exists() else None, content)
        self.assertEqual(self.readers, [])

    def test_unresolved_prior_sync_is_retained_and_not_repeated(self):
        prior = self.store.begin_sync_attempt(source="unfinished", started_at=self.now)
        with self.assertRaises(ProductLauncherError):
            build_product_launcher(db_path=self.path, cdp_port=9222,
                                   runtime_factory=self.factory, allow_offline=True)
        self.assertEqual(self.readers, [])
        self.assertEqual(self.store.sync_recovery_status()["unresolved_count"], 1)
        self.assertEqual(self.store.latest_sync_attempt().id, prior)

    def test_failed_reader_cleanup_never_grants_offline_fallback(self):
        with self.assertRaisesRegex(ProductLauncherError, "cleanup failed"):
            build_product_launcher(db_path=self.path, cdp_port=9222, allow_offline=True,
                                   runtime_factory=lambda **kw: Inventory(self.failure, close_error=True))

    def test_failed_persistence_is_not_caught_as_connectivity(self):
        self.result = ReadResult.success_nonempty((self.snapshot,))
        with patch.object(SnapshotStore, "append_inventory_result", side_effect=OSError("disk failed")):
            with self.assertRaises(ProductLauncherError):
                build_product_launcher(db_path=self.path, cdp_port=9222,
                                       runtime_factory=self.factory, allow_offline=True)
        self.assertEqual(self.store.ad_history("10001"), (self.snapshot,))
        self.assertEqual(self.store.latest_sync_attempt().error_kind, "persistence_error")

    def test_local_session_cannot_be_combined_with_platform_proxy_or_relay(self):
        proxy = DashboardWriteProxy(host="127.0.0.1", port=19080,
                                    ui_token="a" * 32, bearer_token="b" * 32)
        for options in ({"write_proxy": proxy}, {"public_origin_port": 8875},
                        {"local_session_token": "short"}, {"local_session_token": "x" * 20 + "\n"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                create_server(self.store, port=0, **{"local_session_token": "l" * 32, **options})

    def test_read_only_fallback_does_not_broaden_local_mutation_authority(self):
        runtime = self.start(enable_writes=False)
        self.assertNotIn("write_token", urlsplit(runtime.dashboard_url).fragment)
        config = self.request("GET", "/api/dashboard/config")[1]
        self.assertEqual(config, {"write_ui_available": False, "offline_mode": True})
        for path in ("/api/portfolio/sales/update", "/api/portfolio/price-rules/mutate",
                     "/api/portfolio/jobs/create", "/api/dashboard/sync", "/api/write/ads"):
            with self.subTest(path=path):
                self.assertGreaterEqual(self.request("POST", path, {})[0], 400)
        self.assertEqual(self.store.ad_history("10001"), (self.snapshot,))
        self.writer.assert_not_called()

    def test_explicit_email_import_survives_offline_fallback_and_one_shot_paths(self):
        paths = (self.path.with_name("one.eml"), self.path.with_name("two.eml"))
        report = EmailImportReport(parsed_files=2, inserted_events=1, duplicate_events=1, ad_ids=("10001",))
        importer = Mock(return_value=report)
        runtime = self.start(email_paths=iter(paths), email_importer=importer, enable_writes=False)
        importer.assert_called_once()
        self.assertEqual(importer.call_args.args[1], paths)
        self.assertEqual(importer.call_args.args[0].path, self.path)
        self.assertIs(runtime.startup_email_import_report, report)
        self.assertFalse(self.request("GET", "/api/dashboard/config")[1]["write_ui_available"])

    def test_invalid_explicit_email_import_fails_and_releases_local_lease(self):
        for importer in (Mock(side_effect=OSError("private mail path")), Mock(return_value=None)):
            with self.subTest(importer=importer), self.assertRaisesRegex(ProductLauncherError, "local reaction email import failed"):
                build_product_launcher(db_path=self.path, cdp_port=9222, dashboard_port=0,
                    allow_offline=True, runtime_factory=self.factory,
                    email_paths=(self.path.with_name("missing.eml"),), email_importer=importer)
            importer.assert_called_once()
            lease = acquire_write_api_store_lock(self.store)
            lease.close()
        with self.assertRaisesRegex(ProductLauncherError, "local reaction email import failed"):
            build_product_launcher(db_path=self.path, cdp_port=9222, dashboard_port=0,
                allow_offline=True, runtime_factory=self.factory,
                email_paths=(self.path.with_name("missing.eml"),))
        self.assertEqual(self.store.ad_history("10001"), (self.snapshot,))

    def test_dashboard_bind_failure_releases_offline_lease(self):
        occupied = socket.socket()
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        self.addCleanup(occupied.close)
        with self.assertRaises(OSError):
            build_product_launcher(db_path=self.path, cdp_port=9222,
                                   dashboard_port=occupied.getsockname()[1],
                                   runtime_factory=self.factory, allow_offline=True)
        lease = acquire_write_api_store_lock(self.store)
        lease.close()


if __name__ == "__main__":
    unittest.main()
