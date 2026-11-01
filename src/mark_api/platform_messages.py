"""Optional account-chat transport and one-attempt, locally journalled send protocol.

Independent Kleinanzeigen-Agent beta API (NOT an official kleinanzeigen.de API).
No request occurs until the account is explicitly enabled and configured.
No automated retries or sends; the user must approve each outgoing message.
Message bodies and buyer metadata are never written to the local outbox.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid4

from .domain import LifecycleState
from .storage import SnapshotStore


BASE = "https://api.kleinanzeigen-agent.de/api/v2/kleinanzeigen/account"
MAX_RESPONSE = 512 * 1024
# Full 4,000-character Unicode message plus send-request UUID and metadata.
# Browser JSON.stringify emits raw UTF-8; 16 KiB covers 4-byte scalars.
MAX_MESSAGE_BODY_BYTES = 16_384
_CONV = re.compile(r"[a-zA-Z0-9:_-]{1,128}\Z")
_AD = re.compile(r"[0-9]{1,32}\Z")
_KEY = re.compile(r"klaz_live_[A-Za-z0-9_-]{8,200}\Z")
_OK_SOURCES = frozenset((
    "kleinanzeigen-management", "private-web-owner-inventory",
    "private-web-product-launcher", "private-web-periodic-readonly",
))


class MessagingError(ValueError):
    """Stable, secret-free API error code."""

    def __init__(self, code: str, *, http_status: int = 400, outcome: str = "not_sent"):
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.outcome = outcome


class Transport(Protocol):
    def __call__(
        self, method: str, url: str, headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, bytes]: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrlLibTransport:
    """A single HTTPS request, no redirects, no retries, capped response."""

    def __init__(self, *, timeout: float = 12.0):
        self.timeout = timeout
        self._opener = build_opener(_NoRedirect())

    def __call__(
        self, method: str, url: str, headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, bytes]:
        if not url.startswith(BASE + "/"):
            raise MessagingError("invalid_provider_origin")
        req = Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                data = response.read(MAX_RESPONSE + 1)
                status = response.status
        except HTTPError as exc:
            status = exc.code
            data = exc.read(MAX_RESPONSE + 1)
        except (URLError, OSError, TimeoutError) as exc:
            # No error text: URLs and secrets may occur in diagnostics.
            raise MessagingError("transport_outcome_unknown", http_status=502,
                                 outcome="unknown") from None
        if len(data) > MAX_RESPONSE:
            raise MessagingError("oversized_provider_response", http_status=502,
                                 outcome="unknown" if method == "POST" else "not_sent")
        return int(status), data


def _string(value: object, *, maximum: int, name: str, allow_empty: bool = False) -> str:
    if type(value) is not str or len(value) > maximum or (
        not allow_empty and not value.strip()
    ):
        raise MessagingError("invalid_" + name)
    if any(ord(c) < 32 and c not in "\n\t" or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise MessagingError("invalid_" + name)
    return value


def _conversation(value: object) -> str:
    if type(value) is not str or _CONV.fullmatch(value) is None:
        raise MessagingError("invalid_conversation")
    return value


def _ad_id(value: object) -> str:
    if type(value) is not str or _AD.fullmatch(value) is None:
        raise MessagingError("invalid_ad")
    return value


def _uuid(value: object) -> str:
    if type(value) is not str:
        raise MessagingError("invalid_request_id")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise MessagingError("invalid_request_id") from None
    if str(parsed) != value:
        raise MessagingError("invalid_request_id")
    return value


def _pairs(items):
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise MessagingError("duplicate_provider_field", http_status=502)
        result[key] = value
    return result


def _provider_payload(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_RESPONSE:
        raise MessagingError("oversized_provider_response", http_status=502)
    try:
        wire = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise MessagingError("invalid_provider_json", http_status=502) from None
    if not isinstance(wire, dict):
        raise MessagingError("invalid_provider_response", http_status=502)
    return wire


class AccountClient:
    """Provider-specific, opt-in account API; never accepts arbitrary URLs."""

    def __init__(self, key: str, transport: Transport | None = None):
        if type(key) is not str or _KEY.fullmatch(key) is None:
            raise MessagingError("account_api_not_configured", http_status=503)
        self._key = key
        self.fingerprint = hashlib.sha256(key.encode("ascii")).hexdigest()
        self._transport = transport if transport is not None else UrlLibTransport()

    def _call(self, method: str, suffix: str, payload: dict | None = None) -> dict:
        if not suffix.startswith("/") or "//" in suffix:
            raise MessagingError("invalid_api_path")
        headers = {"klaz_key": self._key, "Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            status, raw = self._transport(method, BASE + suffix, headers, body)
        except MessagingError:
            raise
        except Exception:
            raise MessagingError("transport_outcome_unknown", http_status=502,
                                 outcome="unknown" if method == "POST" else "not_sent") from None
        try:
            wire = _provider_payload(raw)
        except MessagingError as exc:
            if method == "POST":
                raise MessagingError("message_outcome_unknown", http_status=502,
                                     outcome="unknown") from None
            raise
        if status < 200 or status >= 300 or wire.get("success") is not True:
            detail = wire.get("error")
            code = detail.get("code") if isinstance(detail, dict) else wire.get("code")
            outcome = wire.get("data", {}).get("outcome") if isinstance(wire.get("data"), dict) else None
            if code == "MESSAGE_OUTCOME_UNKNOWN" or outcome == "unknown":
                raise MessagingError("message_outcome_unknown", http_status=409, outcome="unknown")
            # For failed POST, assume unknown unless provider explicitly attests not_sent.
            if method == "POST" and outcome != "not_sent":
                raise MessagingError("message_outcome_unknown", http_status=502, outcome="unknown")
            safe = {
                "ACCOUNT_SUBSCRIPTION_REQUIRED": "account_subscription_required",
                "ACCOUNT_CONNECTION_PENDING": "account_connection_pending",
                "ACCOUNT_RECONNECT": "account_reconnect_required",
                "ACCOUNT_BUSY": "account_busy",
                "INSUFFICIENT_CREDITS": "insufficient_credits",
                "REQUEST_ID_CONFLICT": "request_id_conflict",
                "PROVIDER_RATE_LIMIT": "provider_rate_limit",
            }.get(code, "provider_unavailable")
            raise MessagingError(safe, http_status=409 if status == 409 else
                                 402 if status == 402 else 502, outcome="not_sent")
        data = wire.get("data")
        if not isinstance(data, dict):
            raise MessagingError("invalid_provider_data", http_status=502,
                                 outcome="unknown" if method == "POST" else "not_sent")
        return data

    def status(self) -> dict[str, object]:
        data = self._call("GET", "/status")
        state = data.get("status")
        if state not in ("connected", "disconnected", "reauth_required"):
            raise MessagingError("invalid_connection_status", http_status=502)
        return {"status": state, "provider": "kleinanzeigen-agent", "official": False}

    def conversations(self, *, page: int = 0, size: int = 25) -> dict[str, object]:
        if type(page) is not int or not 0 <= page <= 1000 or type(size) is not int or not 1 <= size <= 100:
            raise MessagingError("invalid_page")
        data = self._call("GET", "/conversations?" + urlencode({"page": page, "size": size}))
        chats = data.get("conversations")
        if not isinstance(chats, list) or len(chats) > size or type(data.get("has_more")) is not bool:
            raise MessagingError("invalid_provider_conversations", http_status=502)
        if data.get("page") != page:
            raise MessagingError("invalid_provider_page", http_status=502)
        seen: set[str] = set()
        result = []
        for item in chats:
            if not isinstance(item, dict):
                raise MessagingError("invalid_provider_conversations", http_status=502)
            cid = _conversation(item.get("conversation_id"))
            aid = _ad_id(item.get("ad_id"))
            if cid in seen or type(item.get("unread")) is not bool:
                raise MessagingError("invalid_provider_conversations", http_status=502)
            seen.add(cid)
            result.append({
                "conversation_id": cid, "ad_id": aid,
                "ad_title": _string(item.get("ad_title") or "", maximum=160, name="title", allow_empty=True),
                "counterparty": _string(item.get("counterparty") or "", maximum=160, name="counterparty", allow_empty=True),
                "preview": _string(item.get("preview") or "", maximum=800, name="preview", allow_empty=True),
                "received_at": _string(item.get("received_at") or "", maximum=64, name="received_at", allow_empty=True),
                "unread": item["unread"],
            })
        return {"conversations": result, "page": page, "has_more": data["has_more"]}

    def open_conversation(self, conversation_id: str) -> dict[str, object]:
        cid = _conversation(conversation_id)
        data = self._call("POST", "/conversations/" + quote(cid, safe="") + "/open")
        if data.get("conversation_id") not in (None, cid):
            raise MessagingError("conversation_mismatch", http_status=502)
        messages = data.get("messages")
        if not isinstance(messages, list) or len(messages) > 500:
            raise MessagingError("invalid_messages", http_status=502)
        seen: set[str] = set()
        validated = []
        for m in messages:
            if not isinstance(m, dict):
                raise MessagingError("invalid_messages", http_status=502)
            mid = _string(m.get("message_id"), maximum=160, name="message_id")
            if mid in seen or m.get("direction") not in ("received", "sent", "unknown"):
                raise MessagingError("invalid_messages", http_status=502)
            seen.add(mid)
            validated.append({
                "message_id": mid,
                "text": _string(m.get("text"), maximum=4000, name="text", allow_empty=True),
                "direction": m["direction"],
                "received_at": _string(m.get("received_at") or "", maximum=64, name="time", allow_empty=True),
            })
        return {"conversation_id": cid, "messages": validated, "may_mark_read": True}

    def send_message(self, conversation_id: str, message: str, request_id: str) -> dict[str, object]:
        cid = _conversation(conversation_id)
        text = _string(message, maximum=4000, name="message")
        if len(text) > 4000 or text != text.strip():
            raise MessagingError("invalid_message")
        rid = _uuid(request_id)
        data = self._call("POST", "/conversations/" + quote(cid, safe="") + "/messages",
                          {"message": text, "request_id": rid})
        if (data.get("request_id") != rid or data.get("conversation_id") != cid
                or data.get("sent") is not True):
            raise MessagingError("message_outcome_unknown", http_status=502, outcome="unknown")
        return {"request_id": rid, "conversation_id": cid, "sent": True}


class Outbox:
    """Persistent, body-free, one-attempt intent journal.

    A prepared message is marked unknown *before* the external send. A process
    crash cannot silently send a second message after restart.
    """

    _DDL = """CREATE TABLE IF NOT EXISTS message_outbox (
        request_id TEXT PRIMARY KEY, account_fingerprint TEXT NOT NULL,
        conversation_id TEXT NOT NULL, ad_id TEXT NOT NULL,
        body_sha256 TEXT NOT NULL, state TEXT NOT NULL
            CHECK (state IN ('prepared','unknown','sent','not_sent')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )"""

    def __init__(self, path: Path):
        if not isinstance(path, Path):
            raise TypeError("outbox must use a pathlib.Path")
        self.path = path

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=4)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=4000")
        conn.execute(self._DDL)
        return conn

    @staticmethod
    def _hash(message: str) -> str:
        return hashlib.sha256(message.encode("utf-8")).hexdigest()

    def prepare(self, account: str, conversation: str, ad_id: str, message: str) -> dict:
        cid, aid = _conversation(conversation), _ad_id(ad_id)
        text = _string(message, maximum=4000, name="message")
        if text != text.strip():
            raise MessagingError("invalid_message")
        now = datetime.now(timezone.utc).isoformat()
        rid = str(uuid4())
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            # A different request-id must never resend the same text to the same
            # conversation while its older outcome remains uncertain/sent.
            previous = conn.execute(
                "SELECT state FROM message_outbox WHERE account_fingerprint=? "
                "AND conversation_id=? AND body_sha256=? ORDER BY created_at DESC LIMIT 1",
                (account, cid, self._hash(text)),
            ).fetchone()
            if previous and previous["state"] in ("prepared", "unknown", "sent"):
                raise MessagingError("duplicate_or_unknown_send", http_status=409)
            conn.execute(
                "INSERT INTO message_outbox VALUES (?,?,?,?,?,?,?,?)",
                (rid, account, cid, aid, self._hash(text), "prepared", now, now),
            )
        return {"request_id": rid, "conversation_id": cid, "ad_id": aid,
                "state": "prepared", "message_sha256": self._hash(text)}

    def state(self, request_id: str) -> dict:
        rid = _uuid(request_id)
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT request_id,conversation_id,ad_id,state,created_at,updated_at "
                "FROM message_outbox WHERE request_id=?", (rid,),
            ).fetchone()
        if row is None:
            raise MessagingError("unknown_request_id", http_status=404)
        return dict(row)

    def dispatch(self, account: str, request_id: str, conversation: str,
                 ad_id: str, message: str, *, confirmed: bool,
                 client: AccountClient) -> dict:
        rid, cid, aid = _uuid(request_id), _conversation(conversation), _ad_id(ad_id)
        text = _string(message, maximum=4000, name="message")
        if text != text.strip() or confirmed is not True:
            raise MessagingError("explicit_send_confirmation_required")
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM message_outbox WHERE request_id=?", (rid,)).fetchone()
            if (row is None or row["account_fingerprint"] != account
                    or row["conversation_id"] != cid or row["ad_id"] != aid
                    or row["body_sha256"] != self._hash(text)):
                raise MessagingError("send_intent_mismatch", http_status=409)
            if row["state"] != "prepared":
                raise MessagingError("send_not_retryable", http_status=409,
                                     outcome="unknown" if row["state"] == "unknown" else "not_sent")
            conn.execute("UPDATE message_outbox SET state='unknown',updated_at=? "
                         "WHERE request_id=?", (datetime.now(timezone.utc).isoformat(), rid))
        try:
            result = client.send_message(cid, text, rid)
        except MessagingError as exc:
            if exc.outcome == "not_sent":
                with closing(self._connect()) as conn, conn:
                    conn.execute("UPDATE message_outbox SET state='not_sent',updated_at=? "
                                 "WHERE request_id=? AND state='unknown'",
                                 (datetime.now(timezone.utc).isoformat(), rid))
            raise
        except Exception:
            raise MessagingError("message_outcome_unknown", http_status=502, outcome="unknown") from None
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE message_outbox SET state='sent',updated_at=? "
                         "WHERE request_id=? AND state='unknown'",
                         (datetime.now(timezone.utc).isoformat(), rid))
        return {**result, "state": "sent", "delivered": False}


def require_locally_owned(store: SnapshotStore, ad_id: str) -> dict[str, str]:
    """A historical local snapshot is only a *candidate*, never fresh owner proof."""
    aid = _ad_id(ad_id)
    snapshot = store.latest_ad_snapshot(aid)
    if (snapshot is None or snapshot.source not in _OK_SOURCES
            or snapshot.lifecycle_state not in (LifecycleState.ACTIVE, LifecycleState.PAUSED)):
        raise MessagingError("no_confirmed_local_owner", http_status=403)
    return {"ad_id": aid, "title": snapshot.title or "", "local_owner_only": True}

# A dedicated, local-only customer window avoids broadening the privileged
# dashboard's proxy/write surface or affecting an existing Chrome login.
# The optional paid third-party account API is never automatically polled.
_PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kleinanzeiger · Nachrichten</title>
<style>
body{font:16px system-ui,sans-serif;background:#f5f8fa;color:#17313b;margin:0}
main{max-width:980px;margin:auto;padding:28px}h1{margin-bottom:0}
section{background:#fff;border:1px solid #d2dee4;border-radius:12px;padding:22px;margin:18px 0}
button,input,textarea{font:inherit}button{padding:9px 15px;cursor:pointer;margin:4px}
button:disabled{opacity:.5;cursor:default}textarea{width:98%;height:120px}
small,.muted{color:#536e79}#status{white-space:pre-wrap;padding:12px 0}
li{border-bottom:1px solid #e6ecef;padding:9px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}
fieldset{border:1px solid #d2dee4;border-radius:8px}label{display:block;margin:10px 0}
</style></head><body><main><h1>Kleinanzeiger · Nachrichten</h1>
<p>Eigenes Konto – optionaler API-Zugang. Der externe Anbieter Kleinanzeigen Agent
ist nicht kleinanzeigen.de. Ohne aktiv eingerichteten Zugang erfolgen keine Anfragen.</p>
<section><h2>1. Konto und Chats</h2><p>Nur nach Klick: Status 0 Credits,
Chatliste 5 Credits, Chat öffnen 10 Credits (kann als gelesen markieren).
Es gibt keine automatische Abfrage.</p>
<button id="status-check">Verbindung prüfen (kostenlos)</button>
<button id="list-chats">Chats laden (5 Credits)</button>
<button id="next-chats" disabled>Nächste Seite (5 Credits)</button>
<p id="connection"></p><ol id="chats"></ol></section>
<section><h2>2. Gespräch und Antwort</h2>
<p>Nur Chats mit passender lokal gespeicherter eigener Anzeige sind in dieser
Ansicht für Antworten freigegeben. Die lokale Zuordnung ersetzt keinen aktuellen
Plattform-Eigentumsnachweis.</p><div id="conversation"></div>
<label>Antwortentwurf (vor dem Versand selbst prüfen)
<textarea id="reply" maxlength="4000" placeholder="Nur die tatsächlich gewünschte Nachricht"></textarea></label>
<button id="template">Neutralen Entwurf einfügen</button>
<button id="send" disabled>Diese eine Antwort senden (25 Credits)</button>
<p>Der Versand benötigt eine gesonderte Bestätigung. Kleinanzeiger speichert
nur die Auftrags-ID, den Text-Hash und den Ausgang, nicht Käuferdaten oder Text.
Ein unbekannter Ausgang wird nie automatisch erneut gesendet.</p></section>
<section><h2>3. Betriebsstatus</h2><p id="status" role="status" aria-live="polite">
Zum Start Verbindung prüfen. Ein kontogebundener Live-Test ist noch nicht erfolgt.</p></section>
</main><script src="/inbox.js"></script></body></html>"""

_SCRIPT = r"""'use strict';
(function(){
  const by=(id)=>document.getElementById(id);
  const keyFromHash=location.hash.startsWith('#token=') ?
    location.hash.substring(7) : '';
  let session=keyFromHash || sessionStorage.getItem('kleinanzeiger-inbox-session') || '';
  if(keyFromHash){sessionStorage.setItem('kleinanzeiger-inbox-session',session);
    history.replaceState(null,'',location.pathname);}
  let page=0,busy=false,selected=null,opened=false;
  const status=(v)=>{by('status').textContent=v};
  const idle=(enabled)=>{busy=!enabled;
    by('status-check').disabled=busy;
    by('list-chats').disabled=busy;
    by('next-chats').disabled=busy || by('next-chats').dataset.hasMore!=='true';
    by('send').disabled=busy || !opened || !by('reply').value.trim();
  };
  async function request(path,method,body){
    if(!session)throw new Error('Sitzung fehlt. Nachrichtenfenster erneut starten.');
    const response=await fetch(path,{method:method || 'GET',
      headers:{'X-Kleinanzeiger-Session':session,'Content-Type':'application/json'},
      body:body===undefined?undefined:JSON.stringify(body),
      credentials:'omit',mode:'same-origin',cache:'no-store',redirect:'error'});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error || ('HTTP '+response.status));
    return data;
  }
  function clearConversation(){
    selected=null;opened=false;by('conversation').replaceChildren();
    by('reply').value='';idle(!busy);
  }
  function addText(parent,tag,value){
    const node=document.createElement(tag);node.textContent=value;parent.append(node);return node;
  }
  by('status-check').addEventListener('click',async()=>{
    idle(false);
    try {let v=await request('/api/status');
      by('connection').textContent='Verbindungsstatus: '+v.status+
        ' · Anbieter: '+v.provider+' (unabhängig, nicht offiziell)';
      status(v.status==='connected'?'Konto verbunden. Chats nur nach Klick laden.':
        'Konto nicht verbunden. Optionalen Zugang einrichten, keine Chats geladen.');
    }catch(e){status('Keine Kontoverbindung: '+e.message)}
    finally{idle(true)}
  });
  async function listChats(next){
    idle(false);clearConversation();
    if(!next)page=0;
    try {
      const data=await request('/api/conversations?page='+page+'&size=25');
      by('chats').replaceChildren();
      for(const item of data.conversations){
        const li=document.createElement('li');
        addText(li,'strong',(item.unread?'Ungelesen · ':'')+
          (item.ad_title||'Ohne Anzeigentitel'));
        addText(li,'p','Anzeige '+item.ad_id+' · '+(item.counterparty||'Unbekannt')+
          ' · '+(item.preview||'Keine Vorschau'));
        const button=document.createElement('button');
        button.type='button';button.textContent='Chat öffnen (10 Credits, ggf. gelesen)';
        button.disabled=item.local_own_ad!==true;
        if(button.disabled)addText(li,'small','Keine lokal bestätigte eigene Anzeige; Antworten hier gesperrt.');
        button.addEventListener('click',()=>openChat(item));li.append(button);by('chats').append(li);
      }
      by('next-chats').dataset.hasMore=data.has_more?'true':'false';
      status('Chatseite '+(page+1)+' geladen. '+data.conversations.length+
        ' Chats; es wird keine Gesamtzahl behauptet.');
    }catch(e){if(next)page=Math.max(0,page-1);status('Chatliste fehlgeschlagen: '+e.message)}
    finally{idle(true)}
  }
  by('list-chats').addEventListener('click',()=>listChats(false));
  by('next-chats').addEventListener('click',()=>{page++;listChats(true)});
  async function openChat(item){
    if(!window.confirm('Diesen Chat öffnen? 10 Credits; er kann auf Kleinanzeigen als gelesen markiert werden.'))return;
    idle(false);clearConversation();
    try{
      const data=await request('/api/open','POST',{
        conversation_id:item.conversation_id,ad_id:item.ad_id,confirm_mark_read:true});
      selected=item;opened=true;
      const target=by('conversation');target.replaceChildren();
      addText(target,'h3','Gespräch zur Anzeige '+item.ad_id);
      for(const m of data.messages){
        addText(target,'p',(m.direction==='received'?'Interessent':'Ausgehend')+
          ' · '+(m.received_at||'Zeitpunkt unbekannt'));
        addText(target,'pre',m.text);
      }
      status('Chat geladen. Die Freigabe für eine Antwort gilt nur in dieser Sitzung.');
    }catch(e){status('Chat konnte nicht geöffnet werden: '+e.message)}
    finally{idle(true)}
  }
  by('template').addEventListener('click',()=>{
    if(!selected || !opened)return;
    by('reply').value='Guten Tag,\n\nvielen Dank für Ihre Anfrage. '+
      '[Bitte die Antwort anhand der tatsächlichen Fakten ergänzen.]\n\nFreundliche Grüße';
    idle(!busy);
  });
  by('reply').addEventListener('input',()=>idle(!busy));
  by('send').addEventListener('click',async()=>{
    if(!selected || !opened || busy)return;
    const message=by('reply').value;
    if(!message.trim() || message.length>4000){status('Antworttext fehlt oder ist zu lang.');return}
    if(!window.confirm('Genau diese Nachricht kostenpflichtig SENDEN? 25 Credits. '+
        'Der Versand an den Gesprächspartner ist eine echte Plattformaktion.\n\n'+
        message.slice(0,550)))return;
    idle(false);
    try{
      const p=await request('/api/prepare','POST',{
        conversation_id:selected.conversation_id,ad_id:selected.ad_id,message});
      const sent=await request('/api/send','POST',{
        request_id:p.request_id,conversation_id:selected.conversation_id,
        ad_id:selected.ad_id,message,confirmed:true});
      if(sent.state!=='sent'||sent.sent!==true)throw Error('Versandausgang unbekannt');
      status('Nachricht von der Plattform angenommen (nicht als zugestellt nachgewiesen). '+
        'Request-ID: '+sent.request_id+'. Kein weiterer Versand.');
      by('reply').value='';opened=false;
    }catch(e){
      status('KEIN BESTÄTIGTER VERSAND: '+e.message+'. Bei unbekanntem Ausgang '+
        'erst den Chat unabhängig prüfen; niemals blind erneut senden.');
      opened=false;
    }finally{idle(true)}
  });
  if(!session)status('Fenster ohne gültige lokale Sitzung gestartet. Bitte aus Kleinanzeiger erneut öffnen.');
})();"""


def create_inbox_server(
    store: SnapshotStore, outbox: Outbox, *,
    client: AccountClient | None,
    session_token: str,
    port: int = 0,
):
    """Only loopback; all account effects are click-driven and session-bound."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import RLock
    from urllib.parse import parse_qs, urlsplit
    import hmac

    if type(store) is not SnapshotStore or type(outbox) is not Outbox:
        raise TypeError("local snapshot and journal required")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("invalid listen port")
    if type(session_token) is not str or len(session_token) < 32:
        raise ValueError("invalid session token")
    lock = RLock()
    known: dict[str, str] = {}
    opened: set[str] = set()

    class InboxHandler(BaseHTTPRequestHandler):
        server_version = "kleinanzeiger-inbox/1"
        sys_version = ""

        def log_message(self, *args):
            pass

        def parse_request(self):
            if not super().parse_request():
                return False
            port_ = self.server.server_address[1]
            if self.headers.get_all("Host") != [f"127.0.0.1:{port_}"]:
                self.send_error(421, "Misdirected Request")
                return False
            return True

        def respond(self, status, data, content_type="application/json; charset=utf-8"):
            if isinstance(data, (dict, list)):
                raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            elif isinstance(data, str):
                raw = data.encode("utf-8")
            else:
                raw = data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
                             "connect-src 'self'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            value = self.headers.get("X-Kleinanzeiger-Session", "")
            return hmac.compare_digest(value, session_token)

        def require_auth(self):
            if not self.authorized():
                self.respond(403, {"error": "local_session_required"})
                return False
            return True

        def require_client(self):
            if client is None:
                self.respond(503, {"error": "account_api_not_configured"})
                return False
            return True

        def error(self, exc):
            if isinstance(exc, MessagingError):
                self.respond(exc.http_status, {"error": exc.code, "outcome": exc.outcome,
                                                "automatic_retry_authorized": False})
            else:
                self.respond(503, {"error": "account_service_unavailable",
                                   "automatic_retry_authorized": False})

        def do_GET(self):
            from urllib.parse import urlsplit, parse_qs
            target = urlsplit(self.path)
            if target.query and target.path not in ("/api/conversations", "/api/outbox"):
                self.respond(400, {"error": "unexpected_query"}); return
            if target.path == "/":
                self.respond(200, _PAGE, "text/html; charset=utf-8");return
            if target.path == "/inbox.js":
                self.respond(200, _SCRIPT, "text/javascript; charset=utf-8");return
            if target.path == "/healthz":
                self.respond(200, {"status": "local_ok"});return
            if not self.require_auth():return
            if target.path == "/api/status":
                if client is None:
                    self.respond(200, {"status": "not_configured",
                                       "provider": "kleinanzeigen-agent", "official": False});return
                try:self.respond(200, client.status())
                except Exception as exc:self.error(exc)
                return
            if target.path == "/api/outbox":
                try:
                    params = parse_qs(target.query, strict_parsing=True)
                    if set(params) != {"request_id"} or len(params["request_id"]) != 1:
                        raise MessagingError("invalid_request_id")
                    self.respond(200, outbox.state(params["request_id"][0]))
                except Exception as exc:self.error(exc)
                return
            if target.path == "/api/conversations":
                if not self.require_client():return
                try:
                    params = parse_qs(target.query, strict_parsing=True)
                    if set(params) - {"page", "size"} or any(len(v) != 1 for v in params.values()):
                        raise MessagingError("invalid_page")
                    page_ = params.get("page", ["0"])[0]
                    size_ = params.get("size", ["25"])[0]
                    if not (page_.isascii() and page_.isdecimal()
                            and size_.isascii() and size_.isdecimal()):
                        raise MessagingError("invalid_page")
                    data = client.conversations(page=int(page_), size=int(size_))
                    for item in data["conversations"]:
                        try:
                            require_locally_owned(store, item["ad_id"])
                            item["local_own_ad"] = True
                        except MessagingError:
                            item["local_own_ad"] = False
                    with lock:
                        known.clear()
                        opened.clear()
                        known.update({item["conversation_id"]: item["ad_id"]
                                      for item in data["conversations"] if item["local_own_ad"]})
                    self.respond(200, data)
                except Exception as exc:self.error(exc)
                return
            self.respond(404, {"error": "not_found"})

        def read_body(self):
            raw_length = self.headers.get("Content-Length", "")
            if not raw_length.isascii() or not raw_length.isdecimal() or len(raw_length) > 5:
                raise MessagingError("invalid_body_length")
            length = int(raw_length)
            if length < 2 or length > MAX_MESSAGE_BODY_BYTES:
                raise MessagingError("invalid_body_length")
            raw = self.rfile.read(length)
            try: value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
            except (UnicodeError, ValueError, TypeError, RecursionError):
                raise MessagingError("invalid_json") from None
            if not isinstance(value, dict):
                raise MessagingError("invalid_json")
            return value

        def do_POST(self):
            target = urlsplit(self.path)
            if (target.query or target.fragment or target.path != self.path or
                    target.path not in ("/api/open", "/api/prepare", "/api/send")):
                self.respond(404, {"error": "not_found"});return
            if not self.require_auth() or not self.require_client():return
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://127.0.0.1:{self.server.server_address[1]}":
                self.respond(403, {"error": "invalid_origin"});return
            try:
                body = self.read_body()
                cid = _conversation(body.get("conversation_id"))
                aid = _ad_id(body.get("ad_id"))
                with lock:
                    if known.get(cid) != aid:
                        raise MessagingError("conversation_not_listed", http_status=409)
                    if target.path != "/api/open" and cid not in opened:
                        raise MessagingError("conversation_not_opened", http_status=409)
                require_locally_owned(store, aid)
                if target.path == "/api/open":
                    if set(body) != {"conversation_id", "ad_id", "confirm_mark_read"} or body["confirm_mark_read"] is not True:
                        raise MessagingError("explicit_open_confirmation_required")
                    answer = client.open_conversation(cid)
                    with lock:opened.add(cid)
                elif target.path == "/api/prepare":
                    if set(body) != {"conversation_id", "ad_id", "message"}:
                        raise MessagingError("invalid_prepare_fields")
                    answer = outbox.prepare(client.fingerprint, cid, aid, body["message"])
                else:
                    if set(body) != {"conversation_id", "ad_id", "message", "request_id", "confirmed"}:
                        raise MessagingError("invalid_send_fields")
                    answer = outbox.dispatch(
                        client.fingerprint, body["request_id"], cid, aid,
                        body["message"], confirmed=body["confirmed"], client=client)
                self.respond(200, answer)
            except Exception as exc:
                self.error(exc)

        def do_OPTIONS(self):
            self.respond(405, {"error": "method_not_allowed"})

    return ThreadingHTTPServer(("127.0.0.1", port), InboxHandler)


def main(argv: list[str] | None = None) -> int:
    """Start optional account window; never subscribes or sends on startup."""
    import argparse
    import secrets
    import webbrowser
    parser = argparse.ArgumentParser(description="Kleinanzeiger eigene Konto-Chats (optionaler unabhängiger Beta-Anbieter)")
    parser.add_argument("--db", type=Path, required=True,
                        help="Existing Kleinanzeiger owner SQLite; never created by this command")
    parser.add_argument("--outbox", type=Path, default=None,
                        help="Separate durable send-intent SQLite; by default next to owner DB")
    parser.add_argument("--port", type=int, default=8773)
    parser.add_argument("--enable-account-api", action="store_true",
                        help="Use the optional paid external account provider when KLAZ_API_KEY is set")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if not args.db.is_file():
        parser.error("Existing owner SQLite database required; it will never be created")
    key = os.environ.get("KLAZ_API_KEY", "")
    client = AccountClient(key) if args.enable_account_api else None
    journal = args.outbox if args.outbox is not None else args.db.with_name("messages-outbox.sqlite")
    if journal.resolve() == args.db.resolve():
        parser.error("Send journal must not replace the owner database")
    session = secrets.token_urlsafe(32)
    server = create_inbox_server(
        SnapshotStore(args.db), Outbox(journal), client=client,
        session_token=session, port=args.port)
    location = f"http://127.0.0.1:{server.server_address[1]}/#token={session}"
    if not args.no_browser:
        webbrowser.open(location)
    print(f"Kleinanzeiger Nachrichtenfenster lokal auf 127.0.0.1:{server.server_address[1]} bereit.", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())