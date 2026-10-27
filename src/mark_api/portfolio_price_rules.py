"""G5: local, bounded price-rule suggestions for imported unpublished drafts.

Rules are independent of marketplace IDs and A6 write capabilities. A rule
update starts its own explicitly named waiting period; it is NOT evidence
of a platform price change. Nothing here writes a draft's price, connects to
a browser/provider, or authorizes marketplace price changes.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets
import sqlite3
from typing import Any

from .portfolio_drafts import _SKU, _has_table
from .storage import SnapshotStore


_REQUEST_KEY = re.compile(r"[0-9a-f]{64}\Z")
_MAX_RULES = 10_000
_MAX_PRICE = 99_999_999
_MAX_DAYS = 3650
_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_price_rules (
 sku TEXT PRIMARY KEY COLLATE BINARY NOT NULL,
 minimum_price_eur INTEGER NOT NULL CHECK(minimum_price_eur >= 1),
 reduction_step_eur INTEGER NOT NULL CHECK(reduction_step_eur >= 1),
 wait_days INTEGER NOT NULL CHECK(wait_days >= 0),
 revision INTEGER NOT NULL CHECK(revision >= 1),
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_price_rule_requests (
 request_key TEXT PRIMARY KEY COLLATE BINARY NOT NULL,
 intent_sha256 TEXT NOT NULL,
 response_json TEXT NOT NULL
);
"""
_COLUMNS = {
    "portfolio_price_rules": {
        "sku", "minimum_price_eur", "reduction_step_eur",
        "wait_days", "revision", "updated_at",
    },
    "portfolio_price_rule_requests": {
        "request_key", "intent_sha256", "response_json",
    },
}


class PriceRuleError(ValueError):
    """Invalid rule, stale revision, reused request identity or absent draft."""


def _sku(value: object) -> str:
    if type(value) is not str or _SKU.fullmatch(value) is None:
        raise PriceRuleError("Ungueltige lokale Artikelnummer.")
    return value


def _key(value: object) -> str:
    if type(value) is not str or _REQUEST_KEY.fullmatch(value) is None:
        raise PriceRuleError("Ungueltiger Anfrage-Schluessel.")
    return value


def _integer(value: object, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise PriceRuleError("Ungueltiger Wert fuer " + label + ".")
    return value


def _now(value: datetime | None) -> datetime:
    timestamp = datetime.now(timezone.utc) if value is None else value
    if (not isinstance(timestamp, datetime)
            or timestamp.tzinfo is None or timestamp.utcoffset() is None):
        raise ValueError("Ein Zeitpunkt mit Zeitzone ist erforderlich.")
    return timestamp.astimezone(timezone.utc)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _schema(conn: sqlite3.Connection, *, create: bool) -> bool:
    found = {
        item["name"]: item["type"] for item in conn.execute(
            "SELECT name,type FROM sqlite_master WHERE name IN "
            "('portfolio_price_rules','portfolio_price_rule_requests')"
        )
    }
    if not found:
        if not create:
            return False
        for statement in _DDL.split(";"):
            if statement.strip():
                conn.execute(statement)
        found = {table: "table" for table in _COLUMNS}
    if found != {name: "table" for name in _COLUMNS}:
        raise sqlite3.DatabaseError("invalid partial price rules schema")
    for table, expected in _COLUMNS.items():
        if {row["name"] for row in conn.execute(
            f"PRAGMA table_info({table})"
        )} != expected:
            raise sqlite3.DatabaseError("invalid price rules columns")
    return True


def _draft_price(conn: sqlite3.Connection, sku: str) -> int:
    if not _has_table(conn):
        raise PriceRuleError("Lokaler Artikelentwurf fehlt.")
    row = conn.execute(
        "SELECT price_eur,status FROM portfolio_drafts "
        "WHERE sku=? COLLATE BINARY", (sku,),
    ).fetchone()
    if row is None:
        raise PriceRuleError("Lokaler Artikelentwurf fehlt.")
    if row["status"] != "draft" or type(row["price_eur"]) is not int or not (
        1 <= row["price_eur"] <= _MAX_PRICE
    ):
        raise sqlite3.DatabaseError("invalid draft price or state")
    return row["price_eur"]


def _replay(conn: sqlite3.Connection, key: str, digest: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT intent_sha256,response_json FROM portfolio_price_rule_requests "
        "WHERE request_key=?", (key,),
    ).fetchone()
    if row is None:
        return None
    if row["intent_sha256"] != digest:
        raise PriceRuleError("Anfrage-Schluessel wurde anders verwendet.")
    return json.loads(row["response_json"])


def _record(conn: sqlite3.Connection, key: str, digest: str, result: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO portfolio_price_rule_requests VALUES (?,?,?)",
        (key, digest, json.dumps(result, ensure_ascii=True, sort_keys=True)),
    )


def set_price_rule(
    store: SnapshotStore, *, sku: str, minimum_price_eur: int,
    reduction_step_eur: int, wait_days: int, expected_revision: int,
    request_key: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Set/update a draft-only rule with replay protection and revision CAS.

    The waiting period starts when the rule is saved, NOT at an unobserved
    marketplace price event. Saving cannot mutate the source draft price.
    """
    item = _sku(sku)
    minimum = _integer(minimum_price_eur, "Mindestpreis", 1, _MAX_PRICE)
    step = _integer(reduction_step_eur, "Preisschritt", 1, _MAX_PRICE)
    days = _integer(wait_days, "Wartezeit", 0, _MAX_DAYS)
    revision = _integer(expected_revision, "Revision", 0, 1_000_000)
    key = _key(request_key)
    timestamp = _now(now)
    digest = _digest({
        "action": "set", "sku": item, "minimum_price_eur": minimum,
        "reduction_step_eur": step, "wait_days": days,
        "expected_revision": revision,
    })
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _schema(conn, create=True)
        old_result = _replay(conn, key, digest)
        if old_result is not None:
            return old_result
        current_price = _draft_price(conn, item)
        if minimum > current_price:
            raise PriceRuleError("Mindestpreis liegt ueber dem lokalen Artikelpreis.")
        old = conn.execute(
            "SELECT revision FROM portfolio_price_rules WHERE sku=? COLLATE BINARY",
            (item,),
        ).fetchone()
        if old is None:
            if revision != 0:
                raise PriceRuleError("Preisregel wurde geaendert; bitte neu laden.")
            if conn.execute("SELECT COUNT(*) FROM portfolio_price_rules").fetchone()[0] >= _MAX_RULES:
                raise PriceRuleError("Maximal 10000 lokale Preisregeln.")
            new_revision = 1
            conn.execute(
                "INSERT INTO portfolio_price_rules "
                "(sku,minimum_price_eur,reduction_step_eur,wait_days,revision,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (item, minimum, step, days, new_revision, timestamp.isoformat()),
            )
        else:
            if old["revision"] != revision:
                raise PriceRuleError("Preisregel wurde geaendert; bitte neu laden.")
            new_revision = revision + 1
            conn.execute(
                "UPDATE portfolio_price_rules SET minimum_price_eur=?,"
                "reduction_step_eur=?,wait_days=?,revision=?,updated_at=? "
                "WHERE sku=? COLLATE BINARY",
                (minimum, step, days, new_revision, timestamp.isoformat(), item),
            )
        result = {
            "sku": item, "minimum_price_eur": minimum,
            "reduction_step_eur": step, "wait_days": days,
            "revision": new_revision, "updated_at": timestamp.isoformat(),
            "local_price_eur_at_save": current_price,
            "platform_writes": 0, "published": False,
        }
        _record(conn, key, digest, result)
        return result


def delete_price_rule(
    store: SnapshotStore, *, sku: str, expected_revision: int, request_key: str,
) -> dict[str, Any]:
    item = _sku(sku)
    revision = _integer(expected_revision, "Revision", 1, 1_000_000)
    key = _key(request_key)
    digest = _digest({"action": "delete", "sku": item, "expected_revision": revision})
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _schema(conn, create=False):
            raise PriceRuleError("Preisregel nicht vorhanden.")
        old_result = _replay(conn, key, digest)
        if old_result is not None:
            return old_result
        removed = conn.execute(
            "DELETE FROM portfolio_price_rules WHERE sku=? COLLATE BINARY "
            "AND revision=?", (item, revision),
        )
        if removed.rowcount != 1:
            raise PriceRuleError("Preisregel wurde geaendert; bitte neu laden.")
        result = {"sku": item, "deleted": True, "platform_writes": 0}
        _record(conn, key, digest, result)
        return result


def _suggestion(row: sqlite3.Row, *, now: datetime) -> dict[str, Any]:
    sku = _sku(row["sku"])
    floor = _integer(row["minimum_price_eur"], "Mindestpreis", 1, _MAX_PRICE)
    step = _integer(row["reduction_step_eur"], "Preisschritt", 1, _MAX_PRICE)
    wait = _integer(row["wait_days"], "Wartezeit", 0, _MAX_DAYS)
    revision = _integer(row["revision"], "Revision", 1, 1_000_000)
    if type(row["updated_at"]) is not str:
        raise sqlite3.DatabaseError("invalid price rule time")
    try:
        updated = datetime.fromisoformat(row["updated_at"])
        if updated.tzinfo is None or updated.utcoffset() is None:
            raise ValueError("missing timezone")
        eligibility = updated.astimezone(timezone.utc) + timedelta(days=wait)
    except (ValueError, TypeError, OverflowError):
        raise sqlite3.DatabaseError("invalid price rule time") from None
    price = row["local_price_eur"]
    if price is not None and (type(price) is not int or not 1 <= price <= _MAX_PRICE):
        raise sqlite3.DatabaseError("invalid current local draft price")
    proposed: int | None = None
    if price is None:
        state, reason = "unknown_local_price", "Lokaler Entwurf fehlt; keine Loesch- oder Plattformaussage."
    elif price < floor:
        state, reason = "below_minimum", "Lokaler Preis liegt unter der Mindestgrenze; keine Senkung."
    elif price == floor:
        state, reason = "at_minimum", "Mindestpreis bereits erreicht; keine Senkung."
    elif now < eligibility:
        state, reason = "waiting", "Wartezeit seit Speichern der lokalen Regel noch nicht abgelaufen."
    else:
        proposed = max(floor, price - step)
        state, reason = "suggested", "Eine lokale Preisstufe vorgeschlagen; keine Plattformaktion."
    return {
        "sku": sku, "local_price_eur": price,
        "minimum_price_eur": floor, "reduction_step_eur": step,
        "wait_days": wait, "revision": revision,
        "rule_updated_at": updated.astimezone(timezone.utc).isoformat(),
        "eligible_at": eligibility.isoformat(),
        "state": state, "suggested_price_eur": proposed,
        "reason": reason, "price_source": "local_imported_draft",
        "published": False, "platform_writes": 0,
    }


def price_suggestions_page(
    store: SnapshotStore, *, after: str = "", limit: int = 50,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Bounded keyset read of local proposals, including unknown/deleted draft data.

    A missing draft never constitutes proof a platform ad was removed or sold.
    There are no writes even on first query. No automatic price changes.
    """
    if (type(after) is not str or len(after) > 80
            or (after and _SKU.fullmatch(after) is None)
            or type(limit) is not int or not 1 <= limit <= 50):
        raise PriceRuleError("Ungueltiger Katalogcursor oder Seitengroesse.")
    instant = _now(now)
    with closing(store._connect()) as conn:
        if not _schema(conn, create=False):
            return {"rules": [], "total": 0, "has_more": False, "next_after": None}
        has_drafts = _has_table(conn)
        joined = (
            "portfolio_price_rules r LEFT JOIN portfolio_drafts d "
            "ON r.sku=d.sku COLLATE BINARY"
            if has_drafts else "portfolio_price_rules r"
        )
        price_projection = "d.price_eur AS local_price_eur" if has_drafts else "NULL AS local_price_eur"
        count = conn.execute("SELECT COUNT(*) FROM portfolio_price_rules").fetchone()[0]
        rows = conn.execute(
            "SELECT r.sku,r.minimum_price_eur,r.reduction_step_eur,r.wait_days,"
            "r.revision,r.updated_at," + price_projection + " FROM " + joined
            + " WHERE r.sku > ? COLLATE BINARY "
            "ORDER BY r.sku COLLATE BINARY LIMIT ?",
            (after, limit + 1),
        ).fetchall()
    visible = rows[:limit]
    return {
        "rules": [_suggestion(row, now=instant) for row in visible],
        "total": count, "has_more": len(rows) > limit,
        "next_after": visible[-1]["sku"] if len(rows) > limit else None,
        "price_source": "local_imported_draft", "platform_writes": 0,
    }
