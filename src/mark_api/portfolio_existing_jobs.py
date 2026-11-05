"""Durable, fail-closed AI suggestions for verified existing owned ads.

This module never writes to the platform.  It freezes the exact latest owned
observation used for a suggestion and stores human approval as a local fact.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import sqlite3
from typing import Callable

from .storage import SnapshotStore

MAX_ITEMS = 10_000
MAX_CONFIRMED_CALLS = 10
_ID = re.compile(r"[0-9a-f]{32}\Z")
_KEY = re.compile(r"[0-9a-f]{64}\Z")
_AD = re.compile(r"[^\x00-\x1f\x7f]{1,160}\Z")
_OWNED_SOURCES = frozenset((
    "kleinanzeigen-management",
    "private-web-product-launcher",
    "private-web-owner-inventory",
    "private-web-periodic-readonly",
))
_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_existing_jobs(
 job_id TEXT PRIMARY KEY COLLATE BINARY, request_key TEXT UNIQUE NOT NULL,
 request_sha256 TEXT NOT NULL, instruction TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','running','paused','review','needs_attention','budget_exhausted','cancelled')),
 budget_cents INTEGER NOT NULL, estimate_cents INTEGER NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portfolio_existing_job_items(
 job_id TEXT NOT NULL, ad_id TEXT NOT NULL COLLATE BINARY,
 source_json TEXT NOT NULL, source_sha256 TEXT NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('queued','inflight','proposed','approved','rejected','unknown')),
 claim_token TEXT, proposal_json TEXT, reserved_cents INTEGER NOT NULL DEFAULT 0,
 actual_cents INTEGER NOT NULL DEFAULT 0, error_code TEXT, updated_at TEXT NOT NULL,
 PRIMARY KEY(job_id,ad_id));
CREATE INDEX IF NOT EXISTS idx_portfolio_existing_items
 ON portfolio_existing_job_items(job_id,phase,ad_id);
"""
_JOB_COLUMNS = {"job_id", "request_key", "request_sha256", "instruction", "state",
                "budget_cents", "estimate_cents", "created_at", "updated_at"}
_ITEM_COLUMNS = {"job_id", "ad_id", "source_json", "source_sha256", "phase",
                 "claim_token", "proposal_json", "reserved_cents", "actual_cents",
                 "error_code", "updated_at"}


class ExistingJobError(ValueError):
    pass


class _IneligibleAd(ExistingJobError):
    """Expected non-ownership/state outcome, not an integrity failure."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _schema(conn: sqlite3.Connection, create: bool = False) -> bool:
    found = {row["name"]: row["type"] for row in conn.execute(
        "SELECT name,type FROM sqlite_master WHERE name IN "
        "('portfolio_existing_jobs','portfolio_existing_job_items')")}
    if not found:
        if not create:
            return False
        # execute() preserves the caller's BEGIN IMMEDIATE transaction;
        # executescript() would commit it implicitly before creating tables.
        for statement in _DDL.split(";"):
            if statement.strip():
                conn.execute(statement)
        found = {"portfolio_existing_jobs": "table",
                 "portfolio_existing_job_items": "table"}
    if found != {"portfolio_existing_jobs": "table",
                 "portfolio_existing_job_items": "table"}:
        raise sqlite3.DatabaseError("partial existing-job schema")
    for table, expected in (("portfolio_existing_jobs", _JOB_COLUMNS),
                            ("portfolio_existing_job_items", _ITEM_COLUMNS)):
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if columns != expected:
            raise sqlite3.DatabaseError("invalid existing-job columns")
    return True


def _job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    if type(job_id) is not str or not _ID.fullmatch(job_id):
        raise ExistingJobError("invalid job id")
    row = conn.execute("SELECT * FROM portfolio_existing_jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        raise ExistingJobError("unknown job")
    return row


def _progress(conn: sqlite3.Connection, job_id: str) -> dict[str, object]:
    job = _job(conn, job_id)
    counts = {r["phase"]: r["n"] for r in conn.execute(
        "SELECT phase,COUNT(*) n FROM portfolio_existing_job_items WHERE job_id=? GROUP BY phase", (job_id,))}
    cost = conn.execute("SELECT COALESCE(SUM(actual_cents),0),COALESCE(SUM(reserved_cents),0) FROM portfolio_existing_job_items WHERE job_id=?", (job_id,)).fetchone()
    return {"job_id": job_id, "state": job["state"], "instruction": job["instruction"],
            "total": sum(counts.values()), "counts": counts,
            "budget_cents": job["budget_cents"], "estimate_cents": job["estimate_cents"],
            "actual_cents": cost[0], "reserved_cents": cost[1],
            "created_at": job["created_at"], "updated_at": job["updated_at"],
            "platform_writes": 0, "approval_scope": "local_only",
            "autonomy": _autonomy_status_conn(conn, job_id)}


def _instant(value: object) -> datetime:
    if type(value) is not str:
        raise ExistingJobError("malformed observation timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ExistingJobError("malformed observation timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExistingJobError("malformed observation timestamp")
    return parsed.astimezone(timezone.utc)


def _latest_observation(conn: sqlite3.Connection, ad_id: str) -> sqlite3.Row | None:
    rows = conn.execute(
        "SELECT id,ad_id,observed_at,source,lifecycle_state,title,description "
        "FROM ad_snapshots WHERE ad_id=? COLLATE BINARY", (ad_id,)).fetchall()
    if not rows:
        return None
    # Parse every candidate: an unorderable observation makes ownership unclear.
    return max(rows, key=lambda row: (_instant(row["observed_at"]), row["id"]))


def _latest_owned(conn: sqlite3.Connection, ad_id: str) -> dict[str, object]:
    row = _latest_observation(conn, ad_id)
    if (row is None or row["source"] not in _OWNED_SOURCES
            or row["lifecycle_state"] not in ("active", "paused")
            or type(row["title"]) is not str or not row["title"].strip()
            or type(row["description"]) is not str or not row["description"].strip()):
        raise _IneligibleAd("ad is foreign, stale, unknown, or lacks original text")
    return dict(row)


def eligible_ad_ids(store: SnapshotStore) -> list[str]:
    """Return all latest verified owned published IDs, never a truncated prefix."""
    with closing(store._connect()) as conn:
        ad_ids = [row["ad_id"] for row in conn.execute(
            "SELECT DISTINCT ad_id FROM ad_snapshots ORDER BY ad_id COLLATE BINARY")]
        result = []
        for ad_id in ad_ids:
            try:
                _latest_owned(conn, ad_id)
            except _IneligibleAd:
                continue
            result.append(ad_id)
            if len(result) > MAX_ITEMS:
                break
    if len(result) > MAX_ITEMS:
        raise ExistingJobError(f"more than {MAX_ITEMS} eligible ads")
    if not result:
        raise ExistingJobError("no verified owned published ads")
    return result


def create_job(store: SnapshotStore, ad_ids: list[str], *, instruction: str,
               request_key: str, budget_cents: int, estimate_cents: int) -> dict[str, object]:
    if (type(ad_ids) is not list or not 1 <= len(ad_ids) <= MAX_ITEMS
            or len(set(ad_ids)) != len(ad_ids)
            or any(type(x) is not str or not _AD.fullmatch(x) for x in ad_ids)):
        raise ExistingJobError("invalid ad selection")
    if type(request_key) is not str or not _KEY.fullmatch(request_key):
        raise ExistingJobError("invalid request key")
    if type(instruction) is not str or not 1 <= len(instruction.strip()) <= 1500:
        raise ExistingJobError("invalid instruction")
    if (type(budget_cents) is not int or not 0 <= budget_cents <= 100_000_000
            or type(estimate_cents) is not int or not 1 <= estimate_cents <= 100_000_000):
        raise ExistingJobError("invalid budget")
    ids = sorted(ad_ids)
    instruction = instruction.strip()
    fingerprint = _digest([ids, instruction, budget_cents, estimate_cents])
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _schema(conn, True)
        old = conn.execute("SELECT job_id,request_sha256 FROM portfolio_existing_jobs WHERE request_key=?", (request_key,)).fetchone()
        if old:
            if old["request_sha256"] != fingerprint:
                raise ExistingJobError("request key reused")
            return _progress(conn, old["job_id"])
        frozen = [(ad_id, _latest_owned(conn, ad_id)) for ad_id in ids]
        stamp, job_id = _now(), secrets.token_hex(16)
        conn.execute("INSERT INTO portfolio_existing_jobs VALUES(?,?,?,?,?,?,?,?,?)", (job_id,request_key,fingerprint,instruction,"queued",budget_cents,estimate_cents,stamp,stamp))
        conn.executemany("INSERT INTO portfolio_existing_job_items(job_id,ad_id,source_json,source_sha256,phase,updated_at) VALUES(?,?,?,?,?,?)",
                         ((job_id, ad_id, _canonical(source), _digest(source), "queued", stamp) for ad_id,source in frozen))
        return _progress(conn, job_id)


def list_jobs(store: SnapshotStore, limit: int = 50, *, offset: int = 0) -> list[dict[str, object]]:
    if (type(limit) is not int or not 1 <= limit <= 51
            or type(offset) is not int or not 0 <= offset <= 100_000):
        raise ExistingJobError("invalid limit")
    with closing(store._connect()) as conn:
        if not _schema(conn): return []
        return [_progress(conn, r[0]) for r in conn.execute(
            "SELECT job_id FROM portfolio_existing_jobs "
            "ORDER BY created_at DESC,job_id DESC LIMIT ? OFFSET ?", (limit, offset))]


def details(store: SnapshotStore, job_id: str, *, after: str = "", limit: int = 50,
            ad_id: str | None = None) -> dict[str, object]:
    if (type(limit) is not int or not 1 <= limit <= 50
            or type(after) is not str or (after and not _AD.fullmatch(after))
            or (ad_id is not None and (
                type(ad_id) is not str or _AD.fullmatch(ad_id) is None
                or not ad_id.strip() or bool(after)))):
        raise ExistingJobError("invalid page")
    with closing(store._connect()) as conn:
        if not _schema(conn): raise ExistingJobError("no jobs")
        answer = _progress(conn, job_id)
        columns = "SELECT ad_id,source_json,source_sha256,phase,proposal_json,actual_cents,error_code FROM portfolio_existing_job_items WHERE job_id=?"
        if ad_id is None:
            rows = conn.execute(
                columns + " AND ad_id>? COLLATE BINARY ORDER BY ad_id COLLATE BINARY LIMIT ?",
                (job_id, after, limit + 1),
            ).fetchall()
        else:
            # The (job_id, ad_id) primary key provides a bounded exact lookup,
            # even when the stored job contains 10,000 items.
            rows = conn.execute(
                columns + " AND ad_id=? COLLATE BINARY LIMIT 1", (job_id, ad_id),
            ).fetchall()
        answer["items"] = [{"ad_id":r["ad_id"],"original":json.loads(r["source_json"]),"source_sha256":r["source_sha256"],"phase":r["phase"],"proposal":json.loads(r["proposal_json"]) if r["proposal_json"] else None,"actual_cents":r["actual_cents"],"error_code":r["error_code"]} for r in rows[:limit]]
        answer["has_more"] = ad_id is None and len(rows) > limit
        answer["next_after"] = rows[limit-1]["ad_id"] if answer["has_more"] else None
        return answer


def change_state(store: SnapshotStore, job_id: str, action: str) -> dict[str, object]:
    if action not in ("start","pause","resume","cancel"): raise ExistingJobError("invalid action")
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE"); job=_job(conn,job_id)
        unknown=conn.execute("SELECT COUNT(*) FROM portfolio_existing_job_items WHERE job_id=? AND phase IN ('unknown','inflight')",(job_id,)).fetchone()[0]
        if action in ("start","resume"):
            if job["state"]=="running": return _progress(conn,job_id)
            if job["state"] not in ("queued","paused","budget_exhausted") or unknown: raise ExistingJobError("job cannot resume")
            state="running"
        elif action=="pause": state="paused" if job["state"]!="cancelled" else "cancelled"
        else: state="cancelled"
        conn.execute("UPDATE portfolio_existing_jobs SET state=?,updated_at=? WHERE job_id=?",(state,_now(),job_id))
        return _progress(conn,job_id)


def claim_next(store: SnapshotStore, job_id: str) -> dict[str, object] | None:
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE"); job=_job(conn,job_id)
        if job["state"]!="running": raise ExistingJobError("job not running")
        if conn.execute("SELECT COUNT(*) FROM portfolio_existing_job_items WHERE job_id=? AND phase='inflight'",(job_id,)).fetchone()[0]: raise ExistingJobError("unsettled claim")
        row=conn.execute("SELECT ad_id,source_json,source_sha256 FROM portfolio_existing_job_items WHERE job_id=? AND phase='queued' ORDER BY ad_id COLLATE BINARY LIMIT 1",(job_id,)).fetchone()
        if row is None:
            conn.execute("UPDATE portfolio_existing_jobs SET state='review',updated_at=? WHERE job_id=?",(_now(),job_id)); return None
        used=conn.execute("SELECT COALESCE(SUM(actual_cents+reserved_cents),0) FROM portfolio_existing_job_items WHERE job_id=?",(job_id,)).fetchone()[0]
        if used+job["estimate_cents"]>job["budget_cents"]:
            conn.execute("UPDATE portfolio_existing_jobs SET state='budget_exhausted',updated_at=? WHERE job_id=?",(_now(),job_id)); return None
        # This is the last durable gate before the caller may make a billable
        # request. Ownership, source and exact original text must still match.
        try:
            current = _latest_owned(conn, row["ad_id"])
        except ExistingJobError:
            current = None
        if current is None or _digest(current) != row["source_sha256"]:
            stamp = _now()
            conn.execute("UPDATE portfolio_existing_job_items SET error_code='source_changed',"
                         "updated_at=? WHERE job_id=? AND ad_id=?",
                         (stamp, job_id, row["ad_id"]))
            conn.execute("UPDATE portfolio_existing_jobs SET state='needs_attention',"
                         "updated_at=? WHERE job_id=?", (stamp, job_id))
            # Persist the fail-closed attention state before surfacing the
            # rejection; the caller must never reach the provider callback.
            conn.commit()
            raise ExistingJobError("original ownership or source changed")
        token=secrets.token_hex(16)
        conn.execute("UPDATE portfolio_existing_job_items SET phase='inflight',claim_token=?,reserved_cents=?,updated_at=? WHERE job_id=? AND ad_id=?",(token,job["estimate_cents"],_now(),job_id,row["ad_id"]))
        return {"job_id":job_id,"ad_id":row["ad_id"],"claim_token":token,"original":json.loads(row["source_json"]),"instruction":job["instruction"]}


def finish_claim(store: SnapshotStore, job_id: str, ad_id: str, token: str, proposal: dict[str,str], *, actual_cents: int) -> dict[str,object]:
    if (type(proposal) is not dict or set(proposal)!={"title","description"}
            or type(proposal["title"]) is not str
            or not 1 <= len(proposal["title"].strip()) <= 65
            or type(proposal["description"]) is not str
            or not 1 <= len(proposal["description"].strip()) <= 4000
            or any((ord(char) < 32 and char not in "\n\t")
                   or 0xD800 <= ord(char) <= 0xDFFF
                   for value in proposal.values() for char in value)):
        raise ExistingJobError("invalid proposal")
    if type(actual_cents) is not int or actual_cents<0: raise ExistingJobError("invalid cost")
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        changed=conn.execute("UPDATE portfolio_existing_job_items SET phase='proposed',proposal_json=?,actual_cents=?,reserved_cents=0,claim_token=NULL,updated_at=? WHERE job_id=? AND ad_id=? AND phase='inflight' AND claim_token=?",(_canonical(proposal),actual_cents,_now(),job_id,ad_id,token))
        if changed.rowcount!=1: raise ExistingJobError("invalid claim")
        return _progress(conn,job_id)


def mark_unknown(store: SnapshotStore, job_id: str, ad_id: str, token: str) -> dict[str,object]:
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        changed=conn.execute("UPDATE portfolio_existing_job_items SET phase='unknown',error_code='provider_outcome_unknown',updated_at=? WHERE job_id=? AND ad_id=? AND phase='inflight' AND claim_token=?",(_now(),job_id,ad_id,token))
        if changed.rowcount!=1: raise ExistingJobError("invalid claim")
        conn.execute("UPDATE portfolio_existing_jobs SET state='needs_attention',updated_at=? WHERE job_id=?",(_now(),job_id))
        return _progress(conn,job_id)



def recover_interrupted(store: SnapshotStore, job_id: str) -> dict[str, object]:
    """Quarantine unfinished provider claims after a stopped session.

    A client may call this only after no generation call is active. It does
    not requeue, re-call or assume a possibly charged request was free.
    Repeated recovery is idempotent and never resets an UNKNOWN claim.
    """
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _job(conn, job_id)
        stamp = _now()
        changed = conn.execute(
            "UPDATE portfolio_existing_job_items "
            "SET phase='unknown',error_code='interrupted_outcome_unknown',updated_at=? "
            "WHERE job_id=? AND phase='inflight'",
            (stamp, job_id),
        )
        if changed.rowcount:
            conn.execute(
                "UPDATE portfolio_existing_jobs SET state='needs_attention',updated_at=? "
                "WHERE job_id=? AND state!='cancelled'",
                (stamp, job_id),
            )
        return _progress(conn, job_id)


def settle_unknown(
    store: SnapshotStore, job_id: str, ad_id: str, *,
    actual_cents: int, explicitly_checked: bool,
) -> dict[str, object]:
    """Resolve one independently verified UNKNOWN without retrying the call.

    The discarded proposal is never recycled as new work. Conservatively
    record the manually confirmed USD-cent charge, including 0 only when
    independently verified, and make remaining queued work resumable only
    after *all* uncertain/inflight claims are gone.
    """
    if explicitly_checked is not True:
        raise ExistingJobError("independent provider outcome and cost check required")
    if type(actual_cents) is not int or not 0 <= actual_cents <= 100_000_000:
        raise ExistingJobError("invalid verified cost")
    if type(ad_id) is not str or _AD.fullmatch(ad_id) is None:
        raise ExistingJobError("invalid ad id")
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        job = _job(conn, job_id)
        stamp = _now()
        changed = conn.execute(
            "UPDATE portfolio_existing_job_items SET phase='rejected', "
            "actual_cents=?,reserved_cents=0,claim_token=NULL, "
            "error_code='manually_settled',updated_at=? "
            "WHERE job_id=? AND ad_id=? COLLATE BINARY AND phase='unknown' "
            "AND proposal_json IS NULL",
            (actual_cents, stamp, job_id, ad_id),
        )
        if changed.rowcount != 1:
            raise ExistingJobError("no unknown proposal-free claim to settle")
        unsettled = conn.execute(
            "SELECT COUNT(*) FROM portfolio_existing_job_items "
            "WHERE job_id=? AND phase IN ('unknown','inflight')",
            (job_id,),
        ).fetchone()[0]
        if not unsettled and job["state"] == "needs_attention":
            conn.execute(
                "UPDATE portfolio_existing_jobs SET state='paused',updated_at=? "
                "WHERE job_id=? AND state='needs_attention'",
                (stamp, job_id),
            )
        return _progress(conn, job_id)

def process_one(store: SnapshotStore, job_id: str, generator: Callable[[dict[str,object],str],tuple[dict[str,str],int]]) -> dict[str,object]:
    claim=claim_next(store,job_id)
    if claim is None: return {"claimed":False,"job_id":job_id}
    try:
        proposal,cost=generator(claim["original"],claim["instruction"])
        return {"claimed":True,"ad_id":claim["ad_id"],"progress":finish_claim(store,job_id,claim["ad_id"],claim["claim_token"],proposal,actual_cents=cost)}
    except BaseException:
        mark_unknown(store,job_id,claim["ad_id"],claim["claim_token"]); raise


def review_items(store: SnapshotStore, job_id: str, ad_ids: list[str], *, approve: bool) -> dict[str,object]:
    if type(approve) is not bool or type(ad_ids) is not list or not 1<=len(ad_ids)<=MAX_ITEMS or len(set(ad_ids))!=len(ad_ids): raise ExistingJobError("invalid review")
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE"); job=_job(conn,job_id)
        if job["state"] in ("cancelled","needs_attention"): raise ExistingJobError("blocked")
        for ad_id in ad_ids:
            row=conn.execute(
                "SELECT phase,source_sha256,proposal_json,error_code "
                "FROM portfolio_existing_job_items WHERE job_id=? AND ad_id=?",
                (job_id,ad_id),
            ).fetchone()
            if row is None or row["phase"] not in ("proposed","approved","rejected"):
                raise ExistingJobError("unreviewable")
            # A manually settled UNKNOWN is a discarded provider attempt,
            # not a generated proposal. Its checked expense survives, but
            # neither a stale dashboard selection nor local replay can turn
            # a proposal-free row into an approved suggestion.
            if approve and (row["proposal_json"] is None
                            or row["error_code"] == "manually_settled"):
                raise ExistingJobError("no provider proposal to approve")
            if approve and _digest(_latest_owned(conn, ad_id)) != row["source_sha256"]:
                raise ExistingJobError("original changed since suggestion")
        conn.executemany("UPDATE portfolio_existing_job_items SET phase=?,updated_at=? WHERE job_id=? AND ad_id=?",(("approved" if approve else "rejected",_now(),job_id,x) for x in ad_ids))
        return _progress(conn,job_id)


# Explicit, durable provider-spend permission for a *specific* existing job.
# Job budget and per-request reservations remain the authority for money;
# this record additionally caps autonomous request starts over restarts.
_AUTONOMY_CREATE = """
CREATE TABLE IF NOT EXISTS portfolio_existing_job_autonomy (
 job_id TEXT PRIMARY KEY COLLATE BINARY,
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 max_calls INTEGER NOT NULL CHECK(max_calls BETWEEN 1 AND 10000),
 used_calls INTEGER NOT NULL CHECK(used_calls BETWEEN 0 AND 10000),
 reason TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portfolio_existing_job_autonomy_grants (
 job_id TEXT NOT NULL COLLATE BINARY,
 grant_key TEXT NOT NULL UNIQUE,
 max_calls INTEGER NOT NULL CHECK(max_calls BETWEEN 1 AND 10000),
 authorized_at TEXT NOT NULL,
 PRIMARY KEY(job_id,grant_key));
"""
_GRANT_COLUMNS = {"job_id", "grant_key", "max_calls", "authorized_at"}
_AUTONOMY_COLUMNS = {"job_id", "enabled", "max_calls", "used_calls", "reason", "updated_at"}


def _autonomy_schema(conn: sqlite3.Connection, *, create: bool = False) -> bool:
    names = ("portfolio_existing_job_autonomy",
             "portfolio_existing_job_autonomy_grants")
    found = {row["name"]: row["type"] for row in conn.execute(
        "SELECT name,type FROM sqlite_master WHERE name IN (?,?)", names)}
    if not found:
        if not create:
            return False
        for statement in _AUTONOMY_CREATE.split(";"):
            if statement.strip():
                conn.execute(statement)
        return True
    if found != {name: "table" for name in names}:
        raise sqlite3.DatabaseError("partial existing-job autonomy schema")
    for name, columns in ((names[0], _AUTONOMY_COLUMNS),
                          (names[1], _GRANT_COLUMNS)):
        if {x["name"] for x in conn.execute(
                f"PRAGMA table_info({name})")} != columns:
            raise sqlite3.DatabaseError("invalid existing-job autonomy schema")
    return True


def _autonomy_status_conn(conn: sqlite3.Connection, job_id: str) -> dict[str, object]:
    if not _autonomy_schema(conn):
        return {"enabled": False, "max_calls": 0, "used_calls": 0,
                "remaining_calls": 0, "reason": "not_authorized"}
    row = conn.execute(
        "SELECT enabled,max_calls,used_calls,reason FROM portfolio_existing_job_autonomy "
        "WHERE job_id=?", (job_id,)
    ).fetchone()
    if row is None:
        return {"enabled": False, "max_calls": 0, "used_calls": 0,
                "remaining_calls": 0, "reason": "not_authorized"}
    return {"enabled": bool(row["enabled"]), "max_calls": row["max_calls"],
            "used_calls": row["used_calls"],
            "remaining_calls": max(0, row["max_calls"]-row["used_calls"]),
            "reason": row["reason"]}


def configure_autonomy(
    store: SnapshotStore, job_id: str, *, max_calls: int, grant_key: str,
) -> dict[str, object]:
    """Idempotent, exact-job authorization; every NEW grant needs a new key."""
    if type(max_calls) is not int or not 1 <= max_calls <= MAX_ITEMS:
        raise ExistingJobError("invalid autonomous call allowance")
    if type(grant_key) is not str or not _KEY.fullmatch(grant_key):
        raise ExistingJobError("invalid autonomy grant key")
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        job = _job(conn, job_id)
        _autonomy_schema(conn, create=True)
        earlier = conn.execute(
            "SELECT job_id,max_calls FROM portfolio_existing_job_autonomy_grants "
            "WHERE grant_key=?", (grant_key,)
        ).fetchone()
        if earlier:
            if earlier["job_id"] != job_id or earlier["max_calls"] != max_calls:
                raise ExistingJobError("autonomy grant key reused")
            # A duplicate message can NEVER reset exhausted credit, including
            # after another grant or a state change. This is a pure readback.
            return _progress(conn, job_id)
        active = conn.execute(
            "SELECT enabled FROM portfolio_existing_job_autonomy WHERE job_id=?",
            (job_id,)
        ).fetchone()
        if active and active["enabled"]:
            raise ExistingJobError("stop the current autonomous grant first")
        if (job["state"] != "running" or job["estimate_cents"] < 20
                or job["budget_cents"] < job["estimate_cents"]):
            raise ExistingJobError("job not ready for autonomous generation")
        counts = {r["phase"]: r["n"] for r in conn.execute(
            "SELECT phase,COUNT(*) n FROM portfolio_existing_job_items "
            "WHERE job_id=? GROUP BY phase", (job_id,)
        )}
        costs = conn.execute(
            "SELECT COALESCE(SUM(actual_cents+reserved_cents),0) "
            "FROM portfolio_existing_job_items WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        if (not counts.get("queued") or counts.get("inflight")
                or counts.get("unknown")
                or costs + job["estimate_cents"] > job["budget_cents"]):
            raise ExistingJobError("job requires manual attention or budget")
        stamp = _now()
        conn.execute(
            "INSERT INTO portfolio_existing_job_autonomy_grants VALUES(?,?,?,?)",
            (job_id, grant_key, max_calls, stamp),
        )
        conn.execute(
            "INSERT INTO portfolio_existing_job_autonomy VALUES(?,1,?,0,'enabled',?) "
            "ON CONFLICT(job_id) DO UPDATE SET enabled=1,max_calls=excluded.max_calls,"
            "used_calls=0,reason='enabled',updated_at=excluded.updated_at",
            (job_id, max_calls, stamp),
        )
        return _progress(conn, job_id)


def stop_autonomy(store: SnapshotStore, job_id: str, *, reason: str = "stopped") -> dict[str, object]:
    """Stop after any in-progress provider request; never revoke its claim."""
    if reason not in ("stopped", "provider_outcome_unknown", "session_limit",
                      "job_needs_attention", "database_error",
                      "finished", "budget_exhausted"):
        raise ExistingJobError("invalid autonomous stop reason")
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _job(conn, job_id)
        if _autonomy_schema(conn):
            conn.execute(
                "UPDATE portfolio_existing_job_autonomy SET enabled=0,reason=?,updated_at=? "
                "WHERE job_id=? AND (enabled=1 OR ?='provider_outcome_unknown')",
                (reason, _now(), job_id, reason)
            )
        return _progress(conn, job_id)


def next_autonomy_job(store: SnapshotStore) -> str | None:
    """One cheap candidate at a time. Never claim merely by polling."""
    with closing(store._connect()) as conn:
        if not _autonomy_schema(conn):
            return None
        row = conn.execute(
            "SELECT a.job_id FROM portfolio_existing_job_autonomy a "
            "JOIN portfolio_existing_jobs j ON j.job_id=a.job_id "
            "WHERE a.enabled=1 AND a.used_calls<a.max_calls AND j.state='running' "
            "AND EXISTS(SELECT 1 FROM portfolio_existing_job_items i "
            "WHERE i.job_id=j.job_id AND i.phase='queued') "
            "AND NOT EXISTS(SELECT 1 FROM portfolio_existing_job_items i "
            "WHERE i.job_id=j.job_id AND i.phase IN ('unknown','inflight')) "
            "ORDER BY a.updated_at,a.job_id LIMIT 1"
        ).fetchone()
        return row["job_id"] if row else None


def consume_autonomy_call(store: SnapshotStore, job_id: str) -> dict[str, object]:
    """Count a *possible* billable invocation durably BEFORE calling provider.

    Must run under the same generation session lock as claim_next/process_one.
    Losing a slot to a crash is safe; retrying an UNKNOWN external effect is not.
    """
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        job = _job(conn, job_id)
        if job["state"] != "running" or not _autonomy_schema(conn):
            raise ExistingJobError("autonomous job not running")
        row = conn.execute(
            "SELECT enabled,max_calls,used_calls FROM portfolio_existing_job_autonomy "
            "WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None or not row["enabled"] or row["used_calls"] >= row["max_calls"]:
            raise ExistingJobError("autonomous permission exhausted")
        next_used = row["used_calls"] + 1
        conn.execute(
            "UPDATE portfolio_existing_job_autonomy "
            "SET used_calls=?,enabled=?,reason=?,updated_at=? WHERE job_id=?",
            (next_used, int(next_used < row["max_calls"]),
             "enabled" if next_used < row["max_calls"] else "call_limit_reached",
             _now(), job_id),
        )
        return _progress(conn, job_id)


def recover_enabled_autonomy(store: SnapshotStore) -> int:
    """Startup: quarantine any pre-restart unresolved claim, never replay it."""
    with closing(store._connect()) as conn:
        if not _autonomy_schema(conn):
            return 0
        ids = [r["job_id"] for r in conn.execute(
            "SELECT DISTINCT a.job_id FROM portfolio_existing_job_autonomy a "
            "JOIN portfolio_existing_job_items i ON i.job_id=a.job_id "
            "WHERE i.phase='inflight'"
        )]
    for job_id in ids:
        recover_interrupted(store, job_id)
        stop_autonomy(store, job_id, reason="provider_outcome_unknown")

    # The last provider result may already be committed when the process
    # stops, before the worker advances the job to REVIEW and ends the grant.
    # Reconcile that entirely local transition atomically on restart. Never
    # contact a provider, repeat a claim, or finalize unresolved UNKNOWN work.
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        completed = conn.execute(
            "SELECT a.job_id,j.state FROM portfolio_existing_job_autonomy a "
            "JOIN portfolio_existing_jobs j ON j.job_id=a.job_id "
            "WHERE j.state IN ('running','review') "
            "AND (a.enabled=1 OR j.state='running') "
            "AND NOT EXISTS(SELECT 1 FROM portfolio_existing_job_items i "
            "WHERE i.job_id=j.job_id AND i.phase IN ('queued','inflight','unknown'))"
        ).fetchall()
        stamp = _now()
        for row in completed:
            if row["state"] == "running":
                conn.execute(
                    "UPDATE portfolio_existing_jobs SET state='review',updated_at=? "
                    "WHERE job_id=? AND state='running'",
                    (stamp, row["job_id"]),
                )
            conn.execute(
                "UPDATE portfolio_existing_job_autonomy "
                "SET enabled=0,reason='finished',updated_at=? WHERE job_id=?",
                (stamp, row["job_id"]),
            )
    return len(ids) + len(completed)
