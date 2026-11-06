"""Verified own-ad observation gates, manual price identity, bounded local plans."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from mark_api.domain import AdSnapshot, LifecycleState
from mark_api.portfolio_price_rules import PriceRuleError
from mark_api.portfolio_owner_price_rules import (
    OwnerPriceRuleError, delete_owner_price_rule, owner_price_rule_detail,
    owner_price_suggestions_page, set_owner_price_rule,
)
from mark_api.results import ReadResult
from mark_api.storage import SnapshotStore


NOW = datetime(2026, 11, 5, 18, 0, tzinfo=timezone.utc)


class OwnListingPricePlans(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="kleinanzeiger-owner-price-")
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "portfolio.sqlite"
        self.store = SnapshotStore(self.db)

    def observed(self, ad="101", *, time=NOW, state=LifecycleState.ACTIVE,
                 source="kleinanzeigen-management"):
        self.store.append_ad_snapshot(AdSnapshot(
            ad_id=ad, observed_at=time, source=source, lifecycle_state=state,
            title="Synthetischer Eigenartikel", description="Keine echte Anzeige",
        ))

    def save(self, *, ad="101", manual=55, minimum=35, step=10, wait=7,
             revision=0, key="a"*64, now=NOW):
        return set_owner_price_rule(
            self.store, ad_id=ad, manual_price_eur=manual,
            minimum_price_eur=minimum, reduction_step_eur=step, wait_days=wait,
            expected_revision=revision, request_key=key, now=now,
        )

    def test_empty_reads_do_not_create_tables_or_claim_platform_price(self):
        self.assertEqual(owner_price_suggestions_page(self.store)["total"], 0)
        detail = owner_price_rule_detail(self.store, ad_id="101")
        self.assertFalse(detail["owner"]["confirmed"])
        self.assertIsNone(detail["rule"])
        self.assertEqual(detail["price_source"], "manually_entered_not_platform_verified")
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE name LIKE 'portfolio_owner_price_%'"
            ).fetchone()[0], 0)

    def test_must_have_current_active_owned_snapshot_and_fresh_clock(self):
        with self.assertRaises(OwnerPriceRuleError):
            self.save()
        self.observed(source="kleinanzeigen-email")
        with self.assertRaises(OwnerPriceRuleError):
            self.save()
        self.observed(time=NOW+timedelta(seconds=1), state=LifecycleState.PAUSED)
        with self.assertRaises(OwnerPriceRuleError):
            self.save(now=NOW+timedelta(seconds=2))
        self.observed(time=NOW+timedelta(seconds=3))
        self.save(now=NOW+timedelta(seconds=4))
        self.assertTrue(owner_price_rule_detail(
            self.store, ad_id="101", now=NOW+timedelta(seconds=5)
        )["owner"]["confirmed"])
        self.observed(time=NOW+timedelta(seconds=6), state=LifecycleState.ABSENT)
        unsafe = owner_price_suggestions_page(self.store, now=NOW+timedelta(days=8))
        self.assertEqual(unsafe["rules"][0]["state"], "owner_unconfirmed")
        self.assertIsNone(unsafe["rules"][0]["suggested_price_eur"])

    def test_manual_price_timing_refresh_and_no_marketplace_write(self):
        self.observed()
        saved = self.save()
        self.assertEqual((saved["revision"], saved["manual_price_eur"]), (1, 55))
        self.assertFalse(saved["platform_price_verified"])
        self.assertTrue(saved["requires_live_price_recheck"])
        self.assertEqual((saved["platform_writes"], saved["provider_requests"]), (0, 0))
        # An out-of-date observation never yields a recommendation, even if
        # the locally configured waiting period has elapsed.
        stale = owner_price_suggestions_page(self.store, now=NOW+timedelta(days=8))
        self.assertEqual(stale["rules"][0]["state"], "owner_unconfirmed")
        self.observed(time=NOW+timedelta(days=6))
        waiting = owner_price_suggestions_page(self.store, now=NOW+timedelta(days=6))
        self.assertEqual(waiting["rules"][0]["state"], "waiting")
        self.observed(time=NOW+timedelta(days=8))
        due = owner_price_suggestions_page(self.store, now=NOW+timedelta(days=8))
        self.assertEqual(due["rules"][0]["suggested_price_eur"], 45)
        self.assertEqual(due["rules"][0]["state"], "suggested")
        self.assertEqual(due["rules"][0]["eligible_at"], (NOW+timedelta(days=7)).isoformat())
        self.assertEqual(due["rules"][0]["price_source"], "manually_entered_not_platform_verified")
        with closing(self.store._connect()) as conn:
            self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
        self.assertEqual(self.store.tracked_ad_ids(), ("101",))
        self.assertEqual(self.store.sync_status()["state"], "never_attempted")

    def test_idempotent_exact_replay_revision_cas_and_deletion_without_owned_state(self):
        self.observed()
        initial = self.save(wait=0)
        self.assertEqual(initial, self.save(wait=0))
        with self.assertRaises(OwnerPriceRuleError):
            self.save(wait=1)
        with self.assertRaises(OwnerPriceRuleError):
            self.save(revision=0, key="b"*64)
        changed = self.save(revision=1, key="c"*64, wait=1,
                            manual=60, minimum=40, now=NOW+timedelta(hours=1))
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(changed["state"], "waiting")
        self.observed(time=NOW+timedelta(hours=2), state=LifecycleState.PAUSED)
        with self.assertRaises(OwnerPriceRuleError):
            self.save(revision=2, key="d"*64, now=NOW+timedelta(hours=2))
        deleted = delete_owner_price_rule(
            self.store, ad_id="101", expected_revision=2, request_key="e"*64)
        self.assertEqual(deleted, delete_owner_price_rule(
            self.store, ad_id="101", expected_revision=2, request_key="e"*64))
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["platform_writes"], 0)
        self.assertEqual(owner_price_suggestions_page(self.store)["total"], 0)
        self.observed(time=NOW+timedelta(hours=3))
        recreated = self.save(revision=0, key="f"*64, now=NOW+timedelta(hours=3))
        self.assertEqual(recreated["revision"], 4)
        with self.assertRaises(OwnerPriceRuleError):
            self.save(revision=2, key="0"*64, now=NOW+timedelta(hours=3))

    def test_validations_and_corrupt_state_fail_explicitly(self):
        self.observed()
        for options in (
            {"manual": False}, {"manual": 0}, {"manual": 100_000_000},
            {"minimum": 56}, {"minimum": 0}, {"step": True},
            {"step": 0}, {"wait": 3651}, {"wait": "7"},
            {"revision": -1}, {"key": "x"}, {"ad": "../101"},
        ):
            with self.subTest(options=options), self.assertRaises(PriceRuleError):
                self.save(**options)
        self.save(wait=0)
        with closing(self.store._connect()) as conn, conn:
            conn.execute("UPDATE portfolio_owner_price_rules "
                         "SET updated_at='not-a-date' WHERE ad_id='101'")
        with self.assertRaises(sqlite3.DatabaseError):
            owner_price_rule_detail(self.store, ad_id="101", now=NOW)
        with self.assertRaises(sqlite3.DatabaseError):
            owner_price_suggestions_page(self.store, now=NOW)
        with closing(self.store._connect()) as conn, conn:
            conn.execute("DROP TABLE portfolio_owner_price_requests")
        with self.assertRaises(sqlite3.DatabaseError):
            owner_price_rule_detail(self.store, ad_id="101")

    def test_manual_floor_does_not_propose_extra_reductions(self):
        self.observed()
        self.save(manual=35, minimum=35, wait=0)
        at_floor = owner_price_rule_detail(self.store, ad_id="101", now=NOW)["rule"]
        self.assertEqual(at_floor["state"], "at_minimum")
        self.assertIsNone(at_floor["suggested_price_eur"])

    def test_large_keyset_page_never_loads_all_10k_rules(self):
        ids = [f"{i:05d}" for i in range(10_000)]
        observations = tuple(AdSnapshot(
            ad_id=ad, observed_at=NOW, source="kleinanzeigen-management",
            lifecycle_state=LifecycleState.ACTIVE, title="synthetic",
        ) for ad in ids)
        self.assertEqual(self.store.append_inventory_result(
            ReadResult.success_nonempty(observations),
            source="kleinanzeigen-management", observed_at=NOW,
        ), 10_000)
        # Seed a homogeneous 10k local rule catalogue in one transaction.
        self.save(ad=ids[0], wait=0)
        with closing(self.store._connect()) as conn, conn:
            conn.executemany(
                "INSERT INTO portfolio_owner_price_rules VALUES (?,?,?,?,?,?,?,?)",
                ((ad, 100, 40, 10, 0, 1, 0, NOW.isoformat()) for ad in ids[1:]),
            )
        first = owner_price_suggestions_page(self.store, limit=50, now=NOW)
        self.assertEqual((first["total"], len(first["rules"]), first["has_more"]),
                         (10_000, 50, True))
        self.assertEqual(first["rules"][0]["ad_id"], "00000")
        second = owner_price_suggestions_page(
            self.store, after=first["next_after"], limit=50, now=NOW)
        self.assertEqual(second["rules"][0]["ad_id"], "00050")
        self.assertEqual(second["total"], 10_000)
        self.assertEqual(second["rules"][0]["suggested_price_eur"], 90)
        self.assertTrue(all(r["platform_writes"] == 0 for r in second["rules"]))

    def test_nonowner_latest_observation_is_not_laundered_by_historical_owner(self):
        self.observed(time=NOW)
        self.save(wait=0)
        self.observed(time=NOW+timedelta(seconds=5), source="imported-local-test")
        detail = owner_price_rule_detail(
            self.store, ad_id="101", now=NOW+timedelta(seconds=10))
        self.assertFalse(detail["owner"]["confirmed"])
        self.assertIsNone(detail["rule"]["suggested_price_eur"])


    def test_chronological_ownership_across_offsets_and_equal_instant(self):
        plus_two = timezone(timedelta(hours=2))
        earlier_active = (NOW - timedelta(hours=1)).astimezone(plus_two)
        self.observed(time=earlier_active)
        self.observed(time=NOW, state=LifecycleState.PAUSED)
        with self.assertRaises(OwnerPriceRuleError):
            self.save(now=NOW + timedelta(seconds=1))
        detail = owner_price_rule_detail(
            self.store, ad_id="101", now=NOW + timedelta(seconds=1))
        self.assertEqual(detail["owner"]["state"], "paused")
        self.assertEqual(detail["owner"]["observed_at"], NOW.isoformat())
        # Equal instants must use the latest insertion ID, not the stored
        # timezone offset's lexical ordering.
        self.observed(time=NOW.astimezone(plus_two), state=LifecycleState.ACTIVE)
        self.assertTrue(owner_price_rule_detail(
            self.store, ad_id="101", now=NOW + timedelta(seconds=1)
        )["owner"]["confirmed"])
        self.save(now=NOW + timedelta(seconds=1))

    def test_reactivating_deleted_rule_still_obeys_active_limit(self):
        from unittest.mock import patch

        for ad in ("101", "102", "103"):
            self.observed(ad=ad)
        with patch("mark_api.portfolio_owner_price_rules._MAX_RULES", 2):
            self.save(ad="101")
            self.save(ad="102", key="b" * 64)
            delete_owner_price_rule(
                self.store, ad_id="101", expected_revision=1,
                request_key="c" * 64)
            self.save(ad="103", key="d" * 64)
            with self.assertRaises(OwnerPriceRuleError):
                self.save(ad="101", key="e" * 64)
            page = owner_price_suggestions_page(self.store, now=NOW)
            self.assertEqual((page["total"], len(page["rules"])), (2, 2))
            self.assertEqual(
                {rule["ad_id"] for rule in page["rules"]}, {"102", "103"})


if __name__ == "__main__":
    unittest.main()
