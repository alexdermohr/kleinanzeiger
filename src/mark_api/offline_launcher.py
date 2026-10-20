from __future__ import annotations

import argparse
import os
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import quote

from .analytics import ANALYTICS_METRICS, REACTION_METRICS, AnalyticsContract
from .dashboard import DashboardGenerationAccess, create_server
from .email_import import import_kleinanzeigen_email_files
from .storage import SnapshotStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Mark with local files and a read-only loopback dashboard. "
            "Never connect to Kleinanzeigen, CDP, or a platform Write API."
        )
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Explicitly create a new local SQLite database.",
    )
    parser.add_argument(
        "--email",
        dest="emails",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="Import an explicitly supplied local RFC822/.eml notification.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Read-only dashboard port on 127.0.0.1 (default: 8765).",
    )
    parser.add_argument(
        "--reaction-metric",
        choices=REACTION_METRICS,
        default=None,
    )
    parser.add_argument(
        "--objective-metric",
        choices=ANALYTICS_METRICS,
        default=None,
    )
    parser.add_argument(
        "--enable-generation",
        action="store_true",
        help=(
            "Enable separately consented text/image drafts using an external "
            "provider; requires mark-api[generation] and OPENAI_API_KEY."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Use only explicitly supplied offline data; no browser/API runtime exists."""
    parser = _parser()
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be an integer between 0 and 65535")

    generation_access = None
    if args.enable_generation:
        if not os.environ.get("OPENAI_API_KEY"):
            parser.error("--enable-generation requires OPENAI_API_KEY in the local process environment")
        # Validate optional dependency before any database creation/import or
        # network bind. Importing the SDK neither creates a client nor a call.
        try:
            from openai import OpenAI as _GenerationSdkClient
        except Exception:
            parser.error("--enable-generation requires installed mark-api[generation]")
        if not callable(_GenerationSdkClient):
            parser.error("--enable-generation requires installed mark-api[generation]")
        generation_access = DashboardGenerationAccess(ui_token=secrets.token_urlsafe(32))

    contract = AnalyticsContract(
        reaction_metric=args.reaction_metric,
        objective_metric=args.objective_metric,
    )
    try:
        store = SnapshotStore(args.db, create_if_missing=args.init_db)
    except (OSError, sqlite3.Error):
        parser.error("SQLite database unavailable or not initialized")

    # A failed batch never opens a dashboard. Parsing and event import are
    # delegated to the existing batch-atomic, idempotent local importer.
    if args.emails:
        try:
            import_report = import_kleinanzeigen_email_files(store, args.emails)
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            parser.error(f"local email import failed: {exc}")
    else:
        import_report = None

    try:
        # Omit write_proxy deliberately: no write routes, CDP, browser
        # inventory, bearer tokens, or platform network clients are constructed.
        server = create_server(
            store,
            port=args.port,
            analytics_contract=contract,
            **(
                {"generation_access": generation_access}
                if generation_access is not None else {}
            ),
        )
    except (OSError, ValueError) as exc:
        parser.error(f"read-only dashboard could not start: {exc}")

    host, port = server.server_address
    token_fragment = (
        "#generation_token=" + quote(generation_access.ui_token, safe="")
        if generation_access is not None else ""
    )
    print(f"Mark offline dashboard: http://{host}:{port}/{token_fragment}", flush=True)
    print("Platform sync and writes: disabled (local files only).", flush=True)
    if generation_access is not None:
        print("Draft generation: opt-in, separate consent and possible provider costs per request.", flush=True)
    if import_report is not None:
        print(
            "Local email import: "
            f"{import_report.parsed_files} file(s), "
            f"{import_report.inserted_events} new event(s), "
            f"{import_report.duplicate_events} duplicate(s).",
            flush=True,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())