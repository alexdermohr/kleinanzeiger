"""P4: read-only evidence-labelled local portfolio report, no model or platform IO.

All SQL reads run in ONE SQLite snapshot. SQLite date() normalizes imported
email source offsets to UTC before calendar-day comparisons. A missing
expense never becomes zero revenue cost or a fictitious causal conversion.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone, timedelta
import sqlite3

from .portfolio_sales import OWN_INVENTORY_SOURCES, schema as sales_schema
from .portfolio_jobs import _schema as jobs_schema
from .storage import SnapshotStore

_WINDOWS=(7,30,90)

class PortfolioReportError(ValueError):
    """Invalid time window or untrusted source report parameters."""


def report(store: SnapshotStore, *, days: int=7,
           now: datetime|None=None) -> dict[str,object]:
    """Non-billable UTC report using only distinct owner-observed ad IDs.

    Imported-message metrics measure only local EML events; unavailable inbox
    history is unknown. Sale aggregates show CURRENT manually-entered ledger
    values, not confirmed sales within the requested time window.
    """
    if type(store) is not SnapshotStore:
        raise PortfolioReportError("trusted local SQLite context required")
    if type(days) is not int or days not in _WINDOWS:
        raise PortfolioReportError("invalid report days")
    instant=now if now is not None else datetime.now(timezone.utc)
    if (not isinstance(instant,datetime)
            or instant.tzinfo is None or instant.utcoffset() is None):
        raise PortfolioReportError("timezone-aware report clock required")
    end=instant.astimezone(timezone.utc).date()
    start=end-timedelta(days=days-1)
    start_day=start.isoformat()
    end_day=end.isoformat()
    sources=OWN_INVENTORY_SOURCES
    placeholders=",".join("?" for _ in sources)
    where=(
        "EXISTS (SELECT 1 FROM ad_snapshots a WHERE "
        f"a.ad_id=e.ad_id COLLATE BINARY AND a.source IN ({placeholders})) "
    )
    with closing(store._connect()) as conn:
        conn.execute("BEGIN")
        # Snapshot/read provenance is bounded to known successful account
        # inventory readers, not arbitrary e-mail-only IDs or CSV SKUs.
        ads=int(conn.execute(
            "SELECT COUNT(DISTINCT ad_id) FROM ad_snapshots "
            f"WHERE source IN ({placeholders})",sources).fetchone()[0])
        # Invalid timestamps must fail closed rather than be silently omitted.
        invalid=conn.execute(
            "SELECT COUNT(*) FROM inbound_message_events e WHERE "
            +where+"AND date(e.observed_at) IS NULL",sources,
        ).fetchone()[0]
        if invalid:
            raise sqlite3.DatabaseError("invalid local event time")
        filt=("WHERE "+where+"AND date(e.observed_at) BETWEEN ? AND ?")
        params=(*sources,start_day,end_day)
        messages=int(conn.execute(
            "SELECT COUNT(*) FROM inbound_message_events e "+filt,
            params,
        ).fetchone()[0])
        conversations=int(conn.execute(
            "SELECT COUNT(*) FROM (SELECT e.ad_id,e.conversation_id "
            "FROM inbound_message_events e "+filt+
            " GROUP BY e.ad_id,e.conversation_id)",params,
        ).fetchone()[0])
        rows=conn.execute(
            "SELECT date(e.observed_at) day,COUNT(*) quantity "
            "FROM inbound_message_events e "+filt+
            " GROUP BY date(e.observed_at) ORDER BY day",params,
        ).fetchall()
        daily_counts={row["day"]:int(row["quantity"]) for row in rows}
        daily=[]
        for j in range(days):
            key=(start+timedelta(days=j)).isoformat()
            daily.append({"day_utc":key,
                          "imported_messages":daily_counts.get(key,0)})
        if sales_schema(conn):
            sale=conn.execute(
                "SELECT SUM(state='sold') sold,SUM(state='reserved') reserved,"
                "SUM(CASE WHEN state='sold' THEN revenue_eur ELSE 0 END) revenue,"
                "SUM(CASE WHEN state='sold' AND cost_eur IS NOT NULL "
                "THEN cost_eur ELSE 0 END) costs,"
                "SUM(state='sold' AND cost_eur IS NULL) missing,"
                "SUM(CASE WHEN state='sold' AND cost_eur IS NOT NULL "
                "THEN revenue_eur-cost_eur ELSE 0 END) surplus,"
                "SUM(state='sold' AND cost_eur IS NOT NULL) complete "
                "FROM portfolio_sale_states",
            ).fetchone()
            sales={"manually_sold":sale["sold"] or 0,
                   "manually_reserved":sale["reserved"] or 0,
                   "revenue_eur":sale["revenue"] or 0,
                   "known_cost_eur":sale["costs"] or 0,
                   "unknown_cost_count":sale["missing"] or 0,
                   "known_surplus_eur":sale["surplus"] or 0,
                   "known_surplus_sale_count":sale["complete"] or 0}
        else:
            sales={"manually_sold":0,"manually_reserved":0,
                   "revenue_eur":0,"known_cost_eur":0,
                   "unknown_cost_count":0,"known_surplus_eur":0,
                   "known_surplus_sale_count":0}
        if jobs_schema(conn,create=False):
            cost=conn.execute(
                "SELECT COALESCE(SUM(actual_cents),0) total,"
                "COALESCE(SUM(reserved_cents),0) reserved,"
                "SUM(phase='unknown') ambiguous,"
                "SUM(phase='inflight') running "
                "FROM portfolio_job_items",
            ).fetchone()
            ai={"usd_cent_estimate":int(cost["total"]),
                "usd_cent_reserved":int(cost["reserved"]),
                "unknown_outcomes":cost["ambiguous"] or 0,
                "inflight_outcomes":cost["running"] or 0,
                "invoice_verified":False}
        else:
            ai=None
    return {
        "window_days":days,
        "start_day_utc":start_day,
        "end_day_utc":end_day,
        "as_of_utc":instant.astimezone(timezone.utc).isoformat(),
        "own_ads_with_inventory_history":ads,
        "imported_inbound_messages_window":messages,
        "imported_conversations_window":conversations,
        "imported_messages_per_day":daily,
        "manual_sales_current_all_time":sales,
        "ai_usage_estimate_all_time":ai,
        "scope": "local_observations_no_platform_or_provider_sync",
        "revenue_period_attributed":False,
        "conversion_rate_supported":False,
        "platform_writes":0,
    }