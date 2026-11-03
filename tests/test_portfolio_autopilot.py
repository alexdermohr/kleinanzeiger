"""Real local SQLite tests for the zero-cost Autopilot attention queue."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from mark_api.domain import AdSnapshot, InboundMessageEvent, LifecycleState
from mark_api.portfolio_autopilot import AutopilotAttentionError, attention_page
from mark_api.portfolio_conversations import update_conversation_review
from mark_api.portfolio_existing_jobs import (
    change_state, claim_next, create_job, process_one,
)
from mark_api.portfolio_followups import _schema as followup_schema, mutate_followup
from mark_api.storage import SnapshotStore


NOW = datetime(2026, 10, 29, 10, tzinfo=timezone.utc)


class AutopilotAttentionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix="kleinanzeiger-autopilot-")
        self.addCleanup(directory.cleanup)
        self.store = SnapshotStore(Path(directory.name)/"data.sqlite", create_if_missing=True)

    def owner(self, ad_id, *, source="kleinanzeigen-management"):
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id=ad_id, observed_at=NOW, source=source,
            lifecycle_state=LifecycleState.ACTIVE,
            title="Belegter Artikel " + ad_id, description="Faktenbasierte Beschreibung",
        ))

    def notify(self, ad_id, conversation, message):
        self.store.append_inbound_message_events((InboundMessageEvent(
            ad_id=ad_id, conversation_id=conversation, provider_message_id=message,
            observed_at=NOW, source="kleinanzeigen-email",
        ),))

    def followup(self, ad_id, due_date, key):
        return mutate_followup(self.store, {
            "action": "save", "ad_id": ad_id, "due_date": due_date,
            "note": "Persönliche Wiedervorlage – nicht öffentlich",
            "expected_revision": 0, "request_key": key,
        }, now=NOW)

    def test_empty_query_does_not_create_tables_or_invent_tasks(self):
        with closing(self.store._connect()) as conn:
            before = {x["name"] for x in conn.execute("SELECT name FROM sqlite_master")}
        result = attention_page(self.store, now=NOW)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 0)
        self.assertFalse(result["has_more"])
        self.assertEqual(result["platform_writes"], 0)
        self.assertEqual(result["provider_requests"], 0)
        self.assertFalse(result["platform_messages_read"])
        self.assertFalse(result["automatic_execution_authorized"])
        with closing(self.store._connect()) as conn:
            after = {x["name"] for x in conn.execute("SELECT name FROM sqlite_master")}
        self.assertEqual(after, before)

    def test_priorities_pagination_and_no_false_external_permissions(self):
        for ad_id in ("10001", "10002", "10003"):
            self.owner(ad_id)
        self.notify("10002", "chat_10002", "email-msg-10002")
        self.notify("99999", "chat_99999", "email-foreign")
        self.followup("10002", "2026-10-27", "1"*64)
        self.followup("10003", "2026-10-31", "2"*64)
        job = create_job(self.store, ["10001"], instruction="Sachlich prüfen",
                         request_key="3"*64,budget_cents=20,estimate_cents=20)
        change_state(self.store, job["job_id"], "start")
        claim = claim_next(self.store, job["job_id"])
        self.assertEqual(claim["ad_id"], "10001")
        proposal_job = create_job(self.store, ["10003"], instruction="Belegtreu",
                                  request_key="4"*64,budget_cents=20,estimate_cents=20)
        change_state(self.store, proposal_job["job_id"], "start")
        process_one(self.store, proposal_job["job_id"], lambda *_: (
            {"title": "Neu", "description": "Faktenbasierte Beschreibung"}, 1))
        page = attention_page(self.store, now=NOW)
        self.assertEqual([item["kind"] for item in page["items"]], [
            "ai_uncertain", "eml_unreviewed", "manual_overdue",
            "ai_proposal", "manual_due_soon",
        ])
        self.assertEqual(page["total"], 5)
        self.assertEqual(page["counts"]["ai_uncertain"], 1)
        self.assertEqual(page["items"][0]["evidence"], "inflight")
        self.assertEqual(page["items"][1]["ad_id"], "10002")
        self.assertTrue(all(item["ad_id"] != "99999" for item in page["items"]))
        self.assertEqual(page["items"][2]["evidence"], "2026-10-27")
        self.assertNotIn("Persönliche Wiedervorlage", str(page))
        self.assertNotIn("Faktenbasierte Beschreibung", str(page))
        self.assertEqual(page["platform_writes"], 0)
        self.assertEqual(page["provider_requests"], 0)
        first = attention_page(self.store, now=NOW, limit=2)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 2)
        second = attention_page(self.store, now=NOW, limit=2, offset=2)
        self.assertEqual([x["kind"] for x in second["items"]], [
            "manual_overdue", "ai_proposal"])
        final = attention_page(self.store, now=NOW, limit=2, offset=4)
        self.assertFalse(final["has_more"])
        self.assertEqual([x["kind"] for x in final["items"]], ["manual_due_soon"])

        # A manual verification is required. Even repeated attention reads
        # cannot clear a possibly billable in-flight reservation.
        self.assertEqual(attention_page(self.store, now=NOW), page)
        with closing(self.store._connect()) as conn:
            pending = conn.execute(
                "SELECT phase,reserved_cents,actual_cents FROM "
                "portfolio_existing_job_items WHERE job_id=? AND ad_id='10001'",
                (job["job_id"],),
            ).fetchone()
            self.assertEqual(
                (pending["phase"],pending["reserved_cents"],pending["actual_cents"]),
                ("inflight",20,0),
            )

        reviewed = update_conversation_review(self.store, {
            "action": "review", "ad_id": "10002", "conversation_id": "chat_10002",
            "expected_revision": 0, "request_key": "5"*64,
        }, now=NOW)
        self.assertEqual(reviewed["review_state"], "locally_reviewed")
        reduced = attention_page(self.store, now=NOW)
        self.assertEqual(reduced["counts"]["eml_unreviewed"], 0)
        self.assertEqual(reduced["total"], 4)

    def test_10000_manual_tasks_global_count_and_last_page(self):
        # Test-only direct fixture over the exact production DDL. An indexed
        # 10,000-item list cannot be silently truncated to the first 50.
        with self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.assertTrue(followup_schema(conn, create=True))
            conn.executemany(
                "INSERT INTO portfolio_followups("
                "ad_id,due_date,note,revision,deleted,updated_at) "
                "VALUES (?, '2026-10-28', 'Privat', 1, 0, ?)",
                ((f"{i:05d}", NOW.isoformat()) for i in range(10_000)),
            )
        first = attention_page(self.store, now=NOW)
        self.assertEqual(first["total"], 10_000)
        self.assertEqual(len(first["items"]), 50)
        self.assertEqual(first["items"][0]["ad_id"], "00000")
        last = attention_page(self.store, offset=9_950, now=NOW)
        self.assertEqual(last["total"], 10_000)
        self.assertFalse(last["has_more"])
        self.assertEqual(len(last["items"]), 50)
        self.assertEqual(last["items"][-1]["ad_id"], "09999")
        self.assertEqual(last["platform_writes"], 0)
        self.assertEqual(last["provider_requests"], 0)

    def test_14day_view_stagnation_requires_fresh_comparable_owned_metrics(self):
        def measured(ad_id, age, views=11, *, source="kleinanzeigen-management",
                     metric="owner-counter", state=LifecycleState.ACTIVE):
            self.store.append_ad_snapshot(AdSnapshot(
                ad_id=ad_id, observed_at=NOW - timedelta(days=age),
                source=source, lifecycle_state=state,
                title="Belegter Artikel", description="Nur synthetische Daten",
                views=views, metric_source=metric,
            ))
        measured("10001", 16)
        measured("10001", 0)
        page = attention_page(self.store, now=NOW)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["counts"]["views_stagnant"], 1)
        task = page["items"][0]
        self.assertEqual(task["kind"], "views_stagnant")
        self.assertEqual(task["priority"], 5)
        self.assertEqual(task["ad_id"], "10001")
        self.assertEqual(task["evidence"], "Aufrufe 11 → 11")
        self.assertEqual(task["source"], "confirmed_own_view_counter_history")
        self.assertEqual(task["reference"], "2026-10-13 bis 2026-10-29")
        self.assertEqual(page["provider_requests"], 0)
        self.assertEqual(page["platform_writes"], 0)
        self.assertFalse(page["automatic_execution_authorized"])

        # Changes in *any* later source, missing metrics, counter resets, old
        # observations or paused/foreign current state invalidate the hint.
        measured("10001", 0, 12)
        self.assertEqual(attention_page(self.store, now=NOW)["total"], 0)
        measured("10001", 0, 11)  # a reset is not lack of demand
        self.assertEqual(attention_page(self.store, now=NOW)["total"], 0)
        measured("10002", 16)
        measured("10002", 0, source="user-supplied-local-catalog")
        measured("10003", 16)
        measured("10003", 0, metric=None)
        measured("10004", 16)
        measured("10004", 0, metric="different-source")
        measured("10005", 20)
        measured("10005", 3)
        measured("10006", 16)
        measured("10006", 0, state=LifecycleState.PAUSED)
        measured("10007", 16, views=None)
        measured("10007", 0)
        measured("10008", 16)
        measured("10008", 0, views=None)
        measured("10010", 16)
        measured("10010", 0, source="private-web-periodic-readonly")
        for ad_id in ("10002", "10003", "10004", "10005",
                      "10006", "10007", "10008", "10010"):
            self.assertNotIn(ad_id, {
                item["ad_id"] for item in attention_page(self.store, now=NOW)["items"]
            })
        # Corrupted timestamps do not grant a positive signal.
        measured("10009", 16)
        measured("10009", 0)
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE ad_snapshots SET observed_at='bad-time' "
                "WHERE ad_id='10009' AND views=11 LIMIT 1"
            )
        self.assertEqual(attention_page(self.store, now=NOW)["counts"]["views_stagnant"], 0)

    def test_view_stagnation_has_real_10000_item_global_priority_pagination(self):
        # Synthetic fixtures use the actual append-only production table.
        with self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                "INSERT INTO ad_snapshots("
                "ad_id,observed_at,source,lifecycle_state,title,description,"
                "views,metric_source) VALUES(?,?,?,?,?,?,?,?)",
                (
                    (f"{n:05d}", when.isoformat(), "kleinanzeigen-management",
                     "active", "Synthetisch", "Nur Testdaten", 3, "stats")
                    for n in range(10_000)
                    for when in (NOW - timedelta(days=15), NOW)
                ),
            )
        first = attention_page(self.store, now=NOW)
        self.assertEqual(first["total"], 10_000)
        self.assertEqual(first["counts"]["views_stagnant"], 10_000)
        self.assertEqual(len(first["items"]), 50)
        self.assertEqual(first["items"][0]["ad_id"], "00000")
        self.assertEqual(first["items"][-1]["ad_id"], "00049")
        last = attention_page(self.store, offset=9_950, now=NOW)
        self.assertEqual([item["ad_id"] for item in last["items"][:2]],
                         ["09950", "09951"])
        self.assertEqual(last["items"][-1]["ad_id"], "09999")
        self.assertFalse(last["has_more"])
        self.assertEqual(last["platform_writes"], 0)
        self.assertEqual(last["provider_requests"], 0)

    def test_attention_page_boundary_between_urgent_and_view_stagnation(self):
        # A high-priority manual task is never displaced or silently skipped
        # when another 10k-view trend occupies a later global page.
        self.owner("10009")
        self.followup("10009", "2026-10-26", "d" * 64)
        with self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                "INSERT INTO ad_snapshots("
                "ad_id,observed_at,source,lifecycle_state,title,description,"
                "views,metric_source) VALUES(?,?,?,?,?,?,?,?)",
                ((ad_id, time.isoformat(), "kleinanzeigen-management",
                  "active", "Belegter Artikel", "Nur lokale Testdaten", 7,
                  "account-stats") for ad_id in ("50001", "50002")
                 for time in (NOW - timedelta(days=15), NOW)),
            )
        first = attention_page(self.store, now=NOW, limit=2)
        self.assertEqual(first["total"], 3)
        self.assertEqual(first["counts"]["manual_overdue"], 1)
        self.assertEqual(first["counts"]["views_stagnant"], 2)
        self.assertEqual([item["kind"] for item in first["items"]],
                         ["manual_overdue", "views_stagnant"])
        self.assertEqual([item["ad_id"] for item in first["items"]],
                         ["10009", "50001"])
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 2)
        second = attention_page(self.store, now=NOW, offset=2, limit=2)
        self.assertEqual([item["ad_id"] for item in second["items"]],
                         ["50002"])
        self.assertFalse(second["has_more"])
        self.assertEqual(second["next_offset"], None)
        self.assertEqual(second["platform_writes"], 0)
        self.assertEqual(second["provider_requests"], 0)

    def test_bad_inputs_corrupt_schema_and_dates_fail_closed(self):
        for args in ({"offset":-1}, {"offset": True}, {"offset":100_001},
                     {"limit":0}, {"limit":51}, {"limit":True},
                     {"now": datetime(2026,10,29)}):
            with self.subTest(args=args), self.assertRaises(AutopilotAttentionError):
                attention_page(self.store, **args)
        with self.store._connect() as conn:
            conn.execute("CREATE TABLE portfolio_followups(ad_id TEXT)")
        with self.assertRaises(sqlite3.DatabaseError):
            attention_page(self.store, now=NOW)


if __name__ == "__main__":
    unittest.main()
