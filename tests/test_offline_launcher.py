from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from email import policy
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import mark_api.offline_launcher as offline
from mark_api.dashboard import DashboardGenerationAccess, create_server as real_create_server
from mark_api.storage import SnapshotStore


def notification(*, sender: str = "Kleinanzeigen <noreply@mail.kleinanzeigen.de>") -> bytes:
    message = EmailMessage(policy=policy.default)
    message["From"] = sender
    message["To"] = "owner@example.invalid"
    message["Date"] = "Thu, 24 Sep 2026 10:37:48 +0000"
    message["Message-ID"] = "<offline-message-a@chat.kleinanzeigen.de>"
    message["X-Conversation-ID"] = "conv-a:thread:one"
    message["X-Message-ID"] = "offline-message-a"
    message.set_content(
        "Anfrage zu deiner Anzeige\n(Anzeigennummer: 1234567890)\n"
        "Um auf diese Nachricht zu antworten: "
        "https://www.kleinanzeigen.de/m-nachrichten.html?"
        "conversationId=conv-a:thread:one"
    )
    return message.as_bytes(policy=policy.default)


class ImmediateServer:
    server_address = ("127.0.0.1", 17000)

    def __init__(self):
        self.served = 0
        self.closed = 0

    def serve_forever(self):
        self.served += 1

    def server_close(self):
        self.closed += 1


class OfflineLauncherTests(unittest.TestCase):
    def _email(self, root: Path, *, sender: str = "Kleinanzeigen <noreply@mail.kleinanzeigen.de>") -> Path:
        path = root / "notification.eml"
        path.write_bytes(notification(sender=sender))
        return path

    def test_private_parent_stop_closes_offline_dashboard_without_platform_runtime(self):
        class StoppableServer:
            server_address = ("127.0.0.1", 17000)

            def __init__(self):
                self.event = threading.Event()
                self.served = 0
                self.closed = 0
                self.shutdown_count = 0

            def serve_forever(self):
                self.served += 1
                if not self.event.wait(timeout=5):
                    raise AssertionError("Private stdin STOP failed to release offline server")

            def shutdown(self):
                self.shutdown_count += 1
                self.event.set()

            def server_close(self):
                self.closed += 1

        for lines in ("ignored\\nSTOP\\n", "unexpected\\n"):
            with self.subTest(lines=lines), tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "customer.sqlite"
                server = StoppableServer()
                output = io.StringIO()
                with (
                    patch.object(offline, "create_server", return_value=server),
                    patch.object(offline.sys, "stdin", io.StringIO(lines.replace("\\n", "\n"))),
                    redirect_stdout(output),
                ):
                    status = offline.main(["--db", str(db), "--init-db",
                                           "--port", "0", "--stop-on-stdin"])
                self.assertEqual(status, 0)
                self.assertEqual((server.served, server.closed, server.shutdown_count),
                                 (1, 1, 1))
                self.assertIn("Platform sync and writes: disabled", output.getvalue())
                self.assertNotIn("Kleinanzeiger write API", output.getvalue())

    def test_missing_database_fails_without_creation_or_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing.sqlite"
            with (
                patch.object(offline, "create_server", side_effect=AssertionError("server must not start")),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as exc,
            ):
                offline.main(["--db", str(db)])
            self.assertEqual(exc.exception.code, 2)
            self.assertFalse(db.exists())

    def test_bad_port_rejected_before_database_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "new.sqlite"
            with (
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as exc,
            ):
                offline.main(["--db", str(db), "--init-db", "--port", "65536"])
            self.assertEqual(exc.exception.code, 2)
            self.assertFalse(db.exists())

    def test_invalid_email_aborts_before_dashboard_bind_and_keeps_events_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mark.sqlite"
            store = SnapshotStore(db)
            email = self._email(root, sender="Other <noreply@example.invalid>")
            with (
                patch.object(offline, "create_server", side_effect=AssertionError("server must not start")),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as exc,
            ):
                offline.main(["--db", str(db), "--email", str(email)])
            self.assertEqual(exc.exception.code, 2)
            self.assertEqual(store.inbound_message_ad_ids(), ())

    def test_idempotent_import_and_no_write_proxy_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mark.sqlite"
            email = self._email(root)
            servers = []

            def factory(store, *, port, analytics_contract):
                self.assertEqual(port, 0)
                self.assertIsNone(analytics_contract.reaction_metric)
                self.assertIsNone(analytics_contract.objective_metric)
                server = ImmediateServer()
                servers.append(server)
                return server

            with patch.object(offline, "create_server", side_effect=factory):
                outputs = []
                for i in range(2):
                    out = io.StringIO()
                    with redirect_stdout(out):
                        code = offline.main(
                            ["--db", str(db)]
                            + (["--init-db"] if i == 0 else [])
                            + ["--port", "0", "--email", str(email)]
                        )
                    self.assertEqual(code, 0)
                    outputs.append(out.getvalue())

            self.assertIn("1 new event(s)", outputs[0])
            self.assertIn("0 new event(s)", outputs[1])
            self.assertIn("Platform sync and writes: disabled", outputs[0])
            self.assertTrue(all(server.served == 1 and server.closed == 1 for server in servers))
            self.assertEqual(SnapshotStore(db, create_if_missing=False).inbound_message_ad_ids(), ("1234567890",))

    def test_real_loopback_http_is_get_only_and_contains_local_email_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mark.sqlite"
            email = self._email(root)
            started = threading.Event()
            captured = {}
            output = io.StringIO()

            def factory(store, *, port, analytics_contract):
                server = real_create_server(store, port=port, analytics_contract=analytics_contract)
                captured["server"] = server
                started.set()
                return server

            def target():
                with patch.object(offline, "create_server", side_effect=factory):
                    with redirect_stdout(output):
                        captured["exit_code"] = offline.main(
                            ["--db", str(db), "--init-db", "--port", "0", "--email", str(email)]
                        )

            worker = threading.Thread(target=target, daemon=True)
            worker.start()
            self.assertTrue(started.wait(6), "read-only server was not created")
            server = captured["server"]
            host, port = server.server_address
            self.assertEqual(host, "127.0.0.1")
            opener = build_opener(ProxyHandler({}))
            base = f"http://127.0.0.1:{port}"
            try:
                def get(path):
                    with opener.open(Request(base + path, method="GET"), timeout=5) as response:
                        return response.status, json.loads(response.read())

                self.assertEqual(get("/healthz"), (200, {"status": "ok"}))
                status, reaction = get("/api/email-reactions")
                self.assertEqual(status, 200)
                self.assertEqual(len(reaction), 1)
                self.assertEqual(reaction[0]["ad_id"], "1234567890")
                self.assertEqual(reaction[0]["inbound_message_count"], 1)
                status, ranking = get(
                    "/api/analytics/ads?metric=email_inbound_message_count"
                )
                self.assertEqual(status, 200)
                self.assertEqual(ranking[0]["value"], 1)
                with self.assertRaises(HTTPError) as post_exc:
                    opener.open(
                        Request(base + "/api/email-reactions", data=b"{}", method="POST"),
                        timeout=5,
                    )
                self.assertEqual(post_exc.exception.code, 405)
                post_exc.exception.close()
                with self.assertRaises(HTTPError) as write_exc:
                    opener.open(Request(base + "/api/write/delete", method="GET"), timeout=5)
                self.assertEqual(write_exc.exception.code, 404)
                write_exc.exception.close()
            finally:
                server.shutdown()
                worker.join(timeout=6)
            self.assertFalse(worker.is_alive())
            self.assertEqual(captured.get("exit_code"), 0)
            self.assertIn(f"Kleinanzeiger offline dashboard: http://127.0.0.1:{port}/", output.getvalue())

    def test_module_import_does_not_load_platform_drivers(self):
        repo = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo / "src")
        child = subprocess.run(
            [sys.executable, "-c",
             "import sys; import mark_api.offline_launcher; "
             "bad=[m for m in sys.modules if m.startswith(('mark_api.private_web', "
             "'mark_api.adapters', 'mark_api.monkrel'))]; "
             "print(bad); raise SystemExit(bool(bad))"],
            cwd=repo, env=env, capture_output=True, text=True,
            timeout=10, check=False,
        )
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertEqual(child.stdout.strip(), "[]")


    def test_generation_opt_in_requires_key_before_database_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "new.sqlite"
            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": ""}),
                patch.object(offline, "create_server") as factory,
                redirect_stderr(io.StringIO()) as error,
                self.assertRaises(SystemExit) as exc,
            ):
                offline.main(["--db", str(db), "--init-db", "--enable-generation"])
            self.assertEqual(exc.exception.code, 2)
            self.assertFalse(db.exists())
            factory.assert_not_called()
            self.assertIn("OPENAI_API_KEY", error.getvalue())

    def test_generation_opt_in_requires_sdk_before_database_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "new.sqlite"
            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "never-log-this"}),
                patch.dict(sys.modules, {"openai": None}),
                patch.object(offline, "create_server") as factory,
                redirect_stderr(io.StringIO()) as error,
                self.assertRaises(SystemExit) as exc,
            ):
                offline.main(["--db", str(db), "--init-db", "--enable-generation"])
            self.assertEqual(exc.exception.code, 2)
            self.assertFalse(db.exists())
            factory.assert_not_called()
            self.assertIn("kleinanzeiger[generation]", error.getvalue())
            self.assertNotIn("never-log-this", error.getvalue())

    def test_generation_opt_in_reuses_offline_dashboard_without_write_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "new.sqlite"
            captured = {}

            def factory(store, *, port, analytics_contract, generation_access):
                self.assertEqual(port, 0)
                self.assertIsNone(analytics_contract.objective_metric)
                self.assertIsInstance(generation_access, DashboardGenerationAccess)
                captured["access"] = generation_access
                return ImmediateServer()

            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-key"}),
                patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=object)}),
                patch.object(offline, "create_server", side_effect=factory),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(offline.main([
                    "--db", str(db), "--init-db", "--port", "0",
                    "--enable-generation",
                ]), 0)
            self.assertTrue(db.exists())
            text = output.getvalue()
            self.assertIn(
                "#generation_token=" + captured["access"].ui_token, text,
            )
            self.assertIn("Platform sync and writes: disabled", text)
            self.assertNotIn("synthetic-key", text)

    def test_generation_off_by_default_in_real_offline_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mark.sqlite"
            store = SnapshotStore(db)
            server = real_create_server(store, port=0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            host, port = server.server_address
            opener = build_opener(ProxyHandler({}))
            try:
                with opener.open(
                    Request(f"http://{host}:{port}/api/dashboard/config"), timeout=5
                ) as response:
                    self.assertEqual(response.status, 200)
                    payload = json.loads(response.read())
                    self.assertFalse(payload["write_ui_available"])
                    self.assertNotIn("generation_ui_available", payload)
                with self.assertRaises(HTTPError) as error:
                    opener.open(
                        Request(
                            f"http://{host}:{port}/api/dashboard/generate",
                            data=b"{}",
                            method="POST",
                        ),
                        timeout=5,
                    )
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=6)
            self.assertFalse(worker.is_alive())


    def test_local_catalog_startup_is_idempotent_and_default_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mark.sqlite"
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({
                "schema": "mark-local-catalog-v1",
                "ads": [{
                    "ad_id": "1234567890",
                    "observed_at": "2026-10-22T02:00:00+00:00",
                    "title": "Locally supplied title",
                    "views": 21,
                }],
            }), encoding="utf-8")
            outputs = []
            for run in range(2):
                output = io.StringIO()
                with (
                    patch.object(offline, "create_server", return_value=ImmediateServer()),
                    redirect_stdout(output),
                ):
                    self.assertEqual(offline.main([
                        "--db", str(db), "--port", "0", "--catalog", str(catalog),
                        *(["--init-db"] if run == 0 else []),
                    ]), 0)
                outputs.append(output.getvalue())
            self.assertIn("1 new observation(s)", outputs[0])
            self.assertIn("0 new observation(s)", outputs[1])
            self.assertIn("unverified", outputs[0])
            store = SnapshotStore(db, create_if_missing=False)
            self.assertEqual(len(store.ad_history("1234567890")), 1)
            self.assertEqual(store.latest_ad_snapshot("1234567890").views, 21)
            self.assertEqual(store.sync_status()["state"], "never_attempted")

    def test_invalid_local_catalog_blocks_dashboard_with_sanitized_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "mark.sqlite"
            store = SnapshotStore(db)
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({
                "schema": "mark-local-catalog-v1",
                "ads": [{
                    "ad_id": "1234567890",
                    "observed_at": "not-a-date",
                }],
            }), encoding="utf-8")
            error = io.StringIO()
            with (
                patch.object(offline, "create_server",
                             side_effect=AssertionError("server must not start")),
                redirect_stderr(error),
                self.assertRaises(SystemExit) as raised,
            ):
                offline.main(["--db", str(db), "--catalog", str(catalog)])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("local catalog import failed", error.getvalue())
            self.assertNotIn("not-a-date", error.getvalue())
            self.assertEqual(store.tracked_ad_ids(), ())

if __name__ == "__main__":
    unittest.main()