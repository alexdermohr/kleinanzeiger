"""Read only the authenticated owner's inventory in their already-open Chrome tab.

Unlike historical HTTP management adapters, this transport never exports
cookies or issues a direct host HTTP request. The CDP endpoint is numerical
loopback, navigation is to a fixed HTTPS management page, and browser fetches
are fixed same-origin GETs with redirects rejected. No write capability exists.
"""
from __future__ import annotations

import json
import math
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from urllib.parse import urlencode

from .adapters.management import (
    DEFAULT_SOURCE, MAX_PAGES, ManagementReadAdapter,
    _normalize_state, _optional_counter, _optional_text,
)
from .domain import AdSnapshot
from .private_web_cdp import (
    PrivateWebCdpError, _CdpClient, _LoopbackCdpClient,
)
from .results import ReadResult, ReadStatus


_ORIGIN = "https://www.kleinanzeigen.de"
_MANAGER_PATH = "/m-meine-anzeigen.html"
_MANAGEMENT_JSON_PATH = "/m-meine-anzeigen-verwalten.json"
_MAX_PAGE_BYTES = 2 * 1024 * 1024


class CdpManagementInventoryReader:
    """A fixed-origin, read-only owner inventory over the attached CDP page."""

    def __init__(
        self,
        *,
        cdp_port: int,
        timeout_seconds: float = 5.0,
        source: str = DEFAULT_SOURCE,
        clock: Callable[[], datetime] | None = None,
        client_factory: Callable[[], _CdpClient] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if isinstance(cdp_port, bool) or not isinstance(cdp_port, int) or not 1 <= cdp_port <= 65535:
            raise ValueError("cdp_port must be an exact loopback port")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or not 0 < float(timeout_seconds) <= 30
        ):
            raise ValueError("timeout_seconds must be finite and in (0, 30]")
        if not isinstance(source, str) or not source:
            raise ValueError("source must be nonempty")
        self._source = source
        self._timeout = float(timeout_seconds)
        self._client_factory = client_factory or (
            lambda: _LoopbackCdpClient(
                f"http://127.0.0.1:{cdp_port}",
                timeout_seconds=self._timeout,
            )
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = Lock()
        self._closed = False

    @staticmethod
    def _value(client: _CdpClient, expression: str, *, await_promise: bool = False) -> object:
        result = client.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
        )
        if "exceptionDetails" in result:
            raise PrivateWebCdpError("inventory_evaluate")
        remote = result.get("result")
        if not isinstance(remote, dict) or "value" not in remote:
            raise PrivateWebCdpError("inventory_evaluate")
        return remote["value"]

    def _open_management(self, client: _CdpClient) -> None:
        marker = json.dumps(f"__mark_inventory_probe_{secrets.token_hex(8)}__")
        armed = self._value(
            client,
            "(() => { const key = " + marker
            + "; globalThis[key] = true; return globalThis[key] === true; })()",
        )
        if armed is not True:
            raise PrivateWebCdpError("inventory_navigate")
        result = client.call(
            "Page.navigate", {"url": _ORIGIN + _MANAGER_PATH},
        )
        if result.get("errorText"):
            raise PrivateWebCdpError("inventory_navigate")
        expression = (
            "(() => ({readyState: document.readyState,"
            "origin: location.origin, path: location.pathname,"
            "oldDocument: globalThis[" + marker + "] === true}))()"
        )
        deadline = self._monotonic() + self._timeout
        while True:
            try:
                ready = self._value(client, expression)
            except PrivateWebCdpError:
                ready = None
            if (
                isinstance(ready, dict)
                and ready.get("readyState") == "complete"
                and ready.get("origin") == _ORIGIN
                and ready.get("path") == _MANAGER_PATH
                and ready.get("oldDocument") is False
            ):
                return
            if self._monotonic() >= deadline:
                raise PrivateWebCdpError("inventory_navigate")
            self._sleep(0.05)

    @staticmethod
    def _page(client: _CdpClient, page: int) -> ReadResult[dict[str, Any]]:
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= MAX_PAGES:
            return ReadResult.failure(ReadStatus.PARSE_ERROR, error="invalid_page_number")
        url = _MANAGEMENT_JSON_PATH + "?" + urlencode(
            {"sort": "DEFAULT", "pageNum": page},
        )
        # Text is collected by a bounded stream reader, not response.text().
        # The browser owns credentials. No Cookie header leaves the page.
        expression = f"""
(async () => {{
  if (location.origin !== {json.dumps(_ORIGIN)} ||
      location.pathname !== {json.dumps(_MANAGER_PATH)}) {{
    return {{error: "wrong_origin_or_page"}};
  }}
  const response = await fetch({json.dumps(url)}, {{
    method: "GET", credentials: "same-origin", mode: "same-origin",
    redirect: "error", cache: "no-store",
    headers: {{"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}}
  }});
  if (!response.body || !response.body.getReader) {{
    return {{error: "missing_body"}};
  }}
  const reader = response.body.getReader();
  let total = 0;
  const chunks = [];
  while (true) {{
    const item = await reader.read();
    if (item.done) break;
    total += item.value.byteLength;
    if (total > {_MAX_PAGE_BYTES}) {{
      void reader.cancel();
      return {{error: "oversized_body"}};
    }}
    chunks.push(item.value);
  }}
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {{
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }}
  return {{status: response.status, body: new TextDecoder("utf-8", {{fatal: true}}).decode(bytes)}};
}})()
"""
        payload = CdpManagementInventoryReader._value(
            client, expression, await_promise=True,
        )
        if not isinstance(payload, dict) or payload.get("error") is not None:
            return ReadResult.failure(
                ReadStatus.TRANSPORT_ERROR, error="cdp_inventory_failed",
            )
        status = payload.get("status")
        body = payload.get("body")
        if (
            isinstance(status, bool) or not isinstance(status, int)
            or not 100 <= status <= 599 or not isinstance(body, str)
        ):
            return ReadResult.failure(ReadStatus.PARSE_ERROR, error="invalid_cdp_response")
        try:
            encoded = body.encode("utf-8")
        except UnicodeEncodeError:
            return ReadResult.failure(ReadStatus.PARSE_ERROR, error="invalid_body_encoding")
        if len(encoded) > _MAX_PAGE_BYTES:
            return ReadResult.failure(ReadStatus.PARSE_ERROR, error="oversized_body")
        if status in (401, 403):
            return ReadResult.failure(ReadStatus.UNAUTHENTICATED, http_status=status)
        if status < 200 or status >= 300:
            return ReadResult.failure(ReadStatus.HTTP_ERROR, http_status=status)
        try:
            value = json.loads(body)
        except json.JSONDecodeError:
            return ReadResult.failure(ReadStatus.PARSE_ERROR, error="invalid_json")
        if not isinstance(value, dict):
            return ReadResult.failure(ReadStatus.PARSE_ERROR, error="root_not_object")
        return ReadResult.success_nonempty(value)

    def read_ads(self) -> ReadResult[tuple[AdSnapshot, ...]]:
        with self._lock:
            if self._closed:
                return ReadResult.failure(
                    ReadStatus.TRANSPORT_ERROR, error="cdp_inventory_closed",
                )
            client: _CdpClient | None = None
            try:
                client = self._client_factory()
                self._open_management(client)
                first = self._page(client, 1)
                if not first.is_success:
                    return ReadResult.failure(
                        first.status, error=first.error, http_status=first.http_status,
                    )
                try:
                    ads, last_page = ManagementReadAdapter._page_shape(first.value or {})
                except ValueError:
                    return ReadResult.failure(ReadStatus.PARSE_ERROR, error="invalid_page_shape")
                entries = list(ads)
                for index in range(2, last_page + 1):
                    result = self._page(client, index)
                    if not result.is_success:
                        return ReadResult.failure(
                            result.status, error=result.error, http_status=result.http_status,
                        )
                    try:
                        items, final_page = ManagementReadAdapter._page_shape(result.value or {})
                    except ValueError:
                        return ReadResult.failure(ReadStatus.PARSE_ERROR, error="invalid_page_shape")
                    if final_page != last_page:
                        return ReadResult.failure(ReadStatus.PARSE_ERROR, error="pagination_drift")
                    entries.extend(items)
                observed_at = self._clock()
                snapshots = []
                for item in entries:
                    raw_id = item.get("id")
                    if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)) or not str(raw_id).strip():
                        return ReadResult.failure(ReadStatus.PARSE_ERROR, error="invalid_ad_shape")
                    try:
                        snapshots.append(AdSnapshot(
                            ad_id=str(raw_id).strip(),
                            observed_at=observed_at,
                            source=self._source,
                            lifecycle_state=_normalize_state(item.get("state")),
                            title=_optional_text(item, "title"),
                            views=_optional_counter(item, "viewCount"),
                            watch_count=_optional_counter(item, "watchCount"),
                            reply_count=_optional_counter(item, "replies"),
                        ))
                    except (ValueError, TypeError):
                        return ReadResult.failure(ReadStatus.PARSE_ERROR, error="invalid_ad_shape")
                if len({snapshot.ad_id for snapshot in snapshots}) != len(snapshots):
                    return ReadResult.failure(ReadStatus.PARSE_ERROR, error="duplicate_owner_ids")
                if not snapshots:
                    return ReadResult.success_empty(())
                return ReadResult.success_nonempty(tuple(snapshots))
            except Exception:
                return ReadResult.failure(
                    ReadStatus.TRANSPORT_ERROR, error="cdp_inventory_failed",
                )
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        # A close failure must not leak provider detail.
                        pass

    def close(self) -> None:
        with self._lock:
            self._closed = True
