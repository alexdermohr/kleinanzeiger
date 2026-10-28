"""G7: local triage for imported notification conversations, never platform mail.

An EML notification is not proof of a live platform inbox, message delivery or
ownership. Reads and local review markers are bounded, revision checked and
separate from the existing confirmed platform write and recovery chain.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
from typing import Any

from .portfolio_sales import AD_ID, OWN_INVENTORY_SOURCES
from .storage import SnapshotStore


CONVERSATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_-]{2,255}\Z")
REQUEST_KEY = re.compile(r"[0-9a-f]{64}\Z")
MAX_PAGE = 50
_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_conversation_reviews (
    ad_id TEXT NOT NULL COLLATE BINARY,
    conversation_id TEXT NOT NULL COLLATE BINARY,
    seen_id INTEGER NOT NULL CHECK(seen_id>=0),
    revision INTEGER NOT NULL CHECK(revision>=1),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ad_id,conversation_id)
);
CREATE TABLE IF NOT EXISTS portfolio_conversation_review_requests (
    request_key TEXT PRIMARY KEY COLLATE BINARY,
    intent_sha256 TEXT NOT NULL,
    response_json TEXT NOT NULL
);
"""
_COLUMNS = {
    "portfolio_conversation_reviews": {
        "ad_id", "conversation_id", "seen_id", "revision", "updated_at",
    },
    "portfolio_conversation_review_requests": {
        "request_key", "intent_sha256", "response_json",
    },
}
_OWNER_MARKS = ",".join("?" for _ in OWN_INVENTORY_SOURCES)


class ConversationError(ValueError):
    """Invalid local conversation action or stale revision."""


def _id(value: object) -> str:
    if type(value) is not str or AD_ID.fullmatch(value) is None:
        raise ConversationError("invalid ad id")
    return value


def _conversation(value: object) -> str:
    if type(value) is not str or CONVERSATION_ID.fullmatch(value) is None:
        raise ConversationError("invalid conversation id")
    return value


def _schema(conn: sqlite3.Connection, *, create: bool) -> bool:
    found = {row["name"]: row["type"] for row in conn.execute(
        "SELECT name,type FROM sqlite_master WHERE name IN "
        "('portfolio_conversation_reviews','portfolio_conversation_review_requests')"
    )}
    if not found:
        if not create:
            return False
        for statement in _DDL.split(";"):
            if statement.strip():
                conn.execute(statement)
        found = {key: "table" for key in _COLUMNS}
    if found != {key: "table" for key in _COLUMNS}:
        raise sqlite3.DatabaseError("partial conversation schema")
    for table, columns in _COLUMNS.items():
        observed = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if observed != columns:
            raise sqlite3.DatabaseError("invalid conversation schema")
    return True


def _owned(conn: sqlite3.Connection, ad_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT lifecycle_state,title FROM ad_snapshots "
        f"WHERE ad_id=? COLLATE BINARY AND source IN ({_OWNER_MARKS}) "
        "ORDER BY id DESC LIMIT 1", (ad_id, *OWN_INVENTORY_SOURCES),
    ).fetchone()


def _cursor(after: object) -> tuple[str, str]:
    if type(after) is not str or len(after) > 290:
        raise ConversationError("invalid page cursor")
    if not after:
        return "", ""
    ad_id, separator, conversation_id = after.partition("/")
    if separator != "/":
        raise ConversationError("invalid page cursor")
    return _id(ad_id), _conversation(conversation_id)


def _clock(value: datetime | None) -> str:
    clock = datetime.now(timezone.utc) if value is None else value
    if type(clock) is not datetime or clock.tzinfo is None or clock.utcoffset() is None:
        raise ConversationError("aware clock required")
    return clock.astimezone(timezone.utc).isoformat()


def conversations_page(
    store: SnapshotStore, *, after: str = "", limit: int = 25,
    watermark: int | None = None, status: str = "all",
) -> dict[str, Any]:
    """Cursor-paginated EML conversations with an immutable event watermark."""
    if type(store) is not SnapshotStore:
        raise ConversationError("native local store required")
    ad_cursor, conversation_cursor = _cursor(after)
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise ConversationError("invalid page size")
    if type(watermark) is not int and watermark is not None:
        raise ConversationError("invalid watermark")
    if watermark is not None and not 0 <= watermark <= 2**63 - 1:
        raise ConversationError("invalid watermark")
    if type(status) is not str or status not in ("all", "new", "reviewed"):
        raise ConversationError("invalid local review filter")
    with closing(store._connect()) as conn:
        present = _schema(conn, create=False)
        if watermark is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(id),0) AS bound FROM inbound_message_events"
            ).fetchone()
            assert row is not None
            watermark = int(row["bound"])
        if status == "reviewed" and not present:
            return {
                "conversations": [], "watermark": watermark, "has_more": False,
                "next_after": None, "filter": status, "platform_messages_read": False,
                "platform_writes": 0,
            }
        join = ("LEFT JOIN portfolio_conversation_reviews AS review ON "
                "events.ad_id=review.ad_id AND events.conversation_id=review.conversation_id"
                if present else "")
        predicate = (
            "AND (review.seen_id IS NULL OR events.last_id>review.seen_id)"
            if present and status == "new" else
            "AND review.seen_id IS NOT NULL AND events.last_id<=review.seen_id"
            if present and status == "reviewed" else ""
        )
        rows = conn.execute(
            """SELECT events.ad_id,events.conversation_id,events.message_count,
                      events.last_id""" +
            (",review.seen_id,review.revision,review.updated_at" if present else
             ",NULL AS seen_id,NULL AS revision,NULL AS updated_at") +
            """ FROM (SELECT ad_id,conversation_id,COUNT(*) AS message_count,
                       MAX(id) AS last_id FROM inbound_message_events
                       WHERE id<=? AND (ad_id>? COLLATE BINARY
                         OR (ad_id=? COLLATE BINARY AND conversation_id>? COLLATE BINARY))
                       GROUP BY ad_id,conversation_id) AS events """ + join +
            " WHERE 1=1 " + predicate +
            " ORDER BY events.ad_id COLLATE BINARY,events.conversation_id COLLATE BINARY "
            "LIMIT ?",
            (watermark, ad_cursor, ad_cursor, conversation_cursor, limit + 1),
        ).fetchall()
        conversations: list[dict[str, Any]] = []
        for row in rows[:limit]:
            ad_id = _id(row["ad_id"])
            conversation_id = _conversation(row["conversation_id"])
            owner = _owned(conn, ad_id)
            own = owner is not None and owner["lifecycle_state"] in ("active", "paused")
            last = conn.execute(
                "SELECT observed_at FROM inbound_message_events WHERE id=?",
                (row["last_id"],),
            ).fetchone()
            if last is None or type(last["observed_at"]) is not str:
                raise sqlite3.DatabaseError("missing notification event")
            reviewed = row["seen_id"] is not None and row["seen_id"] >= row["last_id"]
            conversations.append({
                "ad_id": ad_id, "conversation_id": conversation_id,
                "message_count": int(row["message_count"]),
                "last_imported_event_at": last["observed_at"],
                "own_ad": own, "title": owner["title"] if own else None,
                "review_state": "locally_reviewed" if reviewed else "new_since_review",
                "revision": row["revision"] if row["revision"] is not None else 0,
                "last_seen_event_id": row["seen_id"] if row["seen_id"] is not None else 0,
                "source": "imported_email_metadata", "platform_reply_status": "unknown",
            })
        more = len(rows) > limit
        return {
            "conversations": conversations, "watermark": watermark,
            "has_more": more,
            "next_after": (conversations[-1]["ad_id"] + "/" +
                           conversations[-1]["conversation_id"]) if more else None,
            "filter": status, "platform_messages_read": False, "platform_writes": 0,
        }


def update_conversation_review(
    store: SnapshotStore, payload: dict[str, Any], *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Human review marker, exact CAS and replay key, with zero external effects."""
    if type(store) is not SnapshotStore or type(payload) is not dict or set(payload) != {
        "ad_id", "conversation_id", "action", "expected_revision", "request_key",
    }:
        raise ConversationError("invalid local conversation payload")
    ad_id = _id(payload["ad_id"])
    conversation_id = _conversation(payload["conversation_id"])
    action = payload["action"]
    revision = payload["expected_revision"]
    key = payload["request_key"]
    if type(action) is not str or action not in ("review", "reopen"):
        raise ConversationError("invalid local action")
    if type(revision) is not int or not 0 <= revision <= 1_000_000:
        raise ConversationError("invalid review revision")
    if type(key) is not str or REQUEST_KEY.fullmatch(key) is None:
        raise ConversationError("invalid review request key")
    stamp = _clock(now)
    intent = {
        "ad_id": ad_id, "conversation_id": conversation_id,
        "action": action, "expected_revision": revision,
    }
    digest = hashlib.sha256(json.dumps(
        intent, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _schema(conn, create=True)
        replay = conn.execute(
            "SELECT intent_sha256,response_json FROM portfolio_conversation_review_requests "
            "WHERE request_key=?", (key,),
        ).fetchone()
        if replay is not None:
            if replay["intent_sha256"] != digest:
                raise ConversationError("request key reused for different action")
            return json.loads(replay["response_json"])
        owner = _owned(conn, ad_id)
        if owner is None or owner["lifecycle_state"] not in ("active", "paused"):
            raise ConversationError("verified own active or paused ad required")
        observed = conn.execute(
            "SELECT MAX(id) AS last_id FROM inbound_message_events "
            "WHERE ad_id=? COLLATE BINARY AND conversation_id=? COLLATE BINARY",
            (ad_id, conversation_id),
        ).fetchone()
        if observed is None or observed["last_id"] is None:
            raise ConversationError("unknown imported conversation")
        prior = conn.execute(
            "SELECT seen_id,revision FROM portfolio_conversation_reviews "
            "WHERE ad_id=? COLLATE BINARY AND conversation_id=? COLLATE BINARY",
            (ad_id, conversation_id),
        ).fetchone()
        if prior is None:
            if revision != 0:
                raise ConversationError("review state changed; reload")
            new_revision = 1
            seen_id = int(observed["last_id"]) if action == "review" else 0
            conn.execute(
                "INSERT INTO portfolio_conversation_reviews VALUES (?,?,?,?,?)",
                (ad_id, conversation_id, seen_id, new_revision, stamp),
            )
        else:
            if revision != prior["revision"] or revision >= 1_000_000:
                raise ConversationError("review state changed; reload")
            new_revision = revision + 1
            seen_id = int(observed["last_id"]) if action == "review" else 0
            conn.execute(
                "UPDATE portfolio_conversation_reviews SET seen_id=?,revision=?,"
                "updated_at=? WHERE ad_id=? COLLATE BINARY AND conversation_id=? COLLATE BINARY",
                (seen_id, new_revision, stamp, ad_id, conversation_id),
            )
        result = {
            "ad_id": ad_id, "conversation_id": conversation_id,
            "revision": new_revision, "review_state":
                "locally_reviewed" if action == "review" else "new_since_review",
            "last_seen_event_id": seen_id, "updated_at": stamp,
            "source": "manual_review_of_imported_email",
            "platform_reply_status": "unknown", "platform_writes": 0,
        }
        conn.execute(
            "INSERT INTO portfolio_conversation_review_requests VALUES (?,?,?)",
            (key, digest, json.dumps(result, ensure_ascii=True, sort_keys=True)),
        )
        return result
