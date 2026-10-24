"""Authoritative media readback is reachable only with a live independent Witness.

Synthetic response fixtures never contact Kleinanzeigen, the provider, or an
account. The installed multi-UID test remains the authority for real OS peers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from mark_api.private_web_media import PrivateWebMediaSource
from mark_api.private_web_media_verify import (
    PrivateWebPublicMediaPersistenceVerifier, ProtectedMediaReadbackAuthority,
    _Fetched, _ImageSignature, _default_fetch, _require_exact_readback_url,
    _MediaParseError,
)
from mark_api.results import ReadStatus


AD_ID = "1234567890"
IMAGE = (
    "https://img.kleinanzeigen.de/api/v1/prod-ads/images/"
    "aa/aa111111-1111-4111-8111-111111111111?rule=$_59.JPG"
)
READBACK_IMAGE = IMAGE.replace("$_59.JPG", "$_57.JPG")
LISTING = "https://www.kleinanzeigen.de/s-anzeige/" + AD_ID


class ProtectedReadbackAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.authority = ProtectedMediaReadbackAuthority(
            witness_socket="/tmp/synthetic-independent-witness.sock",
            witness_uid=50043, actor_uid=50044,
        )

    def _peer(self, uid: int):
        socket_patch = patch("mark_api.private_web_media_verify.socket.socket")
        mocked = socket_patch.start()
        self.addCleanup(socket_patch.stop)
        peer = mocked.return_value.__enter__.return_value
        peer.getsockopt.return_value = struct.pack("3i", 111, uid, uid)
        return peer

    def _verifier(self, source: Path, fetch):
        good = _ImageSignature(
            width=100, height=100, normalized_rgb=bytes([40]) * 64,
        )
        wrong = _ImageSignature(
            width=100, height=100, normalized_rgb=bytes([200]) * 64,
        )
        def signature(raw):
            return wrong if raw == b"remote-unrelated" else good
        return PrivateWebPublicMediaPersistenceVerifier(
            fetch=fetch, signature_loader=signature,
            clock=lambda: datetime(2026, 10, 24, tzinfo=timezone.utc),
            monotonic=lambda: 0.0, sleep=lambda _seconds: None,
            protected_authority=self.authority,
        )

    def test_verified_witness_peer_confirms_only_matching_gallery_media(self):
        self._peer(50043)
        fetched = []
        def fetch(url, timeout, maximum):
            fetched.append(url)
            self.assertGreater(timeout, 0)
            self.assertGreater(maximum, 0)
            if url == LISTING:
                return _Fetched(url, "text/html", (
                    '<html><head><link rel="canonical" href="'
                    + LISTING + '"></head><body><div class="vip-image-gallery j-gallery-image">'
                    '<script type="application/ld+json">'
                    '{"@type":"ImageObject","contentUrl":"' + IMAGE + '"}'
                    '</script></div></body></html>'
                ).encode())
            if url == READBACK_IMAGE:
                return _Fetched(url, "image/jpeg", b"remote-matching")
            raise AssertionError("untrusted synthetic destination")
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp, "expected.jpg")
            file.write_bytes(b"local-matching")
            verifier = self._verifier(file, fetch)
            with patch(
                "mark_api.private_web_media_verify.os.geteuid",
                return_value=50044,
            ):
                result = verifier.verify_media(
                    AD_ID, (PrivateWebMediaSource(str(file)),),
                    timeout_seconds=2.0,
                )
        self.assertEqual(result.status, ReadStatus.SUCCESS_NONEMPTY)
        self.assertTrue(result.value.exact_match)
        self.assertEqual(fetched, [LISTING, READBACK_IMAGE])

    def test_wrong_or_missing_witness_never_fetches_or_reports_success(self):
        peer = self._peer(50045)
        fetch = Mock(side_effect=AssertionError("untrusted readback invoked"))
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp, "expected.jpg")
            file.write_bytes(b"local-matching")
            verifier = self._verifier(file, fetch)
            with patch(
                "mark_api.private_web_media_verify.os.geteuid",
                return_value=50044,
            ):
                result = verifier.verify_media(
                    AD_ID, (PrivateWebMediaSource(str(file)),),
                    timeout_seconds=2.0,
                )
                self.assertEqual(result.error, "private_web_media_not_authorized")
                peer.connect.side_effect = FileNotFoundError()
                again = verifier.verify_media(
                    AD_ID, (PrivateWebMediaSource(str(file)),),
                    timeout_seconds=2.0,
                )
        self.assertEqual(result.status, ReadStatus.TRANSPORT_ERROR)
        self.assertEqual(again.status, ReadStatus.TRANSPORT_ERROR)
        fetch.assert_not_called()

    def test_trusted_witness_cannot_authorize_arbitrary_network_origin(self):
        self._peer(50043)
        for url in (
            "http://www.kleinanzeigen.de/s-anzeige/1234567890",
            "https://evil.invalid/s-anzeige/1234567890",
            "https://www.kleinanzeigen.de:444/s-anzeige/1234567890",
            "https://www.kleinanzeigen.de/s-anzeige/1234567890?redirect=1",
            "https://www.kleinanzeigen.de/s-anzeige/../1234567890",
            "https://img.kleinanzeigen.de/api/v1/prod-ads/images/aa/"
            "aa111111-1111-4111-8111-111111111111?rule=$_2.AUTO",
        ):
            with self.subTest(url=url), self.assertRaises(_MediaParseError):
                _require_exact_readback_url(url)
        with patch(
            "mark_api.private_web_media_verify.os.geteuid",
            return_value=50044,
        ), patch(
            "mark_api.private_web_media_verify.build_opener",
            side_effect=AssertionError("must not open untrusted URL"),
        ) as opener:
            with self.assertRaises(_MediaParseError):
                _default_fetch(
                    "https://attacker.invalid/payload", 1, 100,
                    protected_authority=self.authority,
                )
            opener.assert_not_called()

    def test_default_verifier_still_denies_before_injected_fetch(self):
        fetch = Mock(side_effect=AssertionError("must remain fail-closed"))
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp, "expected.jpg")
            file.write_bytes(b"local-matching")
            verifier = PrivateWebPublicMediaPersistenceVerifier(
                fetch=fetch, signature_loader=lambda _: None,
            )
            result = verifier.verify_media(
                AD_ID, (PrivateWebMediaSource(str(file)),),
                timeout_seconds=2.0,
            )
        self.assertEqual(result.error, "private_web_media_not_authorized")
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
