"""Private A6 frontend config and fail-closed admission regression tests."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr

from mark_api.protected_frontend import _read_private_config, main

_CONFIG = {
    "owner_socket": "/tmp/no-owner-on-this-test.sock", "owner_uid": os.geteuid()+100,
    "principal": "synthetic-owner", "owner_port": 18876,
    "api_token": "private-api-token-safe", "ui_token": "private-ui-token-safe",
    "dashboard_port": 0,
}


class ProtectedFrontendTests(unittest.TestCase):
    def _pipe(self, data):
        r, w = os.pipe()
        try:
            os.write(w, data)
        finally:
            os.close(w)
        self.addCleanup(os.close, r)
        return r

    def test_valid_strict_configuration_from_inherited_pipe(self):
        cfg = dict(_CONFIG)
        self.assertEqual(_read_private_config(
            self._pipe((json.dumps(cfg)+"\n").encode())), cfg)

    def test_malformed_duplicate_unsafe_and_oversized_inputs_deny(self):
        cases = [
            b'{"owner_uid":NaN}\n', b'{"owner_uid":1,"owner_uid":2}\n',
            b"{}\n{}\n", b"{}\n"+b"x"*8192, b"{}",
            (json.dumps({**_CONFIG, "sql": "DELETE"})+"\n").encode(),
            (json.dumps({**_CONFIG, "owner_uid": True})+"\n").encode(),
            (json.dumps({**_CONFIG, "owner_socket": "relative"})+"\n").encode(),
        ]
        for raw in cases:
            with self.subTest(data=raw[:35]), self.assertRaises(ValueError):
                _read_private_config(self._pipe(raw))

    def test_public_config_file_denied(self):
        with tempfile.TemporaryFile() as f:
            f.write((json.dumps(_CONFIG)+"\n").encode())
            f.flush()
            os.fchmod(f.fileno(), 0o644)
            f.seek(0)
            with self.assertRaises(ValueError):
                _read_private_config(f.fileno())

    def test_missing_attested_owner_fails_without_exposing_bearers(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            result = main(["--config-fd", str(self._pipe(
                (json.dumps(_CONFIG)+"\n").encode()))])
        self.assertEqual(result, 2)
        self.assertIn("unavailable", buf.getvalue())
        self.assertNotIn(_CONFIG["api_token"], buf.getvalue())
        self.assertNotIn(_CONFIG["ui_token"], buf.getvalue())


if __name__ == "__main__":
    unittest.main()
