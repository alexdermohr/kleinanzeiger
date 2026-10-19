"""Adversarial, exclusively loopback Host-authority tests for Mark dashboard.

No browser, public platform traffic, live accounts or platform writes are used.
"""
from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

from mark_api.dashboard import DashboardWriteProxy, create_server
from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.storage import SnapshotStore


class DashboardHostAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="mark-dashboard-host-")
        self.addCleanup(self.temp.cleanup)
        db = Path(self.temp.name) / "mark.sqlite"
        store = SnapshotStore(db)
        self.store = store
        store.append_ad_snapshot(AdSnapshot(
            ad_id="1234567890",
            observed_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
            source="synthetic-offline",
            lifecycle_state=LifecycleState.ACTIVE,
        ))
        self.server = create_server(store, port=0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        # LIFO test cleanups: shutdown -> close -> join; do not wait on a
        # still-serving worker, and never leave a listener after the case.
        self.addCleanup(self.worker.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        host, self.port = self.server.server_address
        self.assertEqual(host, "127.0.0.1")

    def request(
        self,
        method: str,
        path: str,
        hosts: tuple[str, ...],
    ) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.putrequest(method, path, skip_host=True)
            for host in hosts:
                connection.putheader("Host", host)
            if method in ("POST", "PATCH", "PUT"):
                connection.putheader("Content-Length", "0")
            connection.endheaders()
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_only_exact_local_authority_can_read_existing_summary(self) -> None:
        status, body = self.request(
            "GET", "/api/summary", (f"127.0.0.1:{self.port}",),
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["tracked_ads"], 1)

        status, body = self.request(
            "GET", "/healthz", (f"127.0.0.1:{self.port}",),
        )
        self.assertEqual((status, json.loads(body)), (200, {"status": "ok"}))

    def test_foreign_host_cannot_read_summary_or_dashboard_assets(self) -> None:
        # A hostile website can resolve its own domain to loopback. Its Host
        # remains hostile even if the TCP destination is 127.0.0.1.
        hostile = (
            f"attacker-controlled.example:{self.port}",
            f"127.0.0.1.attacker.example:{self.port}",
            f"localhost.attacker.example:{self.port}",
            f"127.0.0.1:{self.port + 1}",
            "127.0.0.1",
            f"127.0.0.1:{self.port},attacker.example",
            f"[::1]:{self.port}",
        )
        for value in hostile:
            for path in ("/api/summary", "/", "/dashboard.js"):
                with self.subTest(host=value, path=path):
                    status, body = self.request("GET", path, (value,))
                    self.assertEqual(status, 421)
                    self.assertNotIn(b"tracked_ads", body)
                    self.assertNotIn(b"1234567890", body)

    def test_missing_or_duplicate_host_is_rejected_before_routing(self) -> None:
        legitimate = f"127.0.0.1:{self.port}"
        for values in (
            (),
            (legitimate, legitimate),
            (legitimate, f"attacker.example:{self.port}"),
            (f"attacker.example:{self.port}", legitimate),
        ):
            with self.subTest(hosts=values):
                status, body = self.request("GET", "/api/summary", values)
                self.assertEqual(status, 421)
                self.assertNotIn(b"1234567890", body)

    def test_default_http_port_accepts_portless_literal_loopback_host(self) -> None:
        # HTTP clients omit :80 on the wire. Exercise the handler authority
        # contract without binding a privileged host port or changing services.
        from unittest.mock import patch

        with patch.object(self.server, "server_address", ("127.0.0.1", 80)):
            for allowed in ("127.0.0.1", "127.0.0.1:80"):
                with self.subTest(host=allowed):
                    status, body = self.request("GET", "/healthz", (allowed,))
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body), {"status": "ok"})
            for rejected in ("127.0.0.1:81", "attacker.example", "localhost"):
                with self.subTest(host=rejected):
                    status, body = self.request("GET", "/api/summary", (rejected,))
                    self.assertEqual(status, 421)
                    self.assertNotIn(b"1234567890", body)

    def test_invalid_host_precedes_http_method_or_write_proxy_handling(self) -> None:
        foreign = (f"attacker-controlled.example:{self.port}",)
        for method in ("HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                status, _ = self.request(method, "/api/write/delete", foreign)
                self.assertEqual(status, 421)

        # Existing read-only semantics are unchanged for the authorized Host.
        expected = (("HEAD", 405), ("POST", 405), ("OPTIONS", 405))
        for method, status_expected in expected:
            with self.subTest(local_method=method):
                status, _ = self.request(
                    method, "/api/write/delete", (f"127.0.0.1:{self.port}",),
                )
                self.assertEqual(status, status_expected)

    def test_parser_without_current_headers_rejects_even_valid_host(self) -> None:
        # Force an otherwise successful base parser that produced no headers.
        # A stale self.headers from an earlier request must not grant access.
        def legacy_without_headers(handler: BaseHTTPRequestHandler) -> bool:
            handler.command = "GET"
            handler.path = "/api/summary"
            handler.request_version = "HTTP/1.1"
            handler.requestline = "GET /api/summary HTTP/1.1"
            return True

        with patch.object(
            BaseHTTPRequestHandler, "parse_request", legacy_without_headers,
        ):
            status, body = self.request(
                "GET", "/api/summary", (f"127.0.0.1:{self.port}",),
            )
        self.assertEqual(status, 421)
        self.assertNotIn(b"tracked_ads", body)
        self.assertNotIn(b"1234567890", body)

    def test_prior_valid_host_never_survives_headerless_parse_on_same_connection(self) -> None:
        # First request plants valid Host in the live handler. The second
        # synthetic legacy parser path succeeds without assigning headers.
        # Without the production self.headers reset it wrongly reuses Host.
        actual_parse = BaseHTTPRequestHandler.parse_request
        handlers: list[int] = []

        def first_real_then_headerless(handler: BaseHTTPRequestHandler) -> bool:
            handlers.append(id(handler))
            if len(handlers) == 1:
                return actual_parse(handler)
            handler.command = "GET"
            handler.path = "/api/summary"
            handler.request_version = "HTTP/1.1"
            handler.requestline = "GET /api/summary HTTP/1.1"
            return True

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            # HTTP/1.1 response framing retains one TCP socket and handler.
            with (
                patch.object(self.server.RequestHandlerClass, "protocol_version", "HTTP/1.1"),
                patch.object(
                    BaseHTTPRequestHandler, "parse_request",
                    first_real_then_headerless,
                ),
            ):
                connection.putrequest("GET", "/api/summary", skip_host=True)
                connection.putheader("Host", f"127.0.0.1:{self.port}")
                connection.endheaders()
                first = connection.getresponse()
                self.assertEqual(first.status, 200)
                self.assertEqual(json.loads(first.read())["tracked_ads"], 1)
                self.assertIsNotNone(connection.sock)

                # Deliberately omit Host; the patched parent keeps the former
                # header object instead of creating a current-request one.
                connection.putrequest("GET", "/api/summary", skip_host=True)
                connection.endheaders()
                second = connection.getresponse()
                body = second.read()
                self.assertEqual(second.status, 421)
                self.assertNotIn(b"tracked_ads", body)
                self.assertNotIn(b"1234567890", body)
                self.assertEqual(len(handlers), 2)
                self.assertEqual(handlers[0], handlers[1])
        finally:
            connection.close()

    def test_localhost_alias_is_intentionally_not_numeric_authority(self) -> None:
        # The product launcher publishes http://127.0.0.1:<port>/;
        # using localhost would not match the dashboard Write Origin fence.
        for host in ("localhost", f"localhost:{self.port}"):
            with self.subTest(host=host):
                status, body = self.request(
                    "GET", "/api/summary", (host,),
                )
                self.assertEqual(status, 421)
                self.assertNotIn(b"1234567890", body)

    def test_host_guard_does_not_replace_write_origin_and_token_barriers(self) -> None:
        ui_token = "synthetic-dashboard-csrf-token-123"
        backend = DashboardWriteProxy(
            host="127.0.0.1",
            port=9,  # No backend: forbidden Origin must reject before connect.
            bearer_token="synthetic-backend-bearer-12345",
            ui_token=ui_token,
        )
        server = create_server(self.store, port=0, write_proxy=backend)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            host, port = server.server_address
            self.assertEqual(host, "127.0.0.1")
            connection = http.client.HTTPConnection(host, port, timeout=3)
            try:
                connection.putrequest(
                    "POST", "/api/write/ads/1234567890/pause", skip_host=True,
                )
                connection.putheader("Host", f"127.0.0.1:{port}")
                connection.putheader("Origin", "http://attacker.example")
                connection.putheader("X-Mark-Dashboard-Write", "1")
                connection.putheader("X-Mark-Dashboard-Token", ui_token)
                connection.putheader("Idempotency-Key", "synthetic-csrf-1")
                connection.putheader("Content-Length", "0")
                connection.endheaders()
                response = connection.getresponse()
                status, data = response.status, response.read()
            finally:
                connection.close()
            self.assertEqual(status, 403)
            self.assertEqual(
                json.loads(data), {
                    "error": "write_session_required",
                    "platform_retry_authorized": False,
                },
            )
            self.assertEqual(self.store.dashboard_pending_writes(), ())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def test_http_09_two_token_request_fails_closed_without_host(self) -> None:
        # Some Python versions parse legacy HTTP/0.9 into a different
        # headers representation; the Host guard must never crash or leak
        # local inventory when no Host header is present.
        for path in ("/api/summary", "/healthz"):
            with self.subTest(path=path):
                with socket.create_connection(
                    ("127.0.0.1", self.port), timeout=3,
                ) as stream:
                    stream.settimeout(3)
                    stream.sendall(f"GET {path}\r\n\r\n".encode("ascii"))
                    response = stream.recv(4096)
                self.assertNotIn(b"tracked_ads", response)
                self.assertNotIn(b'"status": "ok"', response)


if __name__ == "__main__":
    unittest.main()