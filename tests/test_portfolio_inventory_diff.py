"""G4 read-only owner-sync diff and 10k keyset regressions."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.portfolio_inventory_diff import InventoryDiffError, owner_inventory_diff_page
from mark_api.results import ReadResult, ReadStatus
from mark_api.storage import SnapshotStore

SOURCE = "private-web-owner-inventory"
T = datetime(2026, 10, 27, 10, tzinfo=timezone.utc)


class OwnerDiffTests(unittest.TestCase):
    def setUp(self):
        t = TemporaryDirectory(prefix="kleinanzeiger-g4-")
        self.addCleanup(t.cleanup)
        self.store = SnapshotStore(Path(t.name) / "store.sqlite", create_if_missing=True)

    def item(self, ad_id="A", *, title="Lampe", state=LifecycleState.ACTIVE,
             views=1, observed_at=T, source=SOURCE):
        return AdSnapshot(ad_id=ad_id, observed_at=observed_at, source=source,
                          lifecycle_state=state, title=title, description="Gebraucht",
                          views=views)

    def sync(self, minute, snapshots=(), *, tracked=(), failed=False):
        start = T + timedelta(minutes=minute)
        attempt = self.store.begin_sync_attempt(source=SOURCE, started_at=start)
        records = tuple(self.item(
            value.ad_id, title=value.title, state=value.lifecycle_state,
            views=value.views, observed_at=start + timedelta(seconds=2))
            for value in snapshots)
        result = (
            ReadResult.failure(ReadStatus.TRANSPORT_ERROR)
            if failed else (
                ReadResult.success_nonempty(records) if records
                else ReadResult.success_empty(())
            )
        )
        self.store.append_inventory_result(
            result, tracked_ad_ids=tracked, observed_at=start+timedelta(seconds=2),
            source=SOURCE, attempt_id=attempt,
            completed_at=start+timedelta(seconds=6))
        return attempt

    def test_empty_and_first_sync_claim_no_new_items(self):
        self.assertEqual(owner_inventory_diff_page(self.store)["state"], "unverified")
        self.sync(0, [self.item("A")])
        result = owner_inventory_diff_page(self.store)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["state"], "no_baseline")
        self.assertEqual(result["items"][0]["current"]["title"], "Lampe")
        self.assertIsNone(result["items"][0]["previous"])
        self.assertEqual(result["platform_writes"], 0)

    def test_two_complete_syncs_show_new_changed_paused_and_stable(self):
        self.sync(0, [self.item("A"), self.item("B"),
                      self.item("C"), self.item("D", state=LifecycleState.PAUSED)])
        self.sync(20, [self.item("A", title="Anderer Titel"),
                       self.item("B", views=100),
                       self.item("C", state=LifecycleState.PAUSED),
                       self.item("D", state=LifecycleState.PAUSED),
                       self.item("E")])
        result = owner_inventory_diff_page(self.store, limit=100)
        rows = {x["ad_id"]: x for x in result["items"]}
        self.assertEqual(rows["A"]["state"], "changed")
        self.assertEqual(rows["A"]["changed_fields"], ["title"])
        self.assertEqual(rows["B"]["state"], "unchanged")
        self.assertEqual(rows["C"]["changed_fields"], ["state"])
        self.assertEqual(rows["D"]["state"], "paused")
        self.assertEqual(rows["E"]["state"], "newly_observed")
        self.assertEqual(rows["A"]["current"]["title"], "Anderer Titel")
        self.assertEqual(rows["A"]["previous"]["title"], "Lampe")
        self.assertTrue(all(x["platform_writes"] == 0 for x in rows.values()))

    def test_absent_not_sold_deleted_and_no_automatic_local_mapping(self):
        self.sync(0, [self.item("A"), self.item("B")])
        self.sync(15, (), tracked=("A", "B"))
        result = owner_inventory_diff_page(self.store)
        self.assertEqual(result["state"], "ready")
        for row in result["items"]:
            self.assertEqual(row["state"], "unclear")
            self.assertEqual(row["current"]["state"], "absent")
            self.assertIsNone(row["local_draft_match"])
            self.assertNotIn("sold", str(row))
            self.assertNotIn("deleted", str(row))

    def test_latest_failed_or_open_sync_cannot_claim_current_inventory(self):
        self.sync(0, [self.item("A")])
        self.sync(15, [self.item("A", title="Changed")])
        self.sync(30, failed=True)
        failed = owner_inventory_diff_page(self.store)
        self.assertEqual(failed["state"], "unverified")
        self.assertIsNone(failed["items"][0]["current"])
        self.assertIsNone(failed["confirmed_at"])
        self.store.begin_sync_attempt(source=SOURCE, started_at=T+timedelta(minutes=40))
        open_result = owner_inventory_diff_page(self.store)
        self.assertEqual(open_result["unresolved_sync_attempts"], 1)
        self.assertEqual(open_result["items"][0]["state"], "unverified")

    def test_same_local_title_does_not_bind_foreign_or_draft(self):
        self.store.append_ad_snapshot(self.item("LOCAL", source="local-catalog"))
        self.sync(0, [self.item("OWNER")])
        self.sync(20, [self.item("OWNER")])
        result = owner_inventory_diff_page(self.store)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["ad_id"], "OWNER")
        self.assertIsNone(result["items"][0]["local_draft_match"])

    def test_epoch_detects_page_drift_after_append(self):
        self.sync(0, [self.item("A"), self.item("B"), self.item("C")])
        self.sync(20, [self.item("A"), self.item("B"), self.item("C")])
        page = owner_inventory_diff_page(self.store, limit=1)
        self.assertEqual(page["next_after"], "A")
        self.assertEqual(owner_inventory_diff_page(
            self.store, after="A", limit=1, expected_epoch=page["epoch"]
        )["items"][0]["ad_id"], "B")
        self.store.append_ad_snapshot(self.item("LOCAL", source="local-catalog"))
        with self.assertRaises(InventoryDiffError):
            owner_inventory_diff_page(
                self.store, after="A", limit=1, expected_epoch=page["epoch"])

    def test_bad_cursors_and_bool_limits_are_rejected(self):
        for kwargs in ({"limit": 0}, {"limit": 101}, {"limit": True},
                       {"after": "\x00"}, {"after": "a"*161},
                       {"expected_epoch": "z"*64}):
            with self.subTest(kwargs=kwargs), self.assertRaises(InventoryDiffError):
                owner_inventory_diff_page(self.store, **kwargs)

    def test_ten_thousand_owner_ids_and_stable_keyset_pagination(self):
        start = T + timedelta(minutes=10)
        number = self.store.begin_sync_attempt(source=SOURCE, started_at=start)
        records = tuple(self.item(
            f"AD-{n:05d}", observed_at=start+timedelta(seconds=2), views=n)
            for n in range(10_000))
        self.assertEqual(self.store.append_inventory_result(
            ReadResult.success_nonempty(records), source=SOURCE,
            observed_at=start+timedelta(seconds=2), attempt_id=number,
            completed_at=start+timedelta(seconds=6)), 10_000)
        first = owner_inventory_diff_page(self.store, limit=100)
        self.assertEqual(first["total"], 10_000)
        self.assertEqual(first["next_after"], "AD-00099")
        second = owner_inventory_diff_page(
            self.store, after=first["next_after"], limit=100,
            expected_epoch=first["epoch"])
        self.assertEqual(second["items"][0]["ad_id"], "AD-00100")
        end = owner_inventory_diff_page(
            self.store, after="AD-09899", limit=100,
            expected_epoch=first["epoch"])
        self.assertEqual(end["items"][-1]["ad_id"], "AD-09999")
        self.assertFalse(end["has_more"])
        self.assertEqual(end["platform_writes"], 0)

    def test_corrupt_native_blob_id_is_detected_before_page(self):
        self.sync(0, [self.item("GOOD")])
        with self.store._connect() as connection:
            connection.execute(
                "INSERT INTO ad_snapshots "
                "(ad_id,observed_at,source,lifecycle_state) VALUES (?,?,?,?)",
                (sqlite3.Binary(b"BLOB"),T.isoformat(),SOURCE,"active"))
        with self.assertRaises(sqlite3.DatabaseError):
            owner_inventory_diff_page(self.store)


if __name__ == "__main__":
    unittest.main()
