"""Native NixOS/KDE launcher: separate Chrome profile and real dashboard URL.

The legacy browser profile is never modified. Login and ownership checks belong
to the existing product runtime, not this convenience launcher.
"""
from __future__ import annotations

import argparse
from importlib.metadata import version
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import urlopen


_HOME = Path.home()
_ROOT = _HOME / ".local" / "share" / "kleinanzeiger"
_DB = _ROOT / "data" / "portfolio.sqlite3"
# A separate profile avoids reopening the previous Chrome crash-state profile.
# Do not rename, purge, import cookies from or otherwise alter chrome-profile.
_PROFILE = _ROOT / "chrome-profile-v2"
_OLD_PROFILE = _ROOT / "chrome-profile"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _is_cdp_ready(port: int) -> bool:
    if not 1 <= port <= 65535:
        return False
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            if response.status != 200:
                return False
            data = response.read(12000)
        return b'"Browser"' in data and (b"Chrome/" in data or b"HeadlessChrome/" in data)
    except (OSError, ValueError, TimeoutError):
        return False


def _existing_chrome_port(profile: Path) -> int | None:
    """Return live CDP port, None for no running profile, 0 for unknown/busy.

    Only inspect our own user and exact app-owned directory. Never kill a
    browser or remove Chrome singleton files based on an untrusted status.
    """
    lock = profile / "SingletonLock"
    if not lock.is_symlink():
        return None
    try:
        value = os.readlink(lock)
        prefix = socket.gethostname() + "-"
        if not value.startswith(prefix) or not value[len(prefix):].isdecimal():
            return 0
        pid = value[len(prefix):]
        proc = Path("/proc") / pid
        if not proc.exists():  # Chrome owns stale-lock recovery, not this app.
            return None
        if proc.stat().st_uid != os.getuid():
            return 0
        cmdline = (proc / "cmdline").read_bytes().split(b"\0")
        if b"--user-data-dir=" + os.fsencode(profile) not in cmdline:
            return 0
        for arg in cmdline:
            match = re.fullmatch(rb"--remote-debugging-port=(\d{1,5})", arg)
            if match:
                port = int(match.group(1))
                return port if _is_cdp_ready(port) else 0
        return 0
    except (OSError, ValueError):
        return 0


def _start_chrome(profile: Path) -> int:
    chrome = shutil.which("google-chrome")
    if not chrome:
        raise RuntimeError("Google Chrome ist nicht installiert.")
    if profile.is_symlink() or (profile.exists() and not profile.is_dir()):
        raise RuntimeError("Ungültiges Kleinanzeiger-Chrome-Profil.")
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = _existing_chrome_port(profile)
    if existing == 0:
        raise RuntimeError("Das Kleinanzeiger-Chrome-Profil ist belegt, aber nicht steuerbar.")
    if existing is not None:
        return existing
    port = _free_port()
    state = _HOME / ".local" / "state" / "kleinanzeiger"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    logfile = state / "chrome-start.log"
    fd = os.open(logfile, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        with os.fdopen(fd, "ab", buffering=0) as stderr_log:
            process = subprocess.Popen(
                [chrome, f"--remote-debugging-port={port}",
                 "--remote-debugging-address=127.0.0.1",
                 f"--user-data-dir={profile}", "--no-first-run",
                 "--no-default-browser-check",
                 "https://www.kleinanzeigen.de/m-meine-anzeigen.html"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=stderr_log,
            )
        for _ in range(70):
            if _is_cdp_ready(port):
                return port
            if process.poll() is not None:
                raise RuntimeError(
                    "Chrome wurde beim Start beendet. Siehe ~/.local/state/kleinanzeiger/chrome-start.log."
                )
            time.sleep(0.25)
    except Exception:
        raise
    raise RuntimeError("Chrome-Steuerung nicht bereit; das normale Chrome-Profil bleibt unverändert.")


def _dashboard_url(value: str, port: int) -> str | None:
    """Accept only a URL printed by this product for the bound loopback port.

    Preserve the fragment with its session-scoped write token for the browser;
    never log the token or replace it with a token-less localhost URL.
    """
    match = re.search(r"http://127\.0\.0\.1:\d+/[^\s]*", value)
    if not match:
        return None
    url = match.group(0)
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.port != port:
        return None
    if not parts.path.startswith("/"):
        return None
    return url


def _open_dashboard(port: int, process: subprocess.Popen[str]) -> bool:
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + 35
    try:
        while time.monotonic() < deadline and process.poll() is None:
            if not selector.select(timeout=0.4):
                continue
            message = process.stdout.readline()
            if not message:
                continue
            url = _dashboard_url(message, port)
            if url:
                with urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
                    if response.status != 200:
                        return False
                opener = shutil.which("xdg-open")
                if opener:
                    subprocess.Popen([opener, url], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print("Kleinanzeiger-Dashboard gestartet: http://127.0.0.1:", port, sep="")
                return True
            # Error lines are not printed verbatim; they may include provider data.
        return False
    finally:
        selector.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Kleinanzeiger für NixOS/KDE starten.")
    parser.add_argument("mode", nargs="?", default="konto", choices=("konto", "offline"))
    parser.add_argument("--check", action="store_true", help="Installation prüfen, nicht starten")
    opts = parser.parse_args()
    installed = version("kleinanzeiger")
    if opts.check:
        print("Kleinanzeiger", installed, "bereit")
        return 0
    if (_ROOT.is_symlink() or _DB.parent.is_symlink() or _DB.is_symlink()
            or (_DB.exists() and not _DB.is_file())):
        parser.error("Ungültiger oder verknüpfter Datenbankpfad.")
    _DB.parent.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    if opts.mode == "konto":
        try:
            cdp_port = _start_chrome(_PROFILE)
        except RuntimeError as exc:
            parser.error(str(exc))
        argv = [sys.executable, "-m", "kleinanzeiger", "launch",
                "--db", str(_DB), "--cdp-port", str(cdp_port),
                "--dashboard-port", str(port), "--write-port", "0"]
    else:
        argv = [sys.executable, "-m", "kleinanzeiger", "offline",
                "--db", str(_DB), "--port", str(port)]
    if not _DB.exists():
        argv.append("--init-db")
    if opts.mode == "konto" and os.environ.get("OPENAI_API_KEY"):
        argv.append("--enable-generation")
    child = subprocess.Popen(argv, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        if not _open_dashboard(port, child):
            print("Kleinanzeiger konnte nicht gestartet werden; Anmeldung/Chrome prüfen.",
                  file=sys.stderr)
            return child.poll() or 3
        return child.wait()
    except KeyboardInterrupt:
        print("Kleinanzeiger wird beendet.")
        return 130
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                print("Kleinanzeiger läuft noch; bitte das Fenster prüfen.", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
