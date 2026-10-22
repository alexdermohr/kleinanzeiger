"""Real installed-wheel acceptance: synthetic local mail -> SQLite -> loopback HTTP.

This module deliberately imports no mark_api implementation from the checkout.
Run with MARK_WHEEL_PATH=/absolute/path/to/mark_api-*.whl to exercise the
installed distribution in a separate, isolated virtual environment.
"""
from __future__ import annotations

import csv
import errno
import hashlib
import io
import json
import os
import re
import selectors
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing, contextmanager
from email import policy
from email.message import EmailMessage
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


def _require_loopback_only_network_namespace() -> None:
    """Fail closed unless the Linux kernel exposes only the loopback NIC.

    CI must invoke the entire test using a fresh network namespace with only
    'lo' enabled. Environment variables, missing API keys and proxy removal
    are not an outbound-network isolation boundary.
    """
    if sys.platform != "linux":
        raise AssertionError("installed offline wheel requires Linux network namespace")
    interfaces = {name for _, name in socket.if_nameindex()}
    if interfaces != {"lo"}:
        raise AssertionError(
            "offline wheel test must run in a loopback-only Linux network "
            f"namespace; visible interfaces: {sorted(interfaces)}"
        )

    # A root-created netns with plain runuser is insecure: the CI user may
    # sudo/nsenter into the host network namespace again. Admit ONLY one of:
    # (1) a user namespace mapping inside UID 0 to a NON-ROOT host UID; or
    # (2) an ordinary non-root process with no-new-privs and ZERO capability
    #     sets (including bounding set), enforced by the Linux kernel.
    uid_maps = Path("/proc/self/uid_map").read_text(encoding="ascii").splitlines()
    fields = uid_maps[0].split() if len(uid_maps) == 1 else []
    unprivileged_userns = (
        len(fields) == 3 and fields[0] == "0"
        and fields[1].isdigit() and int(fields[1]) > 0
        and fields[2] == "1"
    )
    status_values = {}
    for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            status_values[key] = value.strip()
    cap_names = ("CapEff", "CapPrm", "CapInh", "CapBnd", "CapAmb")
    unprivileged_no_new_privs = (
        os.geteuid() != 0
        and status_values.get("NoNewPrivs") == "1"
        and all(int(status_values.get(name, "-1"), 16) == 0 for name in cap_names)
    )
    if not (unprivileged_userns or unprivileged_no_new_privs):
        raise AssertionError(
            "offline acceptance requires unprivileged userns OR "
            "non-root no-new-privs with all capabilities dropped"
        )

    # Route lookup for a reserved documentation address must be denied by the
    # kernel, not merely time out or be refused by a remote host.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        status = probe.connect_ex(("192.0.2.1", 443))
    if status != errno.ENETUNREACH:
        raise AssertionError(f"outbound route unexpectedly available: {status}")


def _private_environment(home: Path) -> dict[str, str]:
    """No account credentials, proxy redirects or checkout import paths."""
    scrub_prefixes = ("OPENAI_", "KLEINANZEIGEN_", "MARK_", "AZURE_", "AWS_")
    environment = {
        k: v for k, v in os.environ.items()
        if not k.upper().startswith(scrub_prefixes)
        and k.upper() not in {
            "PYTHONPATH", "PYTHONHOME", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "FTP_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy",
            "ftp_proxy", "no_proxy",
        }
    }
    environment["HOME"] = str(home)
    environment["XDG_CONFIG_HOME"] = str(home / "config")
    environment["XDG_DATA_HOME"] = str(home / "data")
    environment["XDG_CACHE_HOME"] = str(home / "cache")
    environment["NO_PROXY"] = "127.0.0.1,localhost"
    return environment


def _notification(message_id: str, date: str) -> bytes:
    message = EmailMessage(policy=policy.default)
    message["From"] = "Kleinanzeigen <noreply@mail.kleinanzeigen.de>"
    message["To"] = "synthetic@example.invalid"
    message["Date"] = date
    message["Message-ID"] = f"<{message_id}@chat.kleinanzeigen.de>"
    message["X-Conversation-ID"] = "wheel:local:demo"
    message["X-Message-ID"] = message_id
    message.set_content(
        "Anfrage zu deiner Anzeige\n"
        "(Anzeigennummer: 1234567890)\n"
        "https://www.kleinanzeigen.de/m-nachrichten.html?"
        "conversationId=wheel:local:demo"
    )
    return message.as_bytes(policy=policy.default)


def _command(*args: str, cwd: Path, env: dict[str, str], timeout: int = 45):
    return subprocess.run(
        list(args), cwd=cwd, env=env, capture_output=True, text=True,
        timeout=timeout, check=False,
    )


@contextmanager
def _offline_server(
    python: Path, directory: Path, env: dict[str, str], database: Path,
    emails: tuple[Path, ...], *, initialize: bool = False,
    catalogs: tuple[Path, ...] = (),
):
    argv = [
        str(python), "-u", "-I", "-m", "mark_api", "--db", str(database),
        "--port", "0",
    ]
    if initialize:
        argv.append("--init-db")
    for email in emails:
        argv.extend(["--email", str(email)])
    for catalog in catalogs:
        argv.extend(["--catalog", str(catalog)])
    stderr_path = directory / "offline-stderr.log"
    with stderr_path.open("ab") as error_output:
        process = subprocess.Popen(
            argv, cwd=directory, env=env, stdout=subprocess.PIPE,
            stderr=error_output, text=True, bufsize=1,
        )
        address = None
        selector = selectors.DefaultSelector()
        try:
            assert process.stdout is not None
            fd = process.stdout.fileno()
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ)
            deadline = time.monotonic() + 12.0
            pending = bytearray()
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError(
                        "installed offline launcher exited before HTTP ready: "
                        f"code={process.returncode}"
                    )
                remaining = max(0.0, deadline - time.monotonic())
                if not selector.select(timeout=min(0.2, remaining)):
                    continue
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise AssertionError("installed offline launcher closed stdout")
                pending.extend(chunk)
                if len(pending) > 8192:
                    raise AssertionError("installed launcher emitted too much startup output")
                while b"\n" in pending:
                    raw, _, rest = pending.partition(b"\n")
                    pending = bytearray(rest)
                    line = raw.decode("utf-8", errors="strict").strip()
                    match = re.fullmatch(
                        r"Mark offline dashboard: http://127\.0\.0\.1:([0-9]+)/",
                        line,
                    )
                    if match:
                        port = int(match.group(1))
                        if not 0 < port < 65536:
                            raise AssertionError("offline launcher emitted invalid port")
                        address = f"http://127.0.0.1:{port}"
                        break
                if address is not None:
                    break
            if address is None:
                raise AssertionError("installed offline launcher never announced loopback")
            yield address
        finally:
            selector.close()
            if process.poll() is None:
                process.terminate()
            try:
                output, _ = process.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=4)
                raise AssertionError("installed offline launcher did not terminate")
            if process.returncode not in (0, -15, 143):
                raise AssertionError(
                    f"installed offline launcher abnormal shutdown: {process.returncode}; "
                    f"stdout={output[:250]}"
                )


def _request(address: str, path: str, *, method: str = "GET", data=None):
    opener = build_opener(ProxyHandler({}))
    request = Request(
        address + path, method=method, data=data,
        headers={"Accept": "application/json"},
    )
    try:
        with opener.open(request, timeout=4) as response:
            return response.status, response.headers, response.read()
    except HTTPError as exc:
        with exc:
            return exc.code, exc.headers, exc.read()


class InstalledOfflineWheelAcceptance(unittest.TestCase):
    def test_exact_installed_wheel_without_checkout_imports_or_platform_calls(self):
        wheel_setting = os.environ.get("MARK_WHEEL_PATH")
        self.assertTrue(wheel_setting, "MARK_WHEEL_PATH is required; never skip release acceptance")
        _require_loopback_only_network_namespace()
        wheel = Path(wheel_setting)
        self.assertTrue(wheel.is_absolute(), "wheel must be an absolute path")
        self.assertTrue(wheel.is_file(), "wheel must exist before acceptance")
        self.assertEqual(wheel.suffix, ".whl")
        wheel_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()

        with tempfile.TemporaryDirectory(prefix="mark-wheel-e2e-") as temporary:
            root = Path(temporary)
            env = _private_environment(root)
            venv = root / "venv"
            created = _command(
                sys.executable, "-m", "venv", str(venv), cwd=root, env=env,
                timeout=45,
            )
            self.assertEqual(created.returncode, 0, created.stderr[-1600:])
            python = venv / "bin" / "python"
            installed = _command(
                str(python), "-m", "pip", "install", "--no-index", "--no-deps",
                "--disable-pip-version-check", str(wheel), cwd=root, env=env,
                timeout=60,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr[-1600:])
            imported = _command(
                str(python), "-I", "-c",
                "import mark_api,sys;"
                "assert 'site-packages' in mark_api.__file__;"
                "assert not any(n.startswith(('mark_api.private_web','mark_api.monkrel')) "
                "for n in sys.modules);"
                "print(mark_api.__file__)",
                cwd=root, env=env,
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertIn(str(venv), imported.stdout)

            database = root / "mark.sqlite"
            missing = root / "not-created.sqlite"
            refused = _command(
                str(python), "-I", "-m", "mark_api", "--db", str(missing),
                cwd=root, env=env,
            )
            self.assertEqual(refused.returncode, 2)
            self.assertFalse(missing.exists(), "missing database was created without --init-db")

            # A local switch or a browser login is never an official,
            # account-bound Kleinanzeigen entitlement. Exercise the actual
            # installed legacy CLI before any database or CDP side effect.
            forbidden_db = root / "denied-platform" / "must-not-create.sqlite"
            pretend_entitlement = {
                **env,
                "MARK_PLATFORM_WRITES_ENABLED": "true",
                "MARK_PRIVATE_WEB_APPROVED": "true",
                "MARK_PROSELLERS_API_ENTITLEMENT_CONFIRMED": "true",
            }
            denied_launcher = _command(
                str(venv / "bin" / "mark-api-launch"),
                "--db", str(forbidden_db), "--init-db",
                "--cdp-port", "9222",
                "--email", str(root / "nonexistent-private-email.eml"),
                cwd=root, env=pretend_entitlement,
            )
            self.assertEqual(denied_launcher.returncode, 2, denied_launcher.stderr)
            self.assertIn("not authorized", denied_launcher.stderr)
            self.assertEqual(denied_launcher.stdout, "")
            self.assertFalse(forbidden_db.parent.exists())
            self.assertFalse(forbidden_db.exists())

            email_a = root / "synthetic-a.eml"
            email_b = root / "synthetic-b.eml"
            email_a.write_bytes(_notification("wheel-msg-1", "Thu, 24 Sep 2026 10:37:48 +0000"))
            email_b.write_bytes(_notification("wheel-msg-2", "Thu, 24 Sep 2026 10:38:48 +0000"))
            email_files = (email_a, email_b)
            catalog = root / "explicit-local-catalog.json"
            catalog.write_text(json.dumps({
                "schema": "mark-local-catalog-v1",
                "ads": [{
                    "ad_id": "1234567890",
                    "observed_at": "2026-10-22T02:01:00Z",
                    "title": "Locally supplied, unverified listing",
                    "views": 23,
                    "watch_count": 0,
                }],
            }), encoding="utf-8")

            with _offline_server(
                python, root, env, database, email_files,
                initialize=True, catalogs=(catalog,),
            ) as address:
                code, _, payload = _request(address, "/healthz")
                self.assertEqual((code, json.loads(payload)), (200, {"status": "ok"}))
                code, _, payload = _request(address, "/readyz")
                self.assertEqual((code, json.loads(payload)), (200, {"status": "ready"}))
                code, _, payload = _request(address, "/api/dashboard/config")
                self.assertEqual((code, json.loads(payload)), (200, {"write_ui_available": False}))
                code, _, payload = _request(address, "/")
                self.assertEqual(code, 200)
                self.assertIn(b"dashboard.js", payload)

                code, _, payload = _request(address, "/api/ads")
                self.assertEqual(code, 200)
                ads = json.loads(payload)
                self.assertEqual(len(ads), 1)
                self.assertEqual(ads[0]["ad_id"], "1234567890")
                self.assertEqual(ads[0]["views"], 23)
                self.assertEqual(ads[0]["lifecycle_state"], "unknown")
                self.assertFalse(ads[0]["present"], "unverified catalog cannot claim current owner")
                self.assertEqual(ads[0]["source"], "user-supplied-local-catalog")
                code, _, payload = _request(address, "/api/summary")
                self.assertEqual(code, 200)
                summary = json.loads(payload)
                self.assertEqual(summary["tracked_ads"], 1)
                self.assertEqual(summary["current_ads"], 0)
                self.assertEqual(summary["unknown_state_ads"], 1)
                code, _, payload = _request(address, "/api/analytics/ads?metric=views")
                self.assertEqual(code, 200)
                self.assertEqual(
                    [(row["ad_id"], row["value"]) for row in json.loads(payload)],
                    [("1234567890", 23)],
                )

                code, _, payload = _request(address, "/api/email-reactions")
                reactions = json.loads(payload)
                self.assertEqual(code, 200)
                self.assertEqual(len(reactions), 1)
                self.assertEqual(reactions[0]["ad_id"], "1234567890")
                self.assertEqual(reactions[0]["inbound_message_count"], 2)
                self.assertEqual(reactions[0]["conversation_count"], 1)

                metric = "email_inbound_message_count"
                code, _, payload = _request(address, f"/api/analytics/ads?metric={metric}")
                ranks = json.loads(payload)
                self.assertEqual(code, 200)
                self.assertEqual([(row["ad_id"], row["value"]) for row in ranks], [("1234567890", 2)])

                code, headers, payload = _request(address, f"/api/analytics/ads.csv?metric={metric}")
                self.assertEqual(code, 200)
                self.assertTrue(headers.get("Content-Type", "").startswith("text/csv"))
                self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
                rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline="")))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["ad_id"], "1234567890")
                self.assertEqual(rows[0]["value"], "2")

                for method, path, data, expected in (
                    ("POST", "/api/email-reactions", b"{}", 405),
                    ("POST", "/api/write/ads", b"{}", 405),
                    ("GET", "/api/write/delete", None, 404),
                    ("GET", "/api/dashboard/pending-writes", None, 404),
                    ("HEAD", "/healthz", None, 405),
                ):
                    with self.subTest(method=method, path=path):
                        status, _, _ = _request(address, path, method=method, data=data)
                        self.assertEqual(status, expected)

            classified = _command(
                str(venv / "bin" / "mark-api-classify"),
                "--db", str(database), "--ad-id", "1234567890",
                "--city", "Berlin", cwd=root, env=env,
            )
            self.assertEqual(classified.returncode, 0, classified.stderr)

            # Reimporting the same local files must not double-count messages.
            with _offline_server(
                python, root, env, database, email_files, catalogs=(catalog,),
            ) as address:
                code, _, payload = _request(address, "/api/email-reactions")
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(payload)[0]["inbound_message_count"], 2)
                code, _, payload = _request(address, "/api/ads")
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(payload)[0]["views"], 23)
                groups_path = (
                    "/api/analytics/groups.csv?"
                    "dimension=city&metric=email_inbound_message_count"
                )
                code, _, payload = _request(address, groups_path)
                self.assertEqual(code, 200)
                groups = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline="")))
                self.assertEqual(len(groups), 1)
                self.assertEqual(groups[0]["label"], "Berlin")
                self.assertEqual(groups[0]["metric_sum"], "2")
                code, _, payload = _request(address, "/api/dashboard/config")
                self.assertEqual((code, json.loads(payload)), (200, {"write_ui_available": False}))

            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM ad_snapshots").fetchone()[0], 1,
                    "repeated installed catalog start duplicated the observation",
                )
            malformed_catalog = root / "malformed-catalog.json"
            malformed_catalog.write_text(json.dumps({
                "schema": "mark-local-catalog-v1",
                "ads": [{"ad_id": "1234567890", "observed_at": "no-offset"}],
            }), encoding="utf-8")
            denied_catalog = _command(
                str(venv / "bin" / "mark-api-offline"),
                "--db", str(database), "--port", "0",
                "--catalog", str(malformed_catalog),
                cwd=root, env=env,
            )
            self.assertEqual(denied_catalog.returncode, 2)
            self.assertIn("local catalog import failed", denied_catalog.stderr)
            self.assertNotIn("no-offset", denied_catalog.stderr)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM ad_snapshots").fetchone()[0], 1,
                )

            # Optional generation is still a local draft-only capability. No
            # paid provider call may start in either mode without per-call consent.
            for mode in ("text", "image"):
                image_target = root / "should-not-create.png"
                request = [
                    str(venv / "bin" / "mark-api-generate"), mode,
                    "--prompt", "synthetic offline draft",
                ]
                if mode == "image":
                    request.extend(("--output", str(image_target)))
                denied = _command(*request, cwd=root, env=env)
                self.assertEqual(denied.returncode, 2, denied.stderr)
                self.assertIn("--acknowledge-provider-cost", denied.stderr)
                self.assertIn("https://developers.openai.com/api/docs/pricing", denied.stderr)
                self.assertEqual(denied.stdout, "")
                self.assertFalse(image_target.exists())

            # This is a create-only, same-user *offline* backup, NOT hostile
            # service-UID A6 recovery. Test installed CLI, receipt and manual
            # copy-to-new-path recovery; never overwrite the source or backup.
            backup = root / "verified-local-backup.sqlite"
            backup_bin = str(venv / "bin" / "mark-api-backup")
            backed = _command(
                backup_bin, "--db", str(database), "--backup", str(backup),
                cwd=root, env=env, timeout=90,
            )
            self.assertEqual(backed.returncode, 0, backed.stderr)
            backup_receipt = json.loads(backed.stdout)
            self.assertTrue(backup.is_file())
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o400)
            backup_sha256 = hashlib.sha256(backup.read_bytes()).hexdigest()
            self.assertEqual(backup_receipt["backup_sha256"], backup_sha256)
            self.assertEqual(backup_receipt["inbound_message_events"], 2)
            self.assertEqual(backup_receipt["pending_api_writes"], 0)
            self.assertEqual(backup_receipt["pending_dashboard_writes"], 0)
            with closing(sqlite3.connect(f"file:{backup}?mode=ro", uri=True)) as backup_db:
                self.assertEqual(backup_db.execute("PRAGMA quick_check(1)").fetchone()[0], "ok")
                self.assertEqual(
                    backup_db.execute("SELECT COUNT(*) FROM inbound_message_events").fetchone()[0],
                    2,
                )

            denied_overwrite = _command(
                backup_bin, "--db", str(database), "--backup", str(backup),
                cwd=root, env=env,
            )
            self.assertEqual(denied_overwrite.returncode, 2)
            self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), backup_sha256)

            restored = root / "restored-from-local-backup.sqlite"
            shutil.copyfile(backup, restored)
            os.chmod(restored, 0o600)
            with _offline_server(python, root, env, restored, ()) as address:
                code, _, payload = _request(address, "/api/ads")
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(payload)[0]["views"], 23)
                code, _, payload = _request(address, "/api/email-reactions")
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(payload)[0]["inbound_message_count"], 2)
                code, _, payload = _request(address, groups_path.replace(".csv", ""))
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(payload)[0]["label"], "Berlin")

            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("PRAGMA quick_check(1)").fetchone()[0], "ok")

        print(json.dumps({
            "result": "PASS", "scope": "installed-offline-wheel",
            "wheel_sha256": wheel_hash, "platform_access": False,
            "local_eml_events": 2, "local_catalog_snapshots": 1,
            "sqlite_quick_check": "ok",
            "loopback_http": True, "csv": True, "denied_writes": True,
            "restart_deduplication": True,
            "provider_consent_default_deny": True,
            "backup_scope": "create-only-offline-not-a6",
            "backup_sha256": backup_sha256,
            "offline_restore_checked": True,
            "network_isolation": "linux-non-escalating-loopback-netns",
        }, sort_keys=True))


if __name__ == "__main__":
    unittest.main()