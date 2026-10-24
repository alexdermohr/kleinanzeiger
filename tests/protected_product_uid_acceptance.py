"""Root-only installed protected product start/restart in disposable PID/netns.

The live installed role modules are exercised. No real CDP, account, provider,
platform mutation or host cutover is performed. Failures never mint an
operator-recovery clearance or mark old customer databases as safe.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection, HTTPSConnection
import json
import os
from pathlib import Path
import select
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
from time import monotonic, sleep
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import mark_api

UIDS = {"frontend":50042, "witness":50043, "actor":50044,
        "ingress":50045, "owner":50046}
GENERATOR_UID = 50047


def check(condition, reason):
    if not condition:
        raise AssertionError(reason)


def child_roles(supervisor_pid, *, include_generation=False):
    roles = {}
    expected_roles = dict(UIDS)
    if include_generation:
        expected_roles["generation"] = GENERATOR_UID
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal():
            continue
        try:
            text = (path / "status").read_text()
        except FileNotFoundError:
            continue
        fields = {}
        for line in text.splitlines():
            if line.startswith(("PPid:", "Uid:")):
                fields[line.split(":",1)[0]] = line.split(":",1)[1].strip().split()
        if fields.get("PPid") != [str(supervisor_pid)]:
            continue
        uid = int(fields["Uid"][1])
        for role, expected in expected_roles.items():
            if uid == expected:
                check(role not in roles, "duplicate protected role UID")
                roles[role] = int(path.name)
    check(set(roles) == set(expected_roles), "installed protected role count/UID mismatch")
    return roles



def hostile_uid_direct_storage_denied(owner_db, witness_db):
    """Fresh-only kernel DAC negative: untrusted roles cannot write either DB.

    This cannot establish old inherited FD/MAP_SHARED revocation or restore
    authority for legacy data. The whole test runs in disposable PID/netns.
    """
    program = r"""
import errno, os, sys
status = {}
for line in open("/proc/self/status", encoding="ascii"):
    if ":" in line:
        key, value = line.split(":", 1)
        status[key] = value.strip()
if os.geteuid() == 0 or int(status["CapEff"], 16) or int(status["CapPrm"], 16):
    sys.exit(51)
for index, path in enumerate(sys.argv[1:]):
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    if index >= 2:
        flags |= os.O_CREAT
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EPERM):
            sys.exit(52)
    else:
        os.close(fd)
        sys.exit(53)
sys.exit(0)
"""
    paths = (
        str(owner_db), str(witness_db),
        str(owner_db) + "-wal", str(owner_db) + "-shm",
        str(witness_db) + "-wal", str(witness_db) + "-shm",
    )
    for uid in (UIDS["frontend"], UIDS["actor"], UIDS["ingress"], GENERATOR_UID):
        run = subprocess.run(
            [sys.executable, "-I", "-c", program, *paths],
            cwd="/", env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=10,
            user=uid, group=uid, extra_groups=(),
            check=False,
        )
        check(run.returncode == 0 and not run.stdout and not run.stderr,
              "untrusted customer role could access private Owner/Witness SQLite")




def authenticated_media_readback_witness_probe(witness_socket):
    """Real installed nonroot Actor may read back only with live Witness UID.

    This proves the protected transport-admission boundary, NOT a media
    publication or public server persistence claim.
    """
    program = r"""
import sys
from mark_api.private_web_media_verify import (
    ProtectedMediaReadbackAuthority,
    _require_private_web_public_media_transport_authority,
    _PrivateWebMediaAdmissionDenied,
)
socket_path, mode = sys.argv[1:]
grant = ProtectedMediaReadbackAuthority(
    witness_socket=socket_path, witness_uid=50043, actor_uid=50044,
)
try:
    _require_private_web_public_media_transport_authority(grant)
except _PrivateWebMediaAdmissionDenied:
    assert mode == "deny"
    print("WITNESS_MEDIA_READBACK_DENIED", flush=True)
else:
    assert mode == "admit"
    print("WITNESS_MEDIA_READBACK_ADMITTED", flush=True)
"""
    for role, allowed in (("actor", True), ("frontend", False)):
        uid = UIDS[role]
        result = subprocess.run(
            [sys.executable, "-I", "-c", program, str(witness_socket),
             "admit" if allowed else "deny"],
            cwd="/", env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=10,
            user=uid, group=uid, extra_groups=(), check=False,
        )
        marker = (b"WITNESS_MEDIA_READBACK_ADMITTED" if allowed
                  else b"WITNESS_MEDIA_READBACK_DENIED")
        check(result.returncode == 0 and result.stdout.strip() == marker
              and not result.stderr,
              "installed independent media Witness transport admission mismatch")



# Fixed test-only public gallery: production URL/TLS/galleries are unchanged.
# Only the disposable root PID/net/MOUNT namespace may install this fixture.
_SYNTHETIC_MEDIA_AD = "1234567892"
_SYNTHETIC_IMAGE = (
    "https://img.kleinanzeigen.de/api/v1/prod-ads/images/"
    "aa/aa111111-1111-4111-8111-111111111111?rule=$_59.JPG"
)
_SYNTHETIC_IMAGE_PATH = urlsplit(
    _SYNTHETIC_IMAGE.replace("$_59.JPG", "$_57.JPG")
).path + "?rule=$_57.JPG"


def _setup_isolated_synthetic_tls(directory, host_mnt):
    check(os.getpid() == 1 and os.geteuid() == 0,
          "synthetic TLS setup requires isolated PID1 root")
    check(os.readlink("/proc/self/ns/mnt") != host_mnt,
          "synthetic TLS setup has no independent mount namespace")
    bundle = Path("/etc/ssl/certs/ca-certificates.crt")
    check(bundle.is_file() and ssl.get_default_verify_paths().cafile is not None
          and Path(ssl.get_default_verify_paths().cafile).resolve()
          == bundle.resolve(), "synthetic TLS cannot locate the system trust store")
    key = Path(directory) / "synthetic-server.key"
    certificate = Path(directory) / "synthetic-server.crt"
    created = subprocess.run([
        "/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-nodes", "-days", "1", "-keyout", str(key), "-out", str(certificate),
        "-subj", "/CN=www.kleinanzeigen.de",
        "-addext", "subjectAltName=DNS:www.kleinanzeigen.de,DNS:img.kleinanzeigen.de",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=20, check=False)
    check(created.returncode == 0, "isolated synthetic TLS fixture unavailable")
    aliases = Path(directory) / "synthetic-hosts"
    aliases.write_bytes(
        b"127.0.0.1 www.kleinanzeigen.de img.kleinanzeigen.de\n"
        + Path("/etc/hosts").read_bytes()
    )
    trusted = Path(directory) / "synthetic-trust-bundle.pem"
    trusted.write_bytes(bundle.read_bytes() + b"\n" + certificate.read_bytes())
    # Mounts are local to the already verified fresh MOUNT namespace: no
    # host CA/hosts edits, no external route, no authentication bypass.
    for argv in (
        ("/usr/bin/mount", "--make-rprivate", "/"),
        ("/usr/bin/mount", "--bind", str(aliases), "/etc/hosts"),
        ("/usr/bin/mount", "--bind", str(trusted), str(bundle)),
    ):
        result = subprocess.run(argv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=10,
                                check=False)
        check(result.returncode == 0, "namespaced TLS trust setup unavailable")
    return certificate, key


class _PublicMediaGalleryHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _respond(self, status, media_type, content):
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(content)
        self.close_connection = True

    def do_POST(self):
        if (self.path != "/__synthetic_upload/" + _SYNTHETIC_MEDIA_AD
                or self.headers.get("X-Synthetic-Browser-Upload") != "1"):
            self._respond(404, "text/plain", b"unrecognized")
            return
        raw_len = self.headers.get("Content-Length")
        if not raw_len or not raw_len.isdecimal() or not 100 <= int(raw_len) <= 1048576:
            self._respond(400, "text/plain", b"invalid size")
            return
        payload = self.rfile.read(int(raw_len))
        if (len(payload) != int(raw_len) or not payload.startswith(b"\xff\xd8")
                or not payload.endswith(b"\xff\xd9")
                or self.server.media is not None):
            self._respond(409, "text/plain", b"not accepted")
            return
        self.server.media = bytes(payload)
        self.server.media_uploads += 1
        self._respond(201, "text/plain", _SYNTHETIC_MEDIA_AD.encode())

    def do_GET(self):
        if self.server.media is None:
            self._respond(404, "text/plain", b"not published")
            return
        if (self.headers.get("Host") == "www.kleinanzeigen.de"
                and self.path == "/s-anzeige/" + _SYNTHETIC_MEDIA_AD):
            self.server.listing_gets += 1
            body = (
                '<html><head><link rel="canonical" href="'
                'https://www.kleinanzeigen.de/s-anzeige/' + _SYNTHETIC_MEDIA_AD
                + '"></head><body><div class="vip-image-gallery j-gallery-image">'
                  '<script type="application/ld+json">'
                  '{"@type":"ImageObject","contentUrl":"' + _SYNTHETIC_IMAGE
                + '"}</script></div></body></html>'
            ).encode()
            self._respond(200, "text/html", body)
        elif (self.headers.get("Host") == "img.kleinanzeigen.de"
              and self.path == _SYNTHETIC_IMAGE_PATH):
            self.server.image_gets += 1
            self._respond(200, "image/jpeg", self.server.media)
        else:
            self._respond(404, "text/plain", b"unrecognized")


class SyntheticPublicMediaGallery(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, certificate, key):
        super().__init__(("127.0.0.1", 443), _PublicMediaGalleryHandler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(certificate), str(key))
        self.socket = ctx.wrap_socket(self.socket, server_side=True)
        self.media = None
        self.media_uploads = 0
        self.listing_gets = 0
        self.image_gets = 0


class _SyntheticCDPHandler(BaseHTTPRequestHandler):
    """Isolated test-only wire-level CDP responder, not a product adapter."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/json/list":
            self.server.discoveries += 1
            body = json.dumps([{"type": "page", "id": "synthetic-owner",
                "webSocketDebuggerUrl":
                "ws://127.0.0.1:19322/devtools/page/synthetic-owner"}]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/devtools/page/synthetic-owner":
            self.send_error(404)
            return
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or self.headers.get("Upgrade", "").lower() != "websocket":
            self.send_error(400)
            return
        proof = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
        ).digest()).decode()
        self.server.upgrades += 1
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", proof)
        self.end_headers()
        self.close_connection = True
        while True:
            header = self.rfile.read(2)
            if len(header) != 2 or (header[0] & 15) == 8:
                return
            n = header[1] & 127
            if n == 126:
                n = int.from_bytes(self.rfile.read(2), "big")
            elif n == 127:
                n = int.from_bytes(self.rfile.read(8), "big")
            if n > 256 * 1024 or not header[1] & 128 or (header[0] & 15) != 1:
                return
            mask = self.rfile.read(4)
            payload = self.rfile.read(n)
            if len(mask) != 4 or len(payload) != n:
                return
            try:
                data = json.loads(bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))
                method, params = data["method"], data.get("params", {})
                self.server.commands.append(method)
                answer = {"error": {"code": -32601, "message": "not admitted"}}
                if method == "Page.navigate":
                    url = params.get("url")
                    if url == "https://www.kleinanzeigen.de/m-meine-anzeigen.html":
                        self.server.page = "/m-meine-anzeigen.html"
                        answer = {"result": {}}
                    elif url == ("https://www.kleinanzeigen.de/"
                                 "p-anzeige-bearbeiten.html?adId=" + self.server.ad_id):
                        self.server.page = "/p-anzeige-bearbeiten.html"
                        self.server.draft_title = self.server.title
                        self.server.draft_description = self.server.description
                        answer = {"result": {}}
                    elif url == "https://www.kleinanzeigen.de/p-anzeige-aufgeben.html":
                        self.server.page = "/p-anzeige-aufgeben.html"
                        self.server.categories = []
                        self.server.create_title = ""
                        self.server.create_description = ""
                        self.server.create_price = ""
                        self.server.media_selected = ()
                        answer = {"result": {}}
                elif method == "Input.dispatchMouseEvent":
                    if (params.get("type") == "mousePressed"
                            and params.get("button") == "left"):
                        self.server.presses += 1
                        answer = {"result": {}}
                    elif (params.get("type") == "mouseReleased"
                            and self.server.presses == self.server.releases + 1):
                        if self.server.pending_state in ("active", "paused"):
                            self.server.state = self.server.pending_state
                            self.server.pending_state = None
                            self.server.releases += 1
                            answer = {"result": {}}
                        elif (self.server.pending_content
                              and self.server.page == "/p-anzeige-bearbeiten.html"):
                            self.server.title = self.server.draft_title
                            self.server.description = self.server.draft_description
                            self.server.pending_content = False
                            self.server.content_submits += 1
                            self.server.page = "/m-meine-anzeigen.html"
                            self.server.releases += 1
                            answer = {"result": {}}
                        elif (self.server.pending_delete in ("open", "submit")
                              and self.server.page == "/m-meine-anzeigen.html"):
                            if self.server.pending_delete == "open":
                                self.server.delete_modal = True
                            elif self.server.delete_modal:
                                self.server.present = False
                                self.server.delete_modal = False
                                self.server.delete_submits += 1
                            self.server.pending_delete = None
                            self.server.releases += 1
                            answer = {"result": {}}
                        elif self.server.pending_create is not None:
                            action = self.server.pending_create
                            if action.startswith("category:"):
                                self.server.categories.append(action.split(":", 1)[1])
                            elif action == "continue" and self.server.categories == [
                                    "Freizeit", "Sammeln"]:
                                self.server.page = "/p-anzeige-aufgeben-schritt2.html"
                            elif action == "publish" and self.server.page == "/p-anzeige-aufgeben-schritt2.html":
                                if self.server.media_bind_enabled:
                                    # This is the synthetic browser's independent
                                    # platform upload, NOT the Actor verifier.
                                    check(len(self.server.media_selected) == 1,
                                          "synthetic CDP had no selected media bytes")
                                    with closing(HTTPSConnection(
                                        "127.0.0.1", 443, timeout=3,
                                        context=ssl._create_unverified_context(),
                                    )) as http:
                                        http.request(
                                            "POST", "/__synthetic_upload/" + _SYNTHETIC_MEDIA_AD,
                                            body=self.server.media_selected[0][1],
                                            headers={"X-Synthetic-Browser-Upload": "1",
                                                     "Content-Type": "image/jpeg"},
                                        )
                                        upload = http.getresponse()
                                        check(upload.status == 201
                                              and upload.read() == _SYNTHETIC_MEDIA_AD.encode(),
                                              "synthetic CDP media upload not persisted")
                                    self.server.ad_id = _SYNTHETIC_MEDIA_AD
                                    self.server.media_publish_submits += 1
                                else:
                                    self.server.ad_id = "1234567891"
                                self.server.present = True
                                self.server.title = self.server.create_title
                                self.server.description = self.server.create_description
                                self.server.state = "active"
                                self.server.create_submits += 1
                                self.server.page = "/m-meine-anzeigen.html"
                            self.server.pending_create = None
                            self.server.releases += 1
                            answer = {"result": {}}
                elif method == "DOM.setFileInputFiles":
                    names = params.get("files")
                    if (self.server.media_bind_enabled
                            and params.get("objectId") == "synthetic-media-input-v1"
                            and isinstance(names, list) and len(names) == 1
                            and isinstance(names[0], str)):
                        source = Path(names[0])
                        check(source.is_absolute() and source.is_file(),
                              "browser media file input bytes unavailable")
                        self.server.media_selected = ((source.name, source.read_bytes()),)
                        self.server.media_file_set_calls += 1
                        answer = {"result": {}}
                elif method == "Runtime.callFunctionOn":
                    declaration = params.get("functionDeclaration", "")
                    if (self.server.media_bind_enabled
                            and params.get("objectId") == "synthetic-media-input-v1"
                            and self.server.media_selected):
                        value = None
                        if "files: Array.from(input.files)" in declaration:
                            value = {"state": "ready", "files": [
                                {"name": name, "size_bytes": len(payload)}
                                for name, payload in self.server.media_selected
                            ]}
                        elif ("expectedFiles =" in declaration
                                and '"Anzeige aufgeben"' in declaration
                                and self.server.page == "/p-anzeige-aufgeben-schritt2.html"):
                            self.server.pending_create = "publish"
                            value = {"state": "ready", "x": 120.5, "y": 240.25}
                        if value is not None:
                            answer = {"result": {"result": {
                                "type": "object", "value": value,
                            }}}
                elif method == "Runtime.releaseObject":
                    if params.get("objectId") == "synthetic-media-input-v1":
                        answer = {"result": {}}
                elif method == "Runtime.evaluate" and params.get("awaitPromise") is True:
                    expression = params["expression"]
                    if "pageNum=1" in expression and 'credentials: "same-origin"' in expression:
                        ads = {"ads": (
                            [{"id": self.server.ad_id,
                              "title": self.server.title, "state": self.server.state,
                              "viewCount": 7, "watchCount": 2}]
                            if self.server.present else []),
                            "paging": {"last": 1}}
                        answer = {"result": {"result": {"type": "object", "value": {
                            "status": 200, "body": json.dumps(ads)}}}}
                elif method == "Runtime.evaluate":
                    expression = params.get("expression", "")
                    # Observe the actual CDP file-input binding attempt;
                    # an early HTTP rejection is not an adequate media test.
                    if ("const baseline =" in expression
                            and 'input[type="file"]' in expression
                            and "return files[0];" in expression):
                        self.server.media_input_bind_probes += 1
                        if self.server.media_bind_enabled:
                            answer = {"result": {"result": {
                                "type": "object",
                                "objectId": "synthetic-media-input-v1",
                            }}}
                    value = None
                    if (("__mark_private_web_state_navigation_probe_" in expression
                         or "__mark_private_web_navigation_probe_" in expression
                         or "__mark_private_web_delete_navigation_probe_" in expression
                         or "__mark_private_web_create_navigation_probe__" in expression)
                            and "globalThis[key] = true" in expression):
                        value = True
                    elif "oldDocument: globalThis[" in expression:
                        value = {"readyState": "complete", "oldDocument": False}
                        if ("origin: location.origin" in expression
                                and "path: location.pathname" in expression):
                            value.update({"origin": "https://www.kleinanzeigen.de",
                                          "path": self.server.page})
                    elif ("const saveButtons =" in expression
                          and "const adId = new URL(location.href)" in expression
                          and self.server.page == "/p-anzeige-bearbeiten.html"):
                        value = {"state": "ready", "ad_id": self.server.ad_id,
                                 "title": self.server.draft_title,
                                 "description": self.server.draft_description}
                    elif ("if ((link.innerText || \"\").trim() !==" in expression
                          and self.server.page == "/p-anzeige-aufgeben.html"):
                        labels = ("Freizeit", "Sammeln")
                        index = len(self.server.categories)
                        if (index < len(labels)
                                and '!== "' + labels[index] + '"' in expression):
                            self.server.pending_create = "category:" + labels[index]
                            value = {"state": "ready", "x": 120.5, "y": 240.25}
                    elif ("const forms = Array.from(document.forms)" in expression
                          and self.server.page == "/p-anzeige-aufgeben.html"
                          and self.server.categories == ["Freizeit", "Sammeln"]):
                        self.server.pending_create = "continue"
                        value = {"state": "ready", "x": 120.5, "y": 240.25}
                    elif ("const allowedHiddenNames = new Set([" in expression
                          and self.server.page == "/p-anzeige-aufgeben-schritt2.html"):
                        if "if (!true)" in expression:
                            self.server.pending_create = "publish"
                            value = {"state": "ready", "x": 120.5, "y": 240.25}
                        elif "if (!false)" in expression:
                            value = {"state": "ready", "title": self.server.create_title,
                                     "description": self.server.create_description,
                                     "price_amount": self.server.create_price}
                    elif ("descriptor.set.call(element," in expression
                          and self.server.page == "/p-anzeige-aufgeben-schritt2.html"):
                        if ('document.querySelector("#ad-title")' in expression
                                and 'descriptor.set.call(element, "Created synthetic bowl")' in expression):
                            self.server.create_title = "Created synthetic bowl"
                            value = True
                        elif ('document.querySelector("#ad-description")' in expression
                                and 'descriptor.set.call(element, "Created in root installed CDP acceptance")' in expression):
                            self.server.create_description = "Created in root installed CDP acceptance"
                            value = True
                        elif ('document.querySelector("#ad-price-amount")' in expression
                                and 'descriptor.set.call(element, "10")' in expression):
                            self.server.create_price = "10"
                            value = True
                    elif ("descriptor.set.call(element," in expression
                          and self.server.page == "/p-anzeige-bearbeiten.html"):
                        if ('document.querySelector("#ad-title")' in expression
                                and 'descriptor.set.call(element, "Edited synthetic vase")' in expression):
                            self.server.draft_title = "Edited synthetic vase"
                            value = True
                        elif ('document.querySelector("#ad-description")' in expression
                                and 'descriptor.set.call(element, "Edited synthetic description")' in expression):
                            self.server.draft_description = "Edited synthetic description"
                            value = True
                    elif ("const buttons = Array.from(document.querySelectorAll" in expression
                          and '"Anzeige speichern"' in expression
                          and "button.scrollIntoView" in expression
                          and self.server.page == "/p-anzeige-bearbeiten.html"):
                        self.server.pending_content = True
                        value = {"x": 120.5, "y": 240.25}
                    elif ('if (currentPath === "/m-meine-anzeigen.html") return "confirmed"' in expression
                          and self.server.page == "/m-meine-anzeigen.html"):
                        value = "confirmed"
                    elif ("const deleteControls = " in expression
                          and self.server.page == "/m-meine-anzeigen.html"
                          and self.server.present and not self.server.delete_modal):
                        if "const activate = true" in expression:
                            self.server.pending_delete = "open"
                            value = {"x": 120.5, "y": 240.25}
                        elif "const activate = false" in expression:
                            value = {"state": "ready", "ad_id": "1234567890"}
                    elif ("#delete-celebration-sbmt" in expression
                          and "const containers = " in expression
                          and self.server.page == "/m-meine-anzeigen.html"
                          and self.server.present and self.server.delete_modal):
                        if "const activate = true" in expression:
                            self.server.pending_delete = "submit"
                            value = {"x": 120.5, "y": 240.25}
                        elif "const activate = false" in expression:
                            value = {"state": "ready", "ad_id": "1234567890"}
                    elif ("document.querySelector(\"#celebration-container\")" in expression
                          and not self.server.present):
                        value = True
                    elif "const expectedControl =" in expression:
                        if 'const expectedControl = "reservieren"' in expression:
                            if self.server.state == "active":
                                self.server.pending_state = "paused"
                                value = {"x": 120.5, "y": 240.25}
                        elif 'const expectedControl = "aktivieren"' in expression:
                            if self.server.state == "paused":
                                self.server.pending_state = "active"
                                value = {"x": 120.5, "y": 240.25}
                        elif "const expectedControl = null" in expression:
                            value = {"state": "ready", "ad_id": "1234567890",
                                     "lifecycle_state": self.server.state}
                    elif ("readyState: document.readyState" in expression
                            and "origin: location.origin" in expression):
                        value = {"readyState": "complete",
                                 "origin": "https://www.kleinanzeigen.de",
                                 "path": "/m-meine-anzeigen.html"}
                    if value is not None:
                        remote_type = ("boolean" if type(value) is bool else
                                       "string" if type(value) is str else "object")
                        answer = {"result": {"result": {"type": remote_type,
                                                         "value": value}}}
                raw = json.dumps({"id": data["id"], **answer}).encode()
                head = (bytes((0x81, len(raw))) if len(raw) < 126 else
                        bytes((0x81, 126)) + len(raw).to_bytes(2, "big"))
                self.wfile.write(head + raw)
                self.wfile.flush()
            except (OSError, ValueError, KeyError, TypeError):
                return


class SyntheticCDP(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 19322), _SyntheticCDPHandler)
        self.commands = []
        self.discoveries = 0
        self.upgrades = 0
        self.state = "active"
        self.pending_state = None
        self.presses = 0
        self.releases = 0
        self.page = "/m-meine-anzeigen.html"
        self.title = "Synthetic CDP vase"
        self.description = "Original synthetic description"
        self.draft_title = self.title
        self.draft_description = self.description
        self.pending_content = False
        self.content_submits = 0
        self.present = True
        self.delete_modal = False
        self.pending_delete = None
        self.delete_submits = 0
        self.ad_id = "1234567890"
        self.categories = []
        self.pending_create = None
        self.create_title = ""
        self.create_description = ""
        self.create_price = ""
        self.create_submits = 0
        self.media_input_bind_probes = 0
        self.media_bind_enabled = False
        self.media_selected = ()
        self.media_file_set_calls = 0
        self.media_publish_submits = 0


def inventory_confirmed(cdp):
    deadline = monotonic() + 12
    while monotonic() < deadline:
        try:
            with closing(HTTPConnection("127.0.0.1", 8875, timeout=2)) as client:
                client.request("GET", "/api/sync/status")
                response = client.getresponse()
                attempt = (json.loads(response.read()).get("latest_attempt") or {})
            if attempt.get("outcome") == "success_nonempty":
                break
            if attempt.get("outcome") == "failed":
                raise AssertionError("real CDP sync failed d=%d ws=%d calls=%d" % (cdp.discoveries, cdp.upgrades, len(cdp.commands)))
        except (OSError, TimeoutError):
            pass
        sleep(.1)
    else:
        raise AssertionError("real CDP sync timeout d=%d ws=%d calls=%d" % (cdp.discoveries, cdp.upgrades, len(cdp.commands)))
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=2)) as client:
        client.request("GET", "/api/ads")
        reply = client.getresponse()
        rows = json.loads(reply.read())
        check(reply.status == 200 and any(
            item.get("ad_id") == "1234567890"
            and item.get("title") == cdp.title
            and item.get("views") == 7 for item in rows
        ), "real CDP inventory absent from protected dashboard")


def _dashboard_http(method, path, *, token, key=None, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Origin": "http://127.0.0.1:8875",
        "X-Mark-Dashboard-Write": "1",
        "X-Mark-Dashboard-Token": token,
        "Accept": "application/json",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    if body is not None:
        headers["Content-Type"] = "application/json"
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=20)) as client:
        client.request(method, path, body=body, headers=headers)
        response = client.getresponse()
        raw = response.read()
        return response.status, json.loads(raw), response.getheader("Idempotency-Replayed")


def synthetic_state_write(cdp, token, action):
    check(action in ("pause", "activate"), "unexpected synthetic state action")
    key = "synthetic-real-cdp-" + action + "-1234567890"
    path = "/api/write/ads/1234567890/" + action
    presses_before, releases_before = cdp.presses, cdp.releases
    status, body, replayed = _dashboard_http("POST", path, token=token, key=key)
    check(status == 200 and body.get("operation_receipt", {}).get("outcome") == "confirmed",
          "protected real CDP state write was not confirmed")
    check(replayed != "true" and cdp.presses == presses_before + 1
          and cdp.releases == releases_before + 1,
          "real CDP state write did not execute exactly once")
    replay_status, replay_body, replay_flag = _dashboard_http(
        "POST", path, token=token, key=key,
    )
    check(replay_status == status and replay_body == body
          and replay_flag == "true"
          and cdp.presses == presses_before + 1
          and cdp.releases == releases_before + 1,
          "replayed protected state write sent browser input again")
    scope = "ad:1234567890:" + action
    for terminal in (False, True):
        ack_status, ack, _ = _dashboard_http(
            "POST", "/api/dashboard/pending-writes/ack",
            token=token, payload={"scope": scope, "idempotency_key": key},
        )
        check(ack_status == 200 and ack.get("acknowledged") is True
              and ack.get("finalized") is terminal,
              "protected state write two-phase ACK was not durable")



def synthetic_content_write(cdp, token):
    key = "synthetic-real-cdp-content-1234567890"
    path = "/api/write/ads/1234567890"
    payload = {"title": "Edited synthetic vase",
               "description": "Edited synthetic description"}
    before = (cdp.presses, cdp.releases, cdp.content_submits)
    status, body, replayed = _dashboard_http(
        "PATCH", path, token=token, key=key, payload=payload,
    )
    check(status == 200 and body.get("operation_receipt", {}).get("outcome") == "confirmed",
          "protected real CDP content write was not confirmed")
    check(replayed != "true" and cdp.title == payload["title"]
          and cdp.description == payload["description"]
          and (cdp.presses, cdp.releases, cdp.content_submits)
          == (before[0] + 1, before[1] + 1, before[2] + 1),
          "real CDP content update was not applied exactly once")
    repeated, replay_body, replay_flag = _dashboard_http(
        "PATCH", path, token=token, key=key, payload=payload,
    )
    check(repeated == status and replay_body == body and replay_flag == "true"
          and (cdp.presses, cdp.releases, cdp.content_submits)
          == (before[0] + 1, before[1] + 1, before[2] + 1),
          "CDP content write replay performed duplicate browser input")
    for terminal in (False, True):
        ack_status, ack, _ = _dashboard_http(
            "POST", "/api/dashboard/pending-writes/ack", token=token,
            payload={"scope": "ad:1234567890:update", "idempotency_key": key},
        )
        check(ack_status == 200 and ack.get("acknowledged") is True
              and ack.get("finalized") is terminal,
              "protected content write two-phase ACK was not durable")
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=4)) as conn:
        conn.request("GET", "/api/ads")
        response = conn.getresponse()
        rows = json.loads(response.read())
    check(response.status == 200 and any(
        row.get("ad_id") == "1234567890"
        and row.get("title") == payload["title"] for row in rows
    ), "real CDP content write missing from owner SQLite/dashboard")



def synthetic_delete_write(cdp, token):
    path = "/api/write/ads/1234567890"
    key = "synthetic-real-cdp-delete-1234567890"
    before = (cdp.presses, cdp.releases, cdp.delete_submits)
    status, body, replayed = _dashboard_http("DELETE", path, token=token, key=key)
    check(status == 200 and body.get("operation_receipt", {}).get("outcome") == "confirmed",
          "protected real CDP modal delete was not confirmed")
    check(replayed != "true" and cdp.present is False and cdp.delete_modal is False
          and (cdp.presses, cdp.releases, cdp.delete_submits)
          == (before[0] + 2, before[1] + 2, before[2] + 1),
          "real CDP modal delete was not executed exactly once")
    again, replay_body, replay_flag = _dashboard_http(
        "DELETE", path, token=token, key=key,
    )
    check(again == status and replay_body == body and replay_flag == "true"
          and (cdp.presses, cdp.releases, cdp.delete_submits)
          == (before[0] + 2, before[1] + 2, before[2] + 1),
          "replayed protected CDP delete repeated browser input")
    for terminal in (False, True):
        ack_status, ack, _ = _dashboard_http(
            "POST", "/api/dashboard/pending-writes/ack", token=token,
            payload={"scope": "ad:1234567890:delete", "idempotency_key": key},
        )
        check(ack_status == 200 and ack.get("acknowledged") is True
              and ack.get("finalized") is terminal,
              "real CDP delete two-phase durable ACK failed")



def synthetic_create_write(cdp, token):
    key = "synthetic-real-cdp-create-1234567891"
    payload = {"category_path": ["Freizeit", "Sammeln"],
               "title": "Created synthetic bowl",
               "description": "Created in root installed CDP acceptance",
               "price_eur": 10}
    before = (cdp.presses, cdp.releases, cdp.create_submits)
    status, body, replayed = _dashboard_http(
        "POST", "/api/write/ads", token=token, key=key, payload=payload,
    )
    check(status == 200 and body.get("operation_receipt", {}).get("outcome") == "confirmed",
          "protected real CDP publish was not confirmed")
    check(replayed != "true" and cdp.present is True
          and cdp.ad_id == "1234567891" and cdp.title == payload["title"]
          and cdp.categories == payload["category_path"]
          and (cdp.presses, cdp.releases, cdp.create_submits)
          == (before[0] + 4, before[1] + 4, before[2] + 1),
          "real CDP publish was not performed exactly once")
    again, same, flag = _dashboard_http(
        "POST", "/api/write/ads", token=token, key=key, payload=payload,
    )
    check(again == status and same == body and flag == "true"
          and (cdp.presses, cdp.releases, cdp.create_submits)
          == (before[0] + 4, before[1] + 4, before[2] + 1),
          "replayed protected CDP publish issued duplicate browser input")
    for terminal in (False, True):
        ack_status, ack, _ = _dashboard_http(
            "POST", "/api/dashboard/pending-writes/ack", token=token,
            payload={"scope": "create", "idempotency_key": key},
        )
        check(ack_status == 200 and ack.get("acknowledged") is True
              and ack.get("finalized") is terminal,
              "real CDP publish two-phase durable ACK failed")
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=4)) as conn:
        conn.request("GET", "/api/ads")
        response = conn.getresponse()
        rows = json.loads(response.read())
    check(response.status == 200 and any(
        row.get("ad_id") == "1234567891"
        and row.get("title") == payload["title"] for row in rows
    ), "real CDP publish missing from protected owner SQLite/dashboard")



def synthetic_positive_media_publish(cdp, write_token):
    """Full installed real CDP FileList -> one browser submit -> public HTTPS.

    The readback is performed by the UNMODIFIED installed protected Actor
    verifier against the independent test-owned TLS gallery. No fixture
    replaces production media persistence authorization, host namespaces,
    provider, fingerprint algorithm, or Witness completion.
    """
    from io import BytesIO
    from PIL import Image

    image = Image.new("RGB", (256, 192), (38, 125, 181))
    encoded = BytesIO()
    image.save(encoded, format="JPEG", quality=92)
    jpeg = encoded.getvalue()
    check(jpeg.startswith(bytes((255, 216)))
          and jpeg.endswith(bytes((255, 217))),
          "synthetic customer JPEG image is invalid")

    # A blank CDN gallery cannot claim a successful media publication.
    with closing(HTTPSConnection("www.kleinanzeigen.de", 443, timeout=4)) as net:
        net.request("GET", "/s-anzeige/" + _SYNTHETIC_MEDIA_AD)
        unavailable = net.getresponse()
        check(unavailable.status == 404
              and unavailable.read() == b"not published",
              "synthetic public media gallery was not initially empty")

    headers = {
        "Origin": "http://127.0.0.1:8875",
        "X-Mark-Dashboard-Write": "1",
        "X-Mark-Dashboard-Token": write_token,
        "Content-Type": "image/jpeg",
        "X-Mark-Media-Filename": "verified-photo.jpg",
    }
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=15)) as http:
        http.request("POST", "/api/write/media/stage", body=jpeg,
                     headers=headers)
        reply = http.getresponse()
        staged = json.loads(reply.read())
        check(reply.status == 201
              and type(staged.get("media_ref")) is str,
              "synthetic positive media could not stage file")
    media_key = "protected-cdp-published-media-20261024"
    payload = {
        "category_path": ["Freizeit", "Sammeln"],
        "title": "Created synthetic bowl",
        "description": "Created in root installed CDP acceptance",
        "price_eur": 10,
        "media_refs": [staged["media_ref"]],
    }
    before = (cdp.presses, cdp.releases, cdp.create_submits,
              cdp.media_file_set_calls, cdp.media_publish_submits,
              cdp.media_input_bind_probes, cdp.gallery.media_uploads)
    check(cdp.gallery.media is None and not cdp.media_bind_enabled,
          "positive synthetic media fixture not initially isolated")
    cdp.media_bind_enabled = True
    try:
        code, result, replay = _dashboard_http(
            "POST", "/api/write/media/ads", token=write_token,
            key=media_key, payload=payload,
        )
    finally:
        cdp.media_bind_enabled = False

    observed_outcome = result.get("operation_receipt", {}).get("outcome")
    observed_media = result.get("media_post_read_status")
    safe_outcome = (observed_outcome if observed_outcome in {
        "confirmed", "precondition_failed", "ambiguous",
    } else "unavailable")
    safe_media = (observed_media if observed_media in {
        "confirmed", "mismatch", "unknown", "not_read",
        "verifier_unavailable",
    } else "unavailable")
    check(code == 200 and replay != "true"
          and observed_outcome == "confirmed"
          and result.get("media_persistence_confirmed") is True
          and observed_media == "confirmed",
          "protected media readback denied: status=%d,outcome=%s,media=%s,"
          "input=%d,selected=%d,published=%d,gallery=%d,reads=%d/%d"
          % (code, safe_outcome, safe_media, cdp.media_input_bind_probes,
             cdp.media_file_set_calls, cdp.media_publish_submits,
             cdp.gallery.media_uploads, cdp.gallery.listing_gets,
             cdp.gallery.image_gets))
    check(cdp.create_submits == before[2] + 1
          and cdp.media_file_set_calls == before[3] + 1
          and cdp.media_publish_submits == before[4] + 1
          and cdp.media_input_bind_probes == before[5] + 1
          and cdp.gallery.media_uploads == before[6] + 1
          and cdp.gallery.media == jpeg
          and cdp.gallery.listing_gets >= 1
          and cdp.gallery.image_gets >= 1,
          "protected CDP publish did not durably match independently fetched JPEG")
    check(cdp.ad_id == _SYNTHETIC_MEDIA_AD
          and cdp.presses == before[0] + 4
          and cdp.releases == before[1] + 4,
          "protected media attempted duplicate or missing CDP clicks")

    again, same, replayed = _dashboard_http(
        "POST", "/api/write/media/ads", token=write_token,
        key=media_key, payload=payload,
    )
    check(again == code and same == result and replayed == "true"
          and cdp.create_submits == before[2] + 1
          and cdp.media_publish_submits == before[4] + 1
          and cdp.gallery.media_uploads == before[6] + 1,
          "confirmed media replay dispatched another browser publish")
    for terminal in (False, True):
        ack_status, ack, _ = _dashboard_http(
            "POST", "/api/dashboard/pending-writes/ack",
            token=write_token,
            payload={"scope": "create", "idempotency_key": media_key},
        )
        check(ack_status == 200 and ack.get("acknowledged") is True
              and ack.get("finalized") is terminal,
              "protected confirmed media two-phase ACK was not durable")
    return cdp.presses - before[0]


def synthetic_ai_generation_drafts(token, write_token, cdp):
    from mark_api.generation import _validate_png_payload
    base = "http://127.0.0.1:8875"
    calls_before = tuple(cdp.commands)

    with closing(HTTPConnection("127.0.0.1", 8875, timeout=5)) as http:
        http.request("GET", "/api/dashboard/config")
        reply = http.getresponse()
        config = json.loads(reply.read())
    check(reply.status == 200
          and config.get("generation_ui_available") is True
          and config.get("write_ui_available") is True,
          "installed protected customer AI draft panel unavailable")

    def call(mode, prompt, *, consent=True, token_override=None):
        request = json.dumps({"mode": mode, "prompt": prompt,
                              "acknowledge_provider_cost": consent}).encode()
        headers = {
            "Origin": base,
            "Content-Type": "application/json",
            "X-Mark-Generation": "1",
            "X-Mark-Generation-Token": token if token_override is None else token_override,
        }
        with closing(HTTPConnection("127.0.0.1", 8875, timeout=15)) as http:
            http.request("POST", "/api/dashboard/generate", body=request,
                         headers=headers)
            response = http.getresponse()
            return response.status, dict(response.headers), response.read()

    status, _, invalid = call("text", "Synthetischer Schrank", consent=False)
    check(status == 400 and b"invalid_generation_request" in invalid,
          "provider cost consent was not enforced")
    status, _, denied = call("text", "Synthetischer Schrank",
                             token_override="wrong-test-only-token")
    check(status == 403 and b"generation_session_required" in denied,
          "generation token access not independently enforced")
    status, _, result = call("text", "Synthetischer Schrank")
    check(status == 200, "protected installed text generation unavailable")
    draft = json.loads(result)
    check(draft == {
        "title": "Synthetic generated title",
        "description": "Synthetic generated description, no publication.",
        "status": "draft", "published": False,
    }, "installed generated text was not a nonpublished draft")

    status, headers, png = call("image", "Synthetisches Schrankbild")
    check(status == 200 and headers.get("Content-Type") == "image/png",
          "protected installed generated image missing")
    _validate_png_payload(png)
    # The exact AI-generated PNG must be accepted by the *real installed*
    # A6 protected media staging chain, with opaque actor-owned handle.
    # Staging and explicit discard are local-only, never publication.
    headers = {
        "Origin": base,
        "X-Mark-Dashboard-Write": "1",
        "X-Mark-Dashboard-Token": write_token,
        "Content-Type": "image/png",
        "X-Mark-Media-Filename": "generated-draft.png",
    }
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=15)) as http:
        http.request("POST", "/api/write/media/stage", body=png,
                     headers=headers)
        response = http.getresponse()
        stage = json.loads(response.read())
    check(response.status == 201
          and type(stage.get("media_ref")) is str
          and len(stage["media_ref"]) > 12,
          "generated PNG was not staged through installed private actor")
    code, cleanup, _ = _dashboard_http(
        "POST", "/api/write/media/discard", token=write_token,
        payload={"media_refs": [stage["media_ref"]]},
    )
    check(code == 200 and cleanup.get("discarded") == 1,
          "generated PNG private media draft could not be safely discarded")
    check(tuple(cdp.commands) == calls_before,
          "draft generation/media preparation performed a platform CDP action")

    # Verify the positive protected media publication first, then
    # separately reject incomplete CDP file-input binding and replay.
    positive_clicks = synthetic_positive_media_publish(cdp, write_token)
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=15)) as http:
        http.request("POST", "/api/write/media/stage", body=png, headers=headers)
        response = http.getresponse()
        restaged = json.loads(response.read())
    check(response.status == 201
          and type(restaged.get("media_ref")) is str,
          "protected synthetic Media-Create setup could not stage image")
    media_key = "protected-cdp-unverified-media-20261024"
    media_payload = {
        "category_path": ["Freizeit", "Sammeln"],
        "title": "Created synthetic bowl",
        "description": "Created in root installed CDP acceptance",
        "price_eur": 10,
        "media_refs": [restaged["media_ref"]],
    }
    before_submit = cdp.create_submits
    before_media_presses, before_media_releases = cdp.presses, cdp.releases
    before_media_probes = cdp.media_input_bind_probes
    media_status, media_body, _ = _dashboard_http(
        "POST", "/api/write/media/ads", token=write_token,
        key=media_key, payload=media_payload,
    )
    check(media_status != 200
          and media_body.get("operation_receipt", {}).get("outcome") != "confirmed"
          and cdp.create_submits == before_submit,
          "unverified protected CDP file-input claimed successful publication")
    # Repeating the exact key must not turn an unverified media outcome
    # into a fake success or produce a browser publish side effect.
    again_status, again_body, _ = _dashboard_http(
        "POST", "/api/write/media/ads", token=write_token,
        key=media_key, payload=media_payload,
    )
    check(again_status != 200
          and again_body.get("operation_receipt", {}).get("outcome") != "confirmed"
          and cdp.create_submits == before_submit,
          "unverified protected media replay published or confirmed a write")
    # A failed pre-submit may still navigate and click category/continue
    # controls. Count only that bounded preparation separately, never as
    # a successful publish; the successful CRUD journeys keep their own
    # exact one-shot mouse and submit assertions.
    extra_clicks = cdp.presses - before_media_presses
    check(1 <= extra_clicks <= 3
          and cdp.releases - before_media_releases == extra_clicks,
          "unverified media preflight dispatched unexpected browser clicks")
    check(cdp.media_input_bind_probes == before_media_probes + 1,
          "media negative test did not reach exactly one CDP file-input binding")
    return extra_clicks + positive_clicks


def dashboard_ready():
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=4)) as client:
        client.request("GET", "/readyz")
        response = client.getresponse()
        response.read()
        check(response.status == 200, "installed dashboard readiness failed")
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=4)) as client:
        client.request("GET", "/api/dashboard/config")
        response = client.getresponse()
        config = json.loads(response.read())
        check(response.status == 200 and config["write_ui_available"] is True,
              "protected UI does not expose witnessed Write controls")
    with closing(HTTPConnection("127.0.0.1", 8875, timeout=4)) as client:
        client.request("GET", "/api/ads")
        response = client.getresponse()
        response.read()
        check(response.status == 200, "protected stored-inventory UI not available")


def start(binary, root, init_new, *, sync_interval=0,
          enable_generation=False):
    argv = [str(binary), "--state-root", str(root), "--cdp-port", "19322",
            "--sync-interval", str(sync_interval)]
    if init_new:
        argv.append("--init-new")
    if enable_generation:
        argv.append("--enable-generation")
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8"}
    if enable_generation:
        # This accepted test-only credential reaches the installed SDK stub,
        # never an external provider. Real customer credentials are absent.
        env["OPENAI_API_KEY"] = "synthetic-protected-generation-test-only"
    child = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, close_fds=True,
        env=env,
    )
    check(child.stdout is not None, "product startup pipe missing")
    check(select.select([child.stdout], [], [], 40)[0], "product never printed dashboard URL")
    line = child.stdout.readline()
    if not line.startswith("Kleinanzeiger: http://127.0.0.1:8875/#write_token="):
        # A read of EOF may precede waitpid by a few milliseconds. Wait for
        # the actual exit before inspecting only our fixed nonsecret phase.
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            raise AssertionError("protected startup closed readiness before finishing") from None
        raw = child.stderr.read(512).strip()
        marker = "Kleinanzeiger: protected customer startup unavailable ("
        if raw.startswith(marker) and raw.endswith(")") and len(raw) < 190:
            phase = raw[len(marker):-1]
            if phase and all(char.isascii() and (char.isalpha() or char in " -")
                             for char in phase):
                raise AssertionError("startup phase: " + phase)
        raise AssertionError("installed protected dashboard URL unavailable")
    parsed = urlsplit(line.strip().split(": ",1)[1])
    fragment = parse_qs(parsed.fragment)
    check(len(fragment.get("write_token",[])) == 1
          and len(fragment["write_token"][0]) >= 40,
          "private UI token missing")
    generation = fragment.get("generation_token", [])
    check((len(generation) == 1 and len(generation[0]) >= 40)
          if enable_generation else not generation,
          "generation token availability does not match opt-in")
    check(child.poll() is None, "protected product exited after readiness")
    return child, fragment["write_token"][0], (
        generation[0] if generation else None
    )


def shutdown(child):
    if child.poll() is None:
        child.send_signal(signal.SIGTERM)
    try:
        code = child.wait(timeout=70)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(5)
        raise AssertionError("protected shutdown exceeded bounded drain")
    check(code == 0, "protected role shutdown did not drain")
    child.stdout.close()
    child.stderr.close()



def protected_cold_pair_restore(root, scratch, binary, cdp):
    """Root-owned installed Wheel: stopped trusted pair -> new safe state root.

    The public origin, actor-held drafts, live/legacy host cutover and paid
    provider are deliberately outside this cold new-target acceptance.
    """
    pair_binary = Path(sys.executable).with_name("kleinanzeiger-protected-pair")
    check(pair_binary.is_file() and os.access(pair_binary, os.X_OK),
          "installed protected pair command missing")
    archive = Path(scratch) / "protected-pair-archive"
    restored_root = Path(scratch) / "protected-pair-restored"
    source_db = root / "owner-data" / "snapshot.sqlite"
    source_inode = (source_db.stat().st_dev, source_db.stat().st_ino)

    def pair(*args):
        return subprocess.run(
            [str(pair_binary), *args],
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
                 "LANG": "C.UTF-8"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=90, check=False,
        )

    made = pair("backup", "--state-root", str(root),
                "--backup-root", str(archive))
    if made.returncode != 0 or made.stderr:
        # Synthetically provisioned disposable root only. Observe the same
        # read-only admission gates without retrying any backup effect.
        from mark_api import protected_pair
        gates = (
            ("root", protected_pair.product._root_admission),
            ("uids", protected_pair.product._uids_free),
            ("attest", lambda: protected_pair.product._attest_existing(root)),
            ("sidecars", lambda: protected_pair._no_journals(root)),
            ("media", lambda: protected_pair._no_actor_media(root)),
        )
        causes = []
        for label, action in gates:
            try:
                action()
            except Exception as error:
                causes.append(label + ":" + type(error).__name__
                              + ":" + str(error)[:80])
        # All checks concern a disposable fixture. Report only bounded
        # protocol milestones, never a customer path or database contents.
        milestones = {
            "sqlite": archive / "cold" / "mark.sqlite",
            "witness": archive / "cold" / "witness.sqlite",
            "lock": archive / "cold" / "witness.sqlite.lock",
            "intent": archive / "intents" / ".intent.lock",
            "prepared": archive / "intents",
            "operator": archive / "anchor" / ".authority.lock",
            "head": archive / "anchor" / "latest.json",
            "marker": archive / ".protected-pair-v1.json",
        }
        causes.append("staged=" + ",".join(
            key for key, item in milestones.items()
            if (
                any(item.glob("*.prepared.json"))
                if key == "prepared"
                else item.exists()
            )
        ))
        if milestones["sqlite"].exists() and milestones["witness"].exists():
            try:
                from mark_api.paired_restore_preflight import inspect_offline_pair
                inspect_offline_pair(
                    milestones["sqlite"], milestones["witness"]
                )
            except Exception as error:
                causes.append("pair=" + type(error).__name__ +
                              ":" + str(error)[:48])
        raise AssertionError(
            "installed protected cold backup unavailable: "
            + (";".join(causes) if causes else "no read-only admission error")
        )
    saved = json.loads(made.stdout)
    check(saved.get("backup_complete") is True
          and saved.get("restore_authorized") is False
          and len(saved.get("head_sha256", "")) == 64,
          "protected pair backup did not return one independently anchored HEAD")
    check((source_db.stat().st_dev, source_db.stat().st_ino) == source_inode,
          "cold backup replaced existing customer SQLite inode")
    duplicate = pair("backup", "--state-root", str(root),
                     "--backup-root", str(archive))
    check(duplicate.returncode == 2 and not duplicate.stdout,
          "protected backup silently overwrote a prior sealed generation")

    restored = pair(
        "restore", "--backup-root", str(archive),
        "--new-state-root", str(restored_root),
        "--expected-head-sha256", saved["head_sha256"],
    )
    check(restored.returncode == 0 and not restored.stderr,
          "installed protected cold restore into NEW store unavailable")
    loaded = json.loads(restored.stdout)
    check(loaded.get("restored_to_new_store") is True
          and loaded.get("no_platform_write_performed") is True,
          "protected restore claimed incorrect customer outcome")
    restored_db = restored_root / "owner-data" / "snapshot.sqlite"
    check(restored_db.stat().st_uid == UIDS["owner"]
          and stat.S_IMODE(restored_db.stat().st_mode) == 0o600,
          "restored protected SQLite lost its independent owner UID")
    duplicate_restore = pair(
        "restore", "--backup-root", str(archive),
        "--new-state-root", str(restored_root),
        "--expected-head-sha256", saved["head_sha256"],
    )
    check(duplicate_restore.returncode == 2 and not duplicate_restore.stdout,
          "protected restore overwrote existing customer data")

    # The separately provisioned restored owner/witness pair must start via
    # the SAME installed protected customer binary without --init-new.
    restored_child = None
    try:
        restored_child, _, _ = start(
            binary, restored_root, init_new=False, sync_interval=0,
        )
        dashboard_ready()
        inventory_confirmed(cdp)
    finally:
        if restored_child is not None:
            shutdown(restored_child)




def admission(net_before, pid_before, mount_before):
    check(sys.platform == "linux" and os.geteuid() == 0
          and os.getpid() == 1, "root isolated PID1 required")
    check(os.readlink("/proc/self/ns/net") != net_before
          and os.readlink("/proc/self/ns/pid") != pid_before
          and os.readlink("/proc/self/ns/mnt") != mount_before,
          "test must not use any host PID/network/mount namespace")
    image = Path(mark_api.__file__).resolve()
    check("site-packages" in image.parts and image.stat().st_uid == 0,
          "test must execute root-owned installed wheel")
    binary = Path(sys.executable).with_name("kleinanzeiger-protected")
    check(binary.is_file() and os.access(binary,os.X_OK),
          "new installed product start executable missing")

    with tempfile.TemporaryDirectory(prefix="kleinanzeiger-new-product-") as scratch:
        os.chmod(scratch, 0o755)
        root = Path(scratch) / "new-install"
        certificate, key = _setup_isolated_synthetic_tls(scratch, mount_before)
        public_gallery = SyntheticPublicMediaGallery(certificate, key)
        gallery_worker = Thread(target=public_gallery.serve_forever, daemon=True)
        gallery_worker.start()
        child = None
        cdp = SyntheticCDP()
        cdp.gallery = public_gallery
        worker = Thread(target=cdp.serve_forever, daemon=True)
        worker.start()
        try:
            # Real production CDP transport: verify this test-only wire server
            # before interpreting an actor/owner sync failure as product error.
            from mark_api.private_web_inventory_cdp import CdpManagementInventoryReader
            from mark_api.results import ReadStatus
            probe = CdpManagementInventoryReader(cdp_port=19322)
            observed = probe.read_ads()
            probe.close()
            check(observed.status is ReadStatus.SUCCESS_NONEMPTY,
                  "synthetic CDP peer protocol failed: " + observed.status.value)
            child, first_token, absent_gen = start(
                binary, root, init_new=True, sync_interval=60,
            )
            check(absent_gen is None, "default product minted unwanted paid provider credential")
            dashboard_ready()
            inventory_confirmed(cdp)
            synthetic_state_write(cdp, first_token, "pause")
            check(cdp.state == "paused", "CDP pause did not change synthetic inventory")
            synthetic_state_write(cdp, first_token, "activate")
            check(cdp.state == "active", "CDP activate did not restore inventory")
            synthetic_content_write(cdp, first_token)
            roles = child_roles(child.pid)
            host_net = os.readlink("/proc/self/ns/net")
            namespaces = {
                role: os.readlink(f"/proc/{pid}/ns/net")
                for role, pid in roles.items()
            }
            check(namespaces["actor"] == host_net and namespaces["ingress"] == host_net,
                  "Actor must have host-CDP while Landlock ingress has host loopback")
            check(len({namespaces[k] for k in ("owner","frontend","witness")}) == 1
                  and namespaces["owner"] != host_net,
                  "untrusted UI and owner not confined to one private netns")
            db = root / "owner-data" / "snapshot.sqlite"
            witness = root / "witness-data" / "witness.sqlite"
            dbinfo = db.lstat()
            check(dbinfo.st_uid == UIDS["owner"]
                  and stat.S_IMODE(dbinfo.st_mode) == 0o600,
                  "fresh owner SQLite not private")
            check(witness.stat().st_uid == UIDS["witness"],
                  "fresh Witness ledger not independently owned")
            hostile_uid_direct_storage_denied(db, witness)
            authenticated_media_readback_witness_probe(root / "w-actor" / "w.sock")
            first_inode = (dbinfo.st_dev, dbinfo.st_ino)
            shutdown(child)
            child = None
            for role, pid in roles.items():
                check(not Path(f"/proc/{pid}").exists(),
                      "old protected role still active after successful shutdown")
            protected_cold_pair_restore(root, scratch, binary, cdp)

            child, second_token, absent_gen = start(
                binary, root, init_new=False, sync_interval=60,
            )
            check(absent_gen is None, "restarted product minted unwanted provider credential")
            inventory_confirmed(cdp)
            check(first_token != second_token, "restart reused customer UI credential")
            dashboard_ready()
            check((db.stat().st_dev, db.stat().st_ino) == first_inode,
                  "restart replaced a customer's SQLite inode")
            synthetic_delete_write(cdp, second_token)
            synthetic_create_write(cdp, second_token)
            shutdown(child)
            child = None

            # Reusing --init-new with an existing store must fail BEFORE
            # opening/replacing data, not become a migration/restore shortcut.
            invalid = subprocess.run(
                [str(binary), "--state-root", str(root), "--init-new",
                 "--cdp-port", "19322"], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=15, check=False,
            )
            check(invalid.returncode == 2 and not invalid.stdout,
                  "old owner data accepted as an untracked fresh install")
            check((db.stat().st_dev, db.stat().st_ino) == first_inode,
                  "invalid fresh attempt changed existing SQLite")
            # Opt-in on the SAME protected customer store: six real UID roles,
            # frontend/SQLite remain netless while only generator owns egress.
            child, third_token, generation_token = start(
                binary, root, init_new=False, enable_generation=True,
            )
            check(third_token not in (first_token, second_token)
                  and generation_token is not None,
                  "new protected AI session reused private frontend credentials")
            dashboard_ready()
            roles_with_ai = child_roles(child.pid, include_generation=True)
            ai_nets = {
                role: os.readlink(f"/proc/{pid}/ns/net")
                for role, pid in roles_with_ai.items()
            }
            current_net = os.readlink("/proc/self/ns/net")
            check(ai_nets["generation"] == current_net
                  and ai_nets["actor"] == current_net
                  and ai_nets["ingress"] == current_net,
                  "isolated generator does not have its own host-network UID")
            check(len({ai_nets[k] for k in ("owner", "frontend", "witness")}) == 1
                  and ai_nets["frontend"] != current_net,
                  "AI opt-in escaped frontend/owner network isolation")
            check((db.stat().st_dev, db.stat().st_ino) == first_inode,
                  "AI opt-in replaced existing customer SQLite")
            media_preflight_clicks = synthetic_ai_generation_drafts(
                generation_token, third_token, cdp,
            )
            shutdown(child)
            child = None
        finally:
            if child is not None:
                child.kill()
                child.wait(5)
                child.stdout.close()
                child.stderr.close()
            cdp.shutdown()
            cdp.server_close()
            worker.join(3)
            public_gallery.shutdown()
            public_gallery.server_close()
            gallery_worker.join(3)
    check(cdp.commands.count("Runtime.evaluate") >= 10
          and set(cdp.commands) == {
              "Runtime.evaluate", "Runtime.callFunctionOn",
              "Runtime.releaseObject", "DOM.setFileInputFiles",
              "Page.navigate", "Input.dispatchMouseEvent",
          } and cdp.presses == 9 + media_preflight_clicks
          and cdp.releases == 9 + media_preflight_clicks
          and cdp.content_submits == 1 and cdp.delete_submits == 1
          and cdp.create_submits == 2
          and cdp.media_file_set_calls == 1
          and cdp.media_publish_submits == 1
          and public_gallery.media_uploads == 1
          and public_gallery.listing_gets >= 1
          and public_gallery.image_gets >= 1,
          "installed actor did not perform state, content, delete and publish CDP submits")
    print(json.dumps({
        "result":"PASS", "scope":"installed-protected-fresh-customer-start",
        "real_owner_cli":True,"real_actor_cli":True,
        "real_witness_cli":True,"real_frontend_cli":True,
        "uid_isolation":True,"netless_frontend_owner":True,
        "fresh_hostile_uid_storage_denial_attested":True,
        "landlock_ingress":True,"dashboard_http":True,
        "restarted_without_db_recreation":True,
        "legacy_cutover_attested":False,"real_cdp_write_attested":False,
        "real_cdp_inventory_sync_attested":True,
        "protected_cdp_synthetic_state_write_attested":True,
        "protected_cdp_synthetic_content_write_attested":True,
        "protected_cdp_synthetic_delete_write_attested":True,
        "protected_cdp_synthetic_create_write_attested":True,
        "protected_synthetic_ai_generation_attested":True,
        "protected_ai_generated_media_stage_attested":True,
        "protected_cdp_media_positive_https_postread_attested":True,
        "protected_cdp_media_replay_no_duplicate":True,
        "synthetic_public_https_tls_gallery":True,
        "paid_generation_called":False,
    }, sort_keys=True, separators=(",",":")),flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-net",required=True)
    parser.add_argument("--host-pid",required=True)
    parser.add_argument("--host-mnt",required=True)
    args = parser.parse_args()
    admission(args.host_net,args.host_pid,args.host_mnt)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as error:
        # Every assertion message is one of this file's fixed, nonsensitive
        # admission labels or the product's own restricted static phase.
        print("Kleinanzeiger protected installed start acceptance: FAIL",
              str(error)[:190], file=sys.stderr)
        raise SystemExit(1)
    except BaseException as error:
        print("Kleinanzeiger protected installed start acceptance: FAIL",
              type(error).__name__, file=sys.stderr)
        raise SystemExit(1)