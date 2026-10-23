"""Protected installed A6 dashboard. A trusted supervisor must provision the
distinct actor, owner and witness UIDs; this module grants no host cutover.
No direct SQLite, local completion bypass or platform login is permitted.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import stat
import sys
from threading import Thread

from .completion_dashboard import CompletionDashboardClient
from .container_draft import container_draft_denied
from .dashboard import DashboardWriteProxy, create_server
from .storage_owner import StorageOwnerError
from .strict_json import strict_json_object

_FIELDS = frozenset(("owner_socket", "owner_uid", "principal", "owner_port",
                     "api_token", "ui_token", "dashboard_port"))
_LIMIT = 8192


def create_protected_frontend_server(
    *, owner_socket: Path, owner_uid: int, principal: str,
    owner_port: int, api_token: str, ui_token: str, dashboard_port: int = 8765,
):
    """Use the real strict owner/Witness-backed Write proxy, never a test seam."""
    if not isinstance(owner_socket, Path) or not owner_socket.is_absolute():
        raise ValueError("absolute independent owner socket required")
    client = CompletionDashboardClient(
        owner_socket, expected_owner_uid=owner_uid, expected_principal=principal,
    )
    proxy = DashboardWriteProxy(
        host="127.0.0.1", port=owner_port, bearer_token=api_token,
        ui_token=ui_token, backend_principal=principal,
        owner_reconciliation_enabled=True,
    )
    return create_server(client, port=dashboard_port, write_proxy=proxy)


def _read_private_config(fd: int) -> dict[str, object]:
    """Get secrets only over one bounded inherited private pipe/file FD."""
    if type(fd) is not int or fd < 0:
        raise ValueError("private configuration descriptor required")
    info = os.fstat(fd)
    if stat.S_ISREG(info.st_mode):
        if stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid not in (0, os.geteuid()):
            raise ValueError("private configuration descriptor required")
    elif not stat.S_ISFIFO(info.st_mode):
        raise ValueError("private configuration descriptor required")
    with os.fdopen(os.dup(fd), "rb") as source:
        raw = source.read(_LIMIT + 1)
    if (len(raw) < 3 or len(raw) > _LIMIT
            or not raw.endswith(b"\n") or raw.count(b"\n") != 1):
        raise ValueError("invalid protected frontend configuration")
    config = strict_json_object(raw[:-1])
    if set(config) != _FIELDS or type(config["owner_socket"]) is not str:
        raise ValueError("invalid protected frontend configuration")
    if not config["owner_socket"].startswith("/"):
        raise ValueError("invalid protected frontend configuration")
    if any(type(config[field]) is not int
           for field in ("owner_uid", "owner_port", "dashboard_port")):
        raise ValueError("invalid protected frontend configuration")
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Kleinanzeiger protected frontend for a preprovisioned A6 owner")
    parser.add_argument("--config-fd", type=int, default=0,
                        help="Inherited private configuration FD (no secrets in argv)")
    args = parser.parse_args(argv)
    # Only bounded, nonsensitive startup phases may be printed. Socket paths,
    # configuration bytes, tokens and exception strings are never logged.
    if container_draft_denied():
        print("Kleinanzeiger: protected frontend unavailable (draft denied)",
              file=sys.stderr)
        return 2
    try:
        config = _read_private_config(args.config_fd)
    except (ValueError, TypeError, OSError):
        print("Kleinanzeiger: protected frontend unavailable (config rejected)",
              file=sys.stderr)
        return 2
    try:
        server = create_protected_frontend_server(
            owner_socket=Path(config["owner_socket"]),
            owner_uid=config["owner_uid"], principal=config["principal"],
            owner_port=config["owner_port"], api_token=config["api_token"],
            ui_token=config["ui_token"], dashboard_port=config["dashboard_port"],
        )
    except (ValueError, TypeError, OSError, StorageOwnerError):
        print("Kleinanzeiger: protected frontend unavailable (owner unavailable)",
              file=sys.stderr)
        return 2

    def stop(_sig, _frame):
        Thread(target=server.shutdown, daemon=True).start()

    old = signal.signal(signal.SIGTERM, stop)
    try:
        # No backend bearer or UI token in argv, URLs, logs or readiness output.
        print(json.dumps({"ready": True, "uid": os.geteuid(), "port": server.server_port},
                         separators=(",", ":")), flush=True)
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, old)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
