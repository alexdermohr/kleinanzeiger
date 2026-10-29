"""Bounded G4/G5 request parsing; authentication stays in the dashboard owner."""
from __future__ import annotations

import json
from urllib.parse import parse_qs

from .portfolio_autopilot import attention_page
from .portfolio_inventory_diff import product_inventory_diff_page
from .portfolio_price_rules import (
    delete_price_rule, price_rule_detail, price_suggestions_page, set_price_rule,
)
from .storage import SnapshotStore


READ_PATHS = frozenset((
    "/api/portfolio/inventory-diff", "/api/portfolio/price-rules",
    "/api/portfolio/price-rules/detail", "/api/portfolio/autopilot-attention",
))
PRICE_MUTATE_PATH = "/api/portfolio/price-rules/mutate"


class InsightRequestError(ValueError):
    """Malformed or unsupported local request, not a platform operation."""


def _options(query: str, allowed: set[str]) -> dict[str, str]:
    if len(query) > 1024:
        raise InsightRequestError("query too large")
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True,
                          max_num_fields=5, encoding="utf-8", errors="strict")
    except (ValueError, UnicodeError):
        raise InsightRequestError("invalid query") from None
    if set(values) - allowed or any(len(value) != 1 for value in values.values()):
        raise InsightRequestError("invalid or duplicate query field")
    return {key: value[0] for key, value in values.items()}


def read_insight(store: SnapshotStore, path: str, query: str) -> dict:
    if path not in READ_PATHS:
        raise InsightRequestError("unsupported insight")
    if path == "/api/portfolio/autopilot-attention":
        options = _options(query, {"offset", "limit"})
        raw_offset = options.get("offset", "0")
        raw_limit = options.get("limit", "50")
        if (not raw_offset.isascii() or not raw_offset.isdecimal()
                or len(raw_offset) > 6 or not raw_limit.isascii()
                or not raw_limit.isdecimal() or len(raw_limit) > 3):
            raise InsightRequestError("invalid attention page")
        offset, limit = int(raw_offset), int(raw_limit)
        if not 0 <= offset <= 100_000 or not 1 <= limit <= 50:
            raise InsightRequestError("invalid attention bounds")
        return attention_page(store, offset=offset, limit=limit)
    if path.endswith("/detail"):
        options = _options(query, {"sku"})
        if set(options) != {"sku"}:
            raise InsightRequestError("missing sku")
        return price_rule_detail(store, sku=options["sku"])
    options = _options(query, {"after", "limit", "epoch", "state"} if path.endswith("inventory-diff")
                       else {"after", "limit"})
    raw_limit = options.get("limit", "50")
    if not raw_limit.isascii() or not raw_limit.isdecimal() or len(raw_limit) > 3:
        raise InsightRequestError("invalid limit")
    limit = int(raw_limit)
    if not 1 <= limit <= 50:
        raise InsightRequestError("invalid limit")
    if path.endswith("inventory-diff"):
        after = options.get("after", "")
        epoch = options.get("epoch")
        # A later page must be bound to the original snapshot generation.
        if after and not epoch:
            raise InsightRequestError("pagination requires epoch")
        state = options.get("state", "all")
        if state not in ("all", "newly_observed", "changed", "paused",
                         "unchanged", "no_baseline", "unclear", "unverified"):
            raise InsightRequestError("invalid state filter")
        return product_inventory_diff_page(store, after=after, limit=limit,
                                           expected_epoch=epoch, state_filter=state)
    return price_suggestions_page(store, after=options.get("after", ""), limit=limit)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InsightRequestError("duplicate JSON field")
        result[key] = value
    return result


def mutate_price_rule(store: SnapshotStore, body: bytes) -> dict:
    if not body or len(body) > 8192:
        raise InsightRequestError("invalid request size")
    try:
        data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError):
        raise InsightRequestError("invalid JSON") from None
    if type(data) is not dict:
        raise InsightRequestError("object required")
    action = data.get("action")
    base = {"action", "sku", "expected_revision", "request_key"}
    if action == "set":
        if set(data) != base | {"minimum_price_eur", "reduction_step_eur", "wait_days"}:
            raise InsightRequestError("invalid price rule fields")
        return set_price_rule(store, **{key: value for key, value in data.items() if key != "action"})
    if action == "delete":
        if set(data) != base:
            raise InsightRequestError("invalid deletion fields")
        return delete_price_rule(store, **{key: value for key, value in data.items() if key != "action"})
    raise InsightRequestError("unsupported action")