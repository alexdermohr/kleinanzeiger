from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.platform_messages import (
    AccountClient, BASE, MessagingError, Outbox, _provider_payload,
    require_locally_owned,
)
from mark_api.storage import SnapshotStore


KEY = "klaz_live_" + "s" * 34


class FakeAccountTransport:
    def __init__(self):
        self.requests = []
        self.raise_error = None
        self.response_status = 200
        self.next_data = {"status": "connected"}

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, dict(headers), body))
        if self.raise_error is not None:
            raise self.raise_error
        return self.response_status, json.dumps(
            {"success": self.response_status == 200, "data": self.next_data},
        ).encode("utf-8")


class AccountMessageContractTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeAccountTransport()
        self.client = AccountClient(KEY, transport=self.transport)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = Outbox(Path(self.temp.name) / "outbox.sqlite")

    def test_missing_or_invalid_key_never_attempts_network(self):
        for candidate in ("", "abcd", KEY + "\n", "klaz_live_x"):
            with self.subTest(candidate_length=len(candidate)):
                with self.assertRaisesRegex(MessagingError, "account_api_not_configured"):
                    AccountClient(candidate, transport=self.transport)
        self.assertEqual(self.transport.requests, [])

    def test_free_status_is_sanitized_and_bound_to_known_endpoint(self):
        self.assertEqual(self.client.status(), {
            "status": "connected", "provider": "kleinanzeigen-agent",
            "official": False,
        })
        method, url, headers, body = self.transport.requests[0]
        self.assertEqual((method, url, body), ("GET", BASE + "/status", None))
        self.assertEqual(headers["klaz_key"], KEY)
        self.assertNotIn("klaz_live_", repr(self.client.status()))

    def test_inbox_list_with_pagination_and_no_fake_counts(self):
        self.transport.next_data = {
            "page": 0, "size": 25, "has_more": True,
            "conversations": [{
                "conversation_id": "buyer:id-1", "ad_id": "4711",
                "counterparty": "Customer", "ad_title": "Sofa",
                "preview": "Noch verfügbar?", "unread": True,
                "received_at": "2026-11-01T10:00:00+0100",
            }],
        }
        result = self.client.conversations()
        self.assertTrue(result["has_more"])
        self.assertNotIn("total", result)
        self.assertEqual(result["conversations"][0]["ad_id"], "4711")
        self.assertEqual(self.transport.requests[-1][1],
                         BASE + "/conversations?page=0&size=25")

    def test_reject_duplicate_chat_or_wrong_count_and_bogus_ownership(self):
        good = {"conversation_id": "a:b", "ad_id": "4711", "unread": True}
        for data in (
            {"page": 0, "has_more": False, "conversations": [good, good]},
            {"page": 1, "has_more": False, "conversations": [good]},
            {"page": 0, "has_more": "yes", "conversations": [good]},
            {"page": 0, "has_more": False, "conversations": [{**good, "unread": 1}]},
            {"page": 0, "has_more": False, "conversations": [{**good, "ad_id": "../x"}]},
        ):
            with self.subTest(data=data):
                self.transport.next_data = data
                with self.assertRaises(MessagingError):
                    self.client.conversations()

    def test_open_chat_may_mark_read_but_is_not_a_send(self):
        self.transport.next_data = {
            "conversation_id": "a:b",
            "messages": [
                {"message_id": "m1", "text": "Noch da?", "direction": "received",
                 "received_at": "2026-11-01T08:00:00Z"},
            ],
        }
        result = self.client.open_conversation("a:b")
        self.assertEqual(result["messages"][0]["text"], "Noch da?")
        self.assertTrue(result["may_mark_read"])
        method, url, _, body = self.transport.requests[-1]
        self.assertEqual(method, "POST")
        self.assertEqual(url, BASE + "/conversations/a%3Ab/open")
        self.assertIsNone(body)

    def test_explicit_prepared_send_is_single_attempt_and_id_bound(self):
        prep = self.journal.prepare(self.client.fingerprint, "a:b", "4711", "Guten Tag")
        self.transport.next_data = {
            "request_id": prep["request_id"],
            "conversation_id": "a:b", "sent": True,
        }
        with self.assertRaisesRegex(MessagingError, "explicit_send_confirmation_required"):
            self.journal.dispatch(self.client.fingerprint, prep["request_id"], "a:b",
                                  "4711", "Guten Tag", confirmed=False, client=self.client)
        self.assertEqual(self.transport.requests, [])
        with self.assertRaisesRegex(MessagingError, "send_intent_mismatch"):
            self.journal.dispatch(self.client.fingerprint, prep["request_id"], "a:b",
                                  "4711", "Changed", confirmed=True, client=self.client)
        self.assertEqual(self.transport.requests, [])
        result = self.journal.dispatch(
            self.client.fingerprint, prep["request_id"], "a:b", "4711",
            "Guten Tag", confirmed=True, client=self.client)
        self.assertEqual(result["state"], "sent")
        self.assertEqual(result["delivered"], False)
        self.assertEqual(self.journal.state(prep["request_id"])["state"], "sent")
        with self.assertRaisesRegex(MessagingError, "send_not_retryable"):
            self.journal.dispatch(self.client.fingerprint, prep["request_id"], "a:b",
                                  "4711", "Guten Tag", confirmed=True, client=self.client)
        with self.assertRaisesRegex(MessagingError, "duplicate_or_unknown_send"):
            self.journal.prepare(self.client.fingerprint, "a:b", "4711", "Guten Tag")
        self.assertEqual(len(self.transport.requests), 1)
        sent = json.loads(self.transport.requests[0][3])
        self.assertEqual(sent, {"message": "Guten Tag", "request_id": prep["request_id"]})
        self.assertEqual(self.transport.requests[0][1], BASE + "/conversations/a%3Ab/messages")
        self.assertNotIn("Guten Tag", (Path(self.temp.name) / "outbox.sqlite").read_bytes().decode(
            "utf-8", errors="replace"))

    def test_duplicate_prepared_message_blocks_second_intent(self):
        first = self.journal.prepare(self.client.fingerprint, "a:b", "4711", "Hallo")
        with self.assertRaisesRegex(MessagingError, "duplicate_or_unknown_send"):
            self.journal.prepare(self.client.fingerprint, "a:b", "4711", "Hallo")
        self.assertEqual(self.journal.state(first["request_id"])["state"], "prepared")
        self.assertEqual(self.transport.requests, [])

    def test_timeout_before_or_after_provider_is_unknown_not_retried(self):
        prep = self.journal.prepare(self.client.fingerprint, "a:b", "4711", "Antwort")
        self.transport.raise_error = TimeoutError("synthetic timeout containing key " + KEY)
        with self.assertRaisesRegex(MessagingError, "transport_outcome_unknown"):
            self.journal.dispatch(self.client.fingerprint, prep["request_id"], "a:b",
                                  "4711", "Antwort", confirmed=True, client=self.client)
        self.assertEqual(self.journal.state(prep["request_id"])["state"], "unknown")
        self.transport.raise_error = None
        with self.assertRaisesRegex(MessagingError, "duplicate_or_unknown_send"):
            self.journal.prepare(self.client.fingerprint, "a:b", "4711", "Antwort")
        with self.assertRaisesRegex(MessagingError, "send_not_retryable"):
            self.journal.dispatch(self.client.fingerprint, prep["request_id"], "a:b",
                                  "4711", "Antwort", confirmed=True, client=self.client)
        self.assertEqual(len(self.transport.requests), 1)

    def test_explicit_provider_not_sent_response_is_journalled(self):
        prep = self.journal.prepare(self.client.fingerprint, "a:b", "4711", "Antwort")
        self.transport.response_status = 409
        self.transport.next_data = {"outcome": "not_sent"}
        with self.assertRaises(MessagingError) as error:
            self.journal.dispatch(self.client.fingerprint, prep["request_id"], "a:b",
                                  "4711", "Antwort", confirmed=True, client=self.client)
        self.assertEqual(error.exception.outcome, "not_sent")
        self.assertEqual(self.journal.state(prep["request_id"])["state"], "not_sent")
        self.assertEqual(len(self.transport.requests), 1)

    def test_malformed_post_response_stays_unknown_without_retry(self):
        prepared = self.journal.prepare(self.client.fingerprint, "a:b", "4711", "Wichtig")
        def broken(_method, _url, _headers, _body):
            return 200, b'<html>POST already applied, response lost</html>'
        client = AccountClient(KEY, transport=broken)
        with self.assertRaisesRegex(MessagingError, "message_outcome_unknown"):
            self.journal.dispatch(client.fingerprint, prepared["request_id"], "a:b",
                                  "4711", "Wichtig", confirmed=True, client=client)
        self.assertEqual(self.journal.state(prepared["request_id"])["state"], "unknown")
        with self.assertRaisesRegex(MessagingError, "send_not_retryable"):
            self.journal.dispatch(client.fingerprint, prepared["request_id"], "a:b",
                                  "4711", "Wichtig", confirmed=True, client=client)

    def test_unconfirmed_provider_success_is_unknown(self):
        prep = self.journal.prepare(self.client.fingerprint, "a:b", "4711", "Antwort")
        self.transport.next_data = {"request_id": str(uuid4()), "conversation_id": "a:b", "sent": True}
        with self.assertRaisesRegex(MessagingError, "message_outcome_unknown"):
            self.journal.dispatch(self.client.fingerprint, prep["request_id"], "a:b",
                                  "4711", "Antwort", confirmed=True, client=self.client)
        self.assertEqual(self.journal.state(prep["request_id"])["state"], "unknown")

    def test_oversize_message_and_duplicate_json_are_rejected(self):
        with self.assertRaisesRegex(MessagingError, "invalid_message"):
            self.client.send_message("a:b", "x" * 4001, str(uuid4()))
        with self.assertRaises(MessagingError):
            _provider_payload(b'{"success":true,"data":{},"data":{}}')
        self.assertEqual(len(self.transport.requests), 0)

    def test_account_or_conversation_swap_cannot_reuse_request(self):
        prep = self.journal.prepare(self.client.fingerprint, "a:b", "4711", "Antwort")
        with self.assertRaisesRegex(MessagingError, "send_intent_mismatch"):
            self.journal.dispatch("0" * 64, prep["request_id"], "a:b",
                                  "4711", "Antwort", confirmed=True, client=self.client)
        with self.assertRaisesRegex(MessagingError, "send_intent_mismatch"):
            self.journal.dispatch(self.client.fingerprint, prep["request_id"], "different",
                                  "4711", "Antwort", confirmed=True, client=self.client)
        self.assertEqual(len(self.transport.requests), 0)

    def test_local_owner_observation_required_for_send(self):
        with TemporaryDirectory() as folder:
            store = SnapshotStore(Path(folder) / "ads.sqlite", create_if_missing=True)
            with self.assertRaisesRegex(MessagingError, "no_confirmed_local_owner"):
                require_locally_owned(store, "4711")
            store.append_ad_snapshot(AdSnapshot(
                ad_id="4711", observed_at=datetime.now(timezone.utc),
                source="kleinanzeigen-management", lifecycle_state=LifecycleState.ACTIVE,
                title="Testartikel",
            ))
            self.assertTrue(require_locally_owned(store, "4711")["local_owner_only"])
            with self.assertRaisesRegex(MessagingError, "no_confirmed_local_owner"):
                require_locally_owned(store, "9999")


if __name__ == "__main__":
    unittest.main()


class InboxServerTests(unittest.TestCase):
    def setUp(self):
        from mark_api.platform_messages import create_inbox_server
        from threading import Thread
        self.tmp2 = TemporaryDirectory()
        self.addCleanup(self.tmp2.cleanup)
        self.store = SnapshotStore(Path(self.tmp2.name)/"ads.sqlite",create_if_missing=True)
        self.store.append_ad_snapshot(AdSnapshot(ad_id="4711",
            observed_at=datetime.now(timezone.utc),source="kleinanzeigen-management",
            lifecycle_state=LifecycleState.ACTIVE,title="Eigenanzeige"))
        self.calls = []
        def transport(method,url,headers,body):
            self.calls.append((method,url,body))
            if url.endswith("/status"): data={"status":"connected"}
            elif "page=" in url:
                data={"page":0,"size":25,"has_more":False,"conversations":[
                    {"conversation_id":"a:b","ad_id":"4711","unread":True},
                    {"conversation_id":"foreign","ad_id":"9999","unread":False}]}
            elif url.endswith("/open"):
                data={"conversation_id":"a:b","messages":[
                    {"message_id":"m1","text":"Hallo","direction":"received"}]}
            elif url.endswith("/messages"):
                bodydata=json.loads(body)
                data={"request_id":bodydata["request_id"],"conversation_id":"a:b","sent":True}
            else: raise AssertionError("unexpected account endpoint")
            return 200,json.dumps({"success":True,"data":data}).encode()
        self.client=AccountClient(KEY,transport=transport)
        self.journal=Outbox(Path(self.tmp2.name)/"out.sqlite")
        self.token="t"*40
        self.server=create_inbox_server(self.store,self.journal,client=self.client,
                                        session_token=self.token,port=0)
        self.thread=Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join,2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self,method,path,body=None,auth=True,origin=None):
        from http.client import HTTPConnection
        conn=HTTPConnection("127.0.0.1",self.server.server_address[1],timeout=5)
        headers={"X-Kleinanzeiger-Session":self.token if auth else "wrong"}
        if origin is not None:headers["Origin"]=origin
        if body is not None:headers["Content-Type"]="application/json"
        conn.request(method,path,json.dumps(body,ensure_ascii=False).encode() if body is not None else None,headers)
        resp=conn.getresponse()
        status,payload=resp.status,resp.read()
        conn.close()
        return status,json.loads(payload) if path.startswith("/api/") else payload.decode()

    def test_real_http_click_sequence_and_no_automatic_provider_actions(self):
        self.assertEqual(self.call("GET","/")[0],200)
        self.assertIn("X-Kleinanzeiger-Session",self.call("GET","/inbox.js")[1])
        self.assertEqual(self.calls,[])
        self.assertEqual(self.call("GET","/api/status",auth=False)[0],403)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.call("GET","/api/status")[1]["status"],"connected")
        code,data=self.call("GET","/api/conversations?page=0&size=25")
        self.assertEqual(code,200)
        self.assertTrue(data["conversations"][0]["local_own_ad"])
        self.assertFalse(data["conversations"][1]["local_own_ad"])
        self.assertEqual(self.call("POST","/api/open",{
            "conversation_id":"foreign","ad_id":"9999","confirm_mark_read":True})[0],409)
        self.assertEqual(self.call("POST","/api/open",{
            "conversation_id":"a:b","ad_id":"4711","confirm_mark_read":False})[0],400)
        self.assertEqual(self.call("POST","/api/open",{
            "conversation_id":"a:b","ad_id":"4711","confirm_mark_read":True},
            origin="http://evil.invalid")[0],403)
        self.assertEqual(self.call("POST","/api/open",{
            "conversation_id":"a:b","ad_id":"4711","confirm_mark_read":True})[0],200)
        code,intent=self.call("POST","/api/prepare",{
            "conversation_id":"a:b","ad_id":"4711","message":"Antwort"})
        self.assertEqual(code,200)
        packet={"conversation_id":"a:b","ad_id":"4711","message":"Antwort",
                "request_id":intent["request_id"],"confirmed":False}
        self.assertEqual(self.call("POST","/api/send",packet)[0],400)
        self.assertEqual(self.journal.state(intent["request_id"])["state"],"prepared")
        code,ack=self.call("POST","/api/send",{**packet,"confirmed":True})
        self.assertEqual(code,200)
        self.assertEqual(ack["state"],"sent")
        self.assertFalse(ack["delivered"])
        self.assertEqual(self.call("POST","/api/send",{**packet,"confirmed":True})[0],409)
        self.assertEqual(sum(url.endswith("/messages") for _,url,_ in self.calls),1)
        self.assertEqual(self.call("GET","/api/outbox?request_id="+intent["request_id"])[1]["state"],"sent")
    def test_full_utf8_reply_and_send_metadata_are_accepted(self):
        # Codex P2: an 8 KiB cap admitted the preparation of 2700 CJK
        # characters but rejected the same message on /api/send because
        # request_id and confirmed enlarge its UTF-8 JSON envelope.
        self.assertEqual(self.call("GET","/api/conversations?page=0&size=25")[0],200)
        self.assertEqual(self.call("POST","/api/open",{
            "conversation_id":"a:b","ad_id":"4711","confirm_mark_read":True})[0],200)
        for message in ("語"*2700, "🌟"*4000):
            with self.subTest(utf8_bytes=len(message.encode("utf-8"))):
                prepared_status, prepared=self.call("POST","/api/prepare",{
                    "conversation_id":"a:b","ad_id":"4711","message":message})
                self.assertEqual(prepared_status,200)
                send={"conversation_id":"a:b","ad_id":"4711",
                      "request_id":prepared["request_id"],"message":message,"confirmed":True}
                encoded=json.dumps(send,ensure_ascii=False).encode("utf-8")
                self.assertGreater(len(encoded),8192)
                self.assertLessEqual(len(encoded),16384)
                send_status,result=self.call("POST","/api/send",send)
                self.assertEqual(send_status,200)
                self.assertTrue(result["sent"])
                self.assertEqual(result["state"],"sent")
                self.assertEqual(self.journal.state(prepared["request_id"])["state"],"sent")
        self.assertEqual(sum(url.endswith("/messages") for _,url,_ in self.calls),2)
        self.assertEqual(self.call("POST","/api/prepare",{
            "conversation_id":"a:b","ad_id":"4711","message":"x"*17000})[0],400)
        self.assertEqual(sum(url.endswith("/messages") for _,url,_ in self.calls),2)

class AccountInboxBrowserRaceTests(unittest.TestCase):
    def test_switching_chat_during_prepare_never_changes_send_target(self):
        import subprocess
        from mark_api.platform_messages import _SCRIPT
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const js=fs.readFileSync(0,'utf8');
function el(id=''){return {id,dataset:{},disabled:false,children:[],handlers:{},value:'',textContent:'',
 addEventListener(n,f){this.handlers[n]=f},
 append(x){this.children.push(x)},replaceChildren(...x){this.children=[...x]},
 querySelectorAll(t){return this.children.flatMap(c=>[...(c.id===t?[c]:[]),...c.querySelectorAll(t)])},
 click(){if(!this.disabled)return this.handlers.click?.()}}}
const names=['status','status-check','list-chats','next-chats','send','template','reply','chats','conversation','connection'];
const dom=Object.fromEntries(names.map(n=>[n,el(n)]));let release=null,requests=[],confirmTexts=[];
const response=x=>({ok:true,json:async()=>x});
const chats=[
 {conversation_id:'a',ad_id:'4711',local_own_ad:true,unread:false},
 {conversation_id:'b',ad_id:'4722',local_own_ad:true,unread:false},
 {conversation_id:'other',ad_id:'9999',local_own_ad:false,unread:false}];
async function fetch(url,opts){
 const body=opts?.body?JSON.parse(opts.body):null;requests.push({url,body});
 if(url.startsWith('/api/conversations'))return response({page:0,has_more:false,conversations:chats});
 if(url==='/api/status')return response({status:'connected',provider:'synthetic'});
 if(url==='/api/open')return response({messages:[{direction:'received',text:'Frage'}]});
 if(url==='/api/prepare')return await new Promise(ok=>{release=()=>ok(response({request_id:'e0000000-0000-4000-8000-000000000001'}))});
 if(url==='/api/send')return response({state:'sent',sent:true,request_id:body.request_id});
 throw Error(url);
}
vm.runInNewContext(js,{document:{getElementById:n=>dom[n],createElement:n=>el(n)},
 location:{hash:'#token='+'t'.repeat(40),pathname:'/'},window:{confirm:(text)=>{confirmTexts.push(text);return true}},
 sessionStorage:{getItem:()=>null,setItem:()=>{}},history:{replaceState:()=>{}},fetch,console},{timeout:1500});
const pump=async()=>{for(let i=0;i<5;i++)await Promise.resolve()};
(async()=>{
 await dom['list-chats'].handlers.click();await pump();
 const buttons=dom.chats.querySelectorAll('button');
 assert.equal(buttons.length,3);
 assert.deepEqual(buttons.map(x=>x.disabled),[false,false,true]);
 await buttons[0].handlers.click();await pump();
 const fullMessage='Antwort nur an A. '+'X'.repeat(3200)+'ENDE-KONTROLLTEXT';
 dom.reply.value=fullMessage;dom.reply.handlers.input();
 assert.equal(dom.send.disabled,false);
 const send=dom.send.handlers.click();await pump();assert.equal(typeof release,'function');
 assert.ok(confirmTexts.at(-1).includes(fullMessage),'confirm must expose complete text');
 assert.ok(confirmTexts.at(-1).includes('ENDE-KONTROLLTEXT'),'confirm must show actual final characters');
 assert.deepEqual(buttons.map(x=>x.disabled),[true,true,true]);
 assert.equal(dom.reply.disabled,true);assert.equal(dom.template.disabled,true);
 await buttons[1].handlers.click();await dom['list-chats'].handlers.click();
 await dom['status-check'].handlers.click();dom.template.handlers.click();
 assert.equal(requests.filter(x=>x.url==='/api/open').length,1);
 assert.equal(requests.filter(x=>x.url.startsWith('/api/conversations')).length,1);
 assert.equal(dom.reply.value,fullMessage);
 release();await send;await pump();
 const prepared=requests.filter(x=>x.url==='/api/prepare');
 const delivered=requests.filter(x=>x.url==='/api/send');
 assert.equal(prepared.length,1);assert.equal(delivered.length,1);
 for(const req of [...prepared,...delivered]) {
   assert.equal(req.body.conversation_id,'a');assert.equal(req.body.ad_id,'4711');
   assert.equal(req.body.message,fullMessage);
 }
 assert.deepEqual(buttons.map(x=>x.disabled),[false,false,true]);
 await buttons[1].handlers.click();await pump();
 assert.equal(requests.filter(x=>x.url==='/api/open').length,2);
 console.log('ACCOUNT_CHAT_SWITCH_SEND_RACE_PASS');
})().catch(e=>{console.error(e.stack||String(e));process.exitCode=1});
"""
        result = subprocess.run(
            ["node", "-e", harness], input=_SCRIPT, capture_output=True,
            text=True, encoding="utf-8", timeout=35, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ACCOUNT_CHAT_SWITCH_SEND_RACE_PASS", result.stdout)

class AccountInboxManualLaunchTests(unittest.TestCase):
    def test_no_browser_false_launch_and_deliberate_print_url_reveal_valid_local_token(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch, MagicMock
        import re
        from mark_api.platform_messages import main
        with TemporaryDirectory() as folder:
            db = Path(folder) / "existing.sqlite"
            SnapshotStore(db, create_if_missing=True)
            fake_server = MagicMock()
            fake_server.server_address = ("127.0.0.1", 8773)
            cases = (
                (["--no-browser"], False, True, 0),
                ([], False, True, 1),
                ([], True, False, 1),
                (["--print-url"], True, True, 1),
            )
            for flags, opened, visible, expected_opens in cases:
                with self.subTest(flags=flags, browser_opened=opened), \
                     patch("mark_api.platform_messages.create_inbox_server", return_value=fake_server), \
                     patch("webbrowser.open", return_value=opened) as browser_open, \
                     redirect_stdout(StringIO()) as output:
                    result = main(["--db", str(db), *flags])
                    self.assertEqual(result, 0)
                    text = output.getvalue()
                    self.assertEqual(browser_open.call_count, expected_opens)
                    self.assertIn("Nachrichtenfenster lokal", text)
                    match = re.search(r"http://127\.0\.0\.1:8773/#token=([A-Za-z0-9_-]+)", text)
                    self.assertEqual(match is not None, visible)
                    if match:
                        self.assertGreaterEqual(len(match.group(1)), 32)
                        self.assertNotIn("TOKEN_NOT_AVAILABLE", text)
                    self.assertEqual(fake_server.serve_forever.call_count > 0, True)