"""Stable Windows control bridge for older offline-launcher wheels.

The Windows hub survives a .venv-only rollback. Older offline_launcher modules
do not parse --stop-on-stdin, but expose create_server and main. This script
adds the same private STOP/EOF control at the stable Windows script layer;
it never imports a browser, marketplace client, or platform writer.
"""
from __future__ import annotations

import sys
from threading import Thread

from mark_api import offline_launcher


def main() -> int:
    original_create_server = offline_launcher.create_server

    def controlled_server(*args: object, **kwargs: object) -> object:
        server = original_create_server(*args, **kwargs)

        def watch_owner_pipe() -> None:
            try:
                for line in sys.stdin:
                    if line.strip() == "STOP":
                        break
            except (OSError, ValueError):
                pass
            # Called from a different thread than serve_forever, exactly as
            # BaseServer.shutdown requires. EOF also releases abandoned GUIs.
            server.shutdown()

        Thread(target=watch_owner_pipe, daemon=True).start()
        return server

    offline_launcher.create_server = controlled_server
    # Pass through only legacy-compatible arguments (--db, --init-db, ...).
    # Never append --stop-on-stdin: an older wheel rejects it at parse time.
    return offline_launcher.main()


if __name__ == "__main__":
    raise SystemExit(main())
