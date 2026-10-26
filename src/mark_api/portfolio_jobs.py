"""P2: durable local AI proposal queue. No implicit provider or platform writes."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import sqlite3
from typing import Callable

from .portfolio_drafts import _has_table
from .storage import SnapshotStore

_ID = re.compile(r"[0-9a-f]{32}\Z")
_KEY = re.compile(r"[0-9a-f]{64}\Z")
_SKU = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
MAX_JOB_ITEMS = 5000
_DDL = """
CREATE TABLE IF NOT EXISTS portfolio_jobs (
 job_id TEXT PRIMARY KEY COLLATE BINARY,
 request_key TEXT UNIQUE NOT NULL,
 request_sha256 TEXT NOT NULL,
 instruction TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN
 ('queued','running','paused','review','needs_attention','budget_exhausted','cancelled')),
 budget_cents INTEGER NOT NULL CHECK(budget_cents >= 0),
 estimate_cents INTEGER NOT NULL CHECK(estimate_cents >= 1),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_job_items (
 job_id TEXT NOT NULL, sku TEXT NOT NULL COLLATE BINARY,
 source_json TEXT NOT NULL, source_sha256 TEXT NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN
 ('queued','inflight','proposed','approved','rejected','applied_local','unknown')),
 claim_token TEXT, proposal_json TEXT,
 reserved_cents INTEGER NOT NULL DEFAULT 0,
 actual_cents INTEGER NOT NULL DEFAULT 0,
 error_code TEXT, updated_at TEXT NOT NULL,
 PRIMARY KEY(job_id,sku)
);
CREATE INDEX IF NOT EXISTS idx_portfolio_job_items_phase
 ON portfolio_job_items(job_id,phase,sku);
"""
_JOB_COLUMNS = {"job_id","request_key","request_sha256","instruction","state",
                "budget_cents","estimate_cents","created_at","updated_at"}
_ITEM_COLUMNS = {"job_id","sku","source_json","source_sha256","phase",
                 "claim_token","proposal_json","reserved_cents","actual_cents",
                 "error_code","updated_at"}


class PortfolioJobError(ValueError):
    """Rejected job input, transition, expense or stale source."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _schema(conn: sqlite3.Connection, *, create: bool) -> bool:
    """Existing-db only, schema drift fail-closed; lazy additive P2 tables."""
    found = {r["name"]:r["type"] for r in conn.execute(
        "SELECT name,type FROM sqlite_master WHERE name IN ('portfolio_jobs','portfolio_job_items')")}
    if not found:
        if not create:
            return False
        for sql in _DDL.split(";"):
            if sql.strip():
                conn.execute(sql)
        found={"portfolio_jobs":"table","portfolio_job_items":"table"}
    if found != {"portfolio_jobs":"table","portfolio_job_items":"table"}:
        raise sqlite3.DatabaseError("partial portfolio job schema")
    for name, expected in (("portfolio_jobs",_JOB_COLUMNS),
                           ("portfolio_job_items",_ITEM_COLUMNS)):
        columns={r["name"] for r in conn.execute(f"PRAGMA table_info({name})")}
        if columns!=expected:
            raise sqlite3.DatabaseError("invalid portfolio job columns")
    return True


def _job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    if type(job_id) is not str or not _ID.fullmatch(job_id):
        raise PortfolioJobError("invalid job id")
    row=conn.execute("SELECT * FROM portfolio_jobs WHERE job_id=?",(job_id,)).fetchone()
    if row is None:
        raise PortfolioJobError("unknown job")
    return row


def _progress(conn: sqlite3.Connection, job_id: str) -> dict[str, object]:
    row=_job(conn,job_id)
    counts={r["phase"]:r["n"] for r in conn.execute(
        "SELECT phase,COUNT(*) n FROM portfolio_job_items WHERE job_id=? GROUP BY phase",
        (job_id,))}
    costs=conn.execute(
        "SELECT COALESCE(SUM(actual_cents),0) actual,"
        "COALESCE(SUM(reserved_cents),0) reserved "
        "FROM portfolio_job_items WHERE job_id=?",(job_id,)).fetchone()
    return {"job_id":job_id,"state":row["state"],"instruction":row["instruction"],
            "total":sum(counts.values()),"counts":counts,
            "budget_cents":row["budget_cents"],"estimate_cents":row["estimate_cents"],
            "actual_cents":costs["actual"],"reserved_cents":costs["reserved"],
            "created_at":row["created_at"],"updated_at":row["updated_at"],
            "platform_writes":0}


def create_job(store: SnapshotStore, skus: list[str], *, instruction: str,
               request_key: str, budget_cents: int, estimate_cents: int
               ) -> dict[str, object]:
    """Freeze full source data atomically; same request key is idempotent."""
    if type(skus) is not list or not 1<=len(skus)<=MAX_JOB_ITEMS:
        raise PortfolioJobError("select 1..5000 drafts")
    if any(type(s) is not str or not _SKU.fullmatch(s) for s in skus):
        raise PortfolioJobError("invalid SKU")
    if len(set(skus))!=len(skus):
        raise PortfolioJobError("duplicate SKU")
    if type(request_key) is not str or not _KEY.fullmatch(request_key):
        raise PortfolioJobError("invalid request key")
    if type(instruction) is not str or not 1<=len(instruction.strip())<=1500 or any(
        (ord(c)<32 and c not in "\n\t") or 0xD800<=ord(c)<=0xDFFF for c in instruction):
        raise PortfolioJobError("invalid instruction")
    if type(budget_cents) is not int or not 0<=budget_cents<=100_000_000:
        raise PortfolioJobError("invalid total budget")
    if type(estimate_cents) is not int or not 1<=estimate_cents<=100_000_000:
        raise PortfolioJobError("invalid estimated call cost")
    instruction=instruction.strip()
    items=sorted(skus)
    fingerprint=_digest([items,instruction,budget_cents,estimate_cents])
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _has_table(conn):
            raise PortfolioJobError("draft catalog missing")
        _schema(conn,create=True)
        previous=conn.execute(
            "SELECT job_id,request_sha256 FROM portfolio_jobs WHERE request_key=?",
            (request_key,)).fetchone()
        if previous is not None:
            if previous["request_sha256"]!=fingerprint:
                raise PortfolioJobError("request key reused for different payload")
            return _progress(conn,previous["job_id"])
        snapshot=[]
        for sku in items:
            row=conn.execute(
                "SELECT sku,title,description,category_json,price_eur,images_json,status "
                "FROM portfolio_drafts WHERE sku=? COLLATE BINARY",(sku,)).fetchone()
            if row is None or row["status"]!="draft":
                raise PortfolioJobError("missing or invalid SKU in selection")
            payload=dict(row)
            snapshot.append((sku,_canonical(payload),_digest(payload)))
        stamp=_now()
        job_id=secrets.token_hex(16)
        conn.execute("INSERT INTO portfolio_jobs VALUES(?,?,?,?,?,?,?,?,?)",
                     (job_id,request_key,fingerprint,instruction,"queued",
                      budget_cents,estimate_cents,stamp,stamp))
        conn.executemany(
            "INSERT INTO portfolio_job_items "
            "(job_id,sku,source_json,source_sha256,phase,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            ((job_id,sku,value,digest,"queued",stamp) for sku,value,digest in snapshot))
        return _progress(conn,job_id)


def matching_draft_skus(store: SnapshotStore, *, search: str = "") -> list[str]:
    """Select at most 5000 local draft identities; never platform ad IDs.

    This selection is only a candidate list. create_job snapshots and validates
    every selected source again under its own BEGIN IMMEDIATE transaction.
    """
    if (type(search) is not str or len(search)>120
            or any(ord(char)<32 or 0xD800<=ord(char)<=0xDFFF
                   for char in search)):
        raise PortfolioJobError("invalid draft search")
    with closing(store._connect()) as conn:
        if not _has_table(conn):
            return []
        conn.create_function("unicode_casefold", 1, lambda value: value.casefold(),
                             deterministic=True)
        condition="status='draft'"
        params: list[object]=[]
        if search:
            condition+=(" AND (instr(unicode_casefold(sku), ?) > 0 OR "
                        "instr(unicode_casefold(title), ?) > 0)")
            params.extend((search.casefold(),search.casefold()))
        rows=conn.execute(
            "SELECT sku FROM portfolio_drafts WHERE "+condition+
            " ORDER BY sku COLLATE BINARY LIMIT ?",
            (*params,MAX_JOB_ITEMS+1)).fetchall()
    if len(rows)>MAX_JOB_ITEMS:
        raise PortfolioJobError("more than 5000 matching drafts; narrow the search")
    if not rows:
        raise PortfolioJobError("no matching local drafts")
    return [row["sku"] for row in rows]


def list_jobs(store: SnapshotStore, *, limit: int=50) -> list[dict[str, object]]:
    if type(limit) is not int or not 1<=limit<=50:
        raise PortfolioJobError("invalid page size")
    with closing(store._connect()) as conn:
        if not _schema(conn,create=False):
            return []
        ids=[r["job_id"] for r in conn.execute(
            "SELECT job_id FROM portfolio_jobs ORDER BY created_at DESC,job_id DESC LIMIT ?",
            (limit,))]
        return [_progress(conn,jid) for jid in ids]


def details(store: SnapshotStore, job_id: str, *, after: str="",limit: int=50
            ) -> dict[str, object]:
    if type(limit) is not int or not 1<=limit<=50 or type(after) is not str or (
        after and not _SKU.fullmatch(after)):
        raise PortfolioJobError("invalid cursor or limit")
    with closing(store._connect()) as conn:
        if not _schema(conn,create=False):
            raise PortfolioJobError("no jobs")
        result=_progress(conn,job_id)
        rows=conn.execute(
            "SELECT sku,source_json,source_sha256,phase,proposal_json,"
            "actual_cents,error_code FROM portfolio_job_items "
            "WHERE job_id=? AND sku>? COLLATE BINARY "
            "ORDER BY sku COLLATE BINARY LIMIT ?",
            (job_id,after,limit+1)).fetchall()
        result["items"]=[
            {"sku":r["sku"],"original":json.loads(r["source_json"]),
             "source_sha256":r["source_sha256"],"phase":r["phase"],
             "proposal":json.loads(r["proposal_json"]) if r["proposal_json"] else None,
             "actual_cents":r["actual_cents"],"error_code":r["error_code"]}
            for r in rows[:limit]]
        result["has_more"]=len(rows)>limit
        result["next_after"]=rows[limit-1]["sku"] if len(rows)>limit else None
        return result


def change_state(store: SnapshotStore, job_id: str, action: str) -> dict[str, object]:
    if action not in ("start","pause","resume","cancel"):
        raise PortfolioJobError("invalid action")
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _schema(conn,create=False):
            raise PortfolioJobError("no jobs")
        row=_job(conn,job_id)
        state=row["state"]
        if action in ("start","resume"):
            if state=="running":
                return _progress(conn,job_id)
            if state not in ("queued","paused","budget_exhausted"):
                raise PortfolioJobError("job cannot be restarted")
            if conn.execute("SELECT COUNT(*) FROM portfolio_job_items "
                            "WHERE job_id=? AND phase='unknown'",
                            (job_id,)).fetchone()[0]:
                raise PortfolioJobError("unknown request outcome; never blind retry")
            if conn.execute("SELECT COUNT(*) FROM portfolio_job_items "
                            "WHERE job_id=? AND phase='inflight'",
                            (job_id,)).fetchone()[0]:
                raise PortfolioJobError("unsettled in-flight request; recover before resuming")
            if not conn.execute("SELECT COUNT(*) FROM portfolio_job_items "
                                "WHERE job_id=? AND phase='queued'",
                                (job_id,)).fetchone()[0]:
                raise PortfolioJobError("no queued items")
            next_state="running"
        elif action=="pause":
            if state=="cancelled":
                raise PortfolioJobError("job cancelled")
            next_state=("paused" if state in ("queued","running","budget_exhausted")
                        else state)
        else:
            next_state="cancelled"
        conn.execute("UPDATE portfolio_jobs SET state=?,updated_at=? WHERE job_id=?",
                     (next_state,_now(),job_id))
        return _progress(conn,job_id)


def claim_next(store: SnapshotStore, job_id: str) -> dict[str, object] | None:
    """Reserve one budgeted unit before an external request, never after."""
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        job=_job(conn,job_id)
        if job["state"]!="running":
            raise PortfolioJobError("job is not running")
        if conn.execute("SELECT COUNT(*) FROM portfolio_job_items "
                        "WHERE job_id=? AND phase='inflight'",
                        (job_id,)).fetchone()[0]:
            raise PortfolioJobError("in-flight request must finish or be reconciled")
        queued=conn.execute(
            "SELECT sku,source_json FROM portfolio_job_items "
            "WHERE job_id=? AND phase='queued' ORDER BY sku COLLATE BINARY LIMIT 1",
            (job_id,)).fetchone()
        if queued is None:
            inflight=conn.execute("SELECT COUNT(*) FROM portfolio_job_items "
                                  "WHERE job_id=? AND phase='inflight'",
                                  (job_id,)).fetchone()[0]
            if not inflight:
                conn.execute("UPDATE portfolio_jobs SET state='review',updated_at=? "
                             "WHERE job_id=?",(_now(),job_id))
            return None
        used=conn.execute(
            "SELECT COALESCE(SUM(actual_cents+reserved_cents),0) "
            "FROM portfolio_job_items WHERE job_id=?",(job_id,)).fetchone()[0]
        if used+job["estimate_cents"]>job["budget_cents"]:
            conn.execute(
                "UPDATE portfolio_jobs SET state='budget_exhausted',updated_at=? "
                "WHERE job_id=?",(_now(),job_id))
            return None
        token=secrets.token_hex(16)
        update=conn.execute(
            "UPDATE portfolio_job_items SET phase='inflight',claim_token=?,"
            "reserved_cents=?,updated_at=? "
            "WHERE job_id=? AND sku=? AND phase='queued'",
            (token,job["estimate_cents"],_now(),job_id,queued["sku"]))
        if update.rowcount!=1:
            raise sqlite3.DatabaseError("claim race")
        return {"job_id":job_id,"sku":queued["sku"],"claim_token":token,
                "original":json.loads(queued["source_json"]),
                "instruction":job["instruction"],
                "reserved_cents":job["estimate_cents"]}


def _proposal_json(proposal: dict[str,str]) -> str:
    if type(proposal) is not dict or set(proposal)!={"title","description"}:
        raise PortfolioJobError("only title and description suggestions accepted")
    for field,limit in (("title",65),("description",4000)):
        value=proposal[field]
        if type(value) is not str or not value.strip() or (
            len(value.encode("utf-16-le","surrogatepass"))//2>limit) or any(
            (ord(c)<32 and c not in "\n\t") or 0xD800<=ord(c)<=0xDFFF
            for c in value):
            raise PortfolioJobError("invalid suggestion")
    return _canonical(proposal)


def finish_claim(store: SnapshotStore, job_id: str, sku: str, token: str,
                 proposal: dict[str,str], *, actual_cents: int) -> dict[str,object]:
    """Persist one suggestion and actual charge. No automatic data changes."""
    data=_proposal_json(proposal)
    if type(actual_cents) is not int or not 0<=actual_cents<=100_000_000:
        raise PortfolioJobError("invalid actual cost")
    if type(token) is not str or not _ID.fullmatch(token):
        raise PortfolioJobError("invalid claim token")
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        job=_job(conn,job_id)
        updated=conn.execute(
            "UPDATE portfolio_job_items SET phase='proposed',proposal_json=?,"
            "actual_cents=?,reserved_cents=0,claim_token=NULL,updated_at=? "
            "WHERE job_id=? AND sku=? COLLATE BINARY "
            "AND phase='inflight' AND claim_token=?",
            (data,actual_cents,_now(),job_id,sku,token))
        if updated.rowcount!=1:
            raise PortfolioJobError("claim already completed or invalid")
        used=conn.execute(
            "SELECT COALESCE(SUM(actual_cents+reserved_cents),0) "
            "FROM portfolio_job_items WHERE job_id=?",(job_id,)).fetchone()[0]
        waiting=conn.execute(
            "SELECT COUNT(*) FROM portfolio_job_items "
            "WHERE job_id=? AND phase IN ('queued','inflight')",
            (job_id,)).fetchone()[0]
        if job["state"]=="running":
            state=("budget_exhausted" if used>job["budget_cents"] else
                   ("review" if not waiting else "running"))
            conn.execute("UPDATE portfolio_jobs SET state=?,updated_at=? WHERE job_id=?",
                         (state,_now(),job_id))
        return _progress(conn,job_id)


def mark_unknown(store: SnapshotStore, job_id: str, sku: str, token: str,
                 *, error_code: str="provider_outcome_unknown") -> dict[str,object]:
    """Retain reservations for possibly incurred charges; never retry blindly."""
    if type(token) is not str or not _ID.fullmatch(token):
        raise PortfolioJobError("invalid claim token")
    if type(error_code) is not str or not re.fullmatch(r"[a-z_]{1,48}",error_code):
        raise PortfolioJobError("invalid error code")
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _job(conn,job_id)
        updated=conn.execute(
            "UPDATE portfolio_job_items SET phase='unknown',error_code=?,updated_at=? "
            "WHERE job_id=? AND sku=? COLLATE BINARY "
            "AND phase='inflight' AND claim_token=?",
            (error_code,_now(),job_id,sku,token))
        if updated.rowcount!=1:
            raise PortfolioJobError("claim is no longer active")
        conn.execute("UPDATE portfolio_jobs SET state='needs_attention',updated_at=? "
                     "WHERE job_id=? AND state != 'cancelled'",(_now(),job_id))
        return _progress(conn,job_id)


def recover_interrupted(store: SnapshotStore, job_id: str) -> dict[str,object]:
    """After process restart, quarantine any unconfirmed provider calls."""
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _job(conn,job_id)
        rows=conn.execute(
            "UPDATE portfolio_job_items SET phase='unknown',"
            "error_code='interrupted_outcome_unknown',updated_at=? "
            "WHERE job_id=? AND phase='inflight'",(_now(),job_id))
        if rows.rowcount:
            conn.execute("UPDATE portfolio_jobs SET state='needs_attention',updated_at=? "
                         "WHERE job_id=? AND state != 'cancelled'",(_now(),job_id))
        return _progress(conn,job_id)


def settle_unknown(store: SnapshotStore, job_id: str, sku: str,
                   *, actual_cents: int, explicitly_checked: bool
                   ) -> dict[str,object]:
    """Manually reconcile uncertainty: skip consumed request, never re-call it."""
    if explicitly_checked is not True:
        raise PortfolioJobError("manual charge/outcome verification required")
    if type(actual_cents) is not int or not 0<=actual_cents<=100_000_000:
        raise PortfolioJobError("invalid checked charge")
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        job=_job(conn,job_id)
        updated=conn.execute(
            "UPDATE portfolio_job_items SET phase='rejected',actual_cents=?,"
            "reserved_cents=0,claim_token=NULL,error_code='manually_settled',updated_at=? "
            "WHERE job_id=? AND sku=? COLLATE BINARY AND phase='unknown'",
            (actual_cents,_now(),job_id,sku))
        if updated.rowcount!=1:
            raise PortfolioJobError("no unknown item to settle")
        remaining=conn.execute("SELECT COUNT(*) FROM portfolio_job_items "
                               "WHERE job_id=? AND phase='unknown'",
                               (job_id,)).fetchone()[0]
        if not remaining and job["state"]=="needs_attention":
            conn.execute("UPDATE portfolio_jobs SET state='paused',updated_at=? "
                         "WHERE job_id=?",(_now(),job_id))
        return _progress(conn,job_id)


def review_items(store: SnapshotStore, job_id: str, skus: list[str],
                 *, approve: bool) -> dict[str,object]:
    """Persistent human decisions; approval is NEVER platform authorization."""
    if type(approve) is not bool or type(skus) is not list or not 1<=len(skus)<=5000:
        raise PortfolioJobError("invalid review selection")
    if len(set(skus))!=len(skus) or any(
        type(s) is not str or not _SKU.fullmatch(s) for s in skus):
        raise PortfolioJobError("invalid SKU in review selection")
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row=_job(conn,job_id)
        if row["state"] in ("cancelled","needs_attention"):
            raise PortfolioJobError("job not ready for approvals")
        for sku in skus:
            phase=conn.execute("SELECT phase FROM portfolio_job_items "
                               "WHERE job_id=? AND sku=? COLLATE BINARY",
                               (job_id,sku)).fetchone()
            if phase is None or phase["phase"] not in ("proposed","approved","rejected"):
                raise PortfolioJobError("selection contains an unreviewable item")
        desired="approved" if approve else "rejected"
        conn.executemany(
            "UPDATE portfolio_job_items SET phase=?,updated_at=? "
            "WHERE job_id=? AND sku=? COLLATE BINARY",
            ((desired,_now(),job_id,sku) for sku in skus))
        return _progress(conn,job_id)


def apply_approved_local(store: SnapshotStore, job_id: str,
                         *, limit: int=5000) -> dict[str,object]:
    """Apply approved title/description edits to LOCAL DRAFTS only, atomically.

    Revalidates frozen source row before any update. No platform owner claim,
    Publish/Write or external side effect can occur from this function.
    """
    if type(limit) is not int or not 1<=limit<=MAX_JOB_ITEMS:
        raise PortfolioJobError("invalid apply limit")
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        job=_job(conn,job_id)
        if job["state"] in ("cancelled","needs_attention"):
            raise PortfolioJobError("job not eligible to apply")
        rows=conn.execute(
            "SELECT sku,source_sha256,proposal_json FROM portfolio_job_items "
            "WHERE job_id=? AND phase='approved' ORDER BY sku COLLATE BINARY LIMIT ?",
            (job_id,limit+1)).fetchall()
        if len(rows)>limit:
            raise PortfolioJobError("more approved items than apply limit")
        for item in rows:
            current=conn.execute(
                "SELECT sku,title,description,category_json,price_eur,images_json,status "
                "FROM portfolio_drafts WHERE sku=? COLLATE BINARY",
                (item["sku"],)).fetchone()
            if current is None or _digest(dict(current))!=item["source_sha256"]:
                raise PortfolioJobError("draft changed since selection; no partial edits")
        for item in rows:
            suggestion=json.loads(item["proposal_json"])
            conn.execute("UPDATE portfolio_drafts SET title=?,description=? "
                         "WHERE sku=? COLLATE BINARY",
                         (suggestion["title"],suggestion["description"],item["sku"]))
            conn.execute("UPDATE portfolio_job_items SET phase='applied_local',updated_at=? "
                         "WHERE job_id=? AND sku=? COLLATE BINARY",
                         (_now(),job_id,item["sku"]))
        return {"job_id":job_id,"applied":len(rows),"platform_writes":0}


def process_one(store: SnapshotStore, job_id: str,
                generator: Callable[[dict[str,object],str],tuple[dict[str,str],int]]
                ) -> dict[str,object]:
    """Opt-in injected synthetic/provider adapter; NEVER a default paid request."""
    claim=claim_next(store,job_id)
    if claim is None:
        return {"claimed":False,"job_id":job_id}
    try:
        suggestion,cost=generator(claim["original"],claim["instruction"])
        progress=finish_claim(store,job_id,claim["sku"],claim["claim_token"],
                              suggestion,actual_cents=cost)
        return {"claimed":True,"sku":claim["sku"],"progress":progress}
    except BaseException:
        mark_unknown(store,job_id,claim["sku"],claim["claim_token"])
        raise
