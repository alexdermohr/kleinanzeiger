"""Local customer ad choices and explicitly requested classification updates.

Never creates a missing database, contacts the platform, or grants platform
Write authority. Label writes reuse the existing append-only classification
contract and accept private UTF-8 JSON through base64-encoded standard input.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
from pathlib import Path
import sqlite3
import sys

from .classification_cli import update_classification
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


def apply_customer_labels(db: Path, encoded: str) -> None:
    """Preserve quotes, slashes, and Unicode without PS5.1 argv round-trips."""
    if not isinstance(encoded, str) or not encoded or len(encoded) > 65536:
        raise ValueError("invalid classification input")
    try:
        body = base64.b64decode(encoded.encode("ascii"), validate=True)
        if len(body) > 32768:
            raise ValueError("input too large")
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, binascii.Error, ValueError, TypeError):
        raise ValueError("invalid classification input") from None
    if type(payload) is not dict or set(payload) != {"ad_id", "labels"}:
        raise ValueError("invalid classification shape")
    ad_id = payload["ad_id"]
    values = payload["labels"]
    if type(ad_id) is not str or not ad_id.strip() or len(ad_id) > 160:
        raise ValueError("invalid classification id")
    if type(values) is not dict or not values or set(values) - set(_LABEL_FIELDS):
        raise ValueError("invalid classification fields")
    labels: dict[str, str] = {}
    clears: list[str] = []
    for name, value in values.items():
        if value is not None and type(value) is not str:
            raise ValueError("invalid label")
        if value is None or not value.strip():
            clears.append(name)
        elif len(value) <= 120:
            labels[name] = value
        else:
            raise ValueError("label too long")
    store = SnapshotStore(db, create_if_missing=False, require_existing_schema=True)
    update_classification(store, ad_id=ad_id, labels=labels, clears=clears)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List locally evidenced Windows ad choices")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--apply-labels", action="store_true")
    args = parser.parse_args(argv)
    if args.apply_labels:
        try:
            # Controller -> worker uses private stdin; worker -> Python uses a
            # short-lived child environment entry to avoid Windows console
            # codepage and native-argument quote transformations.
            encoded = os.environ.pop("KLEINANZEIGER_LABEL_PAYLOAD", "")
            apply_customer_labels(args.db, encoded)
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            if (os.environ.get("GITHUB_ACTIONS") == "true"
                    and os.environ.get("KLEINANZEIGER_TEST_DIAG") == "1"):
                # Only exception type, never label values, paths or message body.
                print("CI_CLASSIFY_ERROR:" + type(exc).__name__, file=sys.stderr)
            print("Kleinanzeiger: Vergleichslabels wurden nicht gespeichert.", file=sys.stderr)
            return 2
        print('{"status":"saved"}')
        return 0
    try:
        rows = list_customer_choices(args.db)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        print("Kleinanzeiger: vorhandene Anzeigen konnten nicht gelesen werden.", file=sys.stderr)
        return 2
    print(json.dumps({"ads": rows}, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())