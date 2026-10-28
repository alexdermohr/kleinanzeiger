"""P3: manual local sales ledger, separate from platform and EML-only IDs."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import sqlite3
from .storage import SnapshotStore
# Keep the management snapshot provenance available to offline readers without
# importing platform adapters during ordinary package/module startup.
# Must match DEFAULT_SOURCE in adapters/management.py.
MANAGEMENT_SNAPSHOT_SOURCE = "kleinanzeigen-management"

AD_ID=re.compile(r"[0-9]{1,32}\Z")
# The production management reader uses its own ad_snapshot provenance.
# private-web-* are sync journal labels, not the normal snapshot source.
# Local/EML imports remain ineligible for manually recorded sales.
OWN_INVENTORY_SOURCES=(
    MANAGEMENT_SNAPSHOT_SOURCE,
    "private-web-product-launcher",
    "private-web-owner-inventory",
    "private-web-periodic-readonly",
)

KEY=re.compile(r"[0-9a-f]{64}\Z")
DDL="""
CREATE TABLE IF NOT EXISTS portfolio_sale_states (
 ad_id TEXT PRIMARY KEY COLLATE BINARY,
 state TEXT NOT NULL CHECK(state IN ('available','reserved','sold')),
 revenue_eur INTEGER, cost_eur INTEGER,
 location TEXT NOT NULL, note TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>=1),
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_sale_events (
 event_id TEXT PRIMARY KEY COLLATE BINARY,
 request_key TEXT UNIQUE NOT NULL COLLATE BINARY,
 request_sha256 TEXT NOT NULL,
 ad_id TEXT NOT NULL COLLATE BINARY,
 changed_at TEXT NOT NULL, response_json TEXT NOT NULL
);
"""
COLUMNS={"ad_id","state","revenue_eur","cost_eur","location","note",
         "revision","updated_at"}
EVENT_COLUMNS={"event_id","request_key","request_sha256","ad_id",
               "changed_at","response_json"}

class SaleError(ValueError):
    """Invalid manual entry, missing own listing or stale edit."""

def schema(conn,create=False):
    found={r["name"]:r["type"] for r in conn.execute(
        "SELECT name,type FROM sqlite_master WHERE name IN "
        "('portfolio_sale_states','portfolio_sale_events')")}
    if not found:
        if not create:return False
        for statement in DDL.split(";"):
            if statement.strip():conn.execute(statement)
        found={"portfolio_sale_states":"table","portfolio_sale_events":"table"}
    if found!={"portfolio_sale_states":"table","portfolio_sale_events":"table"}:
        raise sqlite3.DatabaseError("partial local sales schema")
    for name,cols in (("portfolio_sale_states",COLUMNS),
                      ("portfolio_sale_events",EVENT_COLUMNS)):
        if {r["name"] for r in conn.execute(f"PRAGMA table_info({name})")}!=cols:
            raise sqlite3.DatabaseError("invalid local sales schema")
    return True

def _text(v,max_len):
    if type(v) is not str or len(v)>max_len or any(
            ord(c)<32 or 0xD800<=ord(c)<=0xDFFF for c in v):
        raise SaleError("invalid local note or location")
    return v.strip()

def _payload(p):
    if type(p) is not dict or set(p)!={"ad_id","state","revenue_eur",
            "cost_eur","location","note","request_key","expected_revision"}:
        raise SaleError("invalid manual sales payload")
    id,state,revenue,cost,key,revision=(p[k] for k in
        ("ad_id","state","revenue_eur","cost_eur","request_key","expected_revision"))
    if type(id) is not str or not AD_ID.fullmatch(id) or type(state) is not str \
            or state not in ("available","reserved","sold"):
        raise SaleError("invalid ad or manual state")
    if type(key) is not str or not KEY.fullmatch(key):
        raise SaleError("invalid idempotency key")
    if type(revision) is not int or not 0<=revision<=1_000_000:
        raise SaleError("invalid revision")
    if state=="sold":
        if type(revenue) is not int or not 0<=revenue<=99_999_999:
            raise SaleError("manual revenue required")
        if cost is not None and (type(cost) is not int or not 0<=cost<=99_999_999):
            raise SaleError("invalid cost")
    elif revenue is not None or cost is not None:
        raise SaleError("money only accepted for manual sale")
    return id,state,revenue,cost,_text(p["location"],120),_text(p["note"],240),key,revision

def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,
        ensure_ascii=True,separators=(",",":")).encode("utf-8")).hexdigest()

def update_manual_sale(store:SnapshotStore,payload:dict)->dict:
    """Atomic request-key replay + per-ad CAS. Never modifies actual account."""
    id,state,revenue,cost,location,note,key,revision=_payload(payload)
    frozen={"ad_id":id,"state":state,"revenue_eur":revenue,
            "cost_eur":cost,"location":location,"note":note,"revision":revision}
    digest=_hash(frozen)
    with closing(store._connect()) as conn,conn:
        conn.execute("BEGIN IMMEDIATE")
        schema(conn,create=True)
        replay=conn.execute("SELECT request_sha256,response_json FROM "
            "portfolio_sale_events WHERE request_key=?",(key,)).fetchone()
        if replay:
            if replay["request_sha256"]!=digest:raise SaleError("request key conflict")
            return json.loads(replay["response_json"])
        observed=conn.execute(
            "SELECT 1 FROM ad_snapshots WHERE ad_id=? COLLATE BINARY "
            "AND source IN (?,?,?,?) LIMIT 1",
            (id,*OWN_INVENTORY_SOURCES)).fetchone()
        if observed is None:raise SaleError("ad has no owned inventory observation")
        old=conn.execute("SELECT revision FROM portfolio_sale_states "
                         "WHERE ad_id=? COLLATE BINARY",(id,)).fetchone()
        if revision!=(old["revision"] if old else 0):
            raise SaleError("sale entry changed; reload")
        when=datetime.now(timezone.utc).isoformat()
        next_revision=revision+1
        conn.execute(
            "INSERT INTO portfolio_sale_states VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(ad_id) DO UPDATE SET state=excluded.state,"
            "revenue_eur=excluded.revenue_eur,cost_eur=excluded.cost_eur,"
            "location=excluded.location,note=excluded.note,"
            "revision=excluded.revision,updated_at=excluded.updated_at",
            (id,state,revenue,cost,location,note,next_revision,when))
        result={"ad_id":id,"state":state,"revenue_eur":revenue,"cost_eur":cost,
                "profit_eur":revenue-cost if revenue is not None and cost is not None else None,
                "location":location,"note":note,"revision":next_revision,
                "updated_at":when,"source":"manual_local_entry","platform_writes":0}
        conn.execute("INSERT INTO portfolio_sale_events VALUES (?,?,?,?,?,?)",
          (secrets.token_hex(16),key,digest,id,when,json.dumps(result,sort_keys=True)))
        return result

def sales_page(store:SnapshotStore,*,limit:int=50,after:str="")->dict:
    if type(limit) is not int or not 1<=limit<=50 or type(after) is not str \
        or (after and not AD_ID.fullmatch(after)):raise SaleError("invalid sales page")
    with closing(store._connect()) as conn:
        if not schema(conn):return {"sales":[],"total":0,"has_more":False,"next_after":None}
        count=conn.execute("SELECT COUNT(*) FROM portfolio_sale_states").fetchone()[0]
        rows=conn.execute(
            "SELECT s.*,(SELECT title FROM ad_snapshots a "
            " WHERE a.ad_id=s.ad_id COLLATE BINARY ORDER BY a.id DESC LIMIT 1) title,"
            "(SELECT COUNT(*) FROM inbound_message_events m "
            " WHERE m.ad_id=s.ad_id COLLATE BINARY) inbound "
            "FROM portfolio_sale_states s WHERE s.ad_id>? COLLATE BINARY "
            "ORDER BY s.ad_id COLLATE BINARY LIMIT ?",
            (after,limit+1)).fetchall()
        sales=[]
        for r in rows[:limit]:
            data={field:r[field] for field in COLUMNS}
            data.update({"title":r["title"] or "","imported_inbound_messages":r["inbound"],
                "profit_eur":r["revenue_eur"]-r["cost_eur"]
                if r["revenue_eur"] is not None and r["cost_eur"] is not None else None,
                "source":"manual_local_entry","platform_writes":0})
            sales.append(data)
        return {"sales":sales,"total":count,"has_more":len(rows)>limit,
            "next_after":rows[limit-1]["ad_id"] if len(rows)>limit else None}

def sale_detail(store:SnapshotStore,ad_id:str)->dict|None:
    """Read exact existing sale revision without scanning 50-item result pages."""
    if type(ad_id) is not str or AD_ID.fullmatch(ad_id) is None:
        raise SaleError("invalid sale detail ad")
    with closing(store._connect()) as conn:
        if not schema(conn):
            return None
        row=conn.execute(
            "SELECT * FROM portfolio_sale_states WHERE ad_id=? COLLATE BINARY",
            (ad_id,),
        ).fetchone()
        if row is None:
            return None
        item={field:row[field] for field in COLUMNS}
        item.update({
            "profit_eur": (row["revenue_eur"]-row["cost_eur"]
                if row["revenue_eur"] is not None and row["cost_eur"] is not None else None),
            "source":"manual_local_entry","platform_writes":0,
        })
        return item


def sale_totals(store:SnapshotStore)->dict:
    """Only reported manual entries; missing cost is UNKNOWN, never 0."""
    with closing(store._connect()) as conn:
        if not schema(conn):return {"sold":0,"reserved":0,"revenue_eur":0,
                                   "known_cost_eur":0,"unknown_cost_count":0,
                                   "known_profit_eur":0,"known_profit_count":0}
        r=conn.execute(
            "SELECT SUM(state='sold') sold,SUM(state='reserved') reserved,"
            "SUM(CASE WHEN state='sold' THEN revenue_eur ELSE 0 END) revenue,"
            "SUM(CASE WHEN state='sold' AND cost_eur IS NOT NULL THEN cost_eur ELSE 0 END) cost,"
            "SUM(state='sold' AND cost_eur IS NULL) unknown,"
            "SUM(CASE WHEN state='sold' AND cost_eur IS NOT NULL "
            " THEN revenue_eur-cost_eur ELSE 0 END) profit,"
            "SUM(state='sold' AND cost_eur IS NOT NULL) known "
            "FROM portfolio_sale_states").fetchone()
        return {"sold":r["sold"] or 0,"reserved":r["reserved"] or 0,
            "revenue_eur":r["revenue"] or 0,"known_cost_eur":r["cost"] or 0,
            "unknown_cost_count":r["unknown"] or 0,
            "known_profit_eur":r["profit"] or 0,
            "known_profit_count":r["known"] or 0}