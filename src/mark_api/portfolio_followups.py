"""G1: explicitly manual owner-ad follow-ups; never inferred platform expiry.

A follow-up is a user-entered date, not a Kleinanzeigen expiry observation.
No CDP, HTTP, AI provider, platform write or automatic renewal is possible here.
Reads are bounded keyset pages and never create tables. Writes are local SQLite
transactions with exact request-key replay, revision CAS and preserved tombstones.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import sqlite3
from typing import Any

from .portfolio_sales import AD_ID, OWN_INVENTORY_SOURCES
from .storage import SnapshotStore

_KEY = re.compile(r"[0-9a-f]{64}\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_MAX_ITEMS = 10_000
_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_followups (
  ad_id TEXT PRIMARY KEY COLLATE BINARY,
  due_date TEXT NOT NULL,
  note TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision>=1),
  deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_followup_requests (
  request_key TEXT PRIMARY KEY COLLATE BINARY,
  intent_sha256 TEXT NOT NULL,
  response_json TEXT NOT NULL
);
"""
_COLUMNS = {
    "portfolio_followups": {"ad_id", "due_date", "note", "revision", "deleted", "updated_at"},
    "portfolio_followup_requests": {"request_key", "intent_sha256", "response_json"},
}
_OWNER_PLACEHOLDERS = ",".join("?" for _ in OWN_INVENTORY_SOURCES)


class FollowupError(ValueError):
    """Invalid request, not a verified own ad or stale local edit."""


def _ad_id(value: object) -> str:
    if type(value) is not str or AD_ID.fullmatch(value) is None:
        raise FollowupError("invalid own ad id")
    return value


def _revision(value: object, *, deleting: bool = False) -> int:
    minimum = 1 if deleting else 0
    if type(value) is not int or not minimum <= value <= 1_000_000:
        raise FollowupError("invalid followup revision")
    return value


def _date(value: object) -> str:
    if type(value) is not str or _DATE.fullmatch(value) is None:
        raise FollowupError("invalid manual followup date")
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise FollowupError("invalid manual followup date") from exc
    if not 2020 <= day.year <= 2100 or day.isoformat() != value:
        raise FollowupError("followup date outside allowed range")
    return value


def _note(value: object) -> str:
    if type(value) is not str or len(value) > 120 or any(
        ord(ch) < 32 or 0x7F <= ord(ch) <= 0x9F
        or 0xD800 <= ord(ch) <= 0xDFFF
        or 0x202A <= ord(ch) <= 0x202E
        or 0x2066 <= ord(ch) <= 0x2069 for ch in value
    ):
        raise FollowupError("invalid manual followup note")
    return value.strip()


def _key(value: object) -> str:
    if type(value) is not str or _KEY.fullmatch(value) is None:
        raise FollowupError("invalid local request key")
    return value


def _clock(value: datetime | None = None) -> datetime:
    now = datetime.now(timezone.utc) if value is None else value
    if (type(now) is not datetime or now.tzinfo is None
            or now.utcoffset() is None):
        raise FollowupError("timezone-aware followup clock required")
    return now.astimezone(timezone.utc)


def _schema(conn: sqlite3.Connection, *, create: bool) -> bool:
    found = {
        item["name"]: item["type"] for item in conn.execute(
            "SELECT name,type FROM sqlite_master WHERE name IN "
            "('portfolio_followups','portfolio_followup_requests')"
        )
    }
    if not found:
        if not create:
            return False
        for statement in _DDL.split(";"):
            if statement.strip():
                conn.execute(statement)
        found = {name: "table" for name in _COLUMNS}
    if found != {name: "table" for name in _COLUMNS}:
        raise sqlite3.DatabaseError("partial followup schema")
    for name, cols in _COLUMNS.items():
        if {row["name"] for row in conn.execute(f"PRAGMA table_info({name})")} != cols:
            raise sqlite3.DatabaseError("invalid followup columns")
    return True


def _latest_owned(conn: sqlite3.Connection, ad_id: str) -> sqlite3.Row | None:
    # EML and manual local catalogs never prove ownership; no fuzzy title match.
    return conn.execute(
        "SELECT observed_at,source,lifecycle_state,title FROM ad_snapshots "
        f"WHERE ad_id=? COLLATE BINARY AND source IN ({_OWNER_PLACEHOLDERS}) "
        "ORDER BY id DESC LIMIT 1",
        (ad_id, *OWN_INVENTORY_SOURCES),
    ).fetchone()


def _intent(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _state(due: str, *, today: date) -> str:
    day = date.fromisoformat(due)
    if day < today:
        return "overdue"
    if day <= today + timedelta(days=7):
        return "due_soon"
    return "scheduled"


def _public(row: sqlite3.Row, owned: sqlite3.Row | None, *, today: date) -> dict[str, Any]:
    # Never turn the manual date or last observation time into an expiry claim.
    return {
        "ad_id": row["ad_id"], "manual_followup_date": row["due_date"],
        "manual_note": row["note"], "revision": row["revision"],
        "updated_at": row["updated_at"],
        "state": _state(row["due_date"], today=today),
        "owned_currently": bool(owned is not None and owned["lifecycle_state"] != "absent"),
        "owner_last_observed_at": owned["observed_at"] if owned is not None else None,
        "title": owned["title"] if owned is not None else None,
        "platform_expiry_at": None,
        "platform_expiry_status": "unknown_no_verified_observation",
        "source": "manual_user_followup", "platform_writes": 0,
    }


def mutate_followup(
    store: SnapshotStore, payload: dict[str, Any], *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Save or tombstone a human follow-up with durable, replay-safe local CAS."""
    if type(store) is not SnapshotStore or type(payload) is not dict:
        raise FollowupError("trusted local store and payload required")
    if set(payload) != {"action", "ad_id", "due_date", "note", "expected_revision", "request_key"}:
        raise FollowupError("invalid followup fields")
    action = payload["action"]
    if action not in ("save", "delete") or type(action) is not str:
        raise FollowupError("invalid followup action")
    ad_id = _ad_id(payload["ad_id"])
    revision = _revision(payload["expected_revision"], deleting=action=="delete")
    key = _key(payload["request_key"])
    due = _date(payload["due_date"]) if action == "save" else None
    note = _note(payload["note"])
    if action == "delete" and (payload["due_date"] is not None or note):
        raise FollowupError("delete requires no date or note")
    stamp = _clock(now)
    frozen = {
        "action": action, "ad_id": ad_id, "due_date": due,
        "note": note, "expected_revision": revision,
    }
    digest = _intent(frozen)
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _schema(conn, create=True)
        replay = conn.execute(
            "SELECT intent_sha256,response_json FROM portfolio_followup_requests "
            "WHERE request_key=?", (key,),
        ).fetchone()
        if replay is not None:
            if replay["intent_sha256"] != digest:
                raise FollowupError("request key conflict")
            return json.loads(replay["response_json"])
        owned = _latest_owned(conn, ad_id)
        if action == "save" and (
            owned is None or owned["lifecycle_state"] == "absent"
        ):
            raise FollowupError("own active or paused ad observation required")
        old = conn.execute(
            "SELECT revision,deleted FROM portfolio_followups WHERE ad_id=? COLLATE BINARY",
            (ad_id,),
        ).fetchone()
        if action == "save":
            if old is None:
                if revision != 0:
                    raise FollowupError("followup revision changed; reload")
                if conn.execute(
                    "SELECT COUNT(*) FROM portfolio_followups WHERE deleted=0"
                ).fetchone()[0] >= _MAX_ITEMS:
                    raise FollowupError("maximum 10000 manual followups")
                next_revision = 1
                conn.execute(
                    "INSERT INTO portfolio_followups VALUES (?,?,?,?,?,?)",
                    (ad_id, due, note, next_revision, 0, stamp.isoformat()),
                )
            else:
                if old["revision"] >= 1_000_000:
                    raise FollowupError("followup revision exhausted")
                if old["deleted"] == 1:
                    # A previous delete advanced this generation. Re-creation
                    # must reference the observed tombstone, never revision 0.
                    if revision != old["revision"]:
                        raise FollowupError("followup revision changed; reload")
                    if conn.execute(
                        "SELECT COUNT(*) FROM portfolio_followups WHERE deleted=0"
                    ).fetchone()[0] >= _MAX_ITEMS:
                        raise FollowupError("maximum 10000 manual followups")
                elif old["deleted"] != 0 or revision != old["revision"]:
                    raise FollowupError("followup revision changed; reload")
                next_revision = old["revision"] + 1
                conn.execute(
                    "UPDATE portfolio_followups SET due_date=?,note=?,revision=?,"
                    "deleted=0,updated_at=? WHERE ad_id=? COLLATE BINARY AND revision=?",
                    (due, note, next_revision, stamp.isoformat(), ad_id, old["revision"]),
                )
            result = {
                "ad_id": ad_id, "revision": next_revision,
                "manual_followup_date": due, "manual_note": note,
                "source": "manual_user_followup", "platform_expiry_at": None,
                "platform_expiry_status": "unknown_no_verified_observation",
                "platform_writes": 0, "updated_at": stamp.isoformat(),
            }
        else:
            if old is None or old["deleted"] != 0 or old["revision"] != revision:
                raise FollowupError("followup revision changed; reload")
            if old["revision"] >= 1_000_000:
                raise FollowupError("followup revision exhausted")
            conn.execute(
                "UPDATE portfolio_followups SET deleted=1,revision=revision+1,"
                "updated_at=? WHERE ad_id=? COLLATE BINARY AND revision=?",
                (stamp.isoformat(), ad_id, revision),
            )
            result = {
                "ad_id": ad_id, "deleted": True, "revision": revision+1,
                "source": "manual_user_followup", "platform_writes": 0,
            }
        conn.execute(
            "INSERT INTO portfolio_followup_requests VALUES (?,?,?)",
            (key, digest, json.dumps(result, sort_keys=True, ensure_ascii=True)),
        )
        return result


def followup_detail(
    store: SnapshotStore, ad_id: str, *, now: datetime | None = None,
) -> dict[str, Any]:
    item = _ad_id(ad_id)
    today = _clock(now).date()
    with closing(store._connect()) as conn:
        if not _schema(conn, create=False):
            return {"followup": None, "tombstone_revision": 0,
                    "platform_expiry_status": "unknown_no_verified_observation"}
        row = conn.execute(
            "SELECT * FROM portfolio_followups WHERE ad_id=? COLLATE BINARY",
            (item,),
        ).fetchone()
        if row is None:
            return {"followup": None, "tombstone_revision": 0,
                    "platform_expiry_status": "unknown_no_verified_observation"}
        if row["deleted"] == 1:
            # Return a deletion generation without exposing the removed note.
            return {"followup": None, "tombstone_revision": row["revision"],
                    "platform_expiry_status": "unknown_no_verified_observation"}
        if row["deleted"] != 0:
            raise sqlite3.DatabaseError("invalid followup tombstone")
        return {
            "followup": _public(row, _latest_owned(conn, item), today=today),
            "tombstone_revision": 0,
            "platform_expiry_status": "unknown_no_verified_observation",
        }


def followups_page(
    store: SnapshotStore, *, after: str = "", limit: int = 50,
    status: str = "all", now: datetime | None = None,
) -> dict[str, Any]:
    if (type(store) is not SnapshotStore
            or type(after) is not str or (after and AD_ID.fullmatch(after) is None)
            or type(limit) is not int or not 1 <= limit <= 50
            or type(status) is not str
            or status not in {"all", "overdue", "due_soon", "scheduled"}):
        raise FollowupError("invalid followup query")
    today = _clock(now).date()
    empty = {
        "followups": [], "has_more": False, "next_after": None,
        "total": 0, "matching": 0, "filter": status,
        "overdue": 0, "due_soon": 0,
        "platform_expiry_status": "unknown_no_verified_observation",
        "platform_writes": 0,
    }
    with closing(store._connect()) as conn:
        if not _schema(conn, create=False):
            return empty
        counts = conn.execute(
            "SELECT COUNT(*) total,"
            "SUM(CASE WHEN due_date<? THEN 1 ELSE 0 END) overdue,"
            "SUM(CASE WHEN due_date>=? AND due_date<=? THEN 1 ELSE 0 END) due_soon "
            "FROM portfolio_followups WHERE deleted=0",
            (today.isoformat(), today.isoformat(), (today+timedelta(days=7)).isoformat()),
        ).fetchone()
        where = ""
        criteria: tuple[str, ...] = ()
        if status == "overdue":
            where = " AND due_date<?"
            criteria = (today.isoformat(),)
        elif status == "due_soon":
            where = " AND due_date>=? AND due_date<=?"
            criteria = (today.isoformat(), (today+timedelta(days=7)).isoformat())
        elif status == "scheduled":
            where = " AND due_date>?"
            criteria = ((today+timedelta(days=7)).isoformat(),)
        # Status predicates are hardcoded, parameters are exclusively bound.
        matched = (counts["total"] if status == "all" else conn.execute(
            "SELECT COUNT(*) FROM portfolio_followups WHERE deleted=0" + where,
            criteria,
        ).fetchone()[0])
        rows = conn.execute(
            "SELECT * FROM portfolio_followups WHERE deleted=0 "
            "AND ad_id>? COLLATE BINARY" + where
            + " ORDER BY ad_id COLLATE BINARY LIMIT ?",
            (after, *criteria, limit+1),
        ).fetchall()
        listed = [
            _public(row, _latest_owned(conn, row["ad_id"]), today=today)
            for row in rows[:limit]
        ]
        return {
            "followups": listed, "has_more": len(rows)>limit,
            "next_after": listed[-1]["ad_id"] if len(rows)>limit else None,
            "total": counts["total"], "matching": matched, "filter": status,
            "overdue": counts["overdue"] or 0,
            "due_soon": counts["due_soon"] or 0,
            "platform_expiry_status": "unknown_no_verified_observation",
            "platform_writes": 0,
        }
