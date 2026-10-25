"""Independent stateful synthetic media gallery over real loopback HTTP.

Exercises the production verifier and persisted platform-side image state, but
substitutes test-owned HTTP for the public HTTPS fetch. Does NOT claim a full
installed protected-CDP media publish, TLS, or A6 host cutover.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import struct
from threading import Thread
import tempfile
import unittest
from unittest.mock import patch

from mark_api.private_web_media import PrivateWebMediaSource
from mark_api.private_web_media_verify import (
    ProtectedMediaReadbackAuthority,
    PrivateWebPublicMediaPersistenceVerifier,
    _Fetched,
    _ImageSignature,
    _MediaHttpStatusError,
    _require_exact_readback_url,
)
from mark_api.results import ReadStatus


AD_ID = "1234567890"
LISTING = "https://www.kleinanzeigen.de/s-anzeige/" + AD_ID
GALLERY_IMAGE = (
    "https://img.kleinanzeigen.de/api/v1/prod-ads/images/"
    "aa/aa111111-1111-4111-8111-111111111111?rule=$_59.JPG"
)
VERIFIED_IMAGE = GALLERY_IMAGE.replace("$_59.JPG", "$_57.JPG")


class _Gallery(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _reply(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/publish":
            self._reply(404, "text/plain", b"absent")
            return
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        identity = self.headers.get("Idempotency-Key")
        if identity != "stateful-synthetic-media-1":
            self._reply(400, "text/plain", b"bad identity")
        elif self.server.stored is not None:
            self._reply(409, "text/plain", b"already published")
        else:
            self.server.stored = bytes(data)
            self.server.submits += 1
            self._reply(201, "text/plain", AD_ID.encode())

    def do_GET(self):
        if self.server.stored is None:
            self._reply(404, "text/plain", b"not yet persisted")
            return
        if self.path == "/listing":
            body = (
                '<html><head><link rel="canonical" href="' + LISTING + '">'
                '</head><body><div class="vip-image-gallery j-gallery-image">'
                '<script type="application/ld+json">'
                '{"@type":"ImageObject","contentUrl":"' + GALLERY_IMAGE + '"}'
                '</script></div></body></html>'
            ).encode()
            self._reply(200, "text/html", body)
        elif self.path == "/image":
            self.server.media_reads += 1
            self._reply(200, "image/jpeg", self.server.stored)
        else:
            self._reply(404, "text/plain", b"unknown")


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class StatefulReadbackTests(unittest.TestCase):
    def setUp(self):
        self.platform = ThreadingHTTPServer(("127.0.0.1", 0), _Gallery)
        self.platform.stored = None
        self.platform.submits = 0
        self.platform.media_reads = 0
        self.thread = Thread(target=self.platform.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.platform.server_close)
        self.addCleanup(self.platform.shutdown)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "image.jpg"
        self.source.write_bytes(b"expected-image-bytes")
        self.media = (PrivateWebMediaSource(str(self.source)),)
        self.authority = ProtectedMediaReadbackAuthority(
            witness_socket="/tmp/actor-bound-witness.sock",
            witness_uid=50043, actor_uid=50044,
        )
        # Separate real-UID and synthetic SO_PEERCRED tests cover admission.
        # This local HTTP test observes independent platform-side persistence,
        # not an OS-attested root/Actor/Witness topology.
        admission = patch(
            "mark_api.private_web_media_verify."
            "_require_private_web_public_media_transport_authority"
        )
        admission.start()
        self.addCleanup(admission.stop)

    def _request(self, method, path, body=b"", headers=None):
        with closing(HTTPConnection(
            "127.0.0.1", self.platform.server_port, timeout=3,
        )) as client:
            client.request(method, path, body=body, headers=headers or {})
            response = client.getresponse()
            return response.status, response.getheader("Content-Type"), response.read()

    def _verifier(self):
        clock = _Clock()
        def fetch(url, seconds, maximum):
            _require_exact_readback_url(url)
            if url == LISTING:
                path = "/listing"
            elif url == VERIFIED_IMAGE:
                path = "/image"
            else:
                raise AssertionError("readback outside exact listing/media")
            code, content_type, body = self._request("GET", path)
            if code != 200:
                raise _MediaHttpStatusError(code)
            if len(body) > maximum or seconds <= 0:
                raise AssertionError("synthetic read exceeded bounds")
            return _Fetched(url, content_type, body)

        def signature(data):
            if data == b"expected-image-bytes":
                value = 50
            elif data == b"unrelated-image-bytes":
                value = 220
            else:
                raise AssertionError("synthetic unexpected media")
            return _ImageSignature(
                width=128, height=128, normalized_rgb=bytes([value]) * 128,
            )

        return PrivateWebPublicMediaPersistenceVerifier(
            fetch=fetch, signature_loader=signature,
            clock=lambda: datetime(2026, 10, 24, tzinfo=timezone.utc),
            monotonic=clock.monotonic, sleep=clock.sleep,
            protected_authority=self.authority,
        )

    def test_absent_then_persisted_then_changed_media_from_separate_http_server(self):
        before = self._verifier().verify_media(
            AD_ID, self.media, timeout_seconds=0.6,
        )
        self.assertNotEqual(before.status, ReadStatus.SUCCESS_NONEMPTY)
        self.assertEqual(self.platform.submits, 0)
        status, _, returned = self._request(
            "POST", "/publish", self.source.read_bytes(),
            {"Idempotency-Key": "stateful-synthetic-media-1"},
        )
        self.assertEqual((status, returned), (201, AD_ID.encode()))
        # A distinct verifier instance has no in-memory state from publishing.
        after = self._verifier().verify_media(
            AD_ID, self.media, timeout_seconds=1.0,
        )
        self.assertEqual(after.status, ReadStatus.SUCCESS_NONEMPTY)
        self.assertEqual(after.value.ad_id, AD_ID)
        self.assertTrue(after.value.exact_match)
        self.assertEqual(self.platform.stored, self.source.read_bytes())
        self.assertEqual(self.platform.submits, 1)
        self.assertGreater(self.platform.media_reads, 0)
        # Corrupt platform-side bytes: an existing ad is not media proof.
        self.platform.stored = b"unrelated-image-bytes"
        mismatch = self._verifier().verify_media(
            AD_ID, self.media, timeout_seconds=0.6,
        )
        self.assertEqual(mismatch.status, ReadStatus.SUCCESS_NONEMPTY)
        self.assertFalse(mismatch.value.exact_match)
        self.assertEqual(self.platform.submits, 1)


if __name__ == "__main__":
    unittest.main()
