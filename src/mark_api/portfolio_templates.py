"""P5: reusable local work instructions and bounded attention counts.

No provider client, browser session, platform write, timers or scheduled
background work is created. Users still approve each P2 job and every
possibly billable generation independently.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import sqlite3

from .storage import SnapshotStore
from .portfolio_jobs import _schema as jobs_schema
from .portfolio_sales import schema as sales_schema

_ID=re.compile(r"[0-9a-f]{32}\Z")
_KEY=re.compile(r"[0-9a-f]{64}\Z")
_DDL="""
CREATE TABLE IF NOT EXISTS portfolio_work_templates (
 template_id TEXT PRIMARY KEY COLLATE BINARY,
 name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE COLLATE BINARY,
 instruction TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>=1),
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_template_requests (
 request_key TEXT PRIMARY KEY COLLATE BINARY,
 request_sha256 TEXT NOT NULL,
 response_json TEXT NOT NULL
);
"""
_TPL_COLS={"template_id","name","name_key","instruction","revision","updated_at"}
_REQUEST_COLS={"request_key","request_sha256","response_json"}
MAX_TEMPLATES=100

class WorkTemplateError(ValueError):
    """Invalid template request, optimistic conflict or duplicate name."""


def _schema(conn:sqlite3.Connection,*,create:bool=False)->bool:
    found={row["name"]:row["type"] for row in conn.execute(
        "SELECT name,type FROM sqlite_master WHERE name IN "
        "('portfolio_work_templates','portfolio_template_requests')")}
    if not found:
        if not create:return False
        for sql in _DDL.split(";"):
            if sql.strip():conn.execute(sql)
        found={"portfolio_work_templates":"table",
               "portfolio_template_requests":"table"}
    if found!={"portfolio_work_templates":"table",
               "portfolio_template_requests":"table"}:
        raise sqlite3.DatabaseError("invalid partial template schema")
    for tab,cols in (("portfolio_work_templates",_TPL_COLS),
                     ("portfolio_template_requests",_REQUEST_COLS)):
        if {r["name"] for r in conn.execute(f"PRAGMA table_info({tab})")}!=cols:
            raise sqlite3.DatabaseError("invalid work template schema")
    return True


def _check_key(v:object)->str:
    if type(v) is not str or _KEY.fullmatch(v) is None:
        raise WorkTemplateError("invalid request identity")
    return v


def _name(value:object)->tuple[str,str]:
    if type(value) is not str:
        raise WorkTemplateError("template name required")
    value=value.strip()
    if (not 1<=len(value)<=60 or any(
            ord(c)<32 or 0xD800<=ord(c)<=0xDFFF for c in value)):
        raise WorkTemplateError("invalid template name")
    return value,value.casefold()


def _instruction(value:object)->str:
    if type(value) is not str:
        raise WorkTemplateError("instruction required")
    value=value.strip()
    if (not 1<=len(value)<=1500 or any(
            (ord(c)<32 and c not in "\n\t") or 0xD800<=ord(c)<=0xDFFF
            for c in value)):
        raise WorkTemplateError("invalid template instruction")
    return value


def _digest(data:object)->str:
    return hashlib.sha256(json.dumps(data,ensure_ascii=True,sort_keys=True,
        separators=(",",":")).encode("utf-8")).hexdigest()


def _row(row:sqlite3.Row)->dict[str,object]:
    return {"template_id":row["template_id"],"name":row["name"],
            "instruction":row["instruction"],"revision":row["revision"],
            "updated_at":row["updated_at"],"platform_writes":0}


def list_templates(store:SnapshotStore)->dict[str,object]:
    with closing(store._connect()) as conn:
        if not _schema(conn,create=False):
            return {"templates":[],"limit":MAX_TEMPLATES}
        items=conn.execute(
            "SELECT template_id,name,name_key,instruction,revision,updated_at "
            "FROM portfolio_work_templates ORDER BY name_key,template_id "
            "LIMIT ?",(MAX_TEMPLATES+1,)).fetchall()
        if len(items)>MAX_TEMPLATES:
            raise sqlite3.DatabaseError("work template count above limit")
        return {"templates":[_row(r) for r in items],
                "limit":MAX_TEMPLATES}


def mutate_template(store:SnapshotStore,payload:dict[str,object]
                   )->dict[str,object]:
    """Create/edit/delete with atomic exact-key replay + revision CAS."""
    if type(payload) is not dict or set(payload)!={
        "template_id","name","instruction","expected_revision",
        "request_key","delete",
    }:
        raise WorkTemplateError("invalid template mutation fields")
    template_id=payload["template_id"]
    revision=payload["expected_revision"]
    deleting=payload["delete"]
    if (template_id is not None and
            (type(template_id) is not str or _ID.fullmatch(template_id) is None)):
        raise WorkTemplateError("invalid template ID")
    if type(revision) is not int or not 0<=revision<=1_000_000:
        raise WorkTemplateError("invalid revision")
    if type(deleting) is not bool or (deleting and (not template_id or revision<1)):
        raise WorkTemplateError("invalid delete")
    key=_check_key(payload["request_key"])
    if deleting:
        if payload["name"] is not None or payload["instruction"] is not None:
            raise WorkTemplateError("deletion must not provide template text")
        name=None
        name_key=None
        instruction=None
    else:
        name,name_key=_name(payload["name"])
        instruction=_instruction(payload["instruction"])
        if (template_id is None)!=(revision==0):
            raise WorkTemplateError("revision mismatch for create/edit")
    intent={"template_id":template_id,"name":name,"instruction":instruction,
            "expected_revision":revision,"delete":deleting}
    digest=_digest(intent)
    with closing(store._connect()) as conn,conn:
        conn.execute("BEGIN IMMEDIATE")
        _schema(conn,create=True)
        replay=conn.execute(
            "SELECT request_sha256,response_json FROM portfolio_template_requests "
            "WHERE request_key=?",(key,)).fetchone()
        if replay is not None:
            if replay["request_sha256"]!=digest:
                raise WorkTemplateError("request key conflict")
            return json.loads(replay["response_json"])
        now=datetime.now(timezone.utc).isoformat()
        if template_id is None:
            count=conn.execute("SELECT COUNT(*) FROM portfolio_work_templates").fetchone()[0]
            if count>=MAX_TEMPLATES:
                raise WorkTemplateError("maximum 100 templates")
            template_id=secrets.token_hex(16)
            existing=conn.execute(
                "SELECT 1 FROM portfolio_work_templates WHERE name_key=?",
                (name_key,)).fetchone()
            if existing is not None:
                raise WorkTemplateError("duplicate template name")
            conn.execute(
                "INSERT INTO portfolio_work_templates VALUES (?,?,?,?,?,?)",
                (template_id,name,name_key,instruction,1,now),
            )
            response={"template_id":template_id,"name":name,
                      "instruction":instruction,"revision":1,
                      "updated_at":now,"platform_writes":0}
        else:
            before=conn.execute(
                "SELECT * FROM portfolio_work_templates "
                "WHERE template_id=? COLLATE BINARY",(template_id,)).fetchone()
            if before is None or before["revision"]!=revision:
                raise WorkTemplateError("template revision changed; reload")
            if deleting:
                conn.execute("DELETE FROM portfolio_work_templates WHERE template_id=?",
                             (template_id,))
                response={"template_id":template_id,"deleted":True,
                          "platform_writes":0}
            else:
                existing=conn.execute(
                    "SELECT template_id FROM portfolio_work_templates "
                    "WHERE name_key=? COLLATE BINARY",(name_key,)).fetchone()
                if existing is not None and existing["template_id"]!=template_id:
                    raise WorkTemplateError("duplicate template name")
                conn.execute(
                    "UPDATE portfolio_work_templates SET name=?,name_key=?,"
                    "instruction=?,revision=?,updated_at=? "
                    "WHERE template_id=?",
                    (name,name_key,instruction,revision+1,now,template_id))
                response={"template_id":template_id,"name":name,
                          "instruction":instruction,"revision":revision+1,
                          "updated_at":now,"platform_writes":0}
        conn.execute(
            "INSERT INTO portfolio_template_requests VALUES (?,?,?)",
            (key,digest,json.dumps(response,sort_keys=True,ensure_ascii=True)))
        return response


def attention_counts(store:SnapshotStore)->dict[str,object]:
    """Bounded counts only; no private subject/body content or auto-actions."""
    with closing(store._connect()) as conn:
        if jobs_schema(conn,create=False):
            pending=conn.execute(
                "SELECT SUM(i.phase='proposed' AND j.state!='cancelled') proposed,"
                "SUM(i.phase='unknown') unknown "
                "FROM portfolio_job_items AS i "
                "JOIN portfolio_jobs AS j ON j.job_id=i.job_id").fetchone()
            proposals=pending["proposed"] or 0
            ambiguous=pending["unknown"] or 0
        else:
            proposals=ambiguous=0
        if sales_schema(conn):
            sales=conn.execute(
                "SELECT SUM(state='sold' AND cost_eur IS NULL) missing,"
                "SUM(state='reserved' AND datetime(updated_at)<"
                "datetime('now','-14 days')) stale "
                "FROM portfolio_sale_states").fetchone()
            unknown_cost=sales["missing"] or 0
            old_reservations=sales["stale"] or 0
        else:
            unknown_cost=old_reservations=0
        return {"proposals_to_review":proposals,
                "unknown_ki_outcomes":ambiguous,
                "manual_sales_missing_cost":unknown_cost,
                "old_local_reservations":old_reservations,
                "platform_writes":0,"provider_calls":0}