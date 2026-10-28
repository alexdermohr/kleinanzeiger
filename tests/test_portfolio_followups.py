"""G1 local follow-up radar: user schedule is NEVER a marketplace expiry date."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.storage import SnapshotStore
from mark_api.portfolio_followups import (
    FollowupError, _schema, followup_detail, followups_page, mutate_followup,
)

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)


class FollowupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="kleinanzeiger-followup-")
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "owner.sqlite"
        self.store = SnapshotStore(self.db, create_if_missing=True)

    def observe(self, ad_id="123", *, state=LifecycleState.ACTIVE,
                source="kleinanzeigen-management", title="Eigener Artikel"):
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id=ad_id, title=title, lifecycle_state=state, source=source,
            observed_at=NOW,
        ))

    def payload(self, *, ad_id="123", due="2026-10-30", revision=0,
                key="1"*64, note="Wiedervorlage", action="save"):
        return {
            "action": action, "ad_id": ad_id, "due_date": due,
            "note": note, "expected_revision": revision, "request_key": key,
        }

    def test_empty_read_never_mutates_schema(self):
        self.assertEqual(followups_page(self.store)["total"], 0)
        self.assertIsNone(followup_detail(self.store, "123")["followup"])
        with closing(self.store._connect()) as conn:
            self.assertFalse(_schema(conn, create=False))
            self.assertNotIn("portfolio_followups", {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master")
            })

    def test_real_management_owner_manual_date_is_not_expiry(self):
        self.observe()
        p = self.payload()
        first = mutate_followup(self.store, p, now=NOW)
        self.assertEqual(first["revision"], 1)
        self.assertIsNone(first["platform_expiry_at"])
        self.assertEqual(first["source"], "manual_user_followup")
        self.assertEqual(first["platform_writes"], 0)
        self.assertEqual(mutate_followup(self.store, p, now=NOW + timedelta(days=1)), first)
        detail = followup_detail(self.store, "123", now=NOW)["followup"]
        self.assertEqual(detail["manual_followup_date"], "2026-10-30")
        self.assertEqual(detail["owner_last_observed_at"], NOW.isoformat())
        self.assertEqual(detail["state"], "due_soon")
        self.assertIsNone(detail["platform_expiry_at"])
        self.assertEqual(detail["platform_expiry_status"], "unknown_no_verified_observation")
        self.assertEqual(followups_page(self.store, now=NOW)["due_soon"], 1)
        self.assertEqual(followups_page(self.store, now=NOW + timedelta(days=3))["overdue"], 1)
        self.assertEqual(followups_page(self.store, now=NOW - timedelta(days=20))["due_soon"], 0)
        self.assertEqual(followups_page(self.store, now=NOW - timedelta(days=20))["followups"][0]["state"], "scheduled")

    def test_no_eml_local_catalog_or_absent_owner_can_create_reminder(self):
        for index, source in enumerate(("local-catalog", "user-supplied-local-catalog", "owner", "email-import", "private-web-owner-journal")):
            ad_id = str(1000 + index)
            self.observe(ad_id, source=source)
            with self.subTest(source=source), self.assertRaises(FollowupError):
                mutate_followup(self.store, self.payload(ad_id=ad_id, key=f"{index+1:064x}"))
        self.observe("1987", state=LifecycleState.ABSENT)
        with self.assertRaises(FollowupError):
            mutate_followup(self.store, self.payload(ad_id="1987", key="b"*64))
        self.assertEqual(followups_page(self.store)["total"], 0)

    def test_owner_source_and_revision_replay_and_tombstones(self):
        self.observe()
        first = mutate_followup(self.store, self.payload(), now=NOW)
        with self.assertRaisesRegex(FollowupError, "conflict"):
            mutate_followup(self.store, self.payload(due="2026-11-01"), now=NOW)
        with self.assertRaisesRegex(FollowupError, "revision changed"):
            mutate_followup(self.store, self.payload(key="2"*64, revision=0), now=NOW)
        second = mutate_followup(self.store, self.payload(
            key="3"*64, revision=1, due="2026-11-30"), now=NOW)
        self.assertEqual(second["revision"], 2)
        removed = mutate_followup(self.store, self.payload(
            key="4"*64, revision=2, due=None, note="", action="delete"), now=NOW)
        self.assertEqual(removed["revision"], 3)
        self.assertEqual(followups_page(self.store)["total"], 0)
        self.assertEqual(mutate_followup(self.store, self.payload(
            key="4"*64, revision=2, due=None, note="", action="delete"), now=NOW), removed)
        with self.assertRaisesRegex(FollowupError, "revision changed"):
            mutate_followup(self.store, self.payload(key="5"*64, revision=2, action="save"), now=NOW)
        recreated = mutate_followup(self.store, self.payload(key="6"*64), now=NOW)
        self.assertEqual(recreated["revision"], 4)
        self.assertEqual(followup_detail(self.store, "123")["followup"]["revision"], 4)
        self.assertEqual(first["revision"], 1)
        with closing(self.store._connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM portfolio_followup_requests").fetchone()[0], 4)

    def test_invalid_payload_and_clock_fail_without_side_effects(self):
        self.observe()
        invalid = [
            {"ad_id": "123 OR 1=1"}, {"ad_id": "１２３"}, {"action": "send"},
            {"due_date": "2026-02-29"}, {"due_date": "20261030"},
            {"due_date": "2026-10-30T00:00:00Z"}, {"due_date": "2101-01-01"},
            {"note": "Titel\n123"}, {"note": "\u202e"}, {"note": "x"*121},
            {"expected_revision": True}, {"request_key": "x"*64},
            {"due_date": None},
        ]
        for i, change in enumerate(invalid):
            with self.subTest(i=i), self.assertRaises(FollowupError):
                mutate_followup(self.store, {**self.payload(key=f"{i+1:064x}"), **change})
        for bad in (NOW.replace(tzinfo=None), "today", True):
            with self.assertRaises(FollowupError):
                mutate_followup(self.store, self.payload(key="a"*64), now=bad)
        self.assertEqual(followups_page(self.store)["total"], 0)

    def test_10000_keyset_pages_backup_and_no_million_row_json(self):
        self.observe()
        mutate_followup(self.store, self.payload(), now=NOW)
        with closing(self.store._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            _schema(conn, create=False)
            sample = (
                (f"{i+100000:06d}", "2026-10-31", "Prüfen", 1, 0, NOW.isoformat())
                for i in range(9999)
            )
            conn.executemany("INSERT INTO portfolio_followups VALUES (?,?,?,?,?,?)", sample)
        first = followups_page(self.store, limit=50, now=NOW)
        self.assertEqual(first["total"], 10000)
        self.assertEqual(len(first["followups"]), 50)
        self.assertLess(len(json.dumps(first)), 32_000)
        cursor = first["next_after"]
        second = followups_page(self.store, after=cursor, limit=50, now=NOW)
        self.assertTrue(second["has_more"])
        self.assertGreater(second["followups"][0]["ad_id"], cursor)
        all_count = 0
        after = ""
        while True:
            result = followups_page(self.store, after=after, limit=50, now=NOW)
            all_count += len(result["followups"])
            if not result["has_more"]:
                break
            after = result["next_after"]
        self.assertEqual(all_count, 10000)
        for kwargs in ({"limit": 51}, {"limit": 0}, {"limit": True},
                       {"after": "123' OR 1=1"}, {"after": "\u0000"}):
            with self.assertRaises(FollowupError):
                followups_page(self.store, **kwargs)
        copy = self.db.parent / "saved.sqlite"
        with closing(self.store._connect()) as src, closing(sqlite3.connect(copy)) as target:
            src.backup(target)
        restored = SnapshotStore(copy, create_if_missing=False, require_existing_schema=True)
        self.assertEqual(followups_page(restored, limit=1)["total"], 10000)
        self.assertEqual(followup_detail(restored, "123")["followup"]["revision"], 1)

    def test_partial_schema_fails_closed(self):
        with closing(self.store._connect()) as conn, conn:
            conn.execute("CREATE TABLE portfolio_followups (ad_id TEXT)")
        with self.assertRaises(sqlite3.DatabaseError):
            followups_page(self.store)
        self.observe()
        with self.assertRaises(sqlite3.DatabaseError):
            mutate_followup(self.store, self.payload())


if __name__ == "__main__":
    unittest.main()