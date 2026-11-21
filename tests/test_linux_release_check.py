"""Offline tests for official release checking; never contact GitHub."""
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from kleinanzeiger import linux_release_check as mod


class ReleaseCheckTests(unittest.TestCase):
    def test_numeric_comparison_and_exact_stable_versions(self):
        self.assertTrue(mod.newer("1.7.1", "1.10.0"))
        self.assertFalse(mod.newer("1.10.0", "1.7.1"))
        self.assertFalse(mod.newer("1.7.1", "1.7.1"))
        for invalid in ("v1.8.0", "1.8.0-rc1", "1.8.0/evil", "1.8", None):
            with self.subTest(invalid=invalid):
                self.assertFalse(mod.newer("1.7.1", invalid))
                with self.assertRaises(ValueError):
                    mod.release_url(invalid)
        self.assertEqual(mod.release_url("1.8.0"),
                         "https://github.com/alexdermohr/kleinanzeiger/releases/tag/v1.8.0")

    def test_public_latest_redirect_parses_official_tag_only(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.geturl.return_value = mod.release_url("1.8.0")
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(mod.ssl, "create_default_context") as tls, patch.object(
            mod, "build_opener", return_value=opener
        ) as create:
            tls.return_value.get_ca_certs.return_value = ["ca"]
            self.assertEqual(mod.latest_release(), "1.8.0")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 7)
        self.assertEqual(opener.open.call_args.args[0].full_url, mod.LATEST_URL)
        self.assertNotIn("api.github.com", mod.LATEST_URL)
        self.assertTrue(any(isinstance(x, mod._OfficialReleaseRedirect)
                            for x in create.call_args.args))

    def test_external_unstable_malformed_or_failed_response_is_unavailable(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(mod.ssl, "create_default_context") as tls, patch.object(
            mod, "build_opener", return_value=opener
        ):
            tls.return_value.get_ca_certs.return_value = ["ca"]
            for url in (
                "https://evil.example/releases/tag/v1.8.0",
                "http://github.com/alexdermohr/kleinanzeiger/releases/tag/v1.8.0",
                "https://github.com/other/repo/releases/tag/v1.8.0",
                mod.release_url("1.8.0") + "?evil=1",
                "https://github.com/alexdermohr/kleinanzeiger/releases/tag/v1.8.0-rc1",
            ):
                with self.subTest(url=url):
                    response.geturl.return_value = url
                    self.assertIsNone(mod.latest_release())
            response.status = 500
            self.assertIsNone(mod.latest_release())
            opener.open.side_effect = URLError("offline")
            self.assertIsNone(mod.latest_release())

    def test_redirect_handler_refuses_different_host(self):
        with self.assertRaises(ValueError):
            mod._OfficialReleaseRedirect().redirect_request(
                None, None, 302, "", {}, "https://other.example/releases/tag/v1.8.0"
            )


if __name__ == "__main__":
    unittest.main()
