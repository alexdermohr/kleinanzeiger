"""G2: reusable local item templates and independent unpublished draft copies.

Distinct from P5 AI work-instruction templates. All mutations are SQLite-local;
no platform IDs, ownership claims, write grants, browser or AI provider are copied.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import sqlite3
from typing import Any

from .portfolio_drafts import (
    DraftImportError, ProductDraft, _DDL as DRAFT_DDL, _SKU, _draft, _has_table,
)
from .storage import SnapshotStore

_ID = re.compile(r"[0-9a-f]{32}\Z")
_KEY = re.compile(r"[0-9a-f]{64}\Z")
_MAX_TEMPLATES = 100
_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_item_templates (
 template_id TEXT PRIMARY KEY COLLATE BINARY,
 name TEXT NOT NULL,
 name_key TEXT NOT NULL UNIQUE COLLATE BINARY,
 title TEXT NOT NULL,
 description TEXT NOT NULL,
 category_json TEXT NOT NULL,
 price_eur INTEGER NOT NULL,
 images_json TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision >= 1),
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_item_template_requests (
 request_key TEXT PRIMARY KEY COLLATE BINARY,
 request_sha256 TEXT NOT NULL,
 response_json TEXT NOT NULL
);
"""
_TABLE_COLS = {
    "portfolio_item_templates": {
        "template_id", "name", "name_key", "title", "description",
        "category_json", "price_eur", "images_json", "revision", "updated_at",
    },
    "portfolio_item_template_requests": {
        "request_key", "request_sha256", "response_json",
    },
}


class ItemTemplateError(ValueError):
    """Invalid local template, unavailable source or optimistic conflict."""


def _validate_id(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ItemTemplateError("Ungueltige Vorlagen-ID.")
    return value


def _validate_key(value: object) -> str:
    if type(value) is not str or _KEY.fullmatch(value) is None:
        raise ItemTemplateError("Ungueltiger Anfrage-Schluessel.")
    return value


def _validate_sku(value: object) -> str:
    if type(value) is not str or _SKU.fullmatch(value) is None:
        raise ItemTemplateError("Ungueltige neue Artikelnummer.")
    return value


def _validate_revision(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 1_000_000:
        raise ItemTemplateError("Ungueltige Vorlagen-Revision.")
    return value


def _validate_name(value: object) -> tuple[str, str]:
    if type(value) is not str:
        raise ItemTemplateError("Name der Artikelvorlage fehlt.")
    name = value.strip()
    if (not 1 <= len(name) <= 60
            or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF
                   for char in name)):
        raise ItemTemplateError("Name der Artikelvorlage ist ungueltig.")
    return name, name.casefold()


def _digest(data: object) -> str:
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _schema(conn: sqlite3.Connection, *, create: bool) -> bool:
    found = {
        row["name"]: row["type"] for row in conn.execute(
            "SELECT name,type FROM sqlite_master "
            "WHERE name IN ('portfolio_item_templates','portfolio_item_template_requests')"
        )
    }
    if not found:
        if not create:
            return False
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        found = {name: "table" for name in _TABLE_COLS}
    if found != {name: "table" for name in _TABLE_COLS}:
        raise sqlite3.DatabaseError("invalid item template schema")
    for table, expected in _TABLE_COLS.items():
        if {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")} != expected:
            raise sqlite3.DatabaseError("invalid item template columns")
    return True


def _fields_from_row(row: sqlite3.Row, sku: str) -> ProductDraft:
    try:
        category = json.loads(row["category_json"])
        images = json.loads(row["images_json"])
        if (type(category) is not list or type(images) is not list
                or any(type(value) is not str for value in category + images)):
            raise ValueError("invalid categories or images")
        return _draft(
            [sku, row["title"], row["description"], " > ".join(category),
             row["price_eur"], ";".join(images)],
            {"sku": 0, "title": 1, "description": 2, "category": 3,
             "price_eur": 4, "images": 5}, 1,
        )
    except (DraftImportError, ValueError, TypeError, KeyError):
        raise sqlite3.DatabaseError("invalid local item source data") from None


def _source_draft(conn: sqlite3.Connection, sku: str) -> ProductDraft:
    if not _has_table(conn):
        raise ItemTemplateError("Lokaler Quellartikel nicht vorhanden.")
    row = conn.execute(
        "SELECT sku,title,description,category_json,price_eur,images_json,status "
        "FROM portfolio_drafts WHERE sku=? COLLATE BINARY", (sku,),
    ).fetchone()
    if row is None:
        raise ItemTemplateError("Lokaler Quellartikel nicht vorhanden.")
    if row["status"] != "draft":
        raise sqlite3.DatabaseError("invalid source draft state")
    return _fields_from_row(row, sku)


def _replay(conn: sqlite3.Connection, key: str, digest: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT request_sha256,response_json "
        "FROM portfolio_item_template_requests WHERE request_key=?", (key,),
    ).fetchone()
    if row is None:
        return None
    if row["request_sha256"] != digest:
        raise ItemTemplateError("Anfrage-Schluessel wird bereits anders verwendet.")
    return json.loads(row["response_json"])


def _record(conn: sqlite3.Connection, key: str, digest: str, value: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO portfolio_item_template_requests VALUES (?,?,?)",
        (key, digest, json.dumps(value, ensure_ascii=True, sort_keys=True)),
    )


def _template_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = _fields_from_row(row, "TEMPLATE").as_dict()
    data.pop("sku")
    data.update({
        "template_id": row["template_id"], "name": row["name"],
        "revision": row["revision"], "updated_at": row["updated_at"],
        "platform_writes": 0,
    })
    return data


def list_item_templates(store: SnapshotStore) -> dict[str, Any]:
    """Read at most 100 item templates without creating a table."""
    with closing(store._connect()) as conn:
        if not _schema(conn, create=False):
            return {"templates": [], "limit": _MAX_TEMPLATES}
        rows = conn.execute(
            "SELECT * FROM portfolio_item_templates ORDER BY name_key,template_id "
            "LIMIT ?", (_MAX_TEMPLATES + 1,),
        ).fetchall()
        if len(rows) > _MAX_TEMPLATES:
            raise sqlite3.DatabaseError("item template count above limit")
        return {"templates": [_template_dict(row) for row in rows],
                "limit": _MAX_TEMPLATES}


def save_item_template(
    store: SnapshotStore, *, source_sku: str, name: str, request_key: str,
    template_id: str | None = None, expected_revision: int = 0,
) -> dict[str, Any]:
    """Capture a *local draft* as a reusable template; never copy an ad ID."""
    sku = _validate_sku(source_sku)
    clean_name, name_key = _validate_name(name)
    key = _validate_key(request_key)
    revision = _validate_revision(expected_revision)
    if (template_id is None) != (revision == 0):
        raise ItemTemplateError("Vorlagen-ID und Revision passen nicht zusammen.")
    if template_id is not None:
        _validate_id(template_id)
    digest = _digest({
        "action": "save", "source_sku": sku, "name": clean_name,
        "template_id": template_id, "expected_revision": revision,
    })
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _schema(conn, create=True)
        replay = _replay(conn, key, digest)
        if replay is not None:
            return replay
        source = _source_draft(conn, sku)
        if template_id is None:
            existing = conn.execute(
                "SELECT 1 FROM portfolio_item_templates WHERE name_key=?", (name_key,),
            ).fetchone()
            if existing is not None:
                raise ItemTemplateError("Artikelvorlage mit diesem Namen existiert.")
            count = conn.execute("SELECT COUNT(*) FROM portfolio_item_templates").fetchone()[0]
            if count >= _MAX_TEMPLATES:
                raise ItemTemplateError("Maximal 100 Artikelvorlagen.")
            template_id = secrets.token_hex(16)
            new_revision = 1
        else:
            before = conn.execute(
                "SELECT revision FROM portfolio_item_templates WHERE template_id=?",
                (template_id,),
            ).fetchone()
            if before is None or before["revision"] != revision:
                raise ItemTemplateError("Vorlage wurde geaendert; bitte neu laden.")
            conflict = conn.execute(
                "SELECT template_id FROM portfolio_item_templates WHERE name_key=?",
                (name_key,),
            ).fetchone()
            if conflict is not None and conflict["template_id"] != template_id:
                raise ItemTemplateError("Artikelvorlage mit diesem Namen existiert.")
            new_revision = revision + 1
        now = datetime.now(timezone.utc).isoformat()
        if revision == 0:
            conn.execute(
                "INSERT INTO portfolio_item_templates "
                "(template_id,name,name_key,title,description,category_json,price_eur,"
                "images_json,revision,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (template_id, clean_name, name_key, *source.fields(), new_revision, now),
            )
        else:
            conn.execute(
                "UPDATE portfolio_item_templates SET name=?,name_key=?,title=?,"
                "description=?,category_json=?,price_eur=?,images_json=?,"
                "revision=?,updated_at=? WHERE template_id=?",
                (clean_name, name_key, *source.fields(), new_revision, now, template_id),
            )
        answer = {
            "template_id": template_id, "name": clean_name,
            "revision": new_revision, "published": False, "platform_writes": 0,
        }
        _record(conn, key, digest, answer)
        return answer


def delete_item_template(
    store: SnapshotStore, *, template_id: str, expected_revision: int,
    request_key: str,
) -> dict[str, Any]:
    template_id = _validate_id(template_id)
    revision = _validate_revision(expected_revision, minimum=1)
    key = _validate_key(request_key)
    digest = _digest({"action": "delete", "template_id": template_id,
                      "expected_revision": revision})
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _schema(conn, create=False):
            raise ItemTemplateError("Artikelvorlage ist nicht vorhanden.")
        replay = _replay(conn, key, digest)
        if replay is not None:
            return replay
        result = conn.execute(
            "DELETE FROM portfolio_item_templates WHERE template_id=? AND revision=?",
            (template_id, revision),
        )
        if result.rowcount != 1:
            raise ItemTemplateError("Vorlage wurde geaendert; bitte neu laden.")
        answer = {"template_id": template_id, "deleted": True, "platform_writes": 0}
        _record(conn, key, digest, answer)
        return answer


def _new_draft(
    store: SnapshotStore, *, new_sku: str, request_key: str,
    source_kind: str, source_id: str,
) -> dict[str, Any]:
    sku = _validate_sku(new_sku)
    key = _validate_key(request_key)
    if source_kind not in ("template", "draft"):
        raise ValueError("invalid source kind")
    if source_kind == "template":
        _validate_id(source_id)
    else:
        _validate_sku(source_id)
    digest = _digest({
        "action": "copy", "source_kind": source_kind,
        "source_id": source_id, "new_sku": sku,
    })
    with closing(store._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _schema(conn, create=True)
        replay = _replay(conn, key, digest)
        if replay is not None:
            return replay
        if source_kind == "draft":
            source = _source_draft(conn, source_id)
        else:
            row = conn.execute(
                "SELECT * FROM portfolio_item_templates WHERE template_id=?",
                (source_id,),
            ).fetchone()
            if row is None:
                raise ItemTemplateError("Artikelvorlage ist nicht vorhanden.")
            source = _fields_from_row(row, sku)
        if not _has_table(conn):
            conn.execute(DRAFT_DDL)
        if conn.execute(
            "SELECT 1 FROM portfolio_drafts WHERE sku=? COLLATE BINARY", (sku,),
        ).fetchone() is not None:
            raise ItemTemplateError("Neue Artikelnummer ist bereits vergeben.")
        # No inherited platform ad ID, owner certificate or write permission exists
        # in the target table. The new SKU is the caller's independent local key.
        conn.execute(
            "INSERT INTO portfolio_drafts (sku,title,description,category_json,"
            "price_eur,images_json,source_sha256,source_name,created_at,status) "
            "VALUES (?,?,?,?,?,?,?,?,?,'draft')",
            (sku, *source.fields(), digest,
             "lokale-artikelvorlage" if source_kind == "template" else "lokale-kopie",
             datetime.now(timezone.utc).isoformat()),
        )
        answer = {
            "sku": sku, "status": "draft", "published": False,
            "platform_writes": 0, "created": True,
        }
        _record(conn, key, digest, answer)
        return answer


def draft_from_item_template(
    store: SnapshotStore, *, template_id: str, new_sku: str, request_key: str,
) -> dict[str, Any]:
    return _new_draft(store, new_sku=new_sku, request_key=request_key,
                      source_kind="template", source_id=template_id)


def duplicate_local_draft(
    store: SnapshotStore, *, source_sku: str, new_sku: str, request_key: str,
) -> dict[str, Any]:
    return _new_draft(store, new_sku=new_sku, request_key=request_key,
                      source_kind="draft", source_id=source_sku)
