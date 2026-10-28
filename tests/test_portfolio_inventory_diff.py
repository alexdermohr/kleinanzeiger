"""G4 read-only owner-sync diff and 10k keyset regressions."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.adapters.management import DEFAULT_SOURCE as OBSERVATION_SOURCE
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
             views=1, observed_at=T, source=OBSERVATION_SOURCE):
        return AdSnapshot(ad_id=ad_id, observed_at=observed_at, source=source,
                          lifecycle_state=state, title=title, description="Gebraucht",
                          views=views)

    def sync(self, minute, snapshots=(), *, tracked=(), failed=False):
        start = T + timedelta(minutes=minute)
        attempt = self.store.begin_sync_attempt(source=SOURCE, started_at=start)
        records = tuple(self.item(
            value.ad_id, title=value.title, state=value.lifecycle_state,
            views=value.views, observed_at=start + timedelta(seconds=2),
            source=value.source)
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

    def test_real_management_provenance_observation_survives_owner_journal(self):
        # The real CDP reader supplies ManagementReadAdapter.DEFAULT_SOURCE,
        # while its separate independent owner journal uses SOURCE.
        self.assertNotEqual(SOURCE, OBSERVATION_SOURCE)
        self.sync(0, [self.item("REAL-1", title="Alt")])
        self.sync(15, [self.item("REAL-1", title="Neu"),
                       self.item("REAL-2", title="Erstmals")])
        result = owner_inventory_diff_page(self.store)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["total"], 2)
        items = {row["ad_id"]: row for row in result["items"]}
        self.assertEqual(items["REAL-1"]["state"], "changed")
        self.assertEqual(items["REAL-1"]["changed_fields"], ["title"])
        self.assertEqual(items["REAL-1"]["current"]["source"], OBSERVATION_SOURCE)
        self.assertEqual(items["REAL-2"]["state"], "newly_observed")
        self.assertEqual(result["source"], SOURCE)

    def test_other_source_unresolved_sync_invalidates_pagination_epoch(self):
        self.sync(0, [self.item("A"), self.item("B")])
        self.sync(20, [self.item("A"), self.item("B")])
        page = owner_inventory_diff_page(self.store, limit=1)
        self.assertEqual(page["state"], "ready")
        self.assertTrue(page["has_more"])
        other = self.store.begin_sync_attempt(
            source="private-web-periodic-readonly",
            started_at=T + timedelta(minutes=50))
        with self.assertRaisesRegex(InventoryDiffError, "neu laden"):
            owner_inventory_diff_page(
                self.store, after="A", limit=1, expected_epoch=page["epoch"])
        blocked = owner_inventory_diff_page(self.store)
        self.assertEqual(blocked["state"], "unverified")
        self.assertEqual(blocked["unresolved_sync_attempts"], 1)
        self.store.fail_sync_attempt(
            other, source="private-web-periodic-readonly",
            completed_at=T + timedelta(minutes=51),
            error_kind="transport_error")
        with self.assertRaisesRegex(InventoryDiffError, "neu laden"):
            owner_inventory_diff_page(
                self.store, after="A", limit=1, expected_epoch=page["epoch"])
        restored = owner_inventory_diff_page(self.store)
        self.assertNotEqual(restored["epoch"], page["epoch"])
        self.assertEqual(restored["state"], "ready")

    def test_completed_periodic_sync_overlapping_owner_round_is_not_owner_evidence(self):
        # Both readers emit the same management snapshot source, but distinct
        # journal sources. Matching only timestamps would falsely attribute the
        # periodic observation to the owner's successful round.
        self.sync(0, [self.item("A", title="Vorher")])
        start = T + timedelta(minutes=20)
        owner = self.store.begin_sync_attempt(source=SOURCE, started_at=start)
        periodic_source = "private-web-periodic-readonly"
        periodic = self.store.begin_sync_attempt(
            source=periodic_source, started_at=start+timedelta(seconds=1))
        self.store.append_inventory_result(
            ReadResult.success_nonempty((self.item(
                "A", title="Periodik", observed_at=start+timedelta(seconds=5)),)),
            source=periodic_source, attempt_id=periodic,
            observed_at=start+timedelta(seconds=5),
            completed_at=start+timedelta(seconds=7))
        self.store.append_inventory_result(
            ReadResult.success_nonempty((self.item(
                "A", title="Owner", observed_at=start+timedelta(seconds=2)),)),
            source=SOURCE, attempt_id=owner,
            observed_at=start+timedelta(seconds=2),
            completed_at=start+timedelta(seconds=8))
        result = owner_inventory_diff_page(self.store)
        self.assertEqual(result["state"], "unverified")
        self.assertEqual(result["items"][0]["state"], "unverified")
        self.assertIsNone(result["items"][0]["current"])
        self.assertIsNone(result["confirmed_at"])

    def test_nonoverlapping_periodic_sync_does_not_mask_owner_comparison(self):
        self.sync(0, [self.item("A", title="Owner alt")])
        periodic_source = "private-web-periodic-readonly"
        started = T + timedelta(minutes=10)
        attempt = self.store.begin_sync_attempt(
            source=periodic_source, started_at=started)
        self.store.append_inventory_result(
            ReadResult.success_nonempty((self.item(
                "A", title="Periodik", observed_at=started+timedelta(seconds=2)),)),
            source=periodic_source, attempt_id=attempt,
            observed_at=started+timedelta(seconds=2),
            completed_at=started+timedelta(seconds=5))
        self.sync(20, [self.item("A", title="Owner neu")])
        result = owner_inventory_diff_page(self.store)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["state"], "changed")
        self.assertEqual(result["items"][0]["current"]["title"], "Owner neu")
        self.assertEqual(result["items"][0]["previous"]["title"], "Owner alt")

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
