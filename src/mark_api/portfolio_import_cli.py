"""Native Windows customer CSV/XLSX portfolio import; strictly local, draft-only."""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
from pathlib import Path
import sqlite3
import sys

from .container_draft import container_draft_denied
from .portfolio_drafts import (
    DraftBatchError, DraftImportError, drafts_page, export_drafts, import_drafts, preview_import,
)
from .storage import SnapshotStore


def main(argv: list[str] | None = None) -> int:
    if container_draft_denied():
        print("mark-api: unattested container draft cannot run Python entrypoints", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description="CSV/XLSX-Artikelentwürfe lokal prüfen, importieren oder exportieren")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--mode", choices=("preview", "import", "export", "list"), required=True)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--expected-sha256", default="")
    parser.add_argument("--after", default="")
    parser.add_argument("--search", default="")
    args = parser.parse_args(argv)
    if args.mode != "list":
        data = os.environ.pop("KLEINANZEIGER_IMPORT_REQUEST", "")
        if data:
            try:
                if len(data) > 32768:
                    raise ValueError
                payload = json.loads(base64.b64decode(data.encode("ascii"), validate=True).decode("utf-8"))
                if type(payload) is not dict or not set(payload).issubset({"path", "sha256"}):
                    raise ValueError
                if type(payload.get("path")) is not str:
                    raise ValueError
                if "sha256" in payload and type(payload["sha256"]) is not str:
                    raise ValueError
                args.file = Path(payload["path"])
                args.expected_sha256 = payload.get("sha256", "")
            except (UnicodeError, ValueError, TypeError, binascii.Error):
                print('{"status":"error","error":"invalid_import_request"}')
                return 2
        if args.file is None:
            parser.error("CSV/XLSX-Datei erforderlich")
    try:
        store = SnapshotStore(args.db, create_if_missing=False, require_existing_schema=True)
        if args.mode == "preview":
            response = preview_import(store, args.file)
        elif args.mode == "import":
            response = import_drafts(store, args.file, args.expected_sha256)
        elif args.mode == "export":
            response = export_drafts(store, args.file)
        else:
            response = drafts_page(store, after=args.after, search=args.search)
    except DraftImportError as exc:
        response = {"status": "invalid", "issues": (
            exc.issues if isinstance(exc, DraftBatchError) else [exc.as_issue()]
        )}
        print(json.dumps(response, ensure_ascii=True, separators=(",", ":")))
        return 2 if args.mode != "preview" else 0
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError):
        print('{"status":"error","error":"local_database_unavailable"}')
        return 2
    print(json.dumps(response, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())