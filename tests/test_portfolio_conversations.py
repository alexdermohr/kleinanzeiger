"""G7: local review of EML-only conversation metadata; never marketplace sends."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from mark_api.domain import AdSnapshot, InboundMessageEvent, LifecycleState
from mark_api.portfolio_conversations import (
    ConversationError, _schema, conversations_page, update_conversation_review,
)
from mark_api.storage import SnapshotStore


NOW = datetime(2026, 10, 28, 10, tzinfo=timezone.utc)


class ConversationTriageTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix="kleinanzeiger-conversation-")
        self.addCleanup(folder.cleanup)
        self.store = SnapshotStore(Path(folder.name) / "mark.sqlite", create_if_missing=True)

    def owner(self, ad_id="10001", *, source="kleinanzeigen-management",
              state=LifecycleState.ACTIVE):
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id=ad_id, title="Eigener Artikel", source=source,
            lifecycle_state=state, observed_at=NOW,
        ))

    def notify(self, ad_id="10001", conversation_id="chat_123",
               message_id="message-123", observed_at=NOW):
        return self.store.append_inbound_message_events((InboundMessageEvent(
            ad_id=ad_id, conversation_id=conversation_id,
            provider_message_id=message_id, observed_at=observed_at,
            source="kleinanzeigen-email",
        ),))

    def review(self, *, action="review", ad_id="10001",
               conversation_id="chat_123", expected_revision=0, request_key="a" * 64):
        return {
            "action": action, "ad_id": ad_id, "conversation_id": conversation_id,
            "expected_revision": expected_revision, "request_key": request_key,
        }

    def test_empty_read_not_a_schema_mutation(self):
        self.assertEqual(conversations_page(self.store)["conversations"], [])
        self.assertEqual(conversations_page(self.store,status="reviewed")["conversations"], [])
        with closing(self.store._connect()) as conn:
            self.assertFalse(_schema(conn, create=False))

    def test_mark_review_reopen_and_new_import_reappears(self):
        self.owner()
        self.notify()
        initial = conversations_page(self.store, status="new")
        self.assertFalse(initial["platform_messages_read"])
        self.assertEqual(initial["platform_writes"], 0)
        self.assertEqual(initial["conversations"][0]["review_state"], "new_since_review")
        self.assertTrue(initial["conversations"][0]["own_ad"])
        self.assertEqual(initial["conversations"][0]["message_count"], 1)
        first = update_conversation_review(self.store, self.review(), now=NOW)
        self.assertEqual(first["revision"], 1)
        self.assertEqual(first["platform_reply_status"], "unknown")
        self.assertEqual(first["platform_writes"], 0)
        self.assertEqual(conversations_page(self.store,status="new")["conversations"], [])
        self.assertEqual(conversations_page(self.store,status="reviewed")["conversations"][0]["revision"], 1)
        self.notify(message_id="message-456",observed_at=NOW+timedelta(minutes=3))
        after_import = conversations_page(self.store,status="new")
        self.assertEqual(after_import["conversations"][0]["message_count"], 2)
        self.assertEqual(after_import["conversations"][0]["review_state"], "new_since_review")
        again = update_conversation_review(self.store,self.review(
            expected_revision=1,request_key="b" * 64),now=NOW)
        self.assertEqual(again["revision"], 2)
        self.assertEqual(conversations_page(self.store,status="reviewed")["conversations"][0]["message_count"],2)
        reopened = update_conversation_review(self.store,self.review(
            action="reopen",expected_revision=2,request_key="c" * 64),now=NOW)
        self.assertEqual(reopened["review_state"],"new_since_review")
        self.assertEqual(len(conversations_page(self.store,status="new")["conversations"]),1)

    def test_no_ownership_by_eml_or_local_catalog_or_absent_snapshot(self):
        self.notify(ad_id="99999",conversation_id="chat_999",message_id="msg999")
        page=conversations_page(self.store)
        self.assertFalse(page["conversations"][0]["own_ad"])
        with self.assertRaises(ConversationError):
            update_conversation_review(self.store,self.review(ad_id="99999",
                conversation_id="chat_999",request_key="d"*64))
        self.owner("99999",source="local-catalog")
        with self.assertRaises(ConversationError):
            update_conversation_review(self.store,self.review(ad_id="99999",
                conversation_id="chat_999",request_key="e"*64))
        self.owner("99999",state=LifecycleState.ABSENT)
        with self.assertRaises(ConversationError):
            update_conversation_review(self.store,self.review(ad_id="99999",
                conversation_id="chat_999",request_key="f"*64))
        self.assertFalse(conversations_page(self.store)["conversations"][0]["own_ad"])

    def test_exact_key_replay_conflicts_and_same_id_other_conversation(self):
        self.owner()
        self.notify()
        self.notify(conversation_id="chat_999",message_id="message-999")
        first=update_conversation_review(self.store,self.review(),now=NOW)
        self.assertEqual(update_conversation_review(self.store,self.review(),
            now=NOW+timedelta(days=1)),first)
        with self.assertRaisesRegex(ConversationError,"reused"):
            update_conversation_review(self.store,self.review(action="reopen"))
        with self.assertRaisesRegex(ConversationError,"changed"):
            update_conversation_review(self.store,self.review(request_key="1"*64))
        with self.assertRaisesRegex(ConversationError,"unknown"):
            update_conversation_review(self.store,self.review(
                conversation_id="chat_456",request_key="2"*64))
        self.assertEqual(conversations_page(self.store,status="reviewed")["conversations"][0]["conversation_id"],"chat_123")
        self.assertEqual(len(conversations_page(self.store,status="new")["conversations"]),1)

    def test_large_cursor_pagination_and_immutable_event_watermark(self):
        self.owner()
        for i in range(122):
            self.notify(conversation_id=f"chat_{i:05d}",message_id=f"event-{i:05d}")
        count=0
        cursor=""
        pinned=None
        seen=set()
        while True:
            page=conversations_page(self.store,after=cursor,limit=25,watermark=pinned)
            pinned=page["watermark"]
            for item in page["conversations"]:
                self.assertNotIn(item["conversation_id"],seen)
                seen.add(item["conversation_id"])
            count+=len(page["conversations"])
            if not page["has_more"]: break
            cursor=page["next_after"]
        self.assertEqual(count,122)
        self.notify(conversation_id="chat_99999",message_id="event-99999")
        self.assertEqual(len(conversations_page(self.store,after=cursor,watermark=pinned)["conversations"]),22)
        self.assertEqual(len(conversations_page(self.store,after=cursor)["conversations"]),23)

    def test_malformed_inputs_do_not_write_local_status(self):
        self.owner()
        self.notify()
        invalid=[
            {"ad_id":"10001 OR 1=1"}, {"conversation_id":"bad/path"},
            {"conversation_id":"x"}, {"action":"send"}, {"expected_revision":True},
            {"expected_revision":-1}, {"request_key":"z"*64},
            {"extraneous":"field"},
        ]
        for i,changes in enumerate(invalid):
            with self.subTest(i=i),self.assertRaises(ConversationError):
                update_conversation_review(self.store,{**self.review(request_key=f"{i+1:064x}"),**changes})
        for opts in (
            {"limit":0}, {"limit":51}, {"limit":True},
            {"after":"something"}, {"after":"123/%%%"},
            {"watermark":True}, {"watermark":-1}, {"status":"sent"},
        ):
            with self.subTest(opts=opts),self.assertRaises(ConversationError):
                conversations_page(self.store,**opts)
        self.assertEqual(conversations_page(self.store)["conversations"][0]["revision"],0)


if __name__ == "__main__":
    unittest.main()
