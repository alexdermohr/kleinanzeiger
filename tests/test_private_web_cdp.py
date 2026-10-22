from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from mark_api.domain import AdSnapshot, LifecycleState, OperationOutcome
from mark_api.orchestrator import SafeWriteOrchestrator
from mark_api.private_web import (
    PrivateWebCreateSnapshot,
    PrivateWebDeleteSnapshot,
    PrivateWebEditorSnapshot,
    PrivateWebEditorState,
    PrivateWebStateSnapshot,
    PrivateWebSubmitUnknownError,
)
from mark_api.private_web_cdp import (
    CdpCookieProvider,
    CdpPrivateWebOwnerReader,
    CdpPrivateWebPage,
    PrivateWebCdpError,
    PrivateWebCdpReadCancelled,
    PrivateWebCdpReadSetupError,
    PrivateWebCdpWriteNotAttemptedError,
    _LoopbackCdpClient,
    _proxy_free_loopback_opener,
    _validated_loopback_endpoint,
)
from mark_api.private_web_inventory_cdp import _cancellable_transport_entry
from mark_api.results import ReadResult, ReadStatus


AD_ID = "3524046688"


class FakeClient:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def call(self, method: str, params=None):
        actual = dict(params or {})
        self.calls.append((method, actual))
        return self.handler(method, actual)

    def close(self) -> None:
        self.closed = True


class FakeResponse:
    def __init__(
        self,
        payload: object,
        *,
        url: str = "http://127.0.0.1:19610/json/list",
    ) -> None:
        self._payload = json.dumps(payload).encode("utf-8")
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, size: int) -> bytes:
        return self._payload[:size]


class FakeSocket:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def send(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def recv(self) -> str:
        if not self.responses:
            raise AssertionError("unexpected recv")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return json.dumps(value)

    def close(self) -> None:
        self.closed = True


class FakeTcpSocket:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def bind_ready_editor(
    page: CdpPrivateWebPage,
    *,
    title: str = "Existing title",
    description: str = "Existing description",
) -> None:
    page._bound_ad_id = AD_ID
    page._last_ready_snapshot = PrivateWebEditorSnapshot(
        state=PrivateWebEditorState.READY,
        ad_id=AD_ID,
        title=title,
        description=description,
    )


def create_ready(
    *,
    title: str = "",
    description: str = "",
    price_amount: str = "",
) -> PrivateWebCreateSnapshot:
    return PrivateWebCreateSnapshot(
        state=PrivateWebEditorState.READY,
        title=title,
        description=description,
        price_amount=price_amount,
    )


def bind_ready_create(
    page: CdpPrivateWebPage,
    *,
    title: str = "Neue Vase",
    description: str = "Beschreibung",
    price_amount: str = "12",
) -> None:
    page._create_bound = True
    page._last_create_snapshot = create_ready(
        title=title,
        description=description,
        price_amount=price_amount,
    )


def page_with_results(*values):
    clients: list[FakeClient] = []
    remaining = list(values)

    def factory():
        if clients:
            raise AssertionError("unexpected second client")

        def handler(method, params):
            if method == "Runtime.evaluate":
                if not remaining:
                    raise AssertionError("unexpected Runtime.evaluate")
                value = remaining.pop(0)
                if isinstance(value, Exception):
                    raise value
                return {"result": {"type": "object", "value": value}}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        clients.append(client)
        return client

    return CdpPrivateWebPage(
        "http://127.0.0.1:19610",
        client_factory=factory,
    ), clients

class CdpPrivateWebPageTests(unittest.TestCase):

    # Existing class-level tests intentionally inject synthetic CDP sockets and
    # responses; bypass admission only for these in-memory unit test cases.
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        synthetic = patch("mark_api.private_web_cdp._require_private_web_cdp_authority")
        synthetic.start()
        cls.addClassCleanup(synthetic.stop)


    def test_open_create_form_uses_exact_labels_and_browser_input_only(self) -> None:
        ready_form = {
            "state": "ready",
            "title": "",
            "description": "",
            "price_amount": "",
        }
        runtime_values = [
            True,
            {
                "readyState": "complete",
                "origin": "https://www.kleinanzeigen.de",
                "path": "/p-anzeige-aufgeben.html",
                "oldDocument": False,
            },
            {"state": "ready", "x": 10.0, "y": 20.0},
            {"state": "ready", "x": 11.0, "y": 21.0},
            {"state": "ready", "x": 12.0, "y": 22.0},
            {"state": "ready", "x": 13.0, "y": 23.0},
            ready_form,
        ]

        def handler(method, params):
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                if not runtime_values:
                    raise AssertionError("unexpected Runtime.evaluate")
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )

        page.open_create_form(
            ("Haus & Garten", "Dekoration", "Weitere Dekoration")
        )

        self.assertEqual(runtime_values, [])
        self.assertTrue(page._create_bound)
        self.assertIsNone(page._last_create_snapshot)
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            8,
        )
        expressions = [
            str(params["expression"])
            for method, params in client.calls
            if method == "Runtime.evaluate" and "expression" in params
        ]
        joined = "\n".join(expressions)
        self.assertIn('"Haus & Garten"', joined)
        self.assertIn('"Dekoration"', joined)
        self.assertIn('"Weitere Dekoration"', joined)
        self.assertIn("/p-anzeige-aufgeben-schritt2.html", joined)
        category_expressions = [
            expression
            for expression in expressions
            if 'if ((link.innerText || "").trim() !==' in expression
        ]
        self.assertEqual(len(category_expressions), 3)
        for expression in category_expressions:
            self.assertIn("link.scrollIntoView", expression)
            self.assertIn('behavior: "instant"', expression)
            self.assertLess(
                expression.index("matches.length !== 1"),
                expression.index("link.scrollIntoView"),
            )
            self.assertLess(
                expression.index("link.scrollIntoView"),
                expression.index("document.elementFromPoint"),
            )
            javascript_check = subprocess.run(
                ["node", "--check", "-"],
                input=expression,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(
                javascript_check.returncode,
                0,
                javascript_check.stdout + javascript_check.stderr,
            )
        self.assertNotIn(".click(", joined)

    def test_open_create_form_never_retries_ambiguous_continue_input(self) -> None:
        runtime_values = [
            True,
            {
                "readyState": "complete",
                "origin": "https://www.kleinanzeigen.de",
                "path": "/p-anzeige-aufgeben.html",
                "oldDocument": False,
            },
            {"state": "ready", "x": 10.0, "y": 20.0},
            {"state": "ready", "x": 11.0, "y": 21.0},
            {"state": "ready", "x": 12.0, "y": 22.0},
            {"state": "ready", "x": 13.0, "y": 23.0},
            {
                "state": "ready",
                "title": "",
                "description": "",
                "price_amount": "",
            },
        ]
        input_calls = 0

        def handler(method, params):
            nonlocal input_calls
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                input_calls += 1
                if input_calls == 7:
                    raise PrivateWebCdpError("call")
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )

        page.open_create_form(
            ("Haus & Garten", "Dekoration", "Weitere Dekoration")
        )

        self.assertTrue(page._create_bound)
        self.assertEqual(input_calls, 7)
        self.assertEqual(runtime_values, [])

    def test_read_create_form_binds_only_supported_private_offer_baseline(self) -> None:
        client = FakeClient(
            lambda method, params: (
                {
                    "result": {
                        "type": "object",
                        "value": {
                            "state": "ready",
                            "title": "Neue Vase",
                            "description": "Beschreibung",
                            "price_amount": "12",
                        },
                    }
                }
                if method == "Runtime.evaluate"
                else (_ for _ in ()).throw(
                    AssertionError(f"unexpected method: {method}")
                )
            )
        )
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        page._create_bound = True

        snapshot = page.read_create_form()

        self.assertEqual(
            snapshot,
            create_ready(
                title="Neue Vase",
                description="Beschreibung",
                price_amount="12",
            ),
        )
        self.assertEqual(page._last_create_snapshot, snapshot)
        expression = str(client.calls[0][1]["expression"])
        for required in (
            'buyNowEligible.value !== "false"',
            'posterType.value !== "PRIVATE"',
            "addressVisibility.checked",
            "marketing.checked",
            "files[0].files.length !== 0",
            'adDraftUuid.value !== ""',
            'adId.value !== ""',
            'offer.value !== "OFFER"',
            'priceType.value !== "FIXED"',
            "unexpected.length !== 0",
            'normalizedButtonText(button) === "Anzeige aufgeben"',
            "city.form !== form",
            "street.form !== form",
            "addressVisibility.form !== form",
            "marketing.form !== form",
            "priceType.form !== form",
            "buyNowEligible.form !== form",
            "posterType.form !== form",
            "locationId.form !== form",
            "categoryId.form !== form",
            "adDraftUuid.form !== form",
            "adId.form !== form",
            "offer.form !== form",
            "wanted.form !== form",
            "files[0].form !== form",
        ):
            self.assertIn(required, expression)
        self.assertNotIn(".click(", expression)
        javascript_check = subprocess.run(
            ["node", "--check", "-"],
            input=expression,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(
            javascript_check.returncode,
            0,
            javascript_check.stdout + javascript_check.stderr,
        )

    def test_replace_create_title_uses_native_value_setter(self) -> None:
        runtime_values = [
            {
                "state": "ready",
                "title": "Alt",
                "description": "Beschreibung",
                "price_amount": "12",
            },
            True,
        ]

        def handler(method, params):
            if method == "Runtime.evaluate":
                return {
                    "result": {
                        "type": "object",
                        "value": runtime_values.pop(0),
                    }
                }
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        bind_ready_create(
            page,
            title="Alt",
            description="Beschreibung",
            price_amount="12",
        )

        page.replace_create_title("Neu")

        self.assertEqual(
            page._last_create_snapshot,
            create_ready(
                title="Neu",
                description="Beschreibung",
                price_amount="12",
            ),
        )
        expression = str(client.calls[1][1]["expression"])
        self.assertIn("Object.getOwnPropertyDescriptor", expression)
        self.assertIn("new InputEvent", expression)
        self.assertIn('element.getAttribute("name") !== "title"', expression)
        self.assertNotIn(".click(", expression)

    def test_create_field_limits_count_utf16_code_units_before_browser_access(self) -> None:
        client = FakeClient(
            lambda method, params: (_ for _ in ()).throw(
                AssertionError("browser must not be touched")
            )
        )
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )

        with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
            page.replace_create_title("😀" * 32 + "x")
        with self.assertRaises(ValueError):
            page.replace_create_title("😀" * 33)
        with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
            page.replace_create_description("😀" * 2000)
        with self.assertRaises(ValueError):
            page.replace_create_description("😀" * 2000 + "x")

        self.assertEqual(client.calls, [])

    def test_submit_create_uses_one_browser_input_pair_and_is_single_shot(self) -> None:
        client = FakeClient(
            lambda method, params: (
                {
                    "result": {
                        "type": "object",
                        "value": {"state": "ready", "x": 10.0, "y": 20.0},
                    }
                }
                if method == "Runtime.evaluate"
                else {}
                if method == "Input.dispatchMouseEvent"
                else (_ for _ in ()).throw(
                    AssertionError(f"unexpected method: {method}")
                )
            )
        )
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        bind_ready_create(page)

        page.submit_create()

        self.assertEqual(
            [method for method, _params in client.calls],
            [
                "Runtime.evaluate",
                "Input.dispatchMouseEvent",
                "Input.dispatchMouseEvent",
            ],
        )
        expression = str(client.calls[0][1]["expression"])
        self.assertIn('"Anzeige aufgeben"', expression)
        self.assertIn('"Neue Vase"', expression)
        self.assertIn('"Beschreibung"', expression)
        self.assertIn('"12"', expression)
        self.assertNotIn(".click(", expression)
        with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
            page.submit_create()
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            2,
        )

    def test_submit_create_provider_failure_after_input_is_unknown_no_retry(self) -> None:
        input_calls = 0

        def handler(method, params):
            nonlocal input_calls
            if method == "Runtime.evaluate":
                return {
                    "result": {
                        "type": "object",
                        "value": {"state": "ready", "x": 10.0, "y": 20.0},
                    }
                }
            if method == "Input.dispatchMouseEvent":
                input_calls += 1
                raise PrivateWebCdpError("call")
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        bind_ready_create(page)

        with self.assertRaises(PrivateWebSubmitUnknownError) as caught:
            page.submit_create()

        self.assertEqual(caught.exception.stage, "create_submit")
        self.assertEqual(input_calls, 1)
        with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
            page.submit_create()
        self.assertEqual(input_calls, 1)

    def test_endpoint_is_strict_loopback_http(self) -> None:
        self.assertEqual(
            _validated_loopback_endpoint("http://127.0.0.1:19610"),
            ("http://127.0.0.1:19610", 19610),
        )
        for invalid in (
            "https://127.0.0.1:19610",
            "http://localhost:19610",
            "http://127.0.0.1:19610/json",
            "http://127.0.0.1:19610?x=1",
            "http://user@127.0.0.1:19610",
            "http://127.0.0.1",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    _validated_loopback_endpoint(invalid)

    def test_nonfinite_timeouts_are_rejected(self) -> None:
        for constructor in (CdpCookieProvider, CdpPrivateWebPage):
            for timeout_seconds in (
                float("nan"),
                float("inf"),
                float("-inf"),
            ):
                with self.subTest(
                    constructor=constructor.__name__,
                    timeout_seconds=timeout_seconds,
                ):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        constructor(
                            "http://127.0.0.1:19610",
                            timeout_seconds=timeout_seconds,
                        )

    def test_open_editor_navigates_only_to_exact_ad_id(self) -> None:
        runtime_values = [
            True,
            {"readyState": "complete", "oldDocument": False},
            {
                "state": "ready",
                "ad_id": AD_ID,
                "title": "Existing title",
                "description": "Existing description",
            },
        ]

        def handler(method, params):
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )

        page.open_editor(AD_ID)

        self.assertEqual(client.calls[0][0], "Runtime.evaluate")
        self.assertIn(
            "__mark_private_web_navigation_probe_",
            str(client.calls[0][1]["expression"]),
        )
        self.assertEqual(client.calls[1][0], "Page.navigate")
        target = client.calls[1][1]["url"]
        parsed = urlparse(str(target))
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "www.kleinanzeigen.de")
        self.assertEqual(parsed.path, "/p-anzeige-bearbeiten.html")
        self.assertEqual(parse_qs(parsed.query), {"adId": [AD_ID]})
        self.assertFalse(client.closed)
        page.close()
        self.assertTrue(client.closed)

    def test_open_editor_waits_until_old_complete_document_is_replaced(self) -> None:
        runtime_values = [
            True,
            {"readyState": "complete", "oldDocument": True},
            {"readyState": "complete", "oldDocument": False},
            {
                "state": "ready",
                "ad_id": AD_ID,
                "title": "Existing title",
                "description": "Existing description",
            },
        ]

        def handler(method, params):
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
            sleep=lambda _seconds: None,
        )

        page.open_editor(AD_ID)

        self.assertEqual(runtime_values, [])
        self.assertEqual(
            [method for method, _params in client.calls],
            [
                "Runtime.evaluate",
                "Page.navigate",
                "Runtime.evaluate",
                "Runtime.evaluate",
                "Runtime.evaluate",
            ],
        )

    def test_open_editor_waits_for_client_rendered_editor_contract(self) -> None:
        runtime_values = [
            True,
            {"readyState": "complete", "oldDocument": False},
            {"state": "unknown"},
            {"readyState": "complete", "oldDocument": False},
            {
                "state": "ready",
                "ad_id": AD_ID,
                "title": "Existing title",
                "description": "Existing description",
            },
        ]

        def handler(method, params):
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
            sleep=lambda _seconds: None,
        )

        page.open_editor(AD_ID)

        self.assertEqual(runtime_values, [])
        self.assertEqual(
            [method for method, _params in client.calls],
            [
                "Runtime.evaluate",
                "Page.navigate",
                "Runtime.evaluate",
                "Runtime.evaluate",
                "Runtime.evaluate",
                "Runtime.evaluate",
            ],
        )
        first_contract_probe = client.calls[3][1]["expression"]
        second_contract_probe = client.calls[5][1]["expression"]
        for expression in (first_contract_probe, second_contract_probe):
            self.assertIn("#ad-title", expression)
            self.assertIn("#ad-description", expression)
            self.assertIn("Anzeige speichern", expression)
            self.assertIn("/p-anzeige-bearbeiten.html", expression)

    def test_open_editor_releases_known_challenge_for_classification(self) -> None:
        runtime_values = [
            True,
            {"readyState": "complete", "oldDocument": False},
            {"state": "captcha_required"},
        ]

        def handler(method, params):
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )

        page.open_editor(AD_ID)

        self.assertEqual(runtime_values, [])
        challenge_probe = client.calls[3][1]["expression"]
        self.assertIn('challengeText.includes("captcha")', challenge_probe)
        self.assertIn('input[type="password"]', challenge_probe)

    def test_open_editor_rejects_ready_snapshot_for_different_ad_id(self) -> None:
        wrong_ad_id = "9999999999"
        runtime_values = [
            True,
            {"readyState": "complete", "oldDocument": False},
            {
                "state": "ready",
                "ad_id": wrong_ad_id,
                "title": "Other title",
                "description": "Other description",
            },
        ]
        monotonic_values = iter((0.0, 0.2))

        def handler(method, params):
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
            timeout_seconds=0.1,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(monotonic_values),
        )

        with self.assertRaisesRegex(PrivateWebCdpError, "navigate"):
            page.open_editor(AD_ID)

        self.assertEqual(runtime_values, [])
        self.assertIsNone(page._bound_ad_id)
        self.assertIsNone(page._last_ready_snapshot)

    def test_wrong_target_ready_snapshot_does_not_arm_mutation(self) -> None:
        wrong_ad_id = "9999999999"
        client = FakeClient(
            lambda method, params: (
                {
                    "result": {
                        "type": "object",
                        "value": {
                            "state": "ready",
                            "ad_id": wrong_ad_id,
                            "title": "Other title",
                            "description": "Other description",
                        },
                    }
                }
                if method == "Runtime.evaluate"
                else (_ for _ in ()).throw(
                    AssertionError(f"unexpected method: {method}")
                )
            )
        )
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        page._bound_ad_id = AD_ID

        snapshot = page.read_editor()
        self.assertEqual(snapshot.state, PrivateWebEditorState.READY)
        self.assertEqual(snapshot.ad_id, wrong_ad_id)
        self.assertIsNone(page._last_ready_snapshot)

        with self.assertRaisesRegex(PrivateWebCdpError, "replace_title"):
            page.replace_title("Must not write")

        self.assertEqual(
            [method for method, _params in client.calls],
            ["Runtime.evaluate"],
        )

    def test_open_editor_times_out_while_editor_contract_is_unknown(self) -> None:
        runtime_values = [
            True,
            {"readyState": "complete", "oldDocument": False},
            {"state": "unknown"},
        ]
        monotonic_values = iter((0.0, 0.2))

        def handler(method, params):
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
            timeout_seconds=0.1,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(monotonic_values),
        )

        with self.assertRaisesRegex(PrivateWebCdpError, "navigate"):
            page.open_editor(AD_ID)

        self.assertEqual(runtime_values, [])

    def test_invalid_ad_id_never_touches_browser(self) -> None:
        calls = 0

        def factory():
            nonlocal calls
            calls += 1
            raise AssertionError("must not connect")

        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=factory,
        )
        with self.assertRaises(ValueError):
            page.open_editor("1;bad")
        self.assertEqual(calls, 0)

    def test_ready_snapshot_requires_exact_contract(self) -> None:
        page, clients = page_with_results(
            {
                "state": "ready",
                "ad_id": AD_ID,
                "title": "Existing title",
                "description": "Existing description",
            }
        )

        snapshot = page.read_editor()

        self.assertEqual(snapshot.state, PrivateWebEditorState.READY)
        self.assertEqual(snapshot.ad_id, AD_ID)
        self.assertEqual(snapshot.title, "Existing title")
        self.assertEqual(snapshot.description, "Existing description")
        expression = clients[0].calls[0][1]["expression"]
        self.assertIn("#ad-title", expression)
        self.assertIn("#ad-description", expression)
        self.assertIn("Anzeige speichern", expression)
        self.assertIn("/p-anzeige-bearbeiten.html", expression)
        self.assertIn('.join("\\n")', expression)
        javascript_check = subprocess.run(
            ["node", "--check", "-"],
            input=expression,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(
            javascript_check.returncode,
            0,
            javascript_check.stdout + javascript_check.stderr,
        )

    def test_captcha_detection_does_not_scan_entire_editor_body(self) -> None:
        page, clients = page_with_results(
            {
                "state": "ready",
                "ad_id": AD_ID,
                "title": "Captcha collection",
                "description": "Ordinary listing text mentioning captcha.",
            }
        )

        snapshot = page.read_editor()

        self.assertEqual(snapshot.state, PrivateWebEditorState.READY)
        expression = clients[0].calls[0][1]["expression"]
        self.assertNotIn("document.body", expression)
        self.assertNotIn('body.includes("captcha")', expression)
        self.assertIn('challengeText.includes("captcha")', expression)
        self.assertIn('[role="dialog"]', expression)
        self.assertIn('[aria-modal="true"]', expression)

    def test_challenge_states_return_no_ad_content(self) -> None:
        for raw_state in (
            "login_required",
            "mfa_required",
            "captcha_required",
            "security_challenge",
            "unknown",
        ):
            with self.subTest(raw_state=raw_state):
                page, _ = page_with_results({"state": raw_state})
                snapshot = page.read_editor()
                self.assertEqual(snapshot.state.value, raw_state)
                self.assertIsNone(snapshot.ad_id)
                self.assertIsNone(snapshot.title)
                self.assertIsNone(snapshot.description)

    def test_malformed_ready_snapshot_fails_closed(self) -> None:
        for value in (
            {"state": "ready", "ad_id": AD_ID, "title": 7, "description": "x"},
            {"state": "ready", "ad_id": "bad", "title": "x", "description": "y"},
            {"state": "not-a-state"},
            "not-a-dict",
        ):
            with self.subTest(value=value):
                page, _ = page_with_results(value)
                snapshot = page.read_editor()
                self.assertEqual(snapshot.state, PrivateWebEditorState.UNKNOWN)

    def test_replace_title_and_description_are_atomic_and_target_bound(self) -> None:
        client = FakeClient(
            lambda method, params: (
                {"result": {"type": "boolean", "value": True}}
                if method == "Runtime.evaluate"
                else (_ for _ in ()).throw(
                    AssertionError(f"unexpected method: {method}")
                )
            )
        )
        factory_calls = 0

        def factory():
            nonlocal factory_calls
            factory_calls += 1
            return client

        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=factory,
        )
        bind_ready_editor(page)

        page.replace_title("New title")
        page.replace_description("New description")

        self.assertEqual(factory_calls, 1)
        self.assertEqual(
            [method for method, _params in client.calls],
            ["Runtime.evaluate", "Runtime.evaluate"],
        )
        title_expression = client.calls[0][1]["expression"]
        description_expression = client.calls[1][1]["expression"]
        for expression in (title_expression, description_expression):
            self.assertIn("location.origin", expression)
            self.assertIn("/p-anzeige-bearbeiten.html", expression)
            self.assertIn(AD_ID, expression)
            self.assertIn("Object.getOwnPropertyDescriptor", expression)
            self.assertIn('new InputEvent(', expression)
            self.assertIn('new Event("change"', expression)
        self.assertIn("Existing title", title_expression)
        self.assertIn("Existing description", title_expression)
        self.assertIn("New title", description_expression)
        self.assertIn("Existing description", description_expression)
        self.assertNotIn("Input.dispatchKeyEvent", str(client.calls))
        self.assertNotIn("Input.insertText", str(client.calls))
        self.assertFalse(client.closed)
        page.close()
        self.assertTrue(client.closed)

    def test_replace_failure_is_sanitized(self) -> None:
        def handler(method, params):
            if method == "Runtime.evaluate":
                raise RuntimeError(
                    "provider https://example.invalid Cookie sensitive listing"
                )
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        bind_ready_editor(page)

        with self.assertRaises(PrivateWebCdpError) as caught:
            page.replace_title("sensitive listing text")

        self.assertEqual(caught.exception.stage, "replace_title")
        self.assertEqual(
            str(caught.exception),
            "private web cdp failed at replace_title",
        )
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertEqual([method for method, _params in client.calls], ["Runtime.evaluate"])
        self.assertFalse(client.closed)
        page.close()
        self.assertTrue(client.closed)

    def test_replace_requires_bound_verified_editor_before_browser_mutation(self) -> None:
        client = FakeClient(
            lambda method, params: (_ for _ in ()).throw(
                AssertionError("browser must not be touched")
            )
        )
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )

        with self.assertRaises(PrivateWebCdpError) as caught:
            page.replace_title("New title")

        self.assertEqual(caught.exception.stage, "replace_title")
        self.assertEqual(client.calls, [])

    def test_replace_rejects_editor_drift_in_same_mutation_expression(self) -> None:
        client = FakeClient(
            lambda method, params: (
                {"result": {"type": "boolean", "value": False}}
                if method == "Runtime.evaluate"
                else (_ for _ in ()).throw(
                    AssertionError(f"unexpected method: {method}")
                )
            )
        )
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        bind_ready_editor(page)

        with self.assertRaises(PrivateWebCdpError) as caught:
            page.replace_description("New description")

        self.assertEqual(caught.exception.stage, "replace_description")
        self.assertEqual(
            [method for method, _params in client.calls],
            ["Runtime.evaluate"],
        )
        expression = client.calls[0][1]["expression"]
        self.assertIn(AD_ID, expression)
        self.assertIn("Existing title", expression)
        self.assertIn("Existing description", expression)
        self.assertIn("descriptor.set.call", expression)

    def test_submit_is_atomic_and_bound_to_verified_editor(self) -> None:
        def handler(method, params):
            if method == "Runtime.evaluate":
                expression = params.get("expression")
                if "scrollIntoView" in str(expression):
                    return {
                        "result": {
                            "type": "object",
                            "value": {"x": 120.5, "y": 240.25},
                        }
                    }
                return {
                    "result": {
                        "type": "string",
                        "value": "confirmed",
                    }
                }
            if method == "Input.dispatchMouseEvent":
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        bind_ready_editor(page)

        page.submit()

        with self.assertRaises(PrivateWebCdpError) as caught:
            page.submit()
        self.assertEqual(caught.exception.stage, "submit_already_attempted")
        self.assertEqual(
            [method for method, _params in client.calls],
            [
                "Runtime.evaluate",
                "Input.dispatchMouseEvent",
                "Input.dispatchMouseEvent",
                "Runtime.evaluate",
            ],
        )
        expression = client.calls[0][1]["expression"]
        self.assertIn("location.origin", expression)
        self.assertIn("/p-anzeige-bearbeiten.html", expression)
        self.assertIn(AD_ID, expression)
        self.assertIn("Existing title", expression)
        self.assertIn("Existing description", expression)
        self.assertIn("Anzeige speichern", expression)
        self.assertIn("getBoundingClientRect", expression)
        self.assertIn("elementFromPoint", expression)
        self.assertIn("Number.isFinite(x)", expression)
        self.assertNotIn("button.click()", expression)
        activation_javascript_check = subprocess.run(
            ["node", "--check", "-"],
            input=expression,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(
            activation_javascript_check.returncode,
            0,
            activation_javascript_check.stdout + activation_javascript_check.stderr,
        )
        pressed = client.calls[1][1]
        released = client.calls[2][1]
        self.assertEqual(
            pressed,
            {
                "type": "mousePressed",
                "x": 120.5,
                "y": 240.25,
                "button": "left",
                "buttons": 1,
                "clickCount": 1,
            },
        )
        self.assertEqual(
            released,
            {
                "type": "mouseReleased",
                "x": 120.5,
                "y": 240.25,
                "button": "left",
                "buttons": 0,
                "clickCount": 1,
            },
        )
        confirmation_expression = client.calls[3][1]["expression"]
        self.assertIn("location.origin", confirmation_expression)
        self.assertIn("/m-meine-anzeigen.html", confirmation_expression)
        self.assertIn('currentPath.startsWith("/u/login/")', confirmation_expression)
        self.assertIn('challengeText.includes("captcha")', confirmation_expression)
        javascript_check = subprocess.run(
            ["node", "--check", "-"],
            input=confirmation_expression,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(
            javascript_check.returncode,
            0,
            javascript_check.stdout + javascript_check.stderr,
        )
        self.assertFalse(client.closed)
        page.close()
        self.assertTrue(client.closed)

    def test_submit_requires_bound_verified_snapshot_before_browser_mutation(self) -> None:
        client = FakeClient(
            lambda method, params: (_ for _ in ()).throw(
                AssertionError("browser must not be touched")
            )
        )
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )

        with self.assertRaises(PrivateWebCdpError) as caught:
            page.submit()

        self.assertEqual(caught.exception.stage, "submit")
        self.assertEqual(client.calls, [])
        with self.assertRaises(PrivateWebCdpError) as second:
            page.submit()
        self.assertEqual(second.exception.stage, "submit_already_attempted")

    def test_submit_rejects_editor_drift_before_activation(self) -> None:
        def handler(method, params):
            if method != "Runtime.evaluate":
                raise AssertionError(f"unexpected method: {method}")
            return {"result": {"type": "boolean", "value": False}}

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        bind_ready_editor(page)

        with self.assertRaises(PrivateWebCdpError) as caught:
            page.submit()

        self.assertEqual(caught.exception.stage, "submit")
        self.assertEqual(
            [method for method, _params in client.calls],
            ["Runtime.evaluate"],
        )
        expression = client.calls[0][1]["expression"]
        self.assertIn(AD_ID, expression)
        self.assertIn("Existing title", expression)
        self.assertIn("Existing description", expression)
        self.assertNotIn("button.click()", expression)

    def test_submit_rejects_invalid_activation_coordinates_before_mouse_dispatch(self) -> None:
        client = FakeClient(
            lambda method, params: {
                "result": {
                    "type": "object",
                    "value": {"x": True, "y": 10.0},
                }
            }
        )
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        bind_ready_editor(page)

        with self.assertRaises(PrivateWebCdpError) as caught:
            page.submit()

        self.assertEqual(caught.exception.stage, "submit")
        self.assertEqual(
            [method for method, _params in client.calls],
            ["Runtime.evaluate"],
        )
        with self.assertRaises(PrivateWebCdpError) as second:
            page.submit()
        self.assertEqual(second.exception.stage, "submit_already_attempted")

    def test_submit_staying_in_editor_is_unconfirmed_and_not_retryable(self) -> None:
        times = iter((0.0, 10.0))

        def handler(method, params):
            if method == "Input.dispatchMouseEvent":
                return {}
            if method != "Runtime.evaluate":
                raise AssertionError(f"unexpected method: {method}")
            expression = params.get("expression")
            if "scrollIntoView" in str(expression):
                return {
                    "result": {
                        "type": "object",
                        "value": {"x": 100.0, "y": 200.0},
                    }
                }
            return {"result": {"type": "string", "value": "pending"}}

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            timeout_seconds=5,
            client_factory=lambda: client,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(times),
        )
        bind_ready_editor(page)

        with self.assertRaises(PrivateWebCdpError) as caught:
            page.submit()
        self.assertEqual(caught.exception.stage, "submit_unconfirmed")

        with self.assertRaises(PrivateWebCdpError) as second:
            page.submit()
        self.assertEqual(second.exception.stage, "submit_already_attempted")
        self.assertEqual(
            [method for method, _params in client.calls],
            [
                "Runtime.evaluate",
                "Input.dispatchMouseEvent",
                "Input.dispatchMouseEvent",
                "Runtime.evaluate",
            ],
        )
        page.close()

    def test_submit_untrusted_redirect_is_unconfirmed_and_not_retryable(self) -> None:
        def handler(method, params):
            if method == "Input.dispatchMouseEvent":
                return {}
            if method != "Runtime.evaluate":
                raise AssertionError(f"unexpected method: {method}")
            expression = params.get("expression")
            if "scrollIntoView" in str(expression):
                return {
                    "result": {
                        "type": "object",
                        "value": {"x": 100.0, "y": 200.0},
                    }
                }
            return {"result": {"type": "string", "value": "unconfirmed"}}

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        bind_ready_editor(page)

        with self.assertRaises(PrivateWebCdpError) as caught:
            page.submit()
        self.assertEqual(caught.exception.stage, "submit_unconfirmed")

        with self.assertRaises(PrivateWebCdpError) as second:
            page.submit()
        self.assertEqual(second.exception.stage, "submit_already_attempted")

        confirmation_expression = client.calls[3][1]["expression"]
        self.assertIn('currentOrigin !== "https://www.kleinanzeigen.de"', confirmation_expression)
        self.assertIn('currentPath.startsWith("/u/login/")', confirmation_expression)
        self.assertIn('challengeText.includes("captcha")', confirmation_expression)
        self.assertIn('challengeText.includes("sicherheitsprüfung")', confirmation_expression)
        self.assertIn("/m-meine-anzeigen.html", confirmation_expression)

    def test_ambiguous_submit_failure_cannot_be_retried(self) -> None:
        def handler(method, params):
            if method != "Runtime.evaluate":
                raise AssertionError(f"unexpected method: {method}")
            raise RuntimeError(
                "provider https://example.invalid Cookie secret listing content"
            )

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        bind_ready_editor(page)

        with self.assertRaises(PrivateWebCdpError) as caught:
            page.submit()
        self.assertEqual(caught.exception.stage, "submit")
        self.assertNotIn("Cookie", str(caught.exception))

        with self.assertRaises(PrivateWebCdpError) as second:
            page.submit()
        self.assertEqual(second.exception.stage, "submit_already_attempted")
        self.assertEqual(
            [method for method, _params in client.calls],
            ["Runtime.evaluate"],
        )
        self.assertFalse(client.closed)
        page.close()
        self.assertTrue(client.closed)

    def test_mouse_press_failure_is_ambiguous_and_not_retryable(self) -> None:
        def handler(method, params):
            if method == "Runtime.evaluate":
                return {
                    "result": {
                        "type": "object",
                        "value": {"x": 100.0, "y": 200.0},
                    }
                }
            if method == "Input.dispatchMouseEvent":
                # Mirror _LoopbackCdpClient.call's production sanitization.
                raise PrivateWebCdpError("call")
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        bind_ready_editor(page)

        with self.assertRaises(PrivateWebCdpError) as caught:
            page.submit()
        self.assertEqual(caught.exception.stage, "submit")
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(
            [method for method, _params in client.calls],
            ["Runtime.evaluate", "Input.dispatchMouseEvent"],
        )
        with self.assertRaises(PrivateWebCdpError) as second:
            page.submit()
        self.assertEqual(second.exception.stage, "submit_already_attempted")

    def test_mouse_release_failure_is_ambiguous_and_not_retryable(self) -> None:
        dispatches = 0

        def handler(method, params):
            nonlocal dispatches
            if method == "Runtime.evaluate":
                return {
                    "result": {
                        "type": "object",
                        "value": {"x": 100.0, "y": 200.0},
                    }
                }
            if method == "Input.dispatchMouseEvent":
                dispatches += 1
                if dispatches == 1:
                    return {}
                # Mirror _LoopbackCdpClient.call's production sanitization.
                raise PrivateWebCdpError("call")
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        bind_ready_editor(page)

        with self.assertRaises(PrivateWebCdpError) as caught:
            page.submit()
        self.assertEqual(caught.exception.stage, "submit")
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(
            [method for method, _params in client.calls],
            [
                "Runtime.evaluate",
                "Input.dispatchMouseEvent",
                "Input.dispatchMouseEvent",
            ],
        )
        with self.assertRaises(PrivateWebCdpError) as second:
            page.submit()
        self.assertEqual(second.exception.stage, "submit_already_attempted")

    def test_cookie_provider_releases_each_client_before_next_read(self) -> None:
        clients = []

        def factory():
            client = FakeClient(
                lambda method, params: {
                    "cookies": [
                        {"name": "session", "value": "opaque-one"},
                        {"name": "other", "value": "opaque-two"},
                    ]
                }
            )
            clients.append(client)
            return client

        provider = CdpCookieProvider(
            "http://127.0.0.1:19610",
            client_factory=factory,
        )

        first = provider()
        second = provider()

        self.assertEqual(first, "session=opaque-one; other=opaque-two")
        self.assertEqual(second, first)
        self.assertEqual(len(clients), 2)
        self.assertTrue(all(client.closed for client in clients))
        self.assertEqual(
            [[method for method, _params in client.calls] for client in clients],
            [["Network.getCookies"], ["Network.getCookies"]],
        )
        self.assertEqual(
            clients[0].calls[0][1]["urls"],
            ["https://www.kleinanzeigen.de/m-meine-anzeigen-verwalten.json"],
        )
        provider.close()

    def test_cookie_lookup_releases_kernel_target_before_editor(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX abstract target reservation required")
        endpoint = "http://127.0.0.1:19616"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19616/devtools/page/opaque-target",
        }
        created = []

        def new_client(cookies):
            sock = FakeSocket(
                [{"id": 1, "result": {"cookies": [
                    {"name": "session", "value": "opaque-one"},
                ]}}] if cookies else []
            )
            created.append(sock)
            return _LoopbackCdpClient(
                endpoint, timeout_seconds=1,
                opener=lambda url, **kwargs: FakeResponse([target], url=url),
                websocket_factory=lambda *args, **kwargs: sock,
                tcp_socket_factory=lambda *args, **kwargs: FakeTcpSocket(),
            )

        provider = CdpCookieProvider(
            endpoint, client_factory=lambda: new_client(True),
        )
        self.assertEqual(provider(), "session=opaque-one")
        self.assertTrue(created[0].closed)
        # Exact bug: retained cookie client would still own the port lock and
        # forbid the editor opening even though the cookie read had completed.
        page = CdpPrivateWebPage(
            endpoint, client_factory=lambda: new_client(False),
        )
        editor_client = page._client()
        self.assertIsNotNone(editor_client)
        page.close()
        self.assertEqual(len(created), 2)
        self.assertTrue(all(sock.closed for sock in created))
        provider.close()

    def test_interrupt_during_ws_handshake_quarantines_uncertain_raw_socket(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX abstract target reservation required")
        endpoint = "http://127.0.0.1:19617"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19617/devtools/page/opaque-target",
        }
        effects = []
        class UncertainRaw:
            def close(self):
                effects.append("raw_close_attempt")
                raise OSError("uncertain close")

        def new_client(factory, tcp_factory=None):
            return _LoopbackCdpClient(
                endpoint, timeout_seconds=1,
                opener=lambda url, **kw: (
                    effects.append("http_discovery") or
                    FakeResponse([target], url=url)
                ),
                websocket_factory=factory,
                tcp_socket_factory=tcp_factory or (
                    lambda *args, **kwargs: UncertainRaw()
                ),
            )

        def interrupt_handshake(*args, **kwargs):
            effects.append("ws_handshake_started")
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            new_client(interrupt_handshake)
        with self.assertRaises(PrivateWebCdpError) as blocked:
            new_client(
                lambda *args, **kwargs: FakeSocket([]),
                tcp_factory=lambda *args, **kwargs: FakeTcpSocket(),
            )
        self.assertEqual(blocked.exception.stage, "target_busy")
        self.assertEqual(effects, [
            "http_discovery", "ws_handshake_started",
            "raw_close_attempt",
        ])
        # Deliberately don't clear process-lifetime quarantine of ambiguous
        # client: a new target reader must stay blocked until the process exits.

    def test_cookie_provider_empty_or_malformed_is_fail_closed(self) -> None:
        empty_client = FakeClient(
            lambda method, params: {"cookies": []}
        )
        empty = CdpCookieProvider(
            "http://127.0.0.1:19610",
            client_factory=lambda: empty_client,
        )
        self.assertIsNone(empty())

        bad_client = FakeClient(
            lambda method, params: {
                "cookies": [
                    {"name": "session", "value": "secret\r\nInjected: yes"}
                ]
            }
        )
        bad = CdpCookieProvider(
            "http://127.0.0.1:19610",
            client_factory=lambda: bad_client,
        )
        with self.assertRaises(PrivateWebCdpError) as caught:
            bad()
        self.assertEqual(str(caught.exception), "private web cdp failed at cookies")
        self.assertNotIn("secret", str(caught.exception))

    def test_proxy_free_loopback_opener_has_no_configured_proxies_or_redirects(self) -> None:
        opener = _proxy_free_loopback_opener()
        director = opener.__self__
        proxy_handlers = [
            handler
            for handler in director.handlers
            if handler.__class__.__name__ == "ProxyHandler"
        ]
        redirect_handlers = [
            handler
            for handler in director.handlers
            if handler.__class__.__name__ == "_RejectRedirectHandler"
        ]
        self.assertEqual(proxy_handlers, [])
        self.assertEqual(len(redirect_handlers), 1)
        self.assertIsNone(
            redirect_handlers[0].redirect_request(
                None,
                None,
                302,
                "Found",
                {},
                "https://example.invalid/",
            )
        )

    def test_proxy_free_opener_ignores_environment_proxy_configuration(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://127.0.0.1:9",
                "http_proxy": "http://127.0.0.1:9",
                "NO_PROXY": "",
                "no_proxy": "",
            },
            clear=False,
        ):
            opener = _proxy_free_loopback_opener()

        director = opener.__self__
        proxy_handlers = [
            handler
            for handler in director.handlers
            if handler.__class__.__name__ == "ProxyHandler"
        ]
        self.assertEqual(proxy_handlers, [])

    def test_loopback_client_rejects_discovery_final_url_escape(self) -> None:
        tcp_calls = 0

        def tcp_socket_factory(*args, **kwargs):
            nonlocal tcp_calls
            tcp_calls += 1
            raise AssertionError("TCP connection must not be attempted")

        with self.assertRaises(PrivateWebCdpError) as caught:
            _LoopbackCdpClient(
                "http://127.0.0.1:19610",
                timeout_seconds=1,
                opener=lambda *args, **kwargs: FakeResponse(
                    [],
                    url="http://127.0.0.1:19610/redirected",
                ),
                websocket_factory=lambda *args, **kwargs: FakeSocket([]),
                tcp_socket_factory=tcp_socket_factory,
            )

        self.assertEqual(caught.exception.stage, "connect")
        self.assertEqual(tcp_calls, 0)

    def test_loopback_client_reuses_one_page_websocket(self) -> None:
        good_target = {
            "type": "page",
            "webSocketDebuggerUrl": (
                "ws://127.0.0.1:19610/devtools/page/opaque-target"
            ),
        }
        socket = FakeSocket(
            [
                {"method": "Page.event", "params": {}},
                {"id": 1, "result": {"value": 1}},
                {"id": 2, "result": {"value": 2}},
            ]
        )
        raw_socket = FakeTcpSocket()
        tcp_calls: list[tuple[object, object]] = []
        websocket_kwargs: dict[str, object] = {}

        def tcp_socket_factory(address, *, timeout):
            tcp_calls.append((address, timeout))
            return raw_socket

        def websocket_factory(*args, **kwargs):
            websocket_kwargs.update(kwargs)
            return socket

        client = _LoopbackCdpClient(
            "http://127.0.0.1:19610",
            timeout_seconds=1,
            opener=lambda *args, **kwargs: FakeResponse([good_target]),
            websocket_factory=websocket_factory,
            tcp_socket_factory=tcp_socket_factory,
        )

        self.assertEqual(tcp_calls, [(("127.0.0.1", 19610), 1)])
        self.assertIs(websocket_kwargs["socket"], raw_socket)
        self.assertEqual(client.call("Network.getCookies"), {"value": 1})
        self.assertEqual(client.call("Network.getCookies"), {"value": 2})
        self.assertEqual(
            [message["method"] for message in socket.sent],
            ["Network.getCookies", "Network.getCookies"],
        )
        self.assertEqual([message["id"] for message in socket.sent], [1, 2])
        client.close()
        self.assertTrue(socket.closed)

    def test_kernel_cdp_target_claim_prevents_second_discovery_and_process(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract UNIX socket namespace is required")
        # No real Chrome: only a kernel claim and fake HTTP/TCP/WebSocket.
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19612/devtools/page/opaque-target",
        }
        effects = []

        def open_fake(url, **kwargs):
            effects.append("discovery")
            return FakeResponse([target], url=url)

        def build():
            return _LoopbackCdpClient(
                "http://127.0.0.1:19612",
                timeout_seconds=1,
                opener=open_fake,
                websocket_factory=lambda *args, **kwargs: FakeSocket([]),
                tcp_socket_factory=lambda *args, **kwargs: (
                    effects.append("tcp") or FakeTcpSocket()
                ),
            )

        first = build()
        try:
            self.assertEqual(effects, ["discovery", "tcp"])
            with self.assertRaises(PrivateWebCdpError) as caught:
                build()
            self.assertEqual(caught.exception.stage, "target_busy")
            self.assertEqual(effects, ["discovery", "tcp"])
            # The kernel reservation must also reject another Python process,
            # even though it has no reference to this client's lock object.
            child = (
                "import socket,sys\n"
                "sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
                "try:\n"
                ' sock.bind(bytes([0])+b"mark-api-cdp-target-v1:19612")\n'
                "except OSError:\n"
                " print('cross_process_target_busy')\n"
                "else:\n"
                " sys.exit(4)\n"
                "finally:\n"
                " sock.close()\n"
            )
            result = subprocess.run(
                [sys.executable, "-B", "-c", child],
                capture_output=True, text=True, check=False, timeout=4,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("cross_process_target_busy", result.stdout)
        finally:
            first.close()

        second = build()
        try:
            self.assertEqual(
                effects, ["discovery", "tcp", "discovery", "tcp"]
            )
        finally:
            second.close()

    def test_failed_discovery_and_interrupt_release_exclusive_target(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract UNIX socket namespace is required")
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19612/devtools/page/opaque-target",
        }

        def build(opener):
            return _LoopbackCdpClient(
                "http://127.0.0.1:19612",
                timeout_seconds=1,
                opener=opener,
                websocket_factory=lambda *args, **kwargs: FakeSocket([]),
                tcp_socket_factory=lambda *args, **kwargs: FakeTcpSocket(),
            )

        with self.assertRaises(PrivateWebCdpError) as failure:
            build(lambda *args, **kwargs: (_ for _ in ()).throw(
                OSError("synthetic failure")
            ))
        self.assertEqual(failure.exception.stage, "connect")
        with self.assertRaises(KeyboardInterrupt):
            build(lambda *args, **kwargs: (_ for _ in ()).throw(
                KeyboardInterrupt()
            ))
        recovered = build(
            lambda url, **kwargs: FakeResponse([target], url=url)
        )
        recovered.close()

    def test_uncertain_websocket_close_keeps_target_quarantined(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract UNIX socket namespace is required")
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19614/devtools/page/opaque-target",
        }
        effects = []

        class FailingSocket(FakeSocket):
            def close(self):
                raise OSError("untrusted cleanup detail")

        def build(socket_factory):
            return _LoopbackCdpClient(
                "http://127.0.0.1:19614",
                timeout_seconds=1,
                opener=lambda url, **kwargs: (
                    effects.append("discovery") or
                    FakeResponse([target], url=url)
                ),
                websocket_factory=lambda *args, **kwargs: socket_factory(),
                tcp_socket_factory=lambda *args, **kwargs: FakeTcpSocket(),
            )

        client = build(lambda: FailingSocket([]))
        with self.assertRaises(PrivateWebCdpError) as failure:
            client.close()
        self.assertEqual(failure.exception.stage, "close")
        self.assertNotIn("untrusted cleanup detail", str(failure.exception))
        with self.assertRaises(PrivateWebCdpError) as blocked:
            build(lambda: FakeSocket([]))
        self.assertEqual(blocked.exception.stage, "target_busy")
        self.assertEqual(effects, ["discovery"])
        # Do NOT free the quarantine: it represents an UNKNOWN close result.
        # The kernel drops this synthetic claim when the test process exits.

    def test_direct_cdp_connect_without_reservation_rejects_before_discovery(self) -> None:
        client = object.__new__(_LoopbackCdpClient)
        client._port = 19612
        client._endpoint = "http://127.0.0.1:19612"
        effects = []
        client._opener = lambda *args, **kwargs: effects.append("discovery")
        with self.assertRaises(PrivateWebCdpError) as caught:
            client._connect()
        self.assertEqual(caught.exception.stage, "target_unowned")
        self.assertEqual(effects, [])


    def test_unowned_cached_socket_cannot_call_cdp(self) -> None:
        client = object.__new__(_LoopbackCdpClient)
        client._port = 19612
        client._target_reservation = None
        client._next_id = 0
        socket = FakeSocket([{"id": 1, "result": {}}])
        client._socket = socket
        with self.assertRaises(PrivateWebCdpWriteNotAttemptedError) as caught:
            client.call("Page.navigate", {"url": "https://example.invalid/"})
        self.assertEqual(caught.exception.stage, "target_unowned")
        self.assertEqual(socket.sent, [])

    def test_interrupt_after_quarantine_append_keeps_kernel_claim(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract UNIX socket namespace required")
        endpoint = "http://127.0.0.1:19618"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19618/devtools/page/synthetic",
        }

        # Simulate SIGINT at the exact unsafe ownership-transfer boundary:
        # the global holder already has the claim but the client still does.
        import mark_api.private_web_cdp as cdp_module

        def interrupt_after_append(client, transport):
            claim = client._target_reservation
            list.append(cdp_module._QUARANTINED_CDP_TARGETS, (claim, transport))
            raise KeyboardInterrupt()

        class UncertainRaw:
            def close(self):
                raise OSError("unresolved raw socket close")

        def make_client(raw_factory, websocket_factory):
            return _LoopbackCdpClient(
                endpoint,
                timeout_seconds=1,
                opener=lambda url, **kwargs: FakeResponse([target], url=url),
                tcp_socket_factory=raw_factory,
                websocket_factory=websocket_factory,
            )

        with patch.object(
            _LoopbackCdpClient, "_quarantine_transport",
            interrupt_after_append,
        ):
            with self.assertRaises(KeyboardInterrupt):
                make_client(
                    lambda *args, **kwargs: UncertainRaw(),
                    lambda *args, **kwargs: (_ for _ in ()).throw(
                        KeyboardInterrupt()
                    ),
                )
            with self.assertRaises(PrivateWebCdpError) as blocked:
                make_client(
                    lambda *args, **kwargs: FakeTcpSocket(),
                    lambda *args, **kwargs: FakeSocket([]),
                )
            self.assertEqual(blocked.exception.stage, "target_busy")

    def test_unknown_media_submit_retains_target_during_reconnect(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract UNIX socket namespace required")
        from mark_api.private_web_cdp_media import CdpPrivateWebMediaPage

        endpoint = "http://127.0.0.1:19619"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19619/devtools/page/synthetic",
        }
        sockets = [
            FakeSocket([]),
            FakeSocket([
                {"id": 1, "result": {
                    "result": {"type": "string", "value": "confirmed"}
                }},
            ]),
        ]
        created = []

        def open_websocket(*args, **kwargs):
            sock = sockets.pop(0)
            created.append(sock)
            return sock

        def build():
            return _LoopbackCdpClient(
                endpoint,
                timeout_seconds=1,
                opener=lambda url, **kwargs: FakeResponse([target], url=url),
                websocket_factory=open_websocket,
                tcp_socket_factory=lambda *args, **kwargs: FakeTcpSocket(),
            )

        owner = build()
        page = CdpPrivateWebMediaPage(
            endpoint,
            client_factory=lambda: owner,
            monotonic=lambda: 0.0,
            sleep=lambda _: None,
        )
        page._client_instance = owner
        page._media_submit_unsettled = True
        # Exercise real low-level settlement ownership, not just a page flag.
        page._media_unknown_client = owner
        owner.arm_unknown_submit(
            settlement_expression=page._media_submit_settlement_expression(),
        )
        try:
            page._discard_media_submit_client()
            self.assertIs(page._client_instance, owner)
            self.assertEqual(len(created), 2)
            self.assertTrue(created[0].closed)
            with self.assertRaises(PrivateWebCdpError) as blocked:
                build()
            self.assertEqual(blocked.exception.stage, "target_busy")
            # Separate process cannot bind even while the old WS was rotated.
            child = (
                "import socket,sys\n"
                "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
                "try:\n"
                " s.bind(bytes([0])+b'mark-api-cdp-target-v1:19619')\n"
                "except OSError:\n"
                " sys.exit(0)\n"
                "else:\n"
                " sys.exit(3)\n"
                "finally:\n"
                " s.close()\n"
            )
            child_result = subprocess.run(
                [sys.executable, "-B", "-c", child],
                capture_output=True, text=True, check=False, timeout=4,
            )
            self.assertEqual(child_result.returncode, 0, child_result.stderr)
            page.reconcile_create_media_submit()
            self.assertFalse(page._media_submit_unsettled)
        finally:
            if page._media_submit_unsettled:
                page._media_submit_unsettled = False
            page.close()


    def test_transient_observation_reconnect_retains_exclusive_claim(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract UNIX socket namespace required")
        endpoint = "http://127.0.0.1:19620"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19620/devtools/page/synthetic",
        }
        discovery_calls = 0
        sockets = [
            FakeSocket([]),
            FakeSocket([{"id": 1, "result": {"value": 3}}]),
        ]

        def opener(url, **kwargs):
            nonlocal discovery_calls
            discovery_calls += 1
            if discovery_calls == 2:
                raise OSError("synthetic observation discovery failure")
            return FakeResponse([target], url=url)

        def build():
            return _LoopbackCdpClient(
                endpoint, timeout_seconds=1,
                opener=opener,
                websocket_factory=lambda *args, **kwargs: sockets.pop(0),
                tcp_socket_factory=lambda *args, **kwargs: FakeTcpSocket(),
            )

        owner = build()
        try:
            with self.assertRaises(PrivateWebCdpError) as transient:
                owner.reconnect_for_observation()
            self.assertEqual(transient.exception.stage, "connect")
            self.assertIsNone(owner._socket)
            with self.assertRaises(PrivateWebCdpError) as blocked:
                build()
            self.assertEqual(blocked.exception.stage, "target_busy")
            owner.reconnect_for_observation()
            self.assertEqual(owner.call("Runtime.evaluate"), {"value": 3})
            self.assertEqual(discovery_calls, 3)
        finally:
            # Reconnected Runtime.evaluate ACK still does not prove read-only JS.
            with self.assertRaises(PrivateWebCdpError) as fenced:
                owner.close()
            self.assertEqual(fenced.exception.stage, "close")


    def test_inflight_call_close_cannot_release_target_ownership(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux kernel AF_UNIX target claim required")
        from threading import Event, Thread

        endpoint = "http://127.0.0.1:19632"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19632/devtools/page/synthetic",
        }
        entered = Event()
        release = Event()
        outcome = []

        class BlockingCallSocket(FakeSocket):
            def send(self, payload):
                entered.set()
                if not release.wait(4):
                    raise RuntimeError("synthetic send timeout")
                super().send(payload)

        def make_client(blocking):
            sock = BlockingCallSocket([{"id": 1, "result": {}}]) if blocking else FakeSocket([])
            return _LoopbackCdpClient(
                endpoint,
                timeout_seconds=1,
                opener=lambda url, **kwargs: FakeResponse([target], url=url),
                websocket_factory=lambda *args, **kwargs: sock,
                tcp_socket_factory=lambda *args, **kwargs: FakeTcpSocket(),
            )

        first = make_client(True)

        def call_first():
            try:
                outcome.append(("success", first.call("Page.navigate", {
                    "url": "https://example.invalid/",
                })))
            except PrivateWebCdpError as exc:
                outcome.append(("error", exc.stage))

        worker = Thread(target=call_first, daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(2), "first CDP send never began")
            # A successful close would falsely publish target availability
            # while the first browser call remains unresolved.
            with self.assertRaises(PrivateWebCdpError) as close_error:
                first.close()
            self.assertEqual(close_error.exception.stage, "close")
            with self.assertRaises(PrivateWebCdpError) as blocked:
                make_client(False)
            self.assertEqual(blocked.exception.stage, "target_busy")
            child = (
                "import socket,sys\n"
                "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
                "try:\n"
                " s.bind(bytes([0])+b'mark-api-cdp-target-v1:19632')\n"
                "except OSError:\n"
                " sys.exit(0)\n"
                "else:\n"
                " sys.exit(4)\n"
                "finally:\n"
                " s.close()\n"
            )
            result = subprocess.run(
                [sys.executable, "-B", "-c", child],
                capture_output=True, text=True, check=False, timeout=4,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, [("error", "call")])


    def test_abandoned_clean_cdp_client_and_owners_release_kernel_claim(self) -> None:
        """Only a definitely clean GC owner may release a stale target claim."""
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX claim required")
        import gc
        import weakref
        from mark_api.private_web_cdp import _ACTIVE_CDP_TARGET_CLAIMS

        for mode, port in (("client", 19911), ("page", 19912), ("cookies", 19913)):
            with self.subTest(mode=mode):
                endpoint = f"http://127.0.0.1:{port}"
                target = {
                    "type": "page",
                    "webSocketDebuggerUrl":
                        f"ws://127.0.0.1:{port}/devtools/page/clean-synthetic",
                }
                websocket = FakeSocket([])
                def build(ws):
                    return _LoopbackCdpClient(
                        endpoint, timeout_seconds=1,
                        opener=lambda url, **kw: FakeResponse([target], url=url),
                        websocket_factory=lambda *args, **kw: ws,
                        tcp_socket_factory=lambda *args, **kw: FakeTcpSocket(),
                    )

                client = build(websocket)
                claim = client._target_reservation
                claim_id = id(claim)
                client_ref = weakref.ref(client)
                if mode == "page":
                    owner = CdpPrivateWebPage(endpoint, client_factory=lambda: None)
                    owner._client_instance = client
                    del owner
                elif mode == "cookies":
                    owner = CdpCookieProvider(endpoint, client_factory=lambda: None)
                    owner._client_instance = client
                    del owner
                del client
                gc.collect()
                try:
                    self.assertIsNone(client_ref())
                    self.assertTrue(websocket.closed)
                    self.assertEqual(claim.fileno(), -1)
                    self.assertNotIn(claim_id, _ACTIVE_CDP_TARGET_CLAIMS)
                    fresh = build(FakeSocket([]))
                    fresh.close()
                finally:
                    # Isolate a failing RED run from later kernel-target tests.
                    if claim.fileno() >= 0:
                        claim.close()
                    _ACTIVE_CDP_TARGET_CLAIMS.pop(claim_id, None)

    def test_abandoned_clean_client_gc_close_failure_quarantines_target(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract target claim required")
        import gc
        import weakref
        from mark_api.private_web_cdp import _ACTIVE_CDP_TARGET_CLAIMS

        port = 19914
        endpoint = f"http://127.0.0.1:{port}"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                f"ws://127.0.0.1:{port}/devtools/page/close-failure",
        }

        class UncertainSocket(FakeSocket):
            def __init__(self):
                super().__init__([])
                self.close_attempted = False

            def close(self):
                self.close_attempted = True
                raise OSError("synthetic websocket close unknown")

        sock = UncertainSocket()
        client = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kw: FakeResponse([target], url=url),
            websocket_factory=lambda *a, **kw: sock,
            tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
        )
        claim_id = id(client._target_reservation)
        ref = weakref.ref(client)
        del client
        gc.collect()
        self.assertIsNone(ref())
        self.assertTrue(sock.close_attempted)
        self.assertIn(claim_id, _ACTIVE_CDP_TARGET_CLAIMS)
        self.assertGreaterEqual(_ACTIVE_CDP_TARGET_CLAIMS[claim_id].fileno(), 0)
        with self.assertRaises(PrivateWebCdpError) as blocked:
            _LoopbackCdpClient(
                endpoint, timeout_seconds=1,
                opener=lambda url, **kw: FakeResponse([target], url=url),
                websocket_factory=lambda *a, **kw: FakeSocket([]),
                tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
            )
        self.assertEqual(blocked.exception.stage, "target_busy")
        # Failed close is UNKNOWN; retain its claim for process lifetime.

    def test_fork_child_gc_never_releases_parent_target_reservation(self) -> None:
        if sys.platform != "linux" or not hasattr(os, "fork"):
            self.skipTest("Linux fork and AF_UNIX are required")
        import gc
        import signal
        from mark_api.private_web_cdp import _ACTIVE_CDP_TARGET_CLAIMS

        port = 19915
        endpoint = f"http://127.0.0.1:{port}"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                f"ws://127.0.0.1:{port}/devtools/page/fork-original",
        }
        def build():
            return _LoopbackCdpClient(
                endpoint, timeout_seconds=1,
                opener=lambda url, **kw: FakeResponse([target], url=url),
                websocket_factory=lambda *a, **kw: FakeSocket([]),
                tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
            )
        client = build()
        claim_id = id(client._target_reservation)
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(read_fd)
                signal.alarm(5)
                del client
                gc.collect()
                preserved = (
                    claim_id in _ACTIVE_CDP_TARGET_CLAIMS
                    and _ACTIVE_CDP_TARGET_CLAIMS[claim_id].fileno() >= 0
                )
                os.write(write_fd, b"1" if preserved else b"0")
            except BaseException:
                try:
                    os.write(write_fd, b"E")
                except OSError:
                    pass
            finally:
                os._exit(0)
        os.close(write_fd)
        try:
            child_result = os.read(read_fd, 1)
            child_status = os.waitpid(pid, 0)[1]
            self.assertTrue(os.WIFEXITED(child_status))
            self.assertEqual(child_result, b"1")
        finally:
            os.close(read_fd)
            client.close()
        replacement = build()
        replacement.close()

    def test_abandoned_nonmedia_browser_input_never_gc_releases_claim(self) -> None:
        """A CDP ACK is not verified platform write settlement."""
        if sys.platform != "linux":
            self.skipTest("Linux exclusive target claim required")
        import gc
        import weakref
        from mark_api.private_web_cdp import _ACTIVE_CDP_TARGET_CLAIMS

        for port, method, params in (
            (19916, "Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": 12.0, "y": 22.0,
                "button": "left", "buttons": 1, "clickCount": 1,
            }),
            (19917, "Runtime.evaluate", {
                "expression": "(() => document.querySelector('button')?.click())()",
                "returnByValue": True, "awaitPromise": False,
            }),
            (19918, "DOM.setFileInputFiles", {
                "files": ["/synthetic/nonexistent"],
            }),
        ):
            with self.subTest(method=method):
                endpoint = f"http://127.0.0.1:{port}"
                target = {
                    "type": "page",
                    "webSocketDebuggerUrl":
                        f"ws://127.0.0.1:{port}/devtools/page/nonmedia",
                }
                def build():
                    return _LoopbackCdpClient(
                        endpoint, timeout_seconds=1,
                        opener=lambda url, **kw: FakeResponse([target], url=url),
                        websocket_factory=lambda *args, **kw:
                            FakeSocket([{"id": 1, "result": {}}]),
                        tcp_socket_factory=lambda *args, **kw: FakeTcpSocket(),
                    )

                client = build()
                claim = client._target_reservation
                claim_id = id(claim)
                ref = weakref.ref(client)
                client.call(method, params)
                del client
                gc.collect()
                try:
                    self.assertIsNone(ref())
                    self.assertIn(claim_id, _ACTIVE_CDP_TARGET_CLAIMS)
                    self.assertGreaterEqual(claim.fileno(), 0)
                    with self.assertRaises(PrivateWebCdpError) as blocked:
                        build()
                    self.assertEqual(blocked.exception.stage, "target_busy")
                finally:
                    # Clean up only the synthetic RED test's known claim.
                    if claim.fileno() >= 0:
                        claim.close()
                    _ACTIVE_CDP_TARGET_CLAIMS.pop(claim_id, None)

    def test_nonmedia_ack_cannot_release_target_via_explicit_close(self) -> None:
        """Only platform settlement, never a CDP transport ACK, permits release."""
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX target claim required")
        from mark_api.private_web_cdp import _ACTIVE_CDP_TARGET_CLAIMS

        cases = (
            (19921, "Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": 12.0, "y": 22.0,
                "button": "left", "buttons": 1, "clickCount": 1,
            }),
            (19922, "Runtime.evaluate", {
                "expression": "(() => document.querySelector('button')?.click())()",
                "returnByValue": True, "awaitPromise": False,
            }),
            (19923, "DOM.setFileInputFiles", {"files": ["/synthetic/nonexistent"]}),
        )
        for port, method, params in cases:
            with self.subTest(method=method):
                endpoint = f"http://127.0.0.1:{port}"
                target = {
                    "type": "page",
                    "webSocketDebuggerUrl":
                        f"ws://127.0.0.1:{port}/devtools/page/nonmedia",
                }

                def new_client():
                    return _LoopbackCdpClient(
                        endpoint, timeout_seconds=1,
                        opener=lambda url, **kw: FakeResponse([target], url=url),
                        websocket_factory=lambda *args, **kw:
                            FakeSocket([{"id": 1, "result": {}}]),
                        tcp_socket_factory=lambda *args, **kw: FakeTcpSocket(),
                    )

                client = new_client()
                claim = client._target_reservation
                claim_id = id(claim)
                client.call(method, params)
                try:
                    with self.assertRaises(PrivateWebCdpError) as raised:
                        client.close()
                    self.assertEqual(raised.exception.stage, "close")
                    self.assertIn(claim_id, _ACTIVE_CDP_TARGET_CLAIMS)
                    self.assertGreaterEqual(claim.fileno(), 0)
                    with self.assertRaises(PrivateWebCdpError) as busy:
                        new_client()
                    self.assertEqual(busy.exception.stage, "target_busy")
                finally:
                    # On the parent RED code this closes a falsely freed
                    # synthetic claim. On GREEN keep the real UNKNOWN claim.
                    if claim.fileno() >= 0 and (
                        claim_id not in _ACTIVE_CDP_TARGET_CLAIMS
                    ):
                        claim.close()

    def test_settling_one_submit_cannot_clear_prior_unresolved_browser_input(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux exclusive CDP target required")
        from mark_api.private_web_cdp import _ACTIVE_CDP_TARGET_CLAIMS

        port = 19924
        endpoint = f"http://127.0.0.1:{port}"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                f"ws://127.0.0.1:{port}/devtools/page/one",
        }
        expression = "(() => 'confirmed')()"
        sock = FakeSocket([
            {"id": 1, "result": {}},
            {"id": 2, "result": {
                "result": {"type": "string", "value": "confirmed"},
            }},
        ])
        client = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kw: FakeResponse([target], url=url),
            websocket_factory=lambda *args, **kw: sock,
            tcp_socket_factory=lambda *args, **kw: FakeTcpSocket(),
        )
        claim = client._target_reservation
        claim_id = id(claim)
        client.call("Runtime.evaluate", {
            "expression": "document.querySelector('button')?.click()",
            "returnByValue": True, "awaitPromise": False,
        })
        # A different pinned operation is confirmed. This is not evidence
        # that the earlier possible page-side click reached platform settlement.
        client.arm_unknown_submit(settlement_expression=expression)
        client.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True, "awaitPromise": False,
        })
        client.settle_unknown_submit()
        with self.assertRaises(PrivateWebCdpError) as fenced:
            client.close()
        self.assertEqual(fenced.exception.stage, "close")
        self.assertIn(claim_id, _ACTIVE_CDP_TARGET_CLAIMS)
        self.assertGreaterEqual(claim.fileno(), 0)

    def test_abandoned_cookie_read_can_release_clean_kernel_claim(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux exclusive target claim required")
        import gc
        import weakref
        from mark_api.private_web_cdp import _ACTIVE_CDP_TARGET_CLAIMS

        port = 19919
        endpoint = f"http://127.0.0.1:{port}"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                f"ws://127.0.0.1:{port}/devtools/page/cookies",
        }
        client = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kw: FakeResponse([target], url=url),
            websocket_factory=lambda *args, **kw:
                FakeSocket([{"id": 1, "result": {"cookies": []}}]),
            tcp_socket_factory=lambda *args, **kw: FakeTcpSocket(),
        )
        claim = client._target_reservation
        claim_id = id(claim)
        ref = weakref.ref(client)
        client.call("Network.getCookies", {"urls": ["https://www.kleinanzeigen.de"]})
        del client
        gc.collect()
        self.assertIsNone(ref())
        self.assertEqual(claim.fileno(), -1)
        self.assertNotIn(claim_id, _ACTIVE_CDP_TARGET_CLAIMS)

    def test_unknown_media_page_gc_cannot_release_kernel_claim(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX claim required")
        import gc
        import weakref
        from mark_api.private_web_cdp_media import CdpPrivateWebMediaPage
        from mark_api.private_web_cdp import _ACTIVE_CDP_TARGET_CLAIMS

        port = 19633
        endpoint = f"http://127.0.0.1:{port}"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                f"ws://127.0.0.1:{port}/devtools/page/unknown-original",
        }
        client = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kw: FakeResponse([target], url=url),
            websocket_factory=lambda *args, **kw: FakeSocket([]),
            tcp_socket_factory=lambda *args, **kw: FakeTcpSocket(),
        )
        client.arm_unknown_submit()
        claim_id = id(client._target_reservation)
        client_ref = weakref.ref(client)
        page = CdpPrivateWebMediaPage(
            endpoint, client_factory=lambda: None,
        )
        page._client_instance = client
        page._media_unknown_client = client
        page._media_submit_unsettled = True
        del page, client
        gc.collect()
        self.assertIsNone(client_ref())
        self.assertIn(claim_id, _ACTIVE_CDP_TARGET_CLAIMS)
        self.assertGreaterEqual(_ACTIVE_CDP_TARGET_CLAIMS[claim_id].fileno(), 0)
        child = (
            "import socket,sys\n"
            "sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
            "try:\n"
            " sock.bind(bytes([0])+b'mark-api-cdp-target-v1:19633')\n"
            "except OSError:\n"
            " sys.exit(0)\n"
            "else:\n"
            " sys.exit(4)\n"
            "finally:\n"
            " sock.close()\n"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", child],
            capture_output=True, text=True, check=False, timeout=4,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # The test intentionally retains this unresolved claim until exit.

    def test_observation_reconnect_rejects_changed_page_identity(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX claim required")
        endpoint = "http://127.0.0.1:19634"
        target_calls = 0
        websocket_calls = []

        def opener(url, **kwargs):
            nonlocal target_calls
            target_calls += 1
            target_suffix = "clicked-original" if target_calls == 1 else "replacement"
            return FakeResponse([{
                "type": "page",
                "webSocketDebuggerUrl":
                    f"ws://127.0.0.1:19634/devtools/page/{target_suffix}",
            }], url=url)

        def websocket_factory(*args, **kwargs):
            websocket_calls.append(args)
            return FakeSocket([])

        owner = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=opener, websocket_factory=websocket_factory,
            tcp_socket_factory=lambda *args, **kw: FakeTcpSocket(),
        )
        owner.arm_unknown_submit()
        try:
            with self.assertRaises(PrivateWebCdpError) as mismatch:
                owner.reconnect_for_observation()
            self.assertEqual(mismatch.exception.stage, "connect")
            self.assertEqual(target_calls, 2)
            self.assertEqual(len(websocket_calls), 1)
            self.assertIsNone(owner._socket)
            with self.assertRaises(PrivateWebCdpError) as blocked:
                _LoopbackCdpClient(
                    endpoint, timeout_seconds=1,
                    opener=opener, websocket_factory=websocket_factory,
                    tcp_socket_factory=lambda *args, **kw: FakeTcpSocket(),
                )
            self.assertEqual(blocked.exception.stage, "target_busy")
        finally:
            with self.assertRaises(PrivateWebCdpError) as close_result:
                owner.close()
            self.assertEqual(close_result.exception.stage, "close")

    def test_uncertain_raw_close_interrupted_before_quarantine_append(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX claim required")
        endpoint = "http://127.0.0.1:19635"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19635/devtools/page/synthetic",
        }

        class UncertainRaw:
            def close(self):
                raise OSError("raw close has unknown outcome")

        def interrupted_before_append(client, transport):
            raise KeyboardInterrupt()

        def new_client():
            return _LoopbackCdpClient(
                endpoint, timeout_seconds=1,
                opener=lambda url, **kw: FakeResponse([target], url=url),
                websocket_factory=lambda *args, **kw: (_ for _ in ()).throw(
                    KeyboardInterrupt()
                ),
                tcp_socket_factory=lambda *args, **kw: UncertainRaw(),
            )

        with patch.object(
            _LoopbackCdpClient, "_quarantine_transport",
            interrupted_before_append,
        ):
            with self.assertRaises(KeyboardInterrupt):
                new_client()
        # The independent process-lifetime registration must remain even
        # when interruption precedes the first quarantine append.
        with self.assertRaises(PrivateWebCdpError) as blocked:
            new_client()
        self.assertEqual(blocked.exception.stage, "target_busy")

    def test_fork_inherited_socket_and_claim_cannot_make_cdp_calls(self) -> None:
        if sys.platform != "linux" or not hasattr(os, "fork"):
            self.skipTest("Linux os.fork required")
        endpoint = "http://127.0.0.1:19636"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19636/devtools/page/synthetic",
        }
        sock = FakeSocket([{"id": 1, "result": {"value": 7}}])
        owner = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kw: FakeResponse([target], url=url),
            websocket_factory=lambda *args, **kw: sock,
            tcp_socket_factory=lambda *args, **kw: FakeTcpSocket(),
        )
        reader, writer = os.pipe()
        try:
            pid = os.fork()
            if pid == 0:
                os.close(reader)
                denied = []
                for method in (
                    lambda: owner.call("Page.navigate"),
                    owner.reconnect_for_observation,
                    owner.close,
                ):
                    try:
                        method()
                    except PrivateWebCdpWriteNotAttemptedError as exc:
                        denied.append(exc.stage == "target_unowned")
                    except Exception:
                        denied.append(False)
                    else:
                        denied.append(False)
                os.write(writer, b"PASS" if all(denied) else b"FAIL")
                os._exit(0 if all(denied) else 4)
            os.close(writer)
            child_result = os.read(reader, 16)
            _, status = os.waitpid(pid, 0)
            self.assertEqual(child_result, b"PASS")
            self.assertTrue(os.WIFEXITED(status))
            self.assertEqual(os.WEXITSTATUS(status), 0)
            self.assertEqual(sock.sent, [])
            self.assertEqual(owner.call("Runtime.evaluate"), {"value": 7})
        finally:
            os.close(reader)
            # A CDP Runtime.evaluate ACK cannot certify no page-side effect.
            with self.assertRaises(PrivateWebCdpError) as fenced:
                owner.close()
            self.assertEqual(fenced.exception.stage, "close")


    def test_owned_client_cannot_open_second_transport_by_direct_connect(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX kernel claim required")
        endpoint = "http://127.0.0.1:19638"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19638/devtools/page/initial",
        }
        observations = []

        def opener(url, **kwargs):
            observations.append("discovery")
            if len(observations) != 1:
                raise RuntimeError("unexpected second browser-target discovery")
            return FakeResponse([target], url=url)

        owner = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=opener,
            websocket_factory=lambda *args, **kwargs: FakeSocket([]),
            tcp_socket_factory=lambda *args, **kwargs: FakeTcpSocket(),
        )
        try:
            with self.assertRaises(
                PrivateWebCdpWriteNotAttemptedError
            ) as rejected:
                owner._connect()
            self.assertEqual(rejected.exception.stage, "target_unowned")
            self.assertEqual(observations, ["discovery"])
        finally:
            owner.close()


    def test_stdlib_tcp_refusal_does_not_permanently_quarantine_target(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX target ownership required")
        import socket

        # A locally bound but non-listening AF_INET socket guarantees a
        # refused loopback connect without contacting a real Chrome browser.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(("127.0.0.1", 0))
            port = blocker.getsockname()[1]
            endpoint = f"http://127.0.0.1:{port}"
            target = {
                "type": "page",
                "webSocketDebuggerUrl":
                    f"ws://127.0.0.1:{port}/devtools/page/synthetic",
            }

            def opener(url, **kwargs):
                return FakeResponse([target], url=url)

            with self.assertRaises(PrivateWebCdpError) as refused:
                _LoopbackCdpClient(
                    endpoint,
                    timeout_seconds=1,
                    opener=opener,
                    websocket_factory=lambda *args, **kwargs: FakeSocket([]),
                    tcp_socket_factory=socket.create_connection,
                )
            self.assertEqual(refused.exception.stage, "connect")

            # create_connection closed its attempted raw socket on OSError.
            # A cooperating client must be able to reserve the same CDP port
            # now; permanent quarantine would turn a known failure into DoS.
            recovered = _LoopbackCdpClient(
                endpoint,
                timeout_seconds=1,
                opener=opener,
                websocket_factory=lambda *args, **kwargs: FakeSocket([]),
                tcp_socket_factory=lambda *args, **kwargs: FakeTcpSocket(),
            )
            recovered.close()


    def test_injected_tcp_factory_error_keeps_ambiguous_target_quarantined(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX target claim required")
        endpoint = "http://127.0.0.1:19639"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19639/devtools/page/synthetic",
        }
        retained_raw = []

        def injected_factory(*args, **kwargs):
            # Unlike stdlib create_connection, an injected factory can retain
            # a partially constructed socket before raising ordinary OSError.
            retained_raw.append(FakeTcpSocket())
            raise OSError("unknown injected TCP ownership")

        with self.assertRaises(PrivateWebCdpError) as failed:
            _LoopbackCdpClient(
                endpoint,
                timeout_seconds=1,
                opener=lambda url, **kw: FakeResponse([target], url=url),
                websocket_factory=lambda *args, **kw: FakeSocket([]),
                tcp_socket_factory=injected_factory,
            )
        self.assertEqual(failed.exception.stage, "connect")
        self.assertEqual(len(retained_raw), 1)

        # The unknown injected transport must not make a busy page reusable.
        with self.assertRaises(PrivateWebCdpError) as blocked:
            _LoopbackCdpClient(
                endpoint,
                timeout_seconds=1,
                opener=lambda url, **kw: FakeResponse([target], url=url),
                websocket_factory=lambda *args, **kw: FakeSocket([]),
                tcp_socket_factory=lambda *args, **kw: FakeTcpSocket(),
            )
        self.assertEqual(blocked.exception.stage, "target_busy")




    def test_submit_mouse_sequence_forbids_reconnect_before_release(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract target reservation required")
        port = 19660
        endpoint = f"http://127.0.0.1:{port}"
        target = {"type": "page", "webSocketDebuggerUrl":
                  f"ws://127.0.0.1:{port}/devtools/page/original"}
        expression = "(() => 'confirmed')()"
        pressed = {"type": "mousePressed", "x": 12.0, "y": 20.0,
                   "button": "left", "buttons": 1, "clickCount": 1}
        released = {"type": "mouseReleased", "x": 12.0, "y": 20.0,
                    "button": "left", "buttons": 0, "clickCount": 1}
        opened = []
        sock = FakeSocket([
            {"id": 1, "result": {}},
            {"id": 2, "result": {}},
            {"id": 3, "result": {
                "result": {"type": "string", "value": "confirmed"},
            }},
        ])
        def make_socket(*args, **kwargs):
            opened.append("new_ws")
            return sock if len(opened) == 1 else FakeSocket([])

        owner = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kw: FakeResponse([target], url=url),
            websocket_factory=make_socket,
            tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
        )
        owner.arm_unknown_submit(
            settlement_expression=expression, click_point=(12.0, 20.0),
        )
        with self.assertRaises(PrivateWebCdpError) as before_press:
            owner.reconnect_for_observation()
        self.assertEqual(before_press.exception.stage, "reconnect")
        owner.call("Input.dispatchMouseEvent", pressed)
        with self.assertRaises(PrivateWebCdpError) as before_release:
            owner.reconnect_for_observation()
        self.assertEqual(before_release.exception.stage, "reconnect")
        self.assertEqual(len(opened), 1)
        owner.call("Input.dispatchMouseEvent", released)
        owner.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": False,
        })
        owner.settle_unknown_submit()
        owner.close()
        self.assertEqual(len(opened), 1)

    def test_armed_click_params_are_frozen_before_mutating_serializer_hook(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract target reservation required")
        import mark_api.private_web_cdp as cdp_module

        port = 19659
        endpoint = f"http://127.0.0.1:{port}"
        target = {"type": "page", "webSocketDebuggerUrl":
                  f"ws://127.0.0.1:{port}/devtools/page/original"}
        sock = FakeSocket([{"id": 1, "result": {}}])
        owner = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kw: FakeResponse([target], url=url),
            websocket_factory=lambda *a, **kw: sock,
            tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
        )
        expression = "(() => 'confirmed')()"
        pressed = {"type": "mousePressed", "x": 12.0, "y": 20.0,
                   "button": "left", "buttons": 1, "clickCount": 1}
        owner.arm_unknown_submit(
            settlement_expression=expression, click_point=(12.0, 20.0),
        )
        original = json.dumps

        def mutate_before_json_encoding(request, *args, **kwargs):
            pressed["type"] = "mouseReleased"
            pressed["x"] = 999.0
            return original(request, *args, **kwargs)

        with patch.object(cdp_module.json, "dumps", mutate_before_json_encoding):
            owner.call("Input.dispatchMouseEvent", pressed)
        self.assertEqual(sock.sent[0]["params"]["type"], "mousePressed")
        self.assertEqual(sock.sent[0]["params"]["x"], 12.0)
        with self.assertRaises(PrivateWebCdpError):
            owner.close()

    def test_armed_unknown_click_after_ambiguous_send_rejects_direct_navigation(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract target reservation required")
        endpoint = "http://127.0.0.1:19657"
        target = {"type": "page", "webSocketDebuggerUrl":
                  "ws://127.0.0.1:19657/devtools/page/original"}
        class AmbiguousSend(FakeSocket):
            def send(self, payload):
                super().send(payload)
                if len(self.sent) == 1:
                    raise OSError("mouse press might have reached Chrome")
        sock = AmbiguousSend([])
        owner = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kw: FakeResponse([target], url=url),
            websocket_factory=lambda *a, **kw: sock,
            tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
        )
        owner.arm_unknown_submit(
            settlement_expression="(() => 'confirmed')()",
            click_point=(12.0, 20.0),
        )
        with self.assertRaises(PrivateWebCdpError):
            owner.call("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": 12.0, "y": 20.0,
                "button": "left", "buttons": 1, "clickCount": 1,
            })
        with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
            owner.call("Page.navigate", {"url": "https://example.invalid/"})
        self.assertEqual([x["method"] for x in sock.sent], ["Input.dispatchMouseEvent"])
        with self.assertRaises(PrivateWebCdpError):
            owner.close()

    def test_armed_unknown_between_pressed_and_release_denies_other_writes(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract target reservation required")
        endpoint = "http://127.0.0.1:19658"
        target = {"type": "page", "webSocketDebuggerUrl":
                  "ws://127.0.0.1:19658/devtools/page/original"}
        expression = "(() => 'confirmed')()"
        pressed = {"type": "mousePressed", "x": 12.0, "y": 20.0,
                   "button": "left", "buttons": 1, "clickCount": 1}
        released = {"type": "mouseReleased", "x": 12.0, "y": 20.0,
                    "button": "left", "buttons": 0, "clickCount": 1}
        sock = FakeSocket([
            {"id": 1, "result": {}},
            {"id": 2, "result": {}},
            {"id": 3, "result": {
                "result": {"type": "string", "value": "confirmed"},
            }},
        ])
        owner = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kw: FakeResponse([target], url=url),
            websocket_factory=lambda *a, **kw: sock,
            tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
        )
        owner.arm_unknown_submit(
            settlement_expression=expression, click_point=(12.0, 20.0),
        )
        owner.call("Input.dispatchMouseEvent", pressed)
        with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
            owner.call("Page.navigate", {"url": "https://example.invalid/"})
        self.assertEqual(len(sock.sent), 1)
        owner.call("Input.dispatchMouseEvent", released)
        with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
            owner.call("Input.dispatchMouseEvent", pressed)
        owner.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": False,
        })
        owner.settle_unknown_submit()
        owner.close()
        self.assertEqual(
            [x["method"] for x in sock.sent],
            ["Input.dispatchMouseEvent", "Input.dispatchMouseEvent", "Runtime.evaluate"],
        )

    def test_direct_unknown_settle_cannot_clear_failed_one_shot_input(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract UNIX target reservation required")
        port = 19651
        endpoint = f"http://127.0.0.1:{port}"
        target = {
            "type": "page",
            "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/original",
        }

        class AmbiguousSend(FakeSocket):
            def send(self, payload):
                super().send(payload)
                raise OSError("response lost after possibly effective browser input")

        def build(sock):
            return _LoopbackCdpClient(
                endpoint, timeout_seconds=1,
                opener=lambda u, **kw: FakeResponse([target], url=u),
                websocket_factory=lambda *a, **kw: sock,
                tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
            )

        socket = AmbiguousSend([])
        owner = build(socket)
        owner.arm_unknown_submit(click_point=(1.0, 1.0))
        with self.assertRaises(PrivateWebCdpError):
            owner.call("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": 1.0, "y": 1.0,
                "button": "left", "buttons": 1, "clickCount": 1,
            })
        self.assertEqual(len(socket.sent), 1)
        with self.assertRaises(PrivateWebCdpError) as denied:
            owner.settle_unknown_submit()
        self.assertEqual(denied.exception.stage, "submit_unsettled")
        with self.assertRaises(PrivateWebCdpError) as blocked:
            owner.close()
        self.assertEqual(blocked.exception.stage, "close")
        with self.assertRaises(PrivateWebCdpError) as busy:
            build(FakeSocket([]))
        self.assertEqual(busy.exception.stage, "target_busy")


    def test_native_submit_settlement_requires_original_confirmed_read(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux kernel abstract target reservation required")
        expression = "(() => location.pathname === '/p/success' ? 'confirmed' : 'pending')()"
        for port, value, match_expression, exception_details in (
            (19652, "confirmed", True, False),
            (19653, "confirmed", False, False),
            (19654, "pending", True, False),
            (19655, "confirmed", True, True),
        ):
            with self.subTest(port=port):
                endpoint = f"http://127.0.0.1:{port}"
                target = {
                    "type": "page",
                    "webSocketDebuggerUrl":
                        f"ws://127.0.0.1:{port}/devtools/page/original",
                }
                result = {"result": {"type": "string", "value": value}}
                if exception_details:
                    result["exceptionDetails"] = {"text": "synthetic error"}
                sock = FakeSocket([
                    {"id": 1, "result": {}},
                    {"id": 2, "result": {}},
                    {"id": 3, "result": result},
                ])
                client = _LoopbackCdpClient(
                    endpoint, timeout_seconds=1,
                    opener=lambda url, **kw: FakeResponse([target], url=url),
                    websocket_factory=lambda *args, **kw: sock,
                    tcp_socket_factory=lambda *args, **kw: FakeTcpSocket(),
                )
                client.arm_unknown_submit(
                    settlement_expression=expression,
                    click_point=(12.0, 20.0),
                )
                client.call("Input.dispatchMouseEvent", {
                    "type": "mousePressed", "x": 12.0, "y": 20.0,
                    "button": "left", "buttons": 1, "clickCount": 1,
                })
                client.call("Input.dispatchMouseEvent", {
                    "type": "mouseReleased", "x": 12.0, "y": 20.0,
                    "button": "left", "buttons": 0, "clickCount": 1,
                })
                actual_expression = (
                    expression if match_expression else "(() => 'confirmed')()"
                )
                params = {
                    "expression": actual_expression,
                    "returnByValue": True,
                    "awaitPromise": False,
                }
                if match_expression:
                    client.call("Runtime.evaluate", params)
                else:
                    with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
                        client.call("Runtime.evaluate", params)
                if port == 19652:
                    client.settle_unknown_submit()
                    client.close()
                    self.assertTrue(sock.closed)
                else:
                    with self.assertRaises(PrivateWebCdpError) as denied:
                        client.settle_unknown_submit()
                    self.assertEqual(denied.exception.stage, "submit_unsettled")
                    with self.assertRaises(PrivateWebCdpError):
                        client.close()
                    with self.assertRaises(PrivateWebCdpError) as busy:
                        _LoopbackCdpClient(
                            endpoint, timeout_seconds=1,
                            opener=lambda url, **kw: FakeResponse(
                                [target], url=url,
                            ),
                            websocket_factory=lambda *a, **kw: FakeSocket([]),
                            tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
                        )
                    self.assertEqual(busy.exception.stage, "target_busy")

    def test_media_native_reconcile_uses_exact_pinned_confirmation(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux kernel abstract target reservation required")
        from mark_api.private_web_cdp_media import CdpPrivateWebMediaPage

        port = 19656
        endpoint = f"http://127.0.0.1:{port}"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                f"ws://127.0.0.1:{port}/devtools/page/original",
        }
        sock = FakeSocket([{
            "id": 1,
            "result": {
                "result": {"type": "string", "value": "confirmed"},
            },
        }])
        client = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kw: FakeResponse([target], url=url),
            websocket_factory=lambda *args, **kw: sock,
            tcp_socket_factory=lambda *args, **kw: FakeTcpSocket(),
        )
        page = CdpPrivateWebMediaPage(
            endpoint, client_factory=lambda: client,
            monotonic=lambda: 0.0, sleep=lambda _: None,
        )
        page._client_instance = client
        page._media_unknown_client = client
        page._media_submit_unsettled = True
        expected_expression = page._media_submit_settlement_expression()
        client.arm_unknown_submit(settlement_expression=expected_expression)
        try:
            page.reconcile_create_media_submit()
            self.assertFalse(page._media_submit_unsettled)
            self.assertFalse(client._unresolved_submit)
            self.assertEqual(sock.sent[0]["method"], "Runtime.evaluate")
            self.assertEqual(
                sock.sent[0]["params"]["expression"], expected_expression,
            )
        finally:
            page.close()

    def test_close_during_discovery_cannot_free_target_or_start_websocket(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract UNIX target reservation required")
        port = 19648
        endpoint = f"http://127.0.0.1:{port}"
        target = {
            "type": "page",
            "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/original",
        }
        captured = {}
        state = {"close_denied": False, "rival_admitted": False, "ws_calls": 0}

        class CapturingClient(_LoopbackCdpClient):
            def _connect(self):
                captured["owner"] = self
                return super()._connect()

        def opener(url, **kwargs):
            try:
                captured["owner"].close()
            except PrivateWebCdpError:
                state["close_denied"] = True
            try:
                rival = _LoopbackCdpClient(
                    endpoint, timeout_seconds=1,
                    opener=lambda u, **kw: FakeResponse([target], url=u),
                    websocket_factory=lambda *a, **kw: FakeSocket([]),
                    tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
                )
            except PrivateWebCdpError as caught:
                self.assertEqual(caught.stage, "target_busy")
            else:
                state["rival_admitted"] = True
                rival.close()
            return FakeResponse([target], url=url)

        def websocket_factory(*args, **kwargs):
            state["ws_calls"] += 1
            return FakeSocket([])

        try:
            owner = CapturingClient(
                endpoint, timeout_seconds=1,
                opener=opener,
                websocket_factory=websocket_factory,
                tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
            )
        except PrivateWebCdpError:
            pass
        else:
            owner.close()
        self.assertTrue(state["close_denied"])
        self.assertFalse(state["rival_admitted"])
        self.assertEqual(state["ws_calls"], 0)

    def test_close_inside_raw_tcp_factory_prevents_websocket_handshake(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract UNIX target reservation required")
        port = 19649
        endpoint = f"http://127.0.0.1:{port}"
        target = {
            "type": "page",
            "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/original",
        }
        state = {"close_denied": False, "ws_calls": 0}
        captured = {}
        raw = FakeTcpSocket()

        class CapturingClient(_LoopbackCdpClient):
            def _connect(self):
                captured["owner"] = self
                return super()._connect()

        def tcp_factory(*args, **kwargs):
            try:
                captured["owner"].close()
            except PrivateWebCdpError:
                state["close_denied"] = True
            return raw

        def websocket_factory(*args, **kwargs):
            state["ws_calls"] += 1
            return FakeSocket([])

        try:
            owner = CapturingClient(
                endpoint, timeout_seconds=1,
                opener=lambda u, **kw: FakeResponse([target], url=u),
                websocket_factory=websocket_factory,
                tcp_socket_factory=tcp_factory,
            )
        except PrivateWebCdpError:
            pass
        else:
            owner.close()
        self.assertTrue(state["close_denied"])
        self.assertEqual(state["ws_calls"], 0)
        self.assertTrue(raw.closed)

    def test_opener_cannot_reenter_inner_cdp_connector(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX claim required")
        state = {"discovery": 0, "websockets": 0, "reentry_denied": False}
        owner = {}
        endpoint = "http://127.0.0.1:19641"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19641/devtools/page/pinned",
        }

        class CapturingClient(_LoopbackCdpClient):
            def _connect(self):
                owner["client"] = self
                return super()._connect()

        def opener(url, **kwargs):
            state["discovery"] += 1
            if state["discovery"] == 1:
                try:
                    nested = owner["client"]._connect_under_owner()
                except PrivateWebCdpWriteNotAttemptedError:
                    state["reentry_denied"] = True
                else:
                    nested.close()
            return FakeResponse([target], url=url)

        def websocket_factory(*args, **kwargs):
            state["websockets"] += 1
            return FakeSocket([])

        client = CapturingClient(
            endpoint,
            timeout_seconds=1,
            opener=opener,
            websocket_factory=websocket_factory,
            tcp_socket_factory=lambda *args, **kwargs: FakeTcpSocket(),
        )
        try:
            self.assertTrue(state["reentry_denied"])
            self.assertEqual(state["discovery"], 1)
            self.assertEqual(state["websockets"], 1)
        finally:
            client.close()

    def test_unknown_tcp_reconnect_quarantines_before_second_factory(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX claim required")
        endpoint = "http://127.0.0.1:19642"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19642/devtools/page/pinned",
        }
        calls = []
        retained_raw = []

        def factory(*args, **kwargs):
            calls.append("tcp")
            if len(calls) == 2:
                retained_raw.append(FakeTcpSocket())
                raise OSError("unknown injected transport ownership")
            return FakeTcpSocket()

        client = _LoopbackCdpClient(
            endpoint,
            timeout_seconds=1,
            opener=lambda url, **kwargs: FakeResponse([target], url=url),
            websocket_factory=lambda *args, **kwargs: FakeSocket([]),
            tcp_socket_factory=factory,
        )
        with self.assertRaises(PrivateWebCdpError) as failed:
            client.reconnect_for_observation()
        self.assertEqual(failed.exception.stage, "connect")
        with self.assertRaises(PrivateWebCdpError) as denied:
            client.reconnect_for_observation()
        self.assertIn(denied.exception.stage, ("target_unowned", "reconnect"))
        self.assertEqual(calls, ["tcp", "tcp"])
        self.assertEqual(len(retained_raw), 1)
        with self.assertRaises(PrivateWebCdpError) as another:
            _LoopbackCdpClient(
                endpoint,
                timeout_seconds=1,
                opener=lambda url, **kwargs: FakeResponse([target], url=url),
                websocket_factory=lambda *args, **kwargs: FakeSocket([]),
                tcp_socket_factory=lambda *args, **kwargs: FakeTcpSocket(),
            )
        self.assertEqual(another.exception.stage, "target_busy")
        # Deliberately leave UNKNOWN transport quarantined for process life.



    def test_reconnect_failed_old_socket_close_quarantines_transport(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX kernel reservation required")
        import mark_api.private_web_cdp as cdp_module

        for port, exception in (
            (19644, OSError("ambiguous close")),
            (19645, KeyboardInterrupt("interrupted close")),
        ):
            with self.subTest(port=port):
                endpoint = f"http://127.0.0.1:{port}"
                target = {
                    "type": "page",
                    "webSocketDebuggerUrl":
                        f"ws://127.0.0.1:{port}/devtools/page/pinned",
                }
                opened = []

                class UnknownCloseSocket(FakeSocket):
                    def close(self):
                        # It may have closed the OS socket, or not. Neither
                        # state may be inferred from the raised exception.
                        raise exception

                def websocket_factory(*args, **kwargs):
                    sock = UnknownCloseSocket([
                        {"id": 1, "result": {"unsafe": True}},
                    ]) if not opened else FakeSocket([])
                    opened.append(sock)
                    return sock

                owner = _LoopbackCdpClient(
                    endpoint,
                    timeout_seconds=1,
                    opener=lambda url, **kw: FakeResponse([target], url=url),
                    websocket_factory=websocket_factory,
                    tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
                )
                expected = (
                    PrivateWebCdpError
                    if isinstance(exception, Exception) else type(exception)
                )
                with self.assertRaises(expected):
                    owner.reconnect_for_observation()
                self.assertTrue(owner._close_unknown)
                self.assertIsNone(owner._target_reservation)
                self.assertIsNone(owner._socket)
                self.assertEqual(len(opened), 1)
                with self.assertRaises(PrivateWebCdpError):
                    owner.reconnect_for_observation()
                with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
                    owner.call("Runtime.evaluate", {"expression": "1"})
                self.assertEqual(opened[0].sent, [])
                self.assertTrue(any(
                    transport is opened[0]
                    for _, transport in cdp_module._QUARANTINED_CDP_TARGETS
                ))
                with self.assertRaises(PrivateWebCdpError) as busy:
                    _LoopbackCdpClient(
                        endpoint, timeout_seconds=1,
                        opener=lambda url, **kw: FakeResponse([target], url=url),
                        websocket_factory=lambda *a, **kw: FakeSocket([]),
                        tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
                    )
                self.assertEqual(busy.exception.stage, "target_busy")

    def test_concurrent_same_client_calls_cannot_consume_other_responses(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX target claim required")
        from threading import Event, Thread

        endpoint = "http://127.0.0.1:19643"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                "ws://127.0.0.1:19643/devtools/page/synthetic",
        }
        entered = Event()
        release = Event()
        class BlockingSocket(FakeSocket):
            def __init__(self):
                super().__init__([])
                self.first_send = True

            def send(self, payload):
                if self.first_send:
                    self.first_send = False
                    entered.set()
                    if not release.wait(4):
                        raise AssertionError("first send timed out")
                super().send(payload)
                request_id = self.sent[-1]["id"]
                self.responses.append({
                    "id": request_id, "result": {"method_id": request_id},
                })

        sock = BlockingSocket()
        owner = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kwargs: FakeResponse([target], url=url),
            websocket_factory=lambda *args, **kwargs: sock,
            tcp_socket_factory=lambda *args, **kwargs: FakeTcpSocket(),
        )
        first_outcome = []
        def first():
            try:
                first_outcome.append(owner.call("Runtime.first"))
            except Exception as exc:
                first_outcome.append(exc)

        t = Thread(target=first, daemon=True)
        t.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(PrivateWebCdpWriteNotAttemptedError) as denied:
                owner.call("Runtime.second")
            self.assertEqual(denied.exception.stage, "target_busy")
            # A second request may not dispatch while the first response is
            # unresolved. Dropping an unrelated response would risk UNKNOWN.
            self.assertEqual(sock.sent, [])
        finally:
            release.set()
            t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(first_outcome, [{"method_id": 1}])
        self.assertEqual(
            [item["method"] for item in sock.sent], ["Runtime.first"],
        )
        # No platform settlement can be inferred from a synthetic Runtime.first ACK.
        with self.assertRaises(PrivateWebCdpError) as fenced:
            owner.close()
        self.assertEqual(fenced.exception.stage, "close")


    def test_ambiguous_call_send_exception_retains_cdp_target(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux AF_UNIX target claim required")

        for port, error in (
            (19646, OSError("partial browser send")),
            (19647, KeyboardInterrupt("interrupted after browser input")),
        ):
            with self.subTest(port=port):
                endpoint = f"http://127.0.0.1:{port}"
                target = {
                    "type": "page",
                    "webSocketDebuggerUrl":
                        f"ws://127.0.0.1:{port}/devtools/page/one",
                }

                class PartialSend(FakeSocket):
                    def send(self, payload):
                        # A browser-effecting Runtime.evaluate might already
                        # have been sent when the transport raised.
                        super().send(payload)
                        raise error

                def build(sock):
                    return _LoopbackCdpClient(
                        endpoint, timeout_seconds=1,
                        opener=lambda url, **kw: FakeResponse([target], url=url),
                        websocket_factory=lambda *a, **kw: sock,
                        tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
                    )

                initial_sock = PartialSend([])
                client = build(initial_sock)
                expected = (
                    PrivateWebCdpError
                    if isinstance(error, Exception) else type(error)
                )
                with self.assertRaises(expected):
                    client.call(
                        "Runtime.evaluate",
                        {"expression": "document.querySelector('button').click()"},
                    )
                self.assertEqual(len(initial_sock.sent), 1)
                with self.assertRaises(PrivateWebCdpError):
                    client.close()
                with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
                    client.call("Runtime.evaluate", {"expression": "1"})
                with self.assertRaises(PrivateWebCdpError) as blocked:
                    build(FakeSocket([]))
                self.assertEqual(blocked.exception.stage, "target_busy")

    def test_loopback_client_closes_direct_socket_on_websocket_failure(self) -> None:
        good_target = {
            "type": "page",
            "webSocketDebuggerUrl": (
                "ws://127.0.0.1:19610/devtools/page/opaque-target"
            ),
        }
        raw_socket = FakeTcpSocket()

        with self.assertRaises(PrivateWebCdpError) as caught:
            _LoopbackCdpClient(
                "http://127.0.0.1:19610",
                timeout_seconds=1,
                opener=lambda *args, **kwargs: FakeResponse([good_target]),
                websocket_factory=lambda *args, **kwargs: (
                    (_ for _ in ()).throw(RuntimeError("provider secret"))
                ),
                tcp_socket_factory=lambda *args, **kwargs: raw_socket,
            )

        self.assertEqual(caught.exception.stage, "connect")
        self.assertTrue(raw_socket.closed)
        self.assertNotIn("secret", str(caught.exception))

    def test_loopback_client_requires_one_same_port_page_target(self) -> None:
        good_target = {
            "type": "page",
            "webSocketDebuggerUrl": (
                "ws://127.0.0.1:19610/devtools/page/opaque-target"
            ),
        }
        bad_target_sets = (
            [],
            [good_target, good_target],
            [
                {
                    "type": "page",
                    "webSocketDebuggerUrl": (
                        "ws://127.0.0.1:19611/devtools/page/wrong-port"
                    ),
                }
            ],
            [
                {
                    "type": "page",
                    "webSocketDebuggerUrl": (
                        "ws://example.invalid:19610/devtools/page/offhost"
                    ),
                }
            ],
            [
                {
                    "type": "page",
                    "webSocketDebuggerUrl": (
                        "ws://127.0.0.1:19610/devtools/browser/not-page"
                    ),
                }
            ],
        )
        for targets in bad_target_sets:
            with self.subTest(targets=targets):
                with self.assertRaises(PrivateWebCdpError) as caught:
                    _LoopbackCdpClient(
                        "http://127.0.0.1:19610",
                        timeout_seconds=1,
                        opener=lambda *args, targets=targets, **kwargs: (
                            FakeResponse(targets)
                        ),
                        websocket_factory=lambda *args, **kwargs: FakeSocket([]),
                    )
                self.assertEqual(caught.exception.stage, "connect")

    def test_import_has_no_historical_adapter_or_websocket_side_effect(self) -> None:
        code = (
            "import sys; import mark_api.private_web_cdp; "
            "bad=sorted(name for name in sys.modules "
            "if name == 'websocket' or name.startswith('websocket.') "
            "or name == 'mark_api.adapters' "
            "or name.startswith('mark_api.adapters.')); "
            "print(bad); raise SystemExit(0 if not bad else 1)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            result.stdout + result.stderr,
        )
        self.assertEqual(result.stdout.strip(), "[]")


class CdpPrivateWebOwnerReaderTests(unittest.TestCase):
    @staticmethod
    def owner_snapshot(
        *,
        ad_id: str = AD_ID,
        title: str = "Management title",
        description: str | None = None,
    ) -> AdSnapshot:
        return AdSnapshot(
            ad_id=ad_id,
            observed_at=datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
            source="kleinanzeigen-management",
            lifecycle_state=LifecycleState.ACTIVE,
            title=title,
            description=description,
            views=7,
            watch_count=2,
            reply_count=1,
        )

    def test_owner_reader_enriches_exact_target_from_fresh_editor_read(self) -> None:
        target = self.owner_snapshot()
        other = self.owner_snapshot(ad_id="9999999999", title="Other")
        owner_result = ReadResult.success_nonempty((other, target))

        class OwnerReader:
            def read_ads(self):
                return owner_result

        class Page:
            def __init__(self) -> None:
                self.opened: list[str] = []
                self.closed = False

            def open_editor(self, ad_id: str) -> None:
                self.opened.append(ad_id)

            def read_editor(self) -> PrivateWebEditorSnapshot:
                return PrivateWebEditorSnapshot(
                    state=PrivateWebEditorState.READY,
                    ad_id=AD_ID,
                    title="Fresh title",
                    description="Fresh description",
                )

            def close(self) -> None:
                self.closed = True

        pages: list[Page] = []

        def page_factory():
            page = Page()
            pages.append(page)
            return page

        result = CdpPrivateWebOwnerReader(
            owner_reader=OwnerReader(),
            page_factory=page_factory,
            ad_id=AD_ID,
        ).read_ads()

        self.assertEqual(result.status, ReadStatus.SUCCESS_NONEMPTY)
        self.assertEqual(result.value[0], other)
        enriched = result.value[1]
        self.assertEqual(enriched.ad_id, AD_ID)
        self.assertEqual(enriched.title, "Fresh title")
        self.assertEqual(enriched.description, "Fresh description")
        self.assertEqual(enriched.lifecycle_state, LifecycleState.ACTIVE)
        self.assertEqual(enriched.views, 7)
        self.assertEqual(enriched.watch_count, 2)
        self.assertEqual(enriched.reply_count, 1)
        self.assertEqual(
            enriched.source,
            "kleinanzeigen-management+private-web",
        )
        self.assertEqual(enriched.metric_source, "kleinanzeigen-management")
        self.assertEqual(pages[0].opened, [AD_ID])
        self.assertTrue(pages[0].closed)

    def test_owner_reader_keeps_explicit_counter_origin_when_content_is_enriched(self) -> None:
        target = AdSnapshot(
            ad_id=AD_ID,
            observed_at=datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
            source="owner+mobile",
            metric_source="authoritative-management",
            lifecycle_state=LifecycleState.ACTIVE,
            title="Before",
            description="Before",
            views=0,
            watch_count=3,
            reply_count=2,
        )

        class OwnerReader:
            def read_ads(self):
                return ReadResult.success_nonempty((target,))

        class Page:
            def open_editor(self, ad_id):
                assert ad_id == AD_ID

            def read_editor(self):
                return PrivateWebEditorSnapshot(
                    state=PrivateWebEditorState.READY,
                    ad_id=AD_ID,
                    title="After",
                    description="After",
                )

            def close(self):
                pass

        result = CdpPrivateWebOwnerReader(
            owner_reader=OwnerReader(), page_factory=Page, ad_id=AD_ID,
        ).read_ads()
        self.assertEqual(result.status, ReadStatus.SUCCESS_NONEMPTY)
        item = result.value[0]
        self.assertEqual(item.title, "After")
        self.assertEqual(item.source, "owner+mobile+private-web")
        self.assertEqual(item.metric_source, "authoritative-management")
        self.assertEqual(item.views, 0)
        self.assertEqual(item.watch_count, 3)
        self.assertEqual(item.reply_count, 2)

    def test_owner_reader_does_not_open_editor_for_non_owner_target(self) -> None:
        owner_result = ReadResult.success_nonempty(
            (self.owner_snapshot(ad_id="9999999999"),)
        )

        class OwnerReader:
            def read_ads(self):
                return owner_result

        def page_factory():
            raise AssertionError("editor must not open for absent owner target")

        result = CdpPrivateWebOwnerReader(
            owner_reader=OwnerReader(),
            page_factory=page_factory,
            ad_id=AD_ID,
        ).read_ads()

        self.assertIs(result, owner_result)

    def test_owner_reader_fails_closed_on_editor_target_or_challenge_drift(self) -> None:
        owner_result = ReadResult.success_nonempty((self.owner_snapshot(),))
        snapshots = (
            PrivateWebEditorSnapshot(
                state=PrivateWebEditorState.READY,
                ad_id="9999999999",
                title="Wrong",
                description="Wrong",
            ),
            PrivateWebEditorSnapshot(
                state=PrivateWebEditorState.CAPTCHA_REQUIRED,
            ),
        )

        class OwnerReader:
            def read_ads(self):
                return owner_result

        for editor_snapshot in snapshots:
            with self.subTest(state=editor_snapshot.state):
                class Page:
                    def open_editor(self, ad_id: str) -> None:
                        self.ad_id = ad_id

                    def read_editor(self):
                        return editor_snapshot

                    def close(self) -> None:
                        self.closed = True

                result = CdpPrivateWebOwnerReader(
                    owner_reader=OwnerReader(),
                    page_factory=Page,
                    ad_id=AD_ID,
                ).read_ads()

                self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)
                self.assertIsNone(result.value)

    def test_owner_reader_rejects_invalid_owner_snapshot_before_editor(self) -> None:
        class OwnerReader:
            def read_ads(self):
                return ReadResult.success_nonempty((object(),))

        result = CdpPrivateWebOwnerReader(
            owner_reader=OwnerReader(),
            page_factory=lambda: (_ for _ in ()).throw(
                AssertionError("editor must not open for invalid owner snapshot")
            ),
            ad_id=AD_ID,
        ).read_ads()

        self.assertEqual(result.status, ReadStatus.PARSE_ERROR)
        self.assertEqual(result.error, "invalid_owner_snapshot")

    def test_owner_reader_sanitizes_page_close_failure(self) -> None:
        owner_result = ReadResult.success_nonempty((self.owner_snapshot(),))

        class OwnerReader:
            def read_ads(self):
                return owner_result

        class Page:
            def open_editor(self, ad_id: str) -> None:
                if ad_id != AD_ID:
                    raise AssertionError("wrong target")

            def read_editor(self):
                return PrivateWebEditorSnapshot(
                    state=PrivateWebEditorState.READY,
                    ad_id=AD_ID,
                    title="Fresh title",
                    description="Fresh description",
                )

            def close(self) -> None:
                raise RuntimeError("provider secret")

        result = CdpPrivateWebOwnerReader(
            owner_reader=OwnerReader(),
            page_factory=Page,
            ad_id=AD_ID,
        ).read_ads()

        self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual(result.error, "private_web_owner_read_failed")
        self.assertNotIn("secret", result.error)

    def test_description_update_can_be_confirmed_by_safe_orchestrator(self) -> None:
        state = {
            "title": "Existing title",
            "description": "Existing description",
        }
        target = self.owner_snapshot(title="Inventory title")

        class OwnerReader:
            def read_ads(self):
                return ReadResult.success_nonempty((target,))

        class Page:
            def open_editor(self, ad_id: str) -> None:
                if ad_id != AD_ID:
                    raise AssertionError("wrong target")

            def read_editor(self):
                return PrivateWebEditorSnapshot(
                    state=PrivateWebEditorState.READY,
                    ad_id=AD_ID,
                    title=state["title"],
                    description=state["description"],
                )

            def close(self) -> None:
                return None

        class Writer:
            calls = 0

            def update_content(
                self,
                ad_id: str,
                *,
                title: str | None = None,
                description: str | None = None,
            ) -> None:
                self.calls += 1
                if ad_id != AD_ID:
                    raise AssertionError("wrong target")
                if title is not None:
                    state["title"] = title
                if description is not None:
                    state["description"] = description

        reader = CdpPrivateWebOwnerReader(
            owner_reader=OwnerReader(),
            page_factory=Page,
            ad_id=AD_ID,
        )
        writer = Writer()

        receipt = SafeWriteOrchestrator(writes_enabled=True).update_content(
            ad_id=AD_ID,
            reader=reader,
            writer=writer,
            description="Updated description",
        )

        self.assertEqual(receipt.outcome, OperationOutcome.CONFIRMED)
        self.assertEqual(writer.calls, 1)
        self.assertEqual(
            receipt.post_snapshot.description,
            "Updated description",
        )


class CdpPrivateWebStatePageTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # Only synthetic in-memory CDP tests bypass platform admission.
        fake_only = patch("mark_api.private_web_cdp._require_private_web_cdp_authority")
        fake_only.start()
        cls.addClassCleanup(fake_only.stop)

    def bind_state(
        self,
        page: CdpPrivateWebPage,
        state: LifecycleState,
    ) -> None:
        page._bound_state_ad_id = AD_ID
        page._last_state_snapshot = PrivateWebStateSnapshot(
            state=PrivateWebEditorState.READY,
            ad_id=AD_ID,
            lifecycle_state=state,
        )

    def test_open_state_controls_navigates_to_owner_management_and_binds_target(self) -> None:
        values = [
            True,
            {"readyState": "complete", "oldDocument": False},
            {
                "state": "ready",
                "ad_id": AD_ID,
                "lifecycle_state": "active",
            },
        ]

        def handler(method, params):
            if method == "Page.navigate":
                self.assertEqual(
                    params["url"],
                    "https://www.kleinanzeigen.de/m-meine-anzeigen.html",
                )
                return {}
            if method == "Runtime.evaluate":
                if not values:
                    raise AssertionError("unexpected Runtime.evaluate")
                value = values.pop(0)
                return {"result": {"type": "object", "value": value}}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )

        page.open_state_controls(AD_ID)

        self.assertEqual(page._bound_state_ad_id, AD_ID)
        self.assertEqual(values, [])
        self.assertEqual(
            [method for method, _params in client.calls],
            [
                "Runtime.evaluate",
                "Page.navigate",
                "Runtime.evaluate",
                "Runtime.evaluate",
            ],
        )

    def test_open_state_controls_follows_management_pagination_to_target(self) -> None:
        values = [
            True,
            {"readyState": "complete", "oldDocument": False},
            {
                "state": "target_absent",
                "page_ad_ids": ["1111111111"],
                "next_page": {"x": 40.0, "y": 60.0},
            },
            {
                "state": "ready",
                "ad_id": AD_ID,
                "lifecycle_state": "active",
            },
        ]

        def handler(method, params):
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                if not values:
                    raise AssertionError("unexpected Runtime.evaluate")
                value = values.pop(0)
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
            sleep=lambda _seconds: None,
        )

        page.open_state_controls(AD_ID)

        self.assertEqual(values, [])
        self.assertEqual(page._bound_state_ad_id, AD_ID)
        self.assertEqual(
            [method for method, _params in client.calls],
            [
                "Runtime.evaluate",
                "Page.navigate",
                "Runtime.evaluate",
                "Runtime.evaluate",
                "Input.dispatchMouseEvent",
                "Input.dispatchMouseEvent",
                "Runtime.evaluate",
            ],
        )
        self.assertEqual(
            client.calls[4][1],
            {
                "type": "mousePressed",
                "x": 40.0,
                "y": 60.0,
                "button": "left",
                "buttons": 1,
                "clickCount": 1,
            },
        )
        self.assertEqual(
            client.calls[5][1],
            {
                "type": "mouseReleased",
                "x": 40.0,
                "y": 60.0,
                "button": "left",
                "buttons": 0,
                "clickCount": 1,
            },
        )
        pagination_expression = client.calls[3][1]["expression"]
        self.assertIn('button[aria-label="Nächste"]', pagination_expression)
        self.assertIn("elementFromPoint", pagination_expression)
        self.assertNotIn(".click(", pagination_expression)

    def test_open_state_controls_waits_for_transient_next_control(self) -> None:
        values = [
            True,
            {"readyState": "complete", "oldDocument": False},
            {
                "state": "target_absent",
                "page_ad_ids": ["1111111111"],
                "next_page": None,
            },
            {
                "state": "target_absent",
                "page_ad_ids": ["1111111111"],
                "next_page": {"x": 40.0, "y": 60.0},
            },
            {
                "state": "ready",
                "ad_id": AD_ID,
                "lifecycle_state": "active",
            },
        ]

        def handler(method, params):
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                if not values:
                    raise AssertionError("unexpected Runtime.evaluate")
                value = values.pop(0)
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
            sleep=lambda _seconds: None,
        )

        page.open_state_controls(AD_ID)

        self.assertEqual(values, [])
        self.assertEqual(page._bound_state_ad_id, AD_ID)
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            2,
        )

    def test_open_state_controls_fails_closed_when_target_is_absent_on_last_page(self) -> None:
        setup_values = [
            True,
            {"readyState": "complete", "oldDocument": False},
        ]
        last_page = {
            "state": "target_absent",
            "page_ad_ids": ["1111111111"],
            "next_page": None,
        }
        monotonic_values = iter((0.0, 0.0, 0.05, 0.1))

        def handler(method, params):
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                value = setup_values.pop(0) if setup_values else last_page
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                raise AssertionError("pagination input must not be attempted")
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            timeout_seconds=0.1,
            client_factory=lambda: client,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(monotonic_values),
        )

        with self.assertRaises(PrivateWebCdpError) as caught:
            page.open_state_controls(AD_ID)

        self.assertEqual(caught.exception.stage, "navigate_state")
        self.assertIsNone(page._bound_state_ad_id)
        self.assertEqual(setup_values, [])
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            0,
        )
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Runtime.evaluate"
            ),
            4,
        )

    def test_read_state_controls_is_exact_owner_and_control_bound(self) -> None:
        page, clients = page_with_results(
            {
                "state": "ready",
                "ad_id": AD_ID,
                "lifecycle_state": "active",
            }
        )
        page._bound_state_ad_id = AD_ID

        snapshot = page.read_state_controls()

        self.assertEqual(snapshot.state, PrivateWebEditorState.READY)
        self.assertEqual(snapshot.ad_id, AD_ID)
        self.assertEqual(snapshot.lifecycle_state, LifecycleState.ACTIVE)
        self.assertEqual(page._last_state_snapshot, snapshot)
        expression = clients[0].calls[0][1]["expression"]
        self.assertIn("/m-meine-anzeigen.html", expression)
        self.assertIn("/p-anzeige-bearbeiten.html", expression)
        self.assertIn('url.searchParams.get("adId")', expression)
        self.assertIn("targetEditLinks.length > 1", expression)
        self.assertIn("targetEditLinks.length === 1", expression)
        self.assertIn("editIds.length === 1", expression)
        self.assertIn('label === "reservieren"', expression)
        self.assertIn('label === "aktivieren"', expression)
        self.assertIn("controls.length === 1", expression)
        self.assertNotIn("document.body", expression)
        javascript_check = subprocess.run(
            ["node", "--check", "-"],
            input=expression,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(
            javascript_check.returncode,
            0,
            javascript_check.stdout + javascript_check.stderr,
        )

    def test_state_binding_ignores_collapsed_counter_control(self) -> None:
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: None,
        )
        expression = page._state_control_expression(AD_ID)
        template = """
global.HTMLButtonElement = class HTMLButtonElement {};
const editLink = {
  getAttribute: (name) =>
    name === "href"
      ? "/p-anzeige-bearbeiten.html?adId=__AD_ID__"
      : null,
  parentElement: null,
};
const makeControl = (label, rect) => {
  const control = new HTMLButtonElement();
  control.innerText = label;
  control.disabled = false;
  control.getAttribute = (_name) => null;
  control.getBoundingClientRect = () => rect;
  return control;
};
const visibleControl = makeControl(
  __VISIBLE_LABEL__,
  {left: 20, top: 20, width: 120, height: 30}
);
const collapsedCounterControl = makeControl(
  __COLLAPSED_LABEL__,
  {left: 0, top: 0, width: 0, height: 0}
);
const container = {
  parentElement: null,
  querySelectorAll: (selector) => {
    if (selector === "a[href]") return [editLink];
    if (selector === 'button, a[href], [role="button"]') {
      return [visibleControl, collapsedCounterControl];
    }
    return [];
  },
};
editLink.parentElement = container;
global.location = {
  origin: "https://www.kleinanzeigen.de",
  pathname: "/m-meine-anzeigen.html",
  href: "https://www.kleinanzeigen.de/m-meine-anzeigen.html",
};
global.document = {
  querySelector: (_selector) => null,
  querySelectorAll: (selector) =>
    selector === "a[href]" ? [editLink] : [],
};
global.getComputedStyle = (_element) => ({
  display: "block",
  visibility: "visible",
  pointerEvents: "auto",
  opacity: "1",
});
const result = eval(__EXPRESSION__);
process.stdout.write(JSON.stringify(result));
"""
        cases = (
            ("Reservieren", "Aktivieren", "active"),
            ("Aktivieren", "Reservieren", "paused"),
        )
        for visible_label, collapsed_label, expected_state in cases:
            with self.subTest(expected_state=expected_state):
                script = (
                    template.replace("__AD_ID__", AD_ID)
                    .replace("__VISIBLE_LABEL__", json.dumps(visible_label))
                    .replace("__COLLAPSED_LABEL__", json.dumps(collapsed_label))
                    .replace("__EXPRESSION__", json.dumps(expression))
                )
                completed = subprocess.run(
                    ["node", "-"],
                    input=script,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )

                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )
                self.assertEqual(
                    json.loads(completed.stdout),
                    {
                        "state": "ready",
                        "ad_id": AD_ID,
                        "lifecycle_state": expected_state,
                    },
                )

    def test_state_challenges_hide_target_data(self) -> None:
        for raw_state in (
            "login_required",
            "mfa_required",
            "captcha_required",
            "security_challenge",
            "unknown",
        ):
            with self.subTest(raw_state=raw_state):
                page, _ = page_with_results({"state": raw_state})
                page._bound_state_ad_id = AD_ID

                snapshot = page.read_state_controls()

                self.assertEqual(snapshot.state.value, raw_state)
                self.assertIsNone(snapshot.ad_id)
                self.assertIsNone(snapshot.lifecycle_state)
                self.assertIsNone(page._last_state_snapshot)

    def test_malformed_state_snapshot_fails_closed(self) -> None:
        for value in (
            {"state": "ready", "ad_id": AD_ID, "lifecycle_state": "pending"},
            {"state": "ready", "ad_id": "bad", "lifecycle_state": "active"},
            {"state": "ready", "ad_id": AD_ID, "lifecycle_state": 7},
            {"state": "not-a-state"},
            "not-a-dict",
        ):
            with self.subTest(value=value):
                page, _ = page_with_results(value)
                page._bound_state_ad_id = AD_ID

                snapshot = page.read_state_controls()

                self.assertEqual(snapshot.state, PrivateWebEditorState.UNKNOWN)
                self.assertIsNone(page._last_state_snapshot)

    def test_state_submit_uses_one_browser_level_hit_tested_activation(self) -> None:
        runtime_values = [
            {"x": 120.5, "y": 240.25},
            {
                "state": "ready",
                "ad_id": AD_ID,
                "lifecycle_state": "paused",
            },
        ]

        def handler(method, params):
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        self.bind_state(page, LifecycleState.ACTIVE)

        page.submit_state(LifecycleState.PAUSED)

        self.assertEqual(
            [method for method, _params in client.calls],
            [
                "Runtime.evaluate",
                "Input.dispatchMouseEvent",
                "Input.dispatchMouseEvent",
                "Runtime.evaluate",
            ],
        )
        expression = client.calls[0][1]["expression"]
        self.assertIn('"reservieren"', expression)
        self.assertIn("getComputedStyle(control)", expression)
        self.assertIn("control.disabled", expression)
        self.assertIn('control.getAttribute("aria-disabled")', expression)
        self.assertIn("getBoundingClientRect", expression)
        self.assertIn("elementFromPoint", expression)
        self.assertNotIn(".click(", expression)
        javascript_check = subprocess.run(
            ["node", "--check", "-"],
            input=expression,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(
            javascript_check.returncode,
            0,
            javascript_check.stdout + javascript_check.stderr,
        )
        self.assertEqual(
            client.calls[1][1],
            {
                "type": "mousePressed",
                "x": 120.5,
                "y": 240.25,
                "button": "left",
                "buttons": 1,
                "clickCount": 1,
            },
        )
        self.assertEqual(
            client.calls[2][1],
            {
                "type": "mouseReleased",
                "x": 120.5,
                "y": 240.25,
                "button": "left",
                "buttons": 0,
                "clickCount": 1,
            },
        )
        with self.assertRaisesRegex(
            PrivateWebCdpError,
            "state_submit_already_attempted",
        ):
            page.submit_state(LifecycleState.PAUSED)

    def test_activate_requires_paused_snapshot_and_activate_control(self) -> None:
        runtime_values = [
            {"x": 10.0, "y": 20.0},
            {
                "state": "ready",
                "ad_id": AD_ID,
                "lifecycle_state": "active",
            },
        ]

        def handler(method, params):
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        self.bind_state(page, LifecycleState.PAUSED)

        page.submit_state(LifecycleState.ACTIVE)

        expression = client.calls[0][1]["expression"]
        self.assertIn('"aktivieren"', expression)
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            2,
        )

    def test_state_submit_fails_safe_before_browser_input(self) -> None:
        client = FakeClient(
            lambda method, params: (
                {"result": {"type": "object", "value": None}}
                if method == "Runtime.evaluate"
                else (_ for _ in ()).throw(
                    AssertionError("input must not be attempted")
                )
            )
        )
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        self.bind_state(page, LifecycleState.ACTIVE)

        with self.assertRaisesRegex(PrivateWebCdpError, "state_submit"):
            page.submit_state(LifecycleState.PAUSED)

        self.assertEqual(
            [method for method, _params in client.calls],
            ["Runtime.evaluate"],
        )
        with self.assertRaisesRegex(
            PrivateWebCdpError,
            "state_submit_already_attempted",
        ):
            page.submit_state(LifecycleState.PAUSED)

    def test_state_submit_waits_for_delayed_lifecycle_settlement(self) -> None:
        runtime_values = [
            {"x": 10.0, "y": 20.0},
            {
                "state": "ready",
                "ad_id": AD_ID,
                "lifecycle_state": "active",
            },
            {
                "state": "ready",
                "ad_id": AD_ID,
                "lifecycle_state": "paused",
            },
        ]
        sleeps: list[float] = []

        def handler(method, params):
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
            sleep=sleeps.append,
        )
        self.bind_state(page, LifecycleState.ACTIVE)

        page.submit_state(LifecycleState.PAUSED)

        self.assertEqual(sleeps, [0.05])
        self.assertEqual(
            [method for method, _params in client.calls],
            [
                "Runtime.evaluate",
                "Input.dispatchMouseEvent",
                "Input.dispatchMouseEvent",
                "Runtime.evaluate",
                "Runtime.evaluate",
            ],
        )
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            2,
        )

    def test_state_submit_settlement_timeout_is_unknown_and_never_retried(self) -> None:
        runtime_values = [
            {"x": 10.0, "y": 20.0},
            {
                "state": "ready",
                "ad_id": AD_ID,
                "lifecycle_state": "active",
            },
        ]
        monotonic_values = iter((0.0, 0.1))

        def handler(method, params):
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            timeout_seconds=0.1,
            client_factory=lambda: client,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(monotonic_values),
        )
        self.bind_state(page, LifecycleState.ACTIVE)

        with self.assertRaises(PrivateWebSubmitUnknownError) as caught:
            page.submit_state(LifecycleState.PAUSED)

        self.assertEqual(caught.exception.stage, "state_submit_settle")
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            2,
        )
        with self.assertRaisesRegex(
            PrivateWebCdpError,
            "state_submit_already_attempted",
        ):
            page.submit_state(LifecycleState.PAUSED)
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            2,
        )

    def test_state_submit_provider_failure_after_input_is_submit_unknown(self) -> None:
        def handler(method, params):
            if method == "Runtime.evaluate":
                return {
                    "result": {
                        "type": "object",
                        "value": {"x": 10.0, "y": 20.0},
                    }
                }
            if method == "Input.dispatchMouseEvent":
                raise RuntimeError("provider response lost")
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        self.bind_state(page, LifecycleState.ACTIVE)

        with self.assertRaises(PrivateWebSubmitUnknownError) as caught:
            page.submit_state(LifecycleState.PAUSED)

        self.assertEqual(caught.exception.stage, "state_submit")
        self.assertEqual(
            [method for method, _params in client.calls],
            ["Runtime.evaluate", "Input.dispatchMouseEvent"],
        )
        with self.assertRaisesRegex(
            PrivateWebCdpError,
            "state_submit_already_attempted",
        ):
            page.submit_state(LifecycleState.PAUSED)

    def test_state_submit_rejects_wrong_prestate_before_browser_access(self) -> None:
        client = FakeClient(
            lambda method, params: (_ for _ in ()).throw(
                AssertionError("browser must not be touched")
            )
        )
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        self.bind_state(page, LifecycleState.PAUSED)

        with self.assertRaisesRegex(PrivateWebCdpError, "state_submit"):
            page.submit_state(LifecycleState.PAUSED)

        self.assertEqual(client.calls, [])


class CdpPrivateWebDeletePageTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # Only synthetic in-memory CDP tests bypass platform admission.
        fake_only = patch("mark_api.private_web_cdp._require_private_web_cdp_authority")
        fake_only.start()
        cls.addClassCleanup(fake_only.stop)

    def bind_delete(
        self,
        page: CdpPrivateWebPage,
        *,
        confirmation: bool = False,
    ) -> None:
        page._bound_delete_ad_id = AD_ID
        page._delete_confirmation_attempted = confirmation
        page._last_delete_snapshot = PrivateWebDeleteSnapshot(
            state=PrivateWebEditorState.READY,
            ad_id=AD_ID,
        )

    def test_delete_control_expression_is_exact_target_and_browser_level_only(self) -> None:
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: None,
        )
        expression = page._delete_control_expression(AD_ID)

        self.assertIn("/m-meine-anzeigen.html", expression)
        self.assertIn("/p-anzeige-bearbeiten.html", expression)
        self.assertIn('url.searchParams.get("adId")', expression)
        self.assertIn('label(element) === "löschen"', expression)
        self.assertIn("targetEditLinks.length > 1", expression)
        self.assertIn("editIds.length === 1", expression)
        self.assertIn("controls.length === 1", expression)
        self.assertIn("elementFromPoint", expression)
        self.assertNotIn(".click(", expression)
        javascript_check = subprocess.run(
            ["node", "--check", "-"],
            input=expression,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(
            javascript_check.returncode,
            0,
            javascript_check.stdout + javascript_check.stderr,
        )

    def test_delete_confirmation_expression_matches_observed_single_delete_modal(self) -> None:
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: None,
        )
        expression = page._delete_confirmation_expression(AD_ID)

        self.assertIn("/m-meine-anzeigen.html", expression)
        self.assertIn("#delete-container", expression)
        self.assertIn("#delete-celebration-sbmt", expression)
        self.assertIn("anzeige löschen", expression)
        self.assertIn(
            "bist du sicher, dass du die anzeige löschen möchtest?",
            expression,
        )
        self.assertIn("ja, anzeige löschen", expression)
        self.assertIn("abbrechen", expression)
        self.assertIn("elementFromPoint", expression)
        self.assertNotIn(".click(", expression)
        javascript_check = subprocess.run(
            ["node", "--check", "-"],
            input=expression,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(
            javascript_check.returncode,
            0,
            javascript_check.stdout + javascript_check.stderr,
        )

    def test_open_delete_controls_follows_management_pagination_to_target(self) -> None:
        values = [
            True,
            {"readyState": "complete", "oldDocument": False},
            {
                "state": "target_absent",
                "page_ad_ids": ["1111111111"],
                "next_page": {"x": 40.0, "y": 60.0},
            },
            {
                "state": "ready",
                "ad_id": AD_ID,
            },
        ]

        def handler(method, params):
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                if not values:
                    raise AssertionError("unexpected Runtime.evaluate")
                value = values.pop(0)
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
            sleep=lambda _seconds: None,
        )

        page.open_delete_controls(AD_ID)

        self.assertEqual(values, [])
        self.assertEqual(page._bound_delete_ad_id, AD_ID)
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            2,
        )

    def test_open_delete_controls_waits_for_transient_next_control(self) -> None:
        values = [
            True,
            {"readyState": "complete", "oldDocument": False},
            {
                "state": "target_absent",
                "page_ad_ids": ["1111111111"],
                "next_page": None,
            },
            {
                "state": "target_absent",
                "page_ad_ids": ["1111111111"],
                "next_page": {"x": 40.0, "y": 60.0},
            },
            {
                "state": "ready",
                "ad_id": AD_ID,
            },
        ]

        def handler(method, params):
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                if not values:
                    raise AssertionError("unexpected Runtime.evaluate")
                value = values.pop(0)
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
            sleep=lambda _seconds: None,
        )

        page.open_delete_controls(AD_ID)

        self.assertEqual(values, [])
        self.assertEqual(page._bound_delete_ad_id, AD_ID)
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            2,
        )

    def test_open_delete_controls_last_page_miss_is_bounded_and_no_input(self) -> None:
        setup_values = [
            True,
            {"readyState": "complete", "oldDocument": False},
        ]
        last_page = {
            "state": "target_absent",
            "page_ad_ids": ["1111111111"],
            "next_page": None,
        }
        monotonic_values = iter((0.0, 0.0, 0.1))

        def handler(method, params):
            if method == "Page.navigate":
                return {}
            if method == "Runtime.evaluate":
                value = setup_values.pop(0) if setup_values else last_page
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                raise AssertionError("pagination input must not be attempted")
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            timeout_seconds=0.1,
            client_factory=lambda: client,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(monotonic_values),
        )

        with self.assertRaisesRegex(PrivateWebCdpError, "navigate_delete"):
            page.open_delete_controls(AD_ID)

        self.assertIsNone(page._bound_delete_ad_id)
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            0,
        )

    def test_open_delete_confirmation_uses_one_browser_input_pair(self) -> None:
        runtime_values = [
            {"x": 10.0, "y": 20.0},
            {"state": "ready", "ad_id": AD_ID},
        ]

        def handler(method, params):
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        self.bind_delete(page)

        page.open_delete_confirmation()

        self.assertTrue(page._delete_confirmation_attempted)
        self.assertEqual(runtime_values, [])
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            2,
        )

    def test_open_delete_confirmation_input_failure_is_unknown_no_retry(self) -> None:
        def handler(method, params):
            if method == "Runtime.evaluate":
                return {
                    "result": {
                        "type": "object",
                        "value": {"x": 10.0, "y": 20.0},
                    }
                }
            if method == "Input.dispatchMouseEvent":
                raise RuntimeError("browser response lost")
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        self.bind_delete(page)

        with self.assertRaises(PrivateWebSubmitUnknownError) as caught:
            page.open_delete_confirmation()

        self.assertEqual(caught.exception.stage, "delete_open_confirmation")
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            1,
        )
        with self.assertRaisesRegex(
            PrivateWebCdpError,
            "delete_confirmation_already_attempted",
        ):
            page.open_delete_confirmation()
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            1,
        )

    def test_submit_delete_uses_one_confirm_input_pair_and_settles(self) -> None:
        runtime_values = [
            {"x": 10.0, "y": 20.0},
            True,
        ]

        def handler(method, params):
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        self.bind_delete(page, confirmation=True)

        page.submit_delete()

        self.assertEqual(runtime_values, [])
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            2,
        )
        with self.assertRaisesRegex(
            PrivateWebCdpError,
            "delete_submit_already_attempted",
        ):
            page.submit_delete()
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            2,
        )

    def test_submit_delete_provider_failure_after_input_is_unknown_no_retry(self) -> None:
        def handler(method, params):
            if method == "Runtime.evaluate":
                return {
                    "result": {
                        "type": "object",
                        "value": {"x": 10.0, "y": 20.0},
                    }
                }
            if method == "Input.dispatchMouseEvent":
                raise RuntimeError("provider response lost")
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            client_factory=lambda: client,
        )
        self.bind_delete(page, confirmation=True)

        with self.assertRaises(PrivateWebSubmitUnknownError) as caught:
            page.submit_delete()

        self.assertEqual(caught.exception.stage, "delete_submit")
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            1,
        )
        with self.assertRaisesRegex(
            PrivateWebCdpError,
            "delete_submit_already_attempted",
        ):
            page.submit_delete()
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            1,
        )

    def test_submit_delete_settlement_timeout_is_unknown_no_retry(self) -> None:
        runtime_values = [
            {"x": 10.0, "y": 20.0},
            False,
        ]
        monotonic_values = iter((0.0, 0.1))

        def handler(method, params):
            if method == "Runtime.evaluate":
                value = runtime_values.pop(0)
                return {"result": {"type": "object", "value": value}}
            if method == "Input.dispatchMouseEvent":
                return {}
            raise AssertionError(f"unexpected method: {method}")

        client = FakeClient(handler)
        page = CdpPrivateWebPage(
            "http://127.0.0.1:19610",
            timeout_seconds=0.1,
            client_factory=lambda: client,
            sleep=lambda _seconds: None,
            monotonic=lambda: next(monotonic_values),
        )
        self.bind_delete(page, confirmation=True)

        with self.assertRaises(PrivateWebSubmitUnknownError) as caught:
            page.submit_delete()

        self.assertEqual(caught.exception.stage, "delete_submit_settle")
        self.assertEqual(
            [method for method, _params in client.calls].count(
                "Input.dispatchMouseEvent"
            ),
            2,
        )
        with self.assertRaisesRegex(
            PrivateWebCdpError,
            "delete_submit_already_attempted",
        ):
            page.submit_delete()


class HostileSubmitParameterGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        synthetic = patch("mark_api.private_web_cdp._require_private_web_cdp_authority")
        synthetic.start()
        cls.addClassCleanup(synthetic.stop)

    def test_float_subclass_cannot_reenter_armed_click_comparison(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract target reservation required")
        port = 19831
        endpoint = f"http://127.0.0.1:{port}"
        target = {
            "type": "page",
            "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/original",
        }
        sock = FakeSocket([
            {"id": 1, "result": {}},
            {"id": 2, "result": {}},
            {"id": 3, "result": {
                "result": {"type": "string", "value": "confirmed"}
            }},
        ])
        client = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kw: FakeResponse([target], url=url),
            websocket_factory=lambda *a, **kw: sock,
            tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
        )
        pressed = {
            "type": "mousePressed", "x": 12.0, "y": 20.0,
            "button": "left", "buttons": 1, "clickCount": 1,
        }
        expression = "(() => 'confirmed')()"
        client.arm_unknown_submit(
            settlement_expression=expression, click_point=(12.0, 20.0),
        )
        comparison_hooks = []

        class HostileFloat(float):
            def __eq__(self, other):
                comparison_hooks.append("entered")
                try:
                    client.call("Input.dispatchMouseEvent", pressed)
                except PrivateWebCdpWriteNotAttemptedError:
                    pass
                else:
                    comparison_hooks.append("nested_browser_click")
                return super().__eq__(other)

        hostile = dict(pressed)
        hostile["x"] = HostileFloat(12.0)
        with self.assertRaises(PrivateWebCdpWriteNotAttemptedError) as blocked:
            client.call("Input.dispatchMouseEvent", hostile)
        self.assertEqual(blocked.exception.stage, "submit_unowned")
        self.assertEqual(comparison_hooks, [])
        self.assertEqual(sock.sent, [])
        client.call("Input.dispatchMouseEvent", pressed)
        client.call("Input.dispatchMouseEvent", dict(pressed, type="mouseReleased", buttons=0))
        client.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": False,
        })
        client.settle_unknown_submit()
        client.close()
        self.assertEqual(
            [frame["method"] for frame in sock.sent],
            ["Input.dispatchMouseEvent", "Input.dispatchMouseEvent", "Runtime.evaluate"],
        )

    def _synthetic_armed_owner(self, port: int, responses):
        endpoint = f"http://127.0.0.1:{port}"
        target = {
            "type": "page",
            "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/original",
        }
        sock = FakeSocket(responses)
        client = _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kw: FakeResponse([target], url=url),
            websocket_factory=lambda *a, **kw: sock,
            tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
        )
        return client, sock

    def test_str_subclass_key_cannot_reenter_pinned_observation(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract target reservation required")
        client, sock = self._synthetic_armed_owner(19832, [{
            "id": 1, "result": {
                "result": {"type": "string", "value": "confirmed"},
            },
        }])
        expression = "(() => 'confirmed')()"
        canonical = {
            "expression": expression, "returnByValue": True, "awaitPromise": False,
        }
        client.arm_unknown_submit(settlement_expression=expression)
        comparison_hooks = []

        class HostileKey(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                comparison_hooks.append("entered")
                try:
                    client.call("Runtime.evaluate", canonical)
                except PrivateWebCdpWriteNotAttemptedError:
                    pass
                else:
                    comparison_hooks.append("nested_observer")
                return super().__eq__(other)

        hostile = {
            HostileKey("expression"): expression,
            "returnByValue": True,
            "awaitPromise": False,
        }
        comparison_hooks.clear()
        with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
            client.call("Runtime.evaluate", hostile)
        self.assertEqual(comparison_hooks, [])
        self.assertEqual(sock.sent, [])
        client.call("Runtime.evaluate", canonical)
        client.settle_unknown_submit()
        client.close()

    def test_str_subclass_method_cannot_reenter_mouse_press(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract target reservation required")
        responses = [
            {"id": 1, "result": {}},
            {"id": 2, "result": {}},
            {"id": 3, "result": {
                "result": {"type": "string", "value": "confirmed"},
            }},
        ]
        client, sock = self._synthetic_armed_owner(19833, responses)
        pressed = {
            "type": "mousePressed", "x": 12.0, "y": 20.0,
            "button": "left", "buttons": 1, "clickCount": 1,
        }
        expression = "(() => 'confirmed')()"
        client.arm_unknown_submit(
            settlement_expression=expression, click_point=(12.0, 20.0),
        )
        comparison_hooks = []

        class HostileMethod(str):
            def __eq__(self, other):
                comparison_hooks.append("entered")
                try:
                    client.call("Input.dispatchMouseEvent", pressed)
                except PrivateWebCdpWriteNotAttemptedError:
                    pass
                else:
                    comparison_hooks.append("nested_click")
                return super().__eq__(other)

        with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
            client.call(HostileMethod("Input.dispatchMouseEvent"), pressed)
        self.assertEqual(comparison_hooks, [])
        self.assertEqual(sock.sent, [])
        client.call("Input.dispatchMouseEvent", pressed)
        client.call("Input.dispatchMouseEvent", dict(
            pressed, type="mouseReleased", buttons=0,
        ))
        client.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": False,
        })
        client.settle_unknown_submit()
        client.close()

    def test_settlement_expression_subclass_cannot_execute_before_arming(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux abstract target reservation required")
        client, sock = self._synthetic_armed_owner(
            19834, [{"id": 1, "result": {}}],
        )
        executed = []

        class HostileExpression(str):
            def strip(self, *args, **kwargs):
                executed.append("strip")
                client.call("Page.navigate", {
                    "url": "https://example.invalid/",
                })
                return super().strip(*args, **kwargs)

        with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
            client.arm_unknown_submit(
                settlement_expression=HostileExpression("(() => 'confirmed')()"),
            )
        self.assertEqual(executed, [])
        self.assertEqual(sock.sent, [])
        self.assertFalse(client._unresolved_submit)
        client.close()


class CancellableSubmitInterlockTests(unittest.TestCase):
    """The inventory cancellation hook is never a P1 submit admission hook."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        synthetic = patch("mark_api.private_web_cdp._require_private_web_cdp_authority")
        synthetic.start()
        cls.addClassCleanup(synthetic.stop)

    @staticmethod
    def _claimed_synthetic_client(port: int) -> _LoopbackCdpClient:
        endpoint = f"http://127.0.0.1:{port}"
        target = {
            "type": "page",
            "webSocketDebuggerUrl":
                f"ws://127.0.0.1:{port}/devtools/page/synthetic",
        }
        sock = FakeSocket([])
        sock.settimeout = lambda _seconds: None
        return _LoopbackCdpClient(
            endpoint, timeout_seconds=1,
            opener=lambda url, **kw: FakeResponse([target], url=url),
            websocket_factory=lambda *a, **kw: sock,
            tcp_socket_factory=lambda *a, **kw: FakeTcpSocket(),
        )

    def test_armed_unknown_submit_rejects_late_untrusted_cancel_binding(self):
        if sys.platform != "linux":
            self.skipTest("Linux exclusive AF_UNIX target claim required")
        client = self._claimed_synthetic_client(19901)
        try:
            client.arm_unknown_submit(
                settlement_expression="(() => 'confirmed')()",
                click_point=(12.0, 20.0),
            )
            with self.assertRaises(PrivateWebCdpReadSetupError):
                client.bind_cancellable_reads(
                    stop_requested=lambda: False,
                    send_guard=lambda: None,
                )
            self.assertIsNone(client._read_stop_check)
        finally:
            try:
                client.close()
            except PrivateWebCdpError:
                pass

    def test_bound_cancellable_inventory_rejects_later_submit_arm(self):
        if sys.platform != "linux":
            self.skipTest("Linux exclusive AF_UNIX target claim required")
        client = self._claimed_synthetic_client(19902)
        try:
            client.bind_cancellable_reads(
                stop_requested=lambda: False,
                send_guard=lambda: None,
            )
            with self.assertRaises(PrivateWebCdpWriteNotAttemptedError):
                client.arm_unknown_submit(
                    settlement_expression="(() => 'confirmed')()",
                    click_point=(12.0, 20.0),
                )
            self.assertFalse(client._unresolved_submit)
        finally:
            try:
                client.close()
            except PrivateWebCdpError:
                pass


class SyntheticCancellableCdpReadTests(unittest.TestCase):
    """Synthetic cancellation-only signal/deadline probes.

    CDP target admission and claim checks are tested separately above. These
    cases isolate the socket send/recv admission path with a scripted socket.
    """
    def setUp(self):
        admission = patch("mark_api.private_web_cdp._require_private_web_cdp_authority")
        admission.start()
        self.addCleanup(admission.stop)
        claim = patch.object(_LoopbackCdpClient, "_assert_live_target_reservation")
        claim.start()
        self.addCleanup(claim.stop)

    @staticmethod
    def _new_client():
        from threading import RLock
        client = object.__new__(_LoopbackCdpClient)
        client._lifecycle_lock = RLock()
        client._closing = False
        client._reconnecting = False
        client._close_unknown = False
        client._active_calls = 0
        client._unresolved_submit = False
        client._unresolved_call = False
        client._submit_settlement_expression = None
        client._submit_phase = "idle"
        client._transport_construction_pending = False
        client._connect_in_progress = False
        client._read_stop_check = None
        client._read_send_guard = None
        client._next_id = 0
        return client

    def test_cancellable_cdp_send_defers_term_only_during_socket_send(self) -> None:
        if not hasattr(signal, "pthread_sigmask"):
            self.skipTest("POSIX signal mask required")
        stopped = []
        class Socket:
            def __init__(self):
                self.sent = 0
                self.received = 0
                self.timeout = 1.0
                self.timeouts = []
            def settimeout(self, value):
                self.timeout = value
                self.timeouts.append(value)
            def send(self, message):
                self_outer.assertEqual(self.timeout, 0.0)
                self.sent += 1
                signal.raise_signal(signal.SIGTERM)
                self_outer.assertEqual(stopped, [])
            def recv(self):
                self.received += 1
                raise AssertionError("no recv after signal")
        self_outer = self
        socket = Socket()
        client = self._new_client()
        client._next_id = 0
        client._socket = socket
        client._timeout_seconds = 5.0
        predicate = lambda: bool(stopped)
        client.bind_cancellable_reads(
            stop_requested=predicate,
            send_guard=lambda: _cancellable_transport_entry(predicate),
        )
        original = signal.signal(signal.SIGTERM, lambda *_: stopped.append(True))
        try:
            with self.assertRaises(PrivateWebCdpReadCancelled):
                client.call("Runtime.evaluate")
        finally:
            signal.signal(signal.SIGTERM, original)
        self.assertEqual(stopped, [True])
        self.assertEqual(socket.sent, 1)
        self.assertEqual(socket.received, 0)
        self.assertEqual(socket.timeouts, [0.0, 0.25])

    def test_default_sigint_interrupts_unbounded_cdp_recv_after_one_frame(self) -> None:
        if not hasattr(signal, "pthread_sigmask"):
            self.skipTest("POSIX signal mask required")
        class EventFlood:
            def __init__(self):
                self.receives = 0
                self.timeouts = []
            def settimeout(self, value):
                self.timeouts.append(value)
            def send(self, message):
                pass
            def recv(self):
                self.receives += 1
                signal.raise_signal(signal.SIGINT)
                return json.dumps({"method": "Page.event"})
        socket = EventFlood()
        client = self._new_client()
        client._next_id = 0
        client._socket = socket
        client._timeout_seconds = 5.0
        client.bind_cancellable_reads(
            stop_requested=lambda: False,
            send_guard=lambda: _cancellable_transport_entry(lambda: False),
        )
        original = signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            with self.assertRaises(KeyboardInterrupt):
                client.call("Runtime.evaluate")
        finally:
            signal.signal(signal.SIGINT, original)
        self.assertEqual(socket.receives, 1)
        self.assertEqual(socket.timeouts, [0.0, 0.25, 0.25])

    def test_cancellable_send_is_nonblocking_while_sigterm_is_masked(self) -> None:
        if not hasattr(signal, "pthread_sigmask"):
            self.skipTest("POSIX signal mask required")
        class WouldBlock:
            def __init__(self):
                self.timeout = 1.0
                self.attempted = 0
                self.received = 0
            def settimeout(self, value):
                self.timeout = value
            def send(self, message):
                self.attempted += 1
                self_outer.assertEqual(self.timeout, 0.0)
                # Emulate full kernel send-buffer. Must not wait under mask.
                raise BlockingIOError("synthetic full socket send buffer")
            def recv(self):
                self.received += 1
                raise AssertionError("no read after an unsent CDP frame")
        self_outer = self
        socket = WouldBlock()
        client = self._new_client()
        client._next_id = 0
        client._socket = socket
        client._timeout_seconds = 5.0
        predicate = lambda: False
        client.bind_cancellable_reads(
            stop_requested=predicate,
            send_guard=lambda: _cancellable_transport_entry(predicate),
        )
        with self.assertRaises(PrivateWebCdpError) as caught:
            client.call("Runtime.evaluate")
        self.assertEqual(caught.exception.stage, "call")
        self.assertEqual(socket.attempted, 1)
        self.assertEqual(socket.received, 0)
        self.assertEqual(socket.timeout, 0.25)

    def test_cancellable_recv_timeout_observes_flag_only_sigterm(self) -> None:
        if not hasattr(signal, "pthread_sigmask"):
            self.skipTest("POSIX signal mask required")
        stopped = []
        class QuietSocket:
            def __init__(self):
                self.timeout = 1.0
                self.received = 0
                self.sent = 0
            def settimeout(self, value):
                self.timeout = value
            def send(self, message):
                self_outer.assertEqual(self.timeout, 0.0)
                self.sent += 1
            def recv(self):
                self_outer.assertEqual(self.timeout, 0.25)
                self.received += 1
                # A signal alone produces no CDP response. The bounded
                # socket timeout must still let the stop flag end the call.
                signal.raise_signal(signal.SIGTERM)
                raise TimeoutError("quiet local CDP reader")
        self_outer = self
        socket = QuietSocket()
        client = self._new_client()
        client._next_id = 0
        client._socket = socket
        client._timeout_seconds = 5.0
        original = signal.signal(signal.SIGTERM, lambda *_: stopped.append(True))
        try:
            client.bind_cancellable_reads(
                stop_requested=lambda: bool(stopped),
                send_guard=lambda: _cancellable_transport_entry(lambda: bool(stopped)),
            )
            with self.assertRaises(PrivateWebCdpReadCancelled):
                client.call("Runtime.evaluate")
        finally:
            signal.signal(signal.SIGTERM, original)
        self.assertEqual(stopped, [True])
        self.assertEqual(socket.sent, 1)
        self.assertEqual(socket.received, 1)

    def test_cancellable_event_flood_has_absolute_call_deadline(self) -> None:
        class EventFlood:
            def __init__(self):
                self.timeouts = []
                self.received = 0
            def settimeout(self, value):
                self.timeouts.append(value)
            def send(self, message):
                pass
            def recv(self):
                self.received += 1
                return json.dumps({"method": "Page.event"})
        socket = EventFlood()
        client = self._new_client()
        client._next_id = 0
        client._socket = socket
        client._timeout_seconds = 0.02
        client.bind_cancellable_reads(
            stop_requested=lambda: False,
            send_guard=lambda: _cancellable_transport_entry(lambda: False),
        )
        with patch(
            "mark_api.private_web_cdp.time.monotonic",
            side_effect=[0.0, 0.005, 0.006, 0.01, 0.02],
        ):
            with self.assertRaises(PrivateWebCdpError) as caught:
                client.call("Runtime.evaluate")
        self.assertEqual(caught.exception.stage, "call")
        self.assertEqual(socket.received, 2)
        self.assertEqual(socket.timeouts[:2], [0.0, 0.25])
        self.assertTrue(all(0 < timeout <= 0.02 for timeout in socket.timeouts[2:]))

    def test_cancellable_short_budget_rejects_late_response(self) -> None:
        # Regression: the original .25s recv poll expanded a caller's .02s
        # budget, accepting a result returned at .08s after the deadline.
        class TimedReply:
            def __init__(self):
                self.timeouts = []
                self.received = 0
            def settimeout(self, value):
                self.timeouts.append(value)
            def send(self, message):
                pass
            def recv(self):
                self.received += 1
                return json.dumps({"id": 1, "result": {"ok": True}})
        socket = TimedReply()
        client = self._new_client()
        client._next_id = 0
        client._socket = socket
        client._timeout_seconds = 0.02
        client.bind_cancellable_reads(
            stop_requested=lambda: False,
            send_guard=lambda: _cancellable_transport_entry(lambda: False),
        )
        with patch("mark_api.private_web_cdp.time.monotonic",
                   side_effect=[0.0, 0.0, 0.08]):
            with self.assertRaises(PrivateWebCdpError) as caught:
                client.call("Runtime.evaluate")
        self.assertEqual(caught.exception.stage, "call")
        self.assertEqual(socket.received, 1)
        self.assertTrue(0 < socket.timeouts[-1] <= 0.02)

    def test_cancellable_short_budget_allows_timely_response(self) -> None:
        class TimedReply:
            def __init__(self):
                self.timeouts = []
            def settimeout(self, value):
                self.timeouts.append(value)
            def send(self, message):
                pass
            def recv(self):
                return json.dumps({"id": 1, "result": {"ok": True}})
        socket = TimedReply()
        client = self._new_client()
        client._next_id = 0
        client._socket = socket
        client._timeout_seconds = 0.02
        client.bind_cancellable_reads(
            stop_requested=lambda: False,
            send_guard=lambda: _cancellable_transport_entry(lambda: False),
        )
        with patch("mark_api.private_web_cdp.time.monotonic",
                   side_effect=[0.0, 0.0, 0.01]):
            result = client.call("Runtime.evaluate")
        self.assertEqual(result, {"ok": True})
        self.assertTrue(0 < socket.timeouts[-1] <= 0.02)

    def test_cancellable_transport_requires_socket_timeout_control(self) -> None:
        class UnsupportedSocket:
            def send(self, message):
                raise AssertionError("must not call unsupported socket")
        client = self._new_client()
        client._next_id = 0
        client._socket = UnsupportedSocket()
        client._timeout_seconds = 5.0
        with self.assertRaises(PrivateWebCdpReadSetupError):
            client.bind_cancellable_reads(
                stop_requested=lambda: False,
                send_guard=lambda: _cancellable_transport_entry(lambda: False),
            )


if __name__ == "__main__":
    unittest.main()