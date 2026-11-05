"""Manual, evidence-gated price planning for confirmed own active listings.

A customer-entered price is NOT a verified marketplace price. Suggestions are
local arithmetic only, never a platform Write API operation or provider call.
Existing draft-only price rules stay intact and independent of owner ad IDs.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any

from .portfolio_price_rules import (
    PriceRuleError, _MAX_DAYS, _MAX_PRICE, _MAX_RULES,
    _digest, _integer, _key, _now,
)
from .portfolio_sales import AD_ID, OWN_INVENTORY_SOURCES
from .storage import SnapshotStore


class OwnerPriceRuleError(PriceRuleError):
    """Invalid, stale, or unverifiable manual price planning request."""


_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_owner_price_rules (
 ad_id TEXT PRIMARY KEY COLLATE BINARY NOT NULL,
 manual_price_eur INTEGER NOT NULL CHECK(manual_price_eur BETWEEN 1 AND 99999999),
 minimum_price_eur INTEGER NOT NULL CHECK(minimum_price_eur BETWEEN 1 AND 99999999),
 reduction_step_eur INTEGER NOT NULL CHECK(reduction_step_eur BETWEEN 1 AND 99999999),
 wait_days INTEGER NOT NULL CHECK(wait_days BETWEEN 0 AND 3650),
 revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 1000000),
 deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_owner_price_requests (
 request_key TEXT PRIMARY KEY COLLATE BINARY NOT NULL,
 intent_sha256 TEXT NOT NULL,
 response_json TEXT NOT NULL
);
"""
_COLUMNS = {
    "portfolio_owner_price_rules": {
        "ad_id", "manual_price_eur", "minimum_price_eur", "reduction_step_eur",
        "wait_days", "revision", "deleted", "updated_at",
    },
    "portfolio_owner_price_requests": {
        "request_key", "intent_sha256", "response_json",
    },
}
_FRESHNESS = timedelta(hours=48)


def _ad_id(value: object) -> str:
    if type(value) is not str or AD_ID.fullmatch(value) is None:
        raise OwnerPriceRuleError("Ungueltige Eigenanzeigen-ID.")
    return value


def _schema(conn: sqlite3.Connection, *, create: bool = False) -> bool:
    rows = {
        row["name"]: row["type"]
        for row in conn.execute(
            "SELECT name,type FROM sqlite_master WHERE name IN "
            "('portfolio_owner_price_rules','portfolio_owner_price_requests')"
        )
    }
    if not rows:
        if not create:
            return False
        for statement in _DDL.split(";"):
            if statement.strip():
                conn.execute(statement)
        rows = {name: "table" for name in _COLUMNS}
    if rows != {name: "table" for name in _COLUMNS}:
        raise sqlite3.DatabaseError("partial owner price rule schema")
    for table, columns in _COLUMNS.items():
        actual = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if actual != columns:
            raise sqlite3.DatabaseError("invalid owner price rule schema")
    return True


def _owner_observation(
    conn: sqlite3.Connection, ad_id: str, instant: datetime,
) -> dict[str, object]:
    """Most recent local observation: never infer ownership from older rows."""
    row = conn.execute(
        "SELECT source,lifecycle_state,observed_at FROM ad_snapshots "
        "WHERE ad_id=? COLLATE BINARY ORDER BY observed_at DESC,id DESC LIMIT 1",
        (ad_id,),
    ).fetchone()
    if row is None:
        return {"confirmed": False, "state": "not_observed", "observed_at": None,
                "source": None}
    source, state, raw_time = row["source"], row["lifecycle_state"], row["observed_at"]
    if (type(source) is not str or type(state) is not str
            or state not in {"active", "paused", "absent", "unknown", "pending"}
            or type(raw_time) is not str):
        raise sqlite3.DatabaseError("invalid own listing observation")
    try:
        timestamp = datetime.fromisoformat(raw_time)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timezone missing")
        timestamp = timestamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise sqlite3.DatabaseError("invalid own listing timestamp") from None
    age = instant - timestamp
    confirmed = (state == "active" and source in OWN_INVENTORY_SOURCES
                 and timedelta(0) <= age <= _FRESHNESS)
    return {
        "confirmed": confirmed, "state": state,
        "observed_at": timestamp.isoformat(), "source": source,
    }


def _proposal(row: sqlite3.Row, owner: dict[str, object], *, instant: datetime) -> dict[str, object]:
    if row["deleted"] != 0:
        raise sqlite3.DatabaseError("deleted owner price rule projected")
    ad_id = _ad_id(row["ad_id"])
    manual = _integer(row["manual_price_eur"], "manuellen Ausgangspreis", 1, _MAX_PRICE)
    minimum = _integer(row["minimum_price_eur"], "Mindestpreis", 1, _MAX_PRICE)
    step = _integer(row["reduction_step_eur"], "Preisschritt", 1, _MAX_PRICE)
    wait = _integer(row["wait_days"], "Wartezeit", 0, _MAX_DAYS)
    revision = _integer(row["revision"], "Revision", 1, 1_000_000)
    try:
        updated = datetime.fromisoformat(row["updated_at"])
        if updated.tzinfo is None or updated.utcoffset() is None:
            raise ValueError("timezone missing")
        updated = updated.astimezone(timezone.utc)
        eligible = updated + timedelta(days=wait)
    except (ValueError, TypeError, OverflowError):
        raise sqlite3.DatabaseError("invalid owner price rule timestamp") from None
    suggested: int | None = None
    if not owner["confirmed"]:
        state, reason = "owner_unconfirmed", "Eigenanzeige nicht aktuell als aktiv bestaetigt; keinen Preisvorschlag verwenden."
    elif manual < minimum:
        state, reason = "below_minimum", "Manueller Ausgangspreis liegt unter dem Mindestpreis."
    elif manual == minimum:
        state, reason = "at_minimum", "Manueller Mindestpreis bereits erreicht."
    elif instant < eligible:
        state, reason = "waiting", "Wartezeit nach lokaler Regelpflege noch nicht abgelaufen."
    else:
        suggested = max(minimum, manual - step)
        state, reason = "suggested", "Rechnerischer Vorschlag. Plattformpreis vor jeder echten Aenderung erneut pruefen."
    return {
        "ad_id": ad_id, "manual_price_eur": manual,
        "minimum_price_eur": minimum, "reduction_step_eur": step,
        "wait_days": wait, "revision": revision,
        "updated_at": updated.isoformat(), "eligible_at": eligible.isoformat(),
        "state": state, "reason": reason,
        "suggested_price_eur": suggested, "owner": owner,
        "price_source": "manually_entered_not_platform_verified",
        "platform_price_verified": False, "platform_writes": 0,
        "provider_requests": 0, "requires_live_price_recheck": True,
    }


def _replay(conn: sqlite3.Connection, key: str, digest: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT intent_sha256,response_json FROM portfolio_owner_price_requests "
        "WHERE request_key=?", (key,),
    ).fetchone()
    if row is None:
        return None
    if row["intent_sha256"] != digest:
        raise OwnerPriceRuleError("Anfrage-Schluessel wurde anders verwendet.")
    return json.loads(row["response_json"])


def _record(conn: sqlite3.Connection, key: str, digest: str, result: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO portfolio_owner_price_requests VALUES (?,?,?)",
        (key, digest, json.dumps(result, ensure_ascii=True, sort_keys=True)),
    )


def set_owner_price_rule(
    store: SnapshotStore, *, ad_id: str, manual_price_eur: int,
    minimum_price_eur: int, reduction_step_eur: int, wait_days: int,
    expected_revision: int, request_key: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Atomically record manual planning parameters for a currently confirmed own ad."""
    ad = _ad_id(ad_id)
    manual = _integer(manual_price_eur, "manuellen Preis", 1, _MAX_PRICE)
    floor = _integer(minimum_price_eur, "Mindestpreis", 1, _MAX_PRICE)
    step = _integer(reduction_step_eur, "Preisschritt", 1, _MAX_PRICE)
    wait = _integer(wait_days, "Wartezeit", 0, _MAX_DAYS)
    revision = _integer(expected_revision, "Revision", 0, 1_000_000)
    key = _key(request_key)
    if floor > manual:
        raise OwnerPriceRuleError("Mindestpreis liegt ueber manuellem Ausgangspreis.")
    instant = _now(now)
    digest = _digest({
        "action": "set", "ad_id": ad, "manual_price_eur": manual,
        "minimum_price_eur": floor, "reduction_step_eur": step,
        "wait_days": wait, "expected_revision": revision,
    })
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _schema(conn, create=True)
        old = _replay(conn, key, digest)
        if old is not None:
            return old
        owner = _owner_observation(conn, ad, instant)
        if not owner["confirmed"]:
            raise OwnerPriceRuleError("Eigenanzeige muss aktuell aktiv bestaetigt sein.")
        previous = conn.execute(
            "SELECT revision,deleted FROM portfolio_owner_price_rules "
            "WHERE ad_id=? COLLATE BINARY", (ad,),
        ).fetchone()
        if previous is None:
            if revision != 0:
                raise OwnerPriceRuleError("Preisregel wurde geaendert; neu laden.")
            if conn.execute(
                "SELECT COUNT(*) FROM portfolio_owner_price_rules WHERE deleted=0"
            ).fetchone()[0] >= _MAX_RULES:
                raise OwnerPriceRuleError("Maximal 10000 lokale Preisregeln.")
            next_revision = 1
            conn.execute(
                "INSERT INTO portfolio_owner_price_rules "
                "(ad_id,manual_price_eur,minimum_price_eur,reduction_step_eur,"
                "wait_days,revision,deleted,updated_at) VALUES (?,?,?,?,?,?,0,?)",
                (ad, manual, floor, step, wait, next_revision, instant.isoformat()),
            )
        else:
            old_revision = previous["revision"]
            if (type(old_revision) is not int or not 1 <= old_revision < 1_000_000
                    or previous["deleted"] not in (0, 1)
                    or revision != (old_revision if previous["deleted"] == 0 else 0)):
                raise OwnerPriceRuleError("Preisregel wurde geaendert; neu laden.")
            next_revision = old_revision + 1
            conn.execute(
                "UPDATE portfolio_owner_price_rules SET manual_price_eur=?,"
                "minimum_price_eur=?,reduction_step_eur=?,wait_days=?,"
                "revision=?,deleted=0,updated_at=? "
                "WHERE ad_id=? COLLATE BINARY AND revision=?",
                (manual, floor, step, wait, next_revision, instant.isoformat(),
                 ad, old_revision),
            )
        row = conn.execute(
            "SELECT * FROM portfolio_owner_price_rules WHERE ad_id=? COLLATE BINARY",
            (ad,),
        ).fetchone()
        result = _proposal(row, owner, instant=instant)
        _record(conn, key, digest, result)
        return result


def delete_owner_price_rule(
    store: SnapshotStore, *, ad_id: str, expected_revision: int, request_key: str,
) -> dict[str, Any]:
    ad = _ad_id(ad_id)
    revision = _integer(expected_revision, "Revision", 1, 1_000_000)
    key = _key(request_key)
    digest = _digest({"action": "delete", "ad_id": ad, "expected_revision": revision})
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _schema(conn):
            raise OwnerPriceRuleError("Preisregel nicht vorhanden.")
        old = _replay(conn, key, digest)
        if old is not None:
            return old
        updated = conn.execute(
            "UPDATE portfolio_owner_price_rules SET deleted=1,revision=revision+1 "
            "WHERE ad_id=? COLLATE BINARY AND revision=? AND deleted=0 AND revision<1000000",
            (ad, revision),
        )
        if updated.rowcount != 1:
            raise OwnerPriceRuleError("Preisregel wurde geaendert; neu laden.")
        result = {"ad_id": ad, "deleted": True, "platform_writes": 0,
                  "provider_requests": 0}
        _record(conn, key, digest, result)
        return result


def owner_price_rule_detail(
    store: SnapshotStore, *, ad_id: str, now: datetime | None = None,
) -> dict[str, Any]:
    ad = _ad_id(ad_id)
    instant = _now(now)
    with closing(store._connect()) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        owner = _owner_observation(conn, ad, instant)
        rule = None
        if _schema(conn):
            row = conn.execute(
                "SELECT * FROM portfolio_owner_price_rules "
                "WHERE ad_id=? COLLATE BINARY AND deleted=0", (ad,),
            ).fetchone()
            if row is not None:
                rule = _proposal(row, owner, instant=instant)
        return {"ad_id": ad, "owner": owner, "rule": rule,
                "price_source": "manually_entered_not_platform_verified",
                "platform_writes": 0, "provider_requests": 0}


def owner_price_suggestions_page(
    store: SnapshotStore, *, after: str = "", limit: int = 50,
    now: datetime | None = None,
) -> dict[str, Any]:
    if (type(after) is not str or (after and AD_ID.fullmatch(after) is None)
            or type(limit) is not int or not 1 <= limit <= 50):
        raise OwnerPriceRuleError("Ungueltiger Eigenanzeigen-Preiscursor.")
    instant = _now(now)
    with closing(store._connect()) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        if not _schema(conn):
            return {"rules": [], "total": 0, "has_more": False, "next_after": None,
                    "platform_writes": 0, "provider_requests": 0}
        total = conn.execute(
            "SELECT COUNT(*) FROM portfolio_owner_price_rules WHERE deleted=0"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM portfolio_owner_price_rules "
            "WHERE ad_id>? COLLATE BINARY AND deleted=0 "
            "ORDER BY ad_id COLLATE BINARY LIMIT ?", (after, limit + 1),
        ).fetchall()
        visible = rows[:limit]
        items = [
            _proposal(row, _owner_observation(conn, row["ad_id"], instant), instant=instant)
            for row in visible
        ]
        return {"rules": items, "total": total, "has_more": len(rows) > limit,
                "next_after": visible[-1]["ad_id"] if len(rows) > limit else None,
                "platform_writes": 0, "provider_requests": 0}
