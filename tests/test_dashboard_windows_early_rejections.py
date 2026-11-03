"""Rejected local HTTP POSTs must reach the client, including Winsock.

All requests are synthetic loopback, including invalid credentials. This test
proves the early 403 is delayed until the finite body was received, without
authorizing any application or platform operation.
"""
from __future__ import annotations

from contextlib import closing
import json
import socket
from tempfile import TemporaryDirectory
from pathlib import Path
from threading import Thread
import unittest

from mark_api.dashboard import DashboardGenerationAccess, create_server
from mark_api.storage import SnapshotStore


class EarlyDeniedPostTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix="kleinanzeiger-windows-reject-")
        self.addCleanup(folder.cleanup)
        store = SnapshotStore(Path(folder.name) / "customer.sqlite", create_if_missing=True)
        self.access = DashboardGenerationAccess(
            ui_token="g" * 32,
            generator_factory=lambda: (_ for _ in ()).throw(
                AssertionError("denied generation must never start provider")),
            max_requests=1,
        )
        self.server = create_server(
            store, port=0, generation_access=self.access, local_session_token="w" * 32,
        )
        worker = Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _denied_in_parts(self, target: str, body: bytes, headers: dict[str, str]):
        port = self.server.server_address[1]
        request_headers = [
            f"POST {target} HTTP/1.1",
            f"Host: 127.0.0.1:{port}",
            "Connection: close",
            f"Content-Length: {len(body)}",
            *[f"{key}: {value}" for key, value in headers.items()],
        ]
        first, rest = body[:len(body) // 2], body[len(body) // 2:]
        with closing(socket.create_connection(("127.0.0.1", port), timeout=4)) as conn:
            conn.sendall(("\r\n".join(request_headers) + "\r\n\r\n").encode("ascii") + first)
            conn.settimeout(0.3)
            with self.assertRaises(socket.timeout, msg="server answered before consuming rejected body"):
                conn.recv(1)
            conn.settimeout(4)
            conn.sendall(rest)
            raw = bytearray()
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                raw.extend(data)
        status, _, payload = bytes(raw).partition(b"\r\n")
        self.assertIn(b" 403 ", status)
        self.assertIn(b"Content-Length:", raw)
        self.assertIn(b"application/json", raw)
        self.assertTrue(payload)

    def test_invalid_generation_token_drains_before_403_without_provider(self):
        body = json.dumps({
            "mode": "text", "prompt": "Nur ein synthetisches Testobjekt",
            "acknowledge_provider_cost": True,
        }).encode("utf-8")
        self._denied_in_parts("/api/dashboard/generate", body, {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{self.server.server_address[1]}",
            "X-Mark-Generation": "1", "X-Mark-Generation-Token": "wrong",
        })

    def test_missing_local_write_token_drains_before_403_without_mutation(self):
        body = json.dumps({
            "action": "save", "ad_id": "12345",
            "due_date": "2026-11-03", "note": "synthetisch",
            "expected_revision": 0, "request_key": "a" * 64,
        }).encode("utf-8")
        self._denied_in_parts("/api/portfolio/followups/mutate", body, {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{self.server.server_address[1]}",
        })


if __name__ == "__main__":
    unittest.main()
