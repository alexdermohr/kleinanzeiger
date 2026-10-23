"""Private A6 frontend config and fail-closed admission regression tests."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

from mark_api.dashboard import DashboardGenerationAccess
from mark_api.protected_frontend import _read_private_config, main, create_protected_frontend_server
from mark_api.storage_owner import StorageOwnerError

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

    def test_real_pathlib_socket_reaches_verified_owner_handshake(self):
        # PosixPath is the concrete type returned by Path(...). The factory
        # must not deny it before the kernel/owner verification can run.
        with patch(
            "mark_api.protected_frontend.CompletionDashboardClient",
            side_effect=StorageOwnerError("synthetic absent owner"),
        ) as authenticated:
            with self.assertRaises(StorageOwnerError):
                create_protected_frontend_server(
                    owner_socket=Path(_CONFIG["owner_socket"]),
                    owner_uid=_CONFIG["owner_uid"],
                    principal=_CONFIG["principal"],
                    owner_port=_CONFIG["owner_port"],
                    api_token=_CONFIG["api_token"],
                    ui_token=_CONFIG["ui_token"],
                    dashboard_port=0,
                )
            authenticated.assert_called_once_with(
                Path(_CONFIG["owner_socket"]),
                expected_owner_uid=_CONFIG["owner_uid"],
                expected_principal=_CONFIG["principal"],
            )

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
        self.assertIn("unavailable (owner unavailable)", buf.getvalue())
        self.assertNotIn(_CONFIG["api_token"], buf.getvalue())
        self.assertNotIn(_CONFIG["ui_token"], buf.getvalue())


    def test_optional_generation_token_is_strict_and_backward_compatible(self):
        token = "private-generation-token-1234567890"
        cfg = {**_CONFIG, "generation_token": token}
        self.assertEqual(
            _read_private_config(self._pipe((json.dumps(cfg) + "\n").encode())),
            cfg,
        )
        for invalid in (None, False, 1, "", "x" * 15, "x" * 513,
                        "line\nfeed" * 3, "unicode-ü-token-123456789"):
            with self.subTest(token=repr(invalid)[:20]), self.assertRaises(ValueError):
                _read_private_config(self._pipe((
                    json.dumps({**cfg, "generation_token": invalid}) + "\n"
                ).encode()))

    def test_protected_generation_uses_existing_dashboard_gate(self):
        token = "synthetic-generation-session-987654321"
        access = DashboardGenerationAccess(ui_token=token)
        stub_server = object()
        with patch("mark_api.protected_frontend.CompletionDashboardClient") as owner, patch(
            "mark_api.protected_frontend.create_server", return_value=stub_server
        ) as create:
            result = create_protected_frontend_server(
                owner_socket=Path(_CONFIG["owner_socket"]),
                owner_uid=_CONFIG["owner_uid"], principal=_CONFIG["principal"],
                owner_port=_CONFIG["owner_port"], api_token=_CONFIG["api_token"],
                ui_token=_CONFIG["ui_token"], dashboard_port=0,
                generation_access=access,
            )
        self.assertIs(result, stub_server)
        owner.assert_called_once()
        self.assertIs(create.call_args.kwargs["generation_access"], access)
        proxy = create.call_args.kwargs["write_proxy"]
        self.assertTrue(proxy.owner_reconciliation_enabled)
        self.assertFalse(proxy.local_reconciliation_enabled)

    def test_generation_without_explicit_credential_does_not_start_owner(self):
        cfg = {**_CONFIG, "generation_token": "safe-generation-token-1234567890"}
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch(
            "mark_api.protected_frontend.create_protected_frontend_server"
        ) as owner, redirect_stderr(io.StringIO()) as err:
            result = main(["--config-fd", str(self._pipe(
                (json.dumps(cfg) + "\n").encode()
            ))])
        self.assertEqual(result, 2)
        self.assertIn("generation credential missing", err.getvalue())
        self.assertNotIn(cfg["generation_token"], err.getvalue())
        owner.assert_not_called()

    def test_generation_explicit_opt_in_wires_token_without_provider_call(self):
        cfg = {**_CONFIG, "generation_token": "safe-generation-token-1234567890"}
        sdk = ModuleType("openai")
        sdk.OpenAI = MagicMock()
        server = MagicMock(server_port=18875)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-not-used"}), patch.dict(
            "sys.modules", {"openai": sdk}
        ), patch(
            "mark_api.protected_frontend.create_protected_frontend_server",
            return_value=server,
        ) as compose, redirect_stdout(io.StringIO()) as stdout:
            result = main(["--config-fd", str(self._pipe(
                (json.dumps(cfg) + "\n").encode()
            ))])
        self.assertEqual(result, 0)
        self.assertIsInstance(compose.call_args.kwargs["generation_access"], DashboardGenerationAccess)
        self.assertEqual(compose.call_args.kwargs["generation_access"].ui_token, cfg["generation_token"])
        self.assertNotIn(cfg["generation_token"], stdout.getvalue())
        self.assertNotIn(cfg["api_token"], stdout.getvalue())
        sdk.OpenAI.assert_not_called()
        server.serve_forever.assert_called_once()
        server.server_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
