from __future__ import annotations

from contextlib import closing, nullcontext
from dataclasses import dataclass, field
import re
from typing import Mapping
from datetime import datetime

from .domain import AdSnapshot, LifecycleState, ReactionSnapshot
from .storage import LOCAL_CATALOG_SOURCE, SnapshotStore
from .storage import SnapshotStore
from .storage_owner import StorageOwnerClient


@dataclass(frozen=True, slots=True)
class MetricEvidence:
    """Evidence for one value, never a claim about sync success."""

    observed_at: datetime
    source: str
    # Only inventory metrics have a meaningful latest-status comparison.
    last_known: bool | None


@dataclass(frozen=True, slots=True)
class AdView:
    ad_id: str
    lifecycle_state: LifecycleState
    present: bool
    observed_at: datetime
    source: str
    title: str | None
    description: str | None
    views: int | None
    watch_count: int | None
    reply_count: int | None
    metric_evidence: Mapping[str, MetricEvidence | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmailReactionView:
    ad_id: str
    conversation_count: int
    inbound_message_count: int
    first_observed_at: datetime
    last_observed_at: datetime


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    tracked_ads: int
    current_ads: int
    absent_ads: int
    unknown_state_ads: int
    views_total_known: int
    views_observed_ads: int
    watch_total_known: int
    watch_observed_ads: int
    replies_total_known: int
    replies_observed_ads: int


def _last_not_none(
    history: tuple[AdSnapshot, ...],
    field_name: str,
) -> str | int | None:
    for snapshot in reversed(history):
        value = getattr(snapshot, field_name)
        if value is not None:
            return value
    return None


def _last_metric_with_evidence(
    history: tuple[AdSnapshot, ...],
    field_name: str,
) -> tuple[int | None, MetricEvidence | None]:
    """Use the same selected snapshot for a metric value and its origin."""

    for position, snapshot in enumerate(reversed(history)):
        value = getattr(snapshot, field_name)
        if value is not None:
            return value, MetricEvidence(
                observed_at=snapshot.observed_at,
                source=(
                    snapshot.metric_source
                    if snapshot.metric_source is not None
                    else snapshot.source
                ),
                last_known=position != 0,
            )
    return None, None


class MarkQueryService:
    """Read-only projections over the append-only SQLite history."""

    def __init__(self, store: SnapshotStore | StorageOwnerClient) -> None:
        self._store = store

    @staticmethod
    def _project_history(
        ad_id: str, history: tuple[AdSnapshot, ...],
    ) -> AdView | None:
        """Same owner-versus-catalog, last-known metric semantics for both UIs."""
        if not history:
            return None
        owner_history = tuple(
            item for item in history if item.source != LOCAL_CATALOG_SOURCE
        )
        catalog_history = tuple(
            item for item in history if item.source == LOCAL_CATALOG_SOURCE
        )
        owner = owner_history[-1] if owner_history else None
        latest = owner if owner is not None else history[-1]
        metrics: dict[str, tuple[int | None, MetricEvidence | None]] = {}
        for field_name in ("views", "watch_count", "reply_count"):
            value, evidence = _last_metric_with_evidence(
                owner_history if owner_history else history, field_name,
            )
            if value is None and owner_history:
                value, evidence = _last_metric_with_evidence(
                    catalog_history, field_name,
                )
                if evidence is not None:
                    evidence = MetricEvidence(
                        observed_at=evidence.observed_at,
                        source=evidence.source,
                        last_known=True,
                    )
            metrics[field_name] = value, evidence
        return AdView(
            ad_id=ad_id,
            lifecycle_state=(
                owner.lifecycle_state if owner is not None
                else LifecycleState.UNKNOWN
            ),
            present=(
                owner is not None
                and owner.lifecycle_state is not LifecycleState.ABSENT
            ),
            observed_at=latest.observed_at,
            source=latest.source,
            title=_last_not_none(
                owner_history if owner_history else history, "title",
            ),
            description=_last_not_none(
                owner_history if owner_history else history, "description",
            ),
            views=metrics["views"][0],
            watch_count=metrics["watch_count"][0],
            reply_count=metrics["reply_count"][0],
            metric_evidence={
                field_name: evidence
                for field_name, (_, evidence) in metrics.items()
            },
        )

    def latest_ads(self) -> tuple[AdView, ...]:
        rows: list[AdView] = []
        # Backward-compatible complete view for existing reports and APIs.
        if isinstance(self._store, StorageOwnerClient):
            rows_by_id = (
                (ad_id, self._store.ad_history(ad_id))
                for ad_id in self._store.tracked_ad_ids()
            )
            history_source = nullcontext(rows_by_id)
        else:
            history_source = closing(self._store.ad_histories())
        with history_source as histories:
            for ad_id, history in histories:
                item = self._project_history(ad_id, history)
                if item is not None:
                    rows.append(item)
        return tuple(rows)

    def catalog_ad(self, ad_id: str) -> dict[str, object] | None:
        """Read exactly one locally observed ad by indexed ID; no write authority.

        This avoids scanning pages or materializing the whole 10k+ portfolio
        during per-ad status checks. Historical owner provenance stays intact.
        """
        if type(ad_id) is not str or re.fullmatch(r"[0-9]{1,32}", ad_id) is None:
            raise ValueError("invalid catalog ad id")
        item = self._project_history(ad_id, self._store.ad_history(ad_id))
        return ad_view_to_dict(item) if item is not None else None

    def catalog_page(
        self, *, after: str = "", limit: int = 50,
        search: str = "", status: str = "all",
    ) -> dict[str, object]:
        """Return a small, ID-ordered, read-only page of own ad observations.

        No platform transport, Write capability or SQL expression is accepted.
        Native Windows SQLite reads *only* the required indexed ad-ID pages
        and their histories, not every item before creating a result.
        Filtering can scan more observed IDs but never exposes a giant JSON
        or grants authority from a stale local observation.
        """
        if (
            type(limit) is not int or not 1 <= limit <= 100
            or type(after) is not str or len(after) > 160
            or type(search) is not str or len(search) > 120
            or type(status) is not str
            or status not in {"all", *(state.value for state in LifecycleState)}
            or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF
                   for char in (after + search))
        ):
            raise ValueError("invalid catalog filter or cursor")
        needle = search.strip().casefold()
        items: list[AdView] = []
        def admit(ad_id: str, history: tuple[AdSnapshot, ...]) -> bool:
            item = self._project_history(ad_id, history)
            if item is None:
                return False
            if status != "all" and item.lifecycle_state.value != status:
                return False
            if needle and needle not in ad_id.casefold() and needle not in (item.title or "").casefold():
                return False
            items.append(item)
            return len(items) > limit

        if isinstance(self._store, StorageOwnerClient):
            # The independent-UID owner exposes only typed history/ID methods,
            # not a browser-accessible direct SQLite path.
            for ad_id in self._store.tracked_ad_ids():
                if ad_id > after and admit(ad_id, self._store.ad_history(ad_id)):
                    break
        else:
            cursor = after
            # Native SQLite loads 16 distinct ID histories in one short read
            # instead of reopening and recertifying the DB for each ID.
            # A page is fully materialized before returning, so filtering
            # never holds a live SQLite reader lock across UI work.
            while True:
                page = self._store.catalog_ad_histories_page(after=cursor, limit=16)
                if not page:
                    break
                found_more = False
                for ad_id, history in page:
                    if admit(ad_id, history):
                        found_more = True
                        break
                if found_more or len(page) < 16:
                    break
                cursor = page[-1][0]

        more = len(items) > limit
        visible = items[:limit]
        return {
            "ads": [ad_view_to_dict(item) for item in visible],
            "next_after": visible[-1].ad_id if more else None,
            "has_more": more,
        }

    def ad_history(self, ad_id: str) -> tuple[AdSnapshot, ...]:
        return self._store.ad_history(ad_id)

    def reaction_history(self, ad_id: str) -> tuple[ReactionSnapshot, ...]:
        return self._store.reaction_history(ad_id)

    def imported_inquiries_page(
        self, *, after: str = "", limit: int = 25,
        watermark: int | None = None,
    ) -> dict[str, object]:
        """Read EML counters only; do not infer ownership from an email ID."""
        if type(self._store) is not SnapshotStore:
            raise ValueError("native EML overview unavailable")
        page = self._store.inbound_inquiries_page(
            after=after, limit=limit, watermark=watermark,
        )
        items = []
        for record in page["inquiries"]:
            ad = self.catalog_ad(record["ad_id"])
            owned = ad is not None and ad["present"] is True
            items.append({**record, "own_ad": owned,
                          "title": ad["title"] if owned else None})
        return {**page, "inquiries": items}

    def email_reaction(self, ad_id: str) -> EmailReactionView | None:
        history = self._store.inbound_message_history(ad_id)
        if not history:
            return None
        # Use the same pinned event generation for counts and provenance.
        # A concurrent import after the history read must not inflate totals.
        conversations = len({event.conversation_id for event in history})
        messages = len(history)
        return EmailReactionView(
            ad_id=ad_id,
            conversation_count=conversations,
            inbound_message_count=messages,
            first_observed_at=history[0].observed_at,
            last_observed_at=history[-1].observed_at,
        )

    def email_reactions(self) -> tuple[EmailReactionView, ...]:
        rows: list[EmailReactionView] = []
        for ad_id in self._store.inbound_message_ad_ids():
            item = self.email_reaction(ad_id)
            if item is not None:
                rows.append(item)
        return tuple(rows)

    def summary(self) -> DashboardSummary:
        ads = self.latest_ads()
        current_ads = sum(1 for item in ads if item.present)
        absent_ads = sum(
            1
            for item in ads
            if item.lifecycle_state is LifecycleState.ABSENT
        )
        unknown_state_ads = sum(
            1
            for item in ads
            if item.lifecycle_state is LifecycleState.UNKNOWN
        )

        views = [item.views for item in ads if item.views is not None]
        watches = [
            item.watch_count
            for item in ads
            if item.watch_count is not None
        ]
        replies = [
            item.reply_count
            for item in ads
            if item.reply_count is not None
        ]

        return DashboardSummary(
            tracked_ads=len(ads),
            current_ads=current_ads,
            absent_ads=absent_ads,
            unknown_state_ads=unknown_state_ads,
            views_total_known=sum(views),
            views_observed_ads=len(views),
            watch_total_known=sum(watches),
            watch_observed_ads=len(watches),
            replies_total_known=sum(replies),
            replies_observed_ads=len(replies),
        )


def metric_evidence_to_dict(
    item: MetricEvidence | None,
) -> dict[str, object] | None:
    if item is None:
        return None
    return {
        "observed_at": item.observed_at.isoformat(),
        "source": item.source,
        "last_known": item.last_known,
    }


def ad_view_to_dict(item: AdView) -> dict[str, object]:
    return {
        "ad_id": item.ad_id,
        "lifecycle_state": item.lifecycle_state.value,
        "present": item.present,
        "observed_at": item.observed_at.isoformat(),
        "source": item.source,
        "title": item.title,
        "description": item.description,
        "views": item.views,
        "watch_count": item.watch_count,
        "reply_count": item.reply_count,
        "metric_evidence": {
            field_name: metric_evidence_to_dict(item.metric_evidence.get(field_name))
            for field_name in ("views", "watch_count", "reply_count")
        },
    }


def ad_snapshot_to_dict(item: AdSnapshot) -> dict[str, object]:
    return {
        "ad_id": item.ad_id,
        "lifecycle_state": item.lifecycle_state.value,
        "observed_at": item.observed_at.isoformat(),
        "source": item.source,
        "title": item.title,
        "description": item.description,
        "views": item.views,
        "watch_count": item.watch_count,
        "reply_count": item.reply_count,
    }


def reaction_snapshot_to_dict(item: ReactionSnapshot) -> dict[str, object]:
    return {
        "ad_id": item.ad_id,
        "observed_at": item.observed_at.isoformat(),
        "source": item.source,
        "conversation_count": item.conversation_count,
        "unique_buyer_count": item.unique_buyer_count,
        "inbound_message_count": item.inbound_message_count,
    }


def email_reaction_view_to_dict(item: EmailReactionView) -> dict[str, object]:
    return {
        "ad_id": item.ad_id,
        "conversation_count": item.conversation_count,
        "inbound_message_count": item.inbound_message_count,
        "first_observed_at": item.first_observed_at.isoformat(),
        "last_observed_at": item.last_observed_at.isoformat(),
    }


def summary_to_dict(item: DashboardSummary) -> dict[str, int]:
    return {
        "tracked_ads": item.tracked_ads,
        "current_ads": item.current_ads,
        "absent_ads": item.absent_ads,
        "unknown_state_ads": item.unknown_state_ads,
        "views_total_known": item.views_total_known,
        "views_observed_ads": item.views_observed_ads,
        "watch_total_known": item.watch_total_known,
        "watch_observed_ads": item.watch_observed_ads,
        "replies_total_known": item.replies_total_known,
        "replies_observed_ads": item.replies_observed_ads,
    }