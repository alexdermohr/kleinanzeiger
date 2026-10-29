"""Synthetic tests for the owner-only CDP inventory; no real browser/network."""
from __future__ import annotations

import json
import multiprocessing
import signal
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from mark_api.private_web_inventory_cdp import (
    CdpManagementInventoryReader, PrivateWebInventoryReadCancelled,
    PrivateWebInventoryReadSetupError,
)
from mark_api.private_web_runtime import build_private_web_inventory_runtime
from mark_api.private_web_cdp import PrivateWebCdpError
from mark_api.results import ReadResult, ReadStatus


class FakeClient:
    def __init__(self, pages, *, origin="https://www.kleinanzeigen.de", path="/m-meine-anzeigen.html"):
        self.pages = list(pages)
        self.origin, self.path = origin, path
        self.calls = []
        self.closed = False
        self._send_guard = None

    def bind_cancellable_reads(self, *, stop_requested, send_guard):
        self._send_guard = send_guard

    def call(self, method, params=None):
        if self._send_guard is not None:
            # This fake has no async receive path. The real CDP client masks
            # only its outbound websocket.send, not its response wait.
            with self._send_guard():
                return self._synthetic_send(method, params)
        return self._synthetic_send(method, params)

    def _synthetic_send(self, method, params=None):
        params = dict(params or {})
        self.calls.append((method, params))
        if method == "Page.navigate":
            raise AssertionError("owner inventory must NEVER navigate any CDP target")
        if method != "Runtime.evaluate":
            raise AssertionError(method)
        if params.get("awaitPromise"):
            if not self.pages:
                raise AssertionError("unexpected second management page read")
            payload = self.pages.pop(0)
            if isinstance(payload, Exception):
                raise payload
            return {"result": {"value": payload}}
        return {"result": {"value": {
            "readyState": "complete",
            "origin": self.origin,
            "path": self.path,
        }}}

    def close(self):
        self.closed = True


def payload(ads, *, last=1, status=200):
    return {"status": status, "body": json.dumps({"ads": ads, "paging": {"last": last}})}


class BrowserInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        # Existing tests exercise only synthetic fake browser clients.
        # The production default-deny guard is unpatched in the independent
        # negative authorization tests below.
        p = patch("mark_api.private_web_inventory_cdp._require_private_web_cdp_authority")
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def reader(client, **kwargs):
        return CdpManagementInventoryReader(
            cdp_port=19610, timeout_seconds=0.2,
            clock=lambda: datetime(2026, 10, 19, tzinfo=timezone.utc),
            client_factory=lambda: client,
            preconnected_client=client,
            sleep=lambda _: None,
            monotonic=lambda: 2.0,
            **kwargs,
        )

    def test_loading_manager_tab_waits_using_only_read_only_acknowledged_probes(self):
        class LoadingClient(FakeClient):
            def __init__(self):
                super().__init__([payload([])])
                self.states = iter(("loading", "interactive", "complete"))
            def _synthetic_send(self, method, params=None):
                params = dict(params or {})
                if not params.get("awaitPromise"):
                    self.calls.append((method, params))
                    return {"result": {"value": {
                        "readyState": next(self.states),
                        "origin": "https://www.kleinanzeigen.de",
                        "path": "/m-meine-anzeigen.html",
                    }}}
                return super()._synthetic_send(method, params)
        client = LoadingClient()
        slept = []
        reader = CdpManagementInventoryReader(
            cdp_port=19610, timeout_seconds=0.5,
            client_factory=lambda: client, sleep=lambda seconds: slept.append(seconds),
        )
        result = reader.read_ads()
        self.assertEqual(result.status, ReadStatus.SUCCESS_EMPTY)
        self.assertEqual(slept, [0.15, 0.15])
        self.assertEqual(
            [params.get("awaitPromise") for _, params in client.calls],
            [False, False, False, True],
        )
        self.assertTrue(client.closed)

    def test_wrong_tab_has_safe_specific_reason_not_a_login_assertion(self):
        client = FakeClient([payload([])], path="/p-anzeige-bearbeiten.html")
        result = self.reader(client).read_ads()
        self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual(result.error, "cdp_inventory_wrong_target")
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(client.closed)

    def test_missing_management_json_response_is_specific_but_not_untrusted(self):
        client = FakeClient([{"error": "missing_body"}])
        result = self.reader(client).read_ads()
        self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual(result.error, "cdp_inventory_fetch_failed")
        self.assertTrue(client.closed)
        redirected = FakeClient([{"error": "wrong_origin_or_page"}])
        result2 = self.reader(redirected).read_ads()
        self.assertEqual(result2.error, "cdp_inventory_wrong_target")

    def test_never_leak_untrusted_provider_errors_through_inventory(self):
        client = FakeClient([RuntimeError("private@example.invalid Cookie=secret")])
        result = self.reader(client).read_ads()
        self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual(result.error, "cdp_inventory_failed")
        self.assertNotIn("private@example.invalid", str(result))

    def test_owner_inventory_uses_only_fixed_same_origin_get_without_cookie_export(self):
        client = FakeClient([
            payload([{"id": "1234567890", "title": "Vase", "state": "active", "viewCount": 5, "watchCount": 2}],last=2),
            payload([{"id": "1234567891", "title": "Tisch", "state": "paused", "viewCount": 9}],last=2),
        ])
        result = self.reader(client).read_ads()
        self.assertEqual(result.status, ReadStatus.SUCCESS_NONEMPTY)
        self.assertEqual([item.ad_id for item in result.value], ["1234567890", "1234567891"])
        self.assertEqual(result.value[0].views, 5)
        self.assertTrue(client.closed)
        navigations = [p for method, p in client.calls if method == "Page.navigate"]
        self.assertEqual(navigations, [], "owner inventory must never navigate an editor target")
        fetches = [p for method, p in client.calls if method == "Runtime.evaluate" and p.get("awaitPromise")]
        self.assertEqual(len(fetches), 2)
        for page, request in enumerate(fetches, start=1):
            self.assertTrue(request["returnByValue"])
            self.assertIn("pageNum=" + str(page), request["expression"])
            self.assertIn('credentials: "same-origin"', request["expression"])
            self.assertIn('mode: "same-origin"', request["expression"])
            self.assertIn('redirect: "error"', request["expression"])
            self.assertIn("location.origin", request["expression"])
            self.assertNotIn("Cookie", request["expression"])
            self.assertNotIn("POST", request["expression"])
            self.assertNotIn("DELETE", request["expression"])

    def test_successful_empty_is_not_transport_failure(self):
        client = FakeClient([payload([])])
        result = self.reader(client).read_ads()
        self.assertEqual(result.status, ReadStatus.SUCCESS_EMPTY)
        self.assertEqual(result.value, ())
        self.assertTrue(client.closed)

    def test_auth_and_http_errors_do_not_confirm_inventory(self):
        for code, expected in ((401, ReadStatus.UNAUTHENTICATED),
                               (403, ReadStatus.UNAUTHENTICATED),
                               (500, ReadStatus.HTTP_ERROR)):
            with self.subTest(status=code):
                client = FakeClient([{"status": code, "body": ""}])
                self.assertEqual(self.reader(client).read_ads().status, expected)
                self.assertTrue(client.closed)

    def test_invalid_json_and_duplicate_ids_fail_closed(self):
        for items, status in (
            ([{"status": 200, "body": "<html>login</html>"}], ReadStatus.PARSE_ERROR),
            ([payload([{"id": "1234567890"}, {"id": "1234567890"}])], ReadStatus.PARSE_ERROR),
            ([payload([{"id": True}])], ReadStatus.PARSE_ERROR),
            ([payload([{"id": "1"}], last=2), payload([{"id": "2"}], last=3)], ReadStatus.PARSE_ERROR),
            ([{"error": "wrong_origin_or_page"}], ReadStatus.TRANSPORT_ERROR),
            ([{"status": 200, "body": "x" * (2 * 1024 * 1024 + 1)}], ReadStatus.PARSE_ERROR),
        ):
            with self.subTest(payloads=str(items)[:75]):
                client = FakeClient(items)
                result = self.reader(client).read_ads()
                self.assertEqual(result.status, status)
                self.assertTrue(client.closed)

    def test_already_open_editor_target_denied_without_navigation(self):
        # The owner reader may attach to a target currently owned by writer.
        # It must not navigate or issue any management-page fetch from it.
        editor = FakeClient([payload([])], path="/p-anzeige-bearbeiten.html")
        refused = self.reader(editor).read_ads()
        self.assertEqual(refused.status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual([name for name, _ in editor.calls], ["Runtime.evaluate"])
        self.assertFalse(any(
            params.get("awaitPromise") or name == "Page.navigate"
            for name, params in editor.calls
        ))
        self.assertTrue(editor.closed)

    def test_editor_switch_between_probe_and_fetch_stays_read_only(self):
        # A writer may change the target after the passive management check.
        # The browser-side fetch expression must recheck page+origin itself.
        class RacingTarget(FakeClient):
            def _synthetic_send(self, method, params=None):
                params = dict(params or {})
                if method == "Runtime.evaluate" and params.get("awaitPromise"):
                    self.calls.append((method, params))
                    self_outer.assertEqual(
                        self.path, "/p-anzeige-bearbeiten.html",
                    )
                    self_outer.assertLess(
                        params["expression"].index("location.origin !=="),
                        params["expression"].index("const response = await fetch"),
                    )
                    return {"result": {"value": {"error": "wrong_origin_or_page"}}}
                reply = super()._synthetic_send(method, params)
                if method == "Runtime.evaluate":
                    self.path = "/p-anzeige-bearbeiten.html"
                return reply

        self_outer = self
        client = RacingTarget([payload([])])
        result = self.reader(client).read_ads()
        self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual(
            [method for method, _ in client.calls],
            ["Runtime.evaluate", "Runtime.evaluate"],
        )
        self.assertTrue(client.closed)
        self.assertEqual(len(client.pages), 1, "editor switch must not fetch inbox")

    def test_second_process_editor_is_never_navigated_by_inventory(self):
        """An unrelated worker may own the same Chrome target as our reader."""
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("requires POSIX fork-capable synthetic worker")
        ctx = multiprocessing.get_context("fork")
        editor_open = ctx.Event()
        editor_release = ctx.Event()
        target_is_editor = ctx.Value("i", 0)
        navigations = ctx.Value("i", 0)

        def writer_worker():
            with target_is_editor.get_lock():
                target_is_editor.value = 1
            editor_open.set()
            editor_release.wait(timeout=7)
            with target_is_editor.get_lock():
                target_is_editor.value = 0

        class SameTargetClient(FakeClient):
            def _synthetic_send(self, method, params=None):
                if method == "Page.navigate":
                    with navigations.get_lock():
                        navigations.value += 1
                    with target_is_editor.get_lock():
                        target_is_editor.value = 0
                    self.calls.append((method, dict(params or {})))
                    return {"frameId": "unwanted-navigation"}
                self.path = (
                    "/p-anzeige-bearbeiten.html" if target_is_editor.value
                    else "/m-meine-anzeigen.html"
                )
                return super()._synthetic_send(method, params)

        worker = ctx.Process(target=writer_worker)
        worker.start()
        try:
            self.assertTrue(editor_open.wait(timeout=3))
            client = SameTargetClient([payload([])])
            result = self.reader(client).read_ads()
            self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)
            self.assertEqual([name for name, _ in client.calls], ["Runtime.evaluate"])
            self.assertEqual(navigations.value, 0)
            self.assertEqual(target_is_editor.value, 1)
            self.assertTrue(worker.is_alive())
            self.assertTrue(client.closed)
        finally:
            editor_release.set()
            worker.join(timeout=4)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=4)
        self.assertFalse(worker.is_alive())

    def test_wrong_browser_origin_or_path_never_fetches(self):
        for origin, path in (("https://attacker.example", "/m-meine-anzeigen.html"),
                             ("https://www.kleinanzeigen.de", "/m-einloggen.html")):
            with self.subTest(origin=origin, path=path):
                client = FakeClient([payload([])], origin=origin, path=path)
                # Immediate synthetic deadline, no real browser or sleeping.
                state = iter([0.0, 1.0, 1.0])
                reader = CdpManagementInventoryReader(
                    cdp_port=19610, timeout_seconds=0.1,
                    client_factory=lambda: client,
                    monotonic=lambda: next(state, 1.0),
                    sleep=lambda _: None,
                )
                result = reader.read_ads()
                self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)
                self.assertTrue(client.closed)
                self.assertFalse(any(
                    method == "Runtime.evaluate" and args.get("awaitPromise")
                    for method, args in client.calls
                ))

    def test_reader_is_closed_and_rejects_nonloopback_or_invalid_ports(self):
        client = FakeClient([payload([])])
        reader = self.reader(client)
        reader.close()
        self.assertEqual(reader.read_ads().status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual(client.calls, [])
        for port in (0, -1, 65536, True, "19610"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                CdpManagementInventoryReader(cdp_port=port)

    def test_only_exact_canonical_inventory_reads_are_certified(self):
        from mark_api.private_web_inventory_cdp import (
            _INVENTORY_TAB_PROBE, _inventory_page_expression,
            _is_fixed_inventory_read_only_evaluation,
        )
        allowed = _is_fixed_inventory_read_only_evaluation
        self.assertTrue(allowed(_INVENTORY_TAB_PROBE, False))
        self.assertTrue(allowed(_inventory_page_expression(1), True))
        self.assertTrue(allowed(_inventory_page_expression(100), True))
        self.assertFalse(allowed(_INVENTORY_TAB_PROBE + ";document.body.remove()", False))
        self.assertFalse(allowed(_inventory_page_expression(1) + ";alert(1)", True))
        self.assertFalse(allowed(_inventory_page_expression(1), False))
        self.assertFalse(allowed(_inventory_page_expression(1), 1))
        self.assertFalse(allowed("document.querySelector('button').click()", False))

    def test_production_inventory_builder_admits_cancellable_native_client(self):
        if not hasattr(signal, "pthread_sigmask"):
            self.skipTest("POSIX signal mask required")
        fake = FakeClient([payload([])])
        with (
            patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"),
            patch("mark_api.private_web_inventory_cdp._LoopbackCdpClient",
                  return_value=fake) as native_factory,
        ):
            runtime = build_private_web_inventory_runtime(
                cdp_port=19610, timeout_seconds=0.2,
            )
            try:
                result = runtime.read_inventory(stop_requested=lambda: False)
                self.assertEqual(result.status, ReadStatus.SUCCESS_EMPTY)
                native_factory.assert_called_once()
                self.assertIsNotNone(fake._send_guard)
                self.assertEqual(
                    [method for method, _ in fake.calls],
                    ["Runtime.evaluate", "Runtime.evaluate"],
                )
            finally:
                runtime.close()
            self.assertTrue(fake.closed)

    def test_stop_aware_reader_never_calls_lazy_client_factory(self):
        invoked = []
        def factory():
            invoked.append("uncontrolled_connect")
            return FakeClient([payload([])])
        reader = CdpManagementInventoryReader(
            cdp_port=19610, client_factory=factory,
        )
        with self.assertRaisesRegex(
            PrivateWebInventoryReadSetupError,
            "cancellable CDP connection not composed",
        ):
            reader.read_ads(stop_requested=lambda: False)
        self.assertEqual(invoked, [])
        reader.close()

    def test_real_sigterm_during_last_admission_predicate_never_starts_cdp(self):
        if not hasattr(signal, "pthread_sigmask"):
            self.skipTest("Linux/POSIX signal mask required")
        client = FakeClient([payload([])])
        stopped = []
        original = signal.signal(signal.SIGTERM, lambda *_: stopped.append(True))
        try:
            calls = []
            def probe():
                calls.append(1)
                # The first check precedes the loopback client connection.
                # The second occurs WITH signals masked before the first
                # command's actual outbound send. Queue a real SIGTERM there.
                if len(calls) == 2:
                    signal.raise_signal(signal.SIGTERM)
                    return False
                return bool(stopped)
            with self.assertRaises(PrivateWebInventoryReadCancelled):
                self.reader(client).read_ads(stop_requested=probe)
        finally:
            signal.signal(signal.SIGTERM, original)
        self.assertEqual(stopped, [True])
        self.assertEqual(client.calls, [])

    def test_real_sigterm_inside_first_cdp_command_prevents_next_navigation(self):
        if not hasattr(signal, "pthread_sigmask"):
            self.skipTest("Linux/POSIX signal mask required")
        stopped = []
        class StopAfterFirstCall(FakeClient):
            def _synthetic_send(self, method, params=None):
                response = super()._synthetic_send(method, params)
                if len(self.calls) == 1:
                    signal.raise_signal(signal.SIGTERM)
                    self_outer.assertEqual(stopped, [])
                return response
        self_outer = self
        client = StopAfterFirstCall([payload([])])
        original = signal.signal(signal.SIGTERM, lambda *_: stopped.append(True))
        try:
            with self.assertRaises(PrivateWebInventoryReadCancelled):
                self.reader(client).read_ads(
                    stop_requested=lambda: bool(stopped),
                )
        finally:
            signal.signal(signal.SIGTERM, original)
        self.assertEqual(stopped, [True])
        self.assertEqual([method for method, _ in client.calls], ["Runtime.evaluate"])
        self.assertTrue(client.closed)

    def test_cancellable_browser_transport_denies_non_main_thread(self):
        client = FakeClient([payload([])])
        failures = []
        worker = threading.Thread(
            target=lambda: self._capture_cancellation(failures, self.reader(client)),
        )
        worker.start()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [PrivateWebInventoryReadSetupError])
        self.assertEqual(client.calls, [])

    @staticmethod
    def _capture_cancellation(failures, reader):
        try:
            reader.read_ads(stop_requested=lambda: False)
        except Exception as exc:
            failures.append(type(exc))

    def test_missing_signal_mask_is_setup_failure_not_cancellation(self):
        client = FakeClient([payload([])])
        with patch("mark_api.private_web_inventory_cdp.signal.pthread_sigmask", None):
            with self.assertRaises(PrivateWebInventoryReadSetupError):
                self.reader(client).read_ads(stop_requested=lambda: False)
        self.assertEqual(client.calls, [])
        self.assertTrue(client.closed)

    def test_production_builder_composes_browser_reader_not_denied_legacy_http(self):
        client = FakeClient([payload([{"id": "1234567890", "state": "active"}])])
        with (
            patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"),
            patch("mark_api.private_web_inventory_cdp._LoopbackCdpClient", return_value=client) as cdp,
            patch("mark_api.private_web_runtime.ManagementReadAdapter",
                  side_effect=AssertionError("legacy manager must stay denied")) as legacy,
            patch("mark_api.private_web_runtime.CdpCookieProvider.from_port",
                  side_effect=AssertionError("cookies must not be exported")) as cookies,
            patch("mark_api.private_web_runtime._NoRedirectManagementTransport",
                  side_effect=AssertionError("direct HTTP must never start")) as http,
        ):
            runtime = build_private_web_inventory_runtime(cdp_port=19610)
            self.assertEqual(client.calls, [])
            result = runtime.read_inventory()
            self.assertEqual(result.status, ReadStatus.SUCCESS_NONEMPTY)
            self.assertEqual(result.value[0].ad_id, "1234567890")
            cdp.assert_called_once()
            legacy.assert_not_called()
            cookies.assert_not_called()
            http.assert_not_called()
            runtime.close()
            self.assertTrue(client.closed)


    def test_synthetic_product_launcher_initial_sync_uses_injected_browser_reader(self):
        # Deliberately inject the real runtime factory behind a synthetic
        # fake-CDP test seam; the public default itself remains unentitled.
        import tempfile
        from pathlib import Path
        from mark_api.launcher import build_product_launcher
        from mark_api.storage import SnapshotStore

        client = FakeClient([payload([{
            "id": "1234567890", "state": "active", "title": "Eigene Anzeige",
            "viewCount": 7, "watchCount": 1,
        }])])
        observed = {}

        class DisabledWriteRuntime:
            server_address = ("127.0.0.1", 18766)
            def start(self):
                return self.server_address
            def close(self):
                return None

        def write_factory(**kwargs):
            observed["write"] = kwargs
            return DisabledWriteRuntime()

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"),
            patch("mark_api.launcher._require_private_web_cdp_authority"),
            patch("mark_api.private_web_inventory_cdp._LoopbackCdpClient",
                  return_value=client),
            patch("mark_api.private_web_runtime.ManagementReadAdapter",
                  side_effect=AssertionError("legacy HTTP path must remain denied")),
            patch("mark_api.private_web_runtime._NoRedirectManagementTransport",
                  side_effect=AssertionError("direct management requests forbidden")),
        ):
            db = Path(tmp) / "state.sqlite"
            launcher = build_product_launcher(
                db_path=db, cdp_port=19610, initialize_db=True,
                dashboard_port=0, write_port=0,
                runtime_factory=lambda **kw: build_private_web_inventory_runtime(**kw),
                write_runtime_factory=write_factory,
                clock=lambda: datetime(2026, 10, 19, tzinfo=timezone.utc),
            )
            try:
                self.assertEqual(launcher.startup_inventory_count, 1)
                self.assertEqual(launcher.startup_persisted_count, 1)
                self.assertTrue(launcher.writes_enabled)
                self.assertIn("#write_token", launcher.dashboard_url)
                self.assertEqual(SnapshotStore(db).tracked_ad_ids(), ("1234567890",))
                sync = SnapshotStore(db).latest_sync_attempt()
                self.assertIsNotNone(sync)
                self.assertEqual(sync.outcome, "success_nonempty")
                self.assertTrue(observed["write"]["core_writes_enabled"])
                self.assertTrue(observed["write"]["media_writes_enabled"])
                self.assertTrue(observed["write"]["access"].writes_enabled)
            finally:
                launcher.close()
            self.assertTrue(client.closed)

    def test_product_launcher_failed_browser_read_never_starts_write_server(self):
        import tempfile
        from pathlib import Path
        from mark_api.launcher import ProductLauncherError, build_product_launcher
        from mark_api.storage import SnapshotStore

        client = FakeClient([{"status": 403, "body": ""}])
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("mark_api.private_web_runtime.require_private_web_runtime_dependency"),
            patch("mark_api.launcher._require_private_web_cdp_authority"),
            patch("mark_api.private_web_inventory_cdp._LoopbackCdpClient",
                  return_value=client),
            patch("mark_api.launcher.build_private_web_write_api_runtime",
                  side_effect=AssertionError("write runtime must never start")) as writer,
        ):
            db = Path(tmp) / "state.sqlite"
            with self.assertRaises(ProductLauncherError):
                build_product_launcher(
                    db_path=db, cdp_port=19610, initialize_db=True,
                    runtime_factory=lambda **kw: build_private_web_inventory_runtime(**kw),
                    clock=lambda: datetime(2026, 10, 19, tzinfo=timezone.utc),
                )
            writer.assert_not_called()
            self.assertTrue(client.closed)
            self.assertEqual(SnapshotStore(db).tracked_ad_ids(), ())
            sync = SnapshotStore(db).latest_sync_attempt()
            self.assertIsNotNone(sync)
            self.assertEqual(sync.outcome, "failed")


class BrowserInventoryTechnicalBoundaryTests(unittest.TestCase):
    """Synthetic CDP reads work while origin, page and close checks stay strict."""

    def test_factory_is_lazy_until_read_and_inventory_never_navigates(self):
        effects = []

        class Factory:
            def __bool__(self):
                raise AssertionError("factory truthiness evaluated")
            def __call__(self):
                effects.append("created")
                return FakeClient([payload([])])

        reader = CdpManagementInventoryReader(
            cdp_port=19610, client_factory=Factory(),
        )
        self.assertEqual(effects, [])
        result = reader.read_ads()
        self.assertEqual(result.status, ReadStatus.SUCCESS_EMPTY)
        self.assertEqual(effects, ["created"])

    def test_preconnected_close_does_not_trigger_a_new_read(self):
        effects = []

        class EffectfulClient:
            def close(self):
                effects.append("closed")

        reader = CdpManagementInventoryReader(
            cdp_port=19610, preconnected_client=EffectfulClient(),
        )
        reader.close()
        self.assertEqual(effects, ["closed"])
        self.assertEqual(reader.read_ads().status, ReadStatus.TRANSPORT_ERROR)

    def test_raw_helpers_refuse_invalid_management_page_without_transport(self):
        client = FakeClient([payload([])])
        for invalid_page in (0, -1, True, "1", 9999999):
            with self.subTest(page=invalid_page):
                result = CdpManagementInventoryReader._page(client, invalid_page)
                self.assertEqual(result.status, ReadStatus.PARSE_ERROR)
        self.assertEqual(client.calls, [])

    def test_wrong_origin_or_editor_stays_passive_without_fetch(self):
        for origin, path in (
            ("https://example.invalid", "/m-meine-anzeigen.html"),
            ("https://www.kleinanzeigen.de", "/p-anzeige-bearbeiten.html"),
        ):
            with self.subTest(origin=origin, path=path):
                client = FakeClient([payload([])], origin=origin, path=path)
                reader = CdpManagementInventoryReader(
                    cdp_port=19610, client_factory=lambda: client,
                )
                self.assertEqual(reader.read_ads().status, ReadStatus.TRANSPORT_ERROR)
                self.assertEqual(
                    [method for method, _ in client.calls],
                    ["Runtime.evaluate"],
                )
                self.assertTrue(client.closed)


class BrowserInventoryDeniedTransportTests(unittest.TestCase):
    """Denied admission must make even injected raw CDP clients inert."""

    def test_denied_preconnected_close_cannot_execute_injected_client(self):
        effects = []

        class EffectfulClient:
            def close(self):
                effects.append("unexpected_transport")

        reader = CdpManagementInventoryReader(
            cdp_port=19610, preconnected_client=EffectfulClient(),
        )
        with patch(
            "mark_api.private_web_inventory_cdp._require_private_web_cdp_authority",
            side_effect=PrivateWebCdpError("not_admitted"),
        ):
            reader.close()
        self.assertEqual(effects, [])
        self.assertEqual(reader.read_ads().status, ReadStatus.TRANSPORT_ERROR)

    def test_denied_raw_helpers_cannot_touch_preconnected_client(self):
        client = FakeClient([payload([])])
        reader = CdpManagementInventoryReader(
            cdp_port=19610, preconnected_client=client,
        )
        with patch(
            "mark_api.private_web_inventory_cdp._require_private_web_cdp_authority",
            side_effect=PrivateWebCdpError("not_admitted"),
        ):
            with self.assertRaises(PrivateWebCdpError):
                CdpManagementInventoryReader._value(client, "2 + 2")
            with self.assertRaises(PrivateWebCdpError):
                reader._open_management(client)
            with self.assertRaises(PrivateWebCdpError):
                reader._page(client, 1)
            result = reader.read_ads()
            reader.close()
        self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual(client.calls, [])
        self.assertFalse(client.closed)


if __name__ == "__main__":
    unittest.main()