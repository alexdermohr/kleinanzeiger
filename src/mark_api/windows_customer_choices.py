"""Existing-data-only choices for the Windows customer control center.

This command never initializes a missing database, runs a platform action,
or grants Write authority. Only user-local, previously evidenced ad IDs
(including email-only IDs) can become classification targets.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .query import MarkQueryService
from .storage import SnapshotStore

_LABEL_FIELDS = ("image_type", "city", "text_type", "title_type")


def list_customer_choices(db: Path) -> list[dict[str, object]]:
    store = SnapshotStore(db, create_if_missing=False, require_existing_schema=True)
    query = MarkQueryService(store)
    by_id: dict[str, dict[str, object]] = {}
    for ad in query.latest_ads():
        by_id[ad.ad_id] = {
            "ad_id": ad.ad_id,
            "title": ad.title or "",
            "email_only": False,
            "labels": {},
        }
    for item in query.email_reactions():
        by_id.setdefault(
            item.ad_id,
            {"ad_id": item.ad_id, "title": "", "email_only": True, "labels": {}},
        )
    for ad_id, record in by_id.items():
        latest = store.latest_classification_bounded(ad_id)
        record["labels"] = {
            field: getattr(latest, field) if latest is not None else None
            for field in _LABEL_FIELDS
        }
    return sorted(by_id.values(), key=lambda row: str(row["ad_id"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List locally evidenced Windows ad choices")
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        rows = list_customer_choices(args.db)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        print("Kleinanzeiger: vorhandene Anzeigen konnten nicht gelesen werden.", file=sys.stderr)
        return 2
    print(json.dumps({"ads": rows}, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
