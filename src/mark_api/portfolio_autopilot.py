"""Offline Autopilot attention radar: deterministic local facts, never a provider or platform action.

This is a prioritised work queue for human review, not AI inference or proof
of a live marketplace inbox.  The query runs in one read-only SQLite snapshot.
It never creates optional tables, modifies jobs, retransmits a pending request
or treats historical owner observations as a current permission to write.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import re
import sqlite3

from .portfolio_conversations import _schema as conversation_schema
from .portfolio_existing_jobs import _schema as existing_jobs_schema
from .portfolio_followups import _schema as followup_schema
from .portfolio_sales import OWN_INVENTORY_SOURCES
from .storage import SnapshotStore


class AutopilotAttentionError(ValueError):
    """Malformed local query, not an authorization to take an action."""


_KINDS = frozenset((
    "ai_uncertain", "eml_unreviewed", "manual_overdue",
    "ai_proposal", "manual_due_soon", "views_stagnant",
))
_AD_ID = re.compile(r"[^\x00-\x1f\x7f]{1,160}\Z")
_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
_CONVERSATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_-]{2,255}\Z")


_STAGNATION_DAYS = 14
_STAGNATION_BASELINE_MAX_DAYS = 30
_STAGNATION_FRESH_HOURS = 48


def _stagnant_owned_views(
    conn: sqlite3.Connection, instant: datetime,
) -> list[tuple[str, str, str]]:
    """Conservative 14-day view-counter evidence, never inferred buyer demand.

    An active owned ad is eligible only when the newest *overall* observation
    is fresh, two owner-observed counters have the same explicit metric source
    at least 14 days apart (but at most 30), and every intervening observation
    remains active, owned, comparable and equal. Unknown counters, timestamp
    errors, counter resets, foreign imports, stale reads and source changes
    suppress the hint rather than manufacture a zero-demand claim.
    """
    fresh = instant - timedelta(hours=_STAGNATION_FRESH_HOURS)
    oldest = instant - timedelta(days=_STAGNATION_BASELINE_MAX_DAYS)
    result: list[tuple[str, str, str]] = []

    def classify(ad_id: object, group: list[sqlite3.Row]) -> None:
        if type(ad_id) is not str or _AD_ID.fullmatch(ad_id) is None or not ad_id.strip():
            return
        observations: list[tuple[datetime, int, sqlite3.Row]] = []
        for row in group:
            try:
                if type(row["id"]) is not int or row["id"] <= 0 or type(row["observed_at"]) is not str:
                    return
                observed = datetime.fromisoformat(row["observed_at"])
                if observed.tzinfo is None or observed.utcoffset() is None:
                    return
                observations.append((observed.astimezone(timezone.utc), row["id"], row))
            except (TypeError, ValueError, OverflowError):
                return
        observations.sort(key=lambda item: (item[0], item[1]))
        last_at, _, latest = observations[-1]
        last_counter, metric_source = latest["views"], latest["metric_source"]
        if (
            not fresh <= last_at <= instant
            or latest["source"] not in OWN_INVENTORY_SOURCES
            or latest["lifecycle_state"] != "active"
            or type(last_counter) is not int or last_counter < 0
            or type(metric_source) is not str or not metric_source.strip()
        ):
            return
        before = last_at - timedelta(days=_STAGNATION_DAYS)
        eligible = [
            item for item in observations
            if oldest <= item[0] <= before
            and item[2]["source"] == latest["source"]
            and item[2]["lifecycle_state"] == "active"
            and item[2]["metric_source"] == metric_source
            and type(item[2]["views"]) is int
            and item[2]["views"] >= 0
        ]
        if not eligible:
            return
        first_at, _, baseline = eligible[-1]
        if baseline["views"] != last_counter:
            return
        # Reject missing measurements between the two comparisons as well:
        # zero growth is only meaningful if no intervening row contradicts it.
        for observed, _, row in observations:
            if first_at <= observed <= last_at and (
                row["source"] != latest["source"]
                or row["lifecycle_state"] != "active"
                or row["metric_source"] != metric_source
                or type(row["views"]) is not int
                or row["views"] != last_counter
            ):
                return
        result.append((
            ad_id,
            first_at.date().isoformat() + " bis " + last_at.date().isoformat(),
            "Aufrufe " + str(last_counter) + " → " + str(last_counter),
        ))

    last_id: object = None
    group: list[sqlite3.Row] = []
    # Existing ad-ID index supports this streaming, memory-bounded group scan;
    # do not create a cache, temp table or persistent claim from a read.
    for row in conn.execute(
        "SELECT id,ad_id,observed_at,source,lifecycle_state,views,metric_source "
        "FROM ad_snapshots ORDER BY ad_id COLLATE BINARY,id"
    ):
        if group and row["ad_id"] != last_id:
            classify(last_id, group)
            group = []
        last_id = row["ad_id"]
        group.append(row)
    if group:
        classify(last_id, group)
    return result


def attention_page(
    store: SnapshotStore, *, offset: int = 0, limit: int = 50,
    now: datetime | None = None,
) -> dict[str, object]:
    """One bounded, globally prioritised snapshot page for the customer's local dashboard.

    Priority: uncertain/potentially billable AI outcomes; imported unreviewed
    notifications for historically observed own ads; manually overdue reminders;
    generated but locally unreviewed AI proposals; manually due-soon reminders;
    finally fresh and comparable views that have not increased for 14 days.

    Automatic actions are prohibited. IDs, local counter values, dates and
    source labels only: message content, model prompts, buyer PII and
    user-entered reminder notes never leave the store through this endpoint.
    """
    if (type(store) is not SnapshotStore
            or type(offset) is not int or not 0 <= offset <= 100_000
            or type(limit) is not int or not 1 <= limit <= 50):
        raise AutopilotAttentionError("invalid attention page")
    instant = datetime.now(timezone.utc) if now is None else now
    if type(instant) is not datetime or instant.tzinfo is None or instant.utcoffset() is None:
        raise AutopilotAttentionError("timezone-aware clock required")
    today: date = instant.astimezone(timezone.utc).date()
    tomorrow_week = today + timedelta(days=7)
    early = today.isoformat()
    later = tomorrow_week.isoformat()

    fragments: list[str] = []
    parameters: list[object] = []
    with closing(store._connect()) as conn:
        conn.execute("BEGIN")
        if existing_jobs_schema(conn, create=False):
            fragments.extend((
                "SELECT 0 priority, 'ai_uncertain' kind, i.ad_id, i.job_id reference, "
                "i.phase evidence FROM portfolio_existing_job_items i "
                "INNER JOIN portfolio_existing_jobs j ON j.job_id=i.job_id "
                "WHERE i.phase IN ('unknown','inflight')",
                "SELECT 3 priority, 'ai_proposal' kind, i.ad_id, i.job_id reference, "
                "i.phase evidence FROM portfolio_existing_job_items i "
                "INNER JOIN portfolio_existing_jobs j ON j.job_id=i.job_id "
                "WHERE i.phase='proposed'",
            ))
        if conversation_schema(conn, create=False):
            owner_marks = ",".join("?" for _ in OWN_INVENTORY_SOURCES)
            fragments.append(
                "SELECT 1 priority, 'eml_unreviewed' kind, e.ad_id, "
                "e.conversation_id reference, CAST(e.last_id AS TEXT) evidence "
                "FROM (SELECT ad_id,conversation_id,MAX(id) last_id "
                "FROM inbound_message_events GROUP BY ad_id,conversation_id) e "
                "LEFT JOIN portfolio_conversation_reviews r ON "
                "e.ad_id=r.ad_id COLLATE BINARY AND "
                "e.conversation_id=r.conversation_id COLLATE BINARY "
                "WHERE (r.seen_id IS NULL OR e.last_id>r.seen_id) "
                "AND EXISTS (SELECT 1 FROM ad_snapshots a WHERE "
                "a.ad_id=e.ad_id COLLATE BINARY AND a.source IN (" + owner_marks + "))"
            )
            parameters.extend(OWN_INVENTORY_SOURCES)
        else:
            # EML notifications can exist without the optional local-review schema.
            owner_marks = ",".join("?" for _ in OWN_INVENTORY_SOURCES)
            fragments.append(
                "SELECT 1 priority, 'eml_unreviewed' kind, e.ad_id, "
                "e.conversation_id reference, CAST(e.last_id AS TEXT) evidence "
                "FROM (SELECT ad_id,conversation_id,MAX(id) last_id "
                "FROM inbound_message_events GROUP BY ad_id,conversation_id) e "
                "WHERE EXISTS (SELECT 1 FROM ad_snapshots a WHERE "
                "a.ad_id=e.ad_id COLLATE BINARY AND a.source IN (" + owner_marks + "))"
            )
            parameters.extend(OWN_INVENTORY_SOURCES)
        if followup_schema(conn, create=False):
            # Never turn malformed dates into skipped reminders and a
            # misleading all-clear. The indexed limit applies to worklist
            # paging, while this cheap existence check is conditional.
            invalid = conn.execute(
                "SELECT 1 FROM portfolio_followups WHERE deleted=0 AND "
                "(typeof(due_date)!='text' OR length(due_date)!=10 "
                "OR date(due_date) IS NULL) LIMIT 1"
            ).fetchone()
            if invalid is not None:
                raise sqlite3.DatabaseError("invalid local follow-up date")
            fragments.extend((
                "SELECT 2 priority, 'manual_overdue' kind, ad_id, "
                "ad_id reference, due_date evidence FROM portfolio_followups "
                "WHERE deleted=0 AND due_date<?",
                "SELECT 4 priority, 'manual_due_soon' kind, ad_id, "
                "ad_id reference, due_date evidence FROM portfolio_followups "
                "WHERE deleted=0 AND due_date>=? AND due_date<=?",
            ))
            parameters.extend((early, early, later))
        # Each source is a bound query against stored local evidence, with
        # no import of CDP drivers, AI provider, write API or browser runtime.
        union = " UNION ALL ".join(fragments)
        cte = "WITH attention AS (" + union + ") "
        counts = conn.execute(
            cte + "SELECT kind,COUNT(*) total FROM attention GROUP BY kind",
            parameters,
        ).fetchall()
        total_by_kind = {kind: 0 for kind in sorted(_KINDS)}
        for r in counts:
            if r["kind"] not in _KINDS or type(r["total"]) is not int:
                raise sqlite3.DatabaseError("invalid local attention tally")
            total_by_kind[r["kind"]] = r["total"]
        prior_total = sum(total_by_kind.values())
        stagnant = _stagnant_owned_views(conn, instant.astimezone(timezone.utc))
        total_by_kind["views_stagnant"] = len(stagnant)
        total = prior_total + len(stagnant)
        prior_size = min(limit, max(0, prior_total - offset))
        rows = conn.execute(
            cte + "SELECT priority,kind,ad_id,reference,evidence FROM attention "
            "ORDER BY priority,ad_id COLLATE BINARY,reference COLLATE BINARY "
            "LIMIT ? OFFSET ?",
            (*parameters, prior_size, offset),
        ).fetchall() if prior_size else []
        results = []
        for row in rows:
            kind, ad_id, reference, evidence, priority = (
                row["kind"], row["ad_id"], row["reference"],
                row["evidence"], row["priority"],
            )
            if (kind not in _KINDS or type(ad_id) is not str
                    or _AD_ID.fullmatch(ad_id) is None or not ad_id.strip()
                    or type(reference) is not str or not reference
                    or type(evidence) is not str
                    or type(priority) is not int or not 0 <= priority <= 4):
                raise sqlite3.DatabaseError("invalid local attention entry")
            if kind.startswith("ai_"):
                if _JOB_ID.fullmatch(reference) is None or evidence not in (
                    ("unknown", "inflight") if kind == "ai_uncertain" else ("proposed",)
                ):
                    raise sqlite3.DatabaseError("invalid AI attention evidence")
            elif kind == "eml_unreviewed":
                if (_CONVERSATION_ID.fullmatch(reference) is None
                        or not evidence.isdecimal() or int(evidence) < 1):
                    raise sqlite3.DatabaseError("invalid EML attention evidence")
            else:
                try:
                    due = date.fromisoformat(evidence)
                except ValueError as exc:
                    raise sqlite3.DatabaseError("invalid reminder date") from exc
                if (kind == "manual_overdue" and due >= today) or (
                    kind == "manual_due_soon" and not today <= due <= tomorrow_week
                ):
                    raise sqlite3.DatabaseError("invalid reminder attention state")
            results.append({
                "kind": kind, "priority": priority,
                "ad_id": ad_id, "reference": reference, "evidence": evidence,
                "source": (
                    "local_ai_job_state" if kind.startswith("ai_") else
                    "imported_email_metadata" if kind == "eml_unreviewed" else
                    "manual_user_followup"
                ),
            })
        if len(results) != prior_size:
            raise sqlite3.DatabaseError("attention page count mismatch")
        if len(results) < limit:
            stagnant_offset = max(0, offset - prior_total)
            for ad_id, period, observed_views in stagnant[
                stagnant_offset:stagnant_offset + limit - len(results)
            ]:
                results.append({
                    "kind": "views_stagnant", "priority": 5,
                    "ad_id": ad_id, "reference": period,
                    "evidence": observed_views,
                    "source": "confirmed_own_view_counter_history",
                })
        if len(results) != min(limit, max(0, total - offset)):
            raise sqlite3.DatabaseError("attention page count mismatch")
        has_more = offset + len(results) < total
    return {
        "items": results,
        "total": total, "counts": total_by_kind, "offset": offset,
        "limit": limit, "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
        "as_of_utc": instant.astimezone(timezone.utc).isoformat(),
        "platform_writes": 0,
        "provider_requests": 0,
        "provider_cost_cents": 0,
        "platform_messages_read": False,
        "automatic_execution_authorized": False,
        "live_ownership_recheck_required": True,
        "scope": "local_observations_only",
    }
