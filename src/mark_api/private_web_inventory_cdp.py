"""Read only the authenticated owner's inventory in their already-open Chrome tab.

Unlike historical HTTP management adapters, this transport never exports
cookies or issues a direct host HTTP request. The CDP endpoint is numerical
loopback, navigation is to a fixed HTTPS management page, and browser fetches
are fixed same-origin GETs with redirects rejected. No write capability exists.
"""
from __future__ import annotations

import json
import math
import re
import secrets
import signal
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock, current_thread, main_thread
from typing import Any
from urllib.parse import urlencode

from .adapters.management import (
    DEFAULT_SOURCE, MAX_PAGES, ManagementReadAdapter,
    _normalize_state, _optional_counter, _optional_text,
)
from .domain import AdSnapshot
from .private_web_cdp import (
    PrivateWebCdpError, PrivateWebCdpReadCancelled,
    PrivateWebCdpReadSetupError, _CdpClient, _LoopbackCdpClient,
    _require_private_web_cdp_authority,
)
from .results import ReadResult, ReadStatus


_ORIGIN = "https://www.kleinanzeigen.de"
_MANAGER_PATH = "/m-meine-anzeigen.html"
_MANAGEMENT_JSON_PATH = "/m-meine-anzeigen-verwalten.json"
_MAX_PAGE_BYTES = 2 * 1024 * 1024
_STOP_SIGNALS = frozenset((signal.SIGTERM, signal.SIGINT))


_INVENTORY_TAB_PROBE = (
    "(() => ({readyState: document.readyState,"
    "origin: location.origin, path: location.pathname}))()"
)


def _inventory_page_expression(page: int) -> str:
    """Exact fixed-origin, GET-only script shared with CDP safety classification."""
    if type(page) is not int or not 1 <= page <= MAX_PAGES:
        raise ValueError("invalid inventory page")
    url = _MANAGEMENT_JSON_PATH + "?" + urlencode(
        {"sort": "DEFAULT", "pageNum": page},
    )
    # Text is collected by a bounded stream reader, not response.text().
    # The browser owns credentials. No Cookie header leaves the page.
    return f"""
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


def _is_fixed_inventory_read_only_evaluation(
    expression: object, await_promise: object,
) -> bool:
    """Accept only exact Mark-owned GET inventory reads, never arbitrary JS."""
    if type(expression) is not str or type(await_promise) is not bool:
        return False
    if not await_promise:
        return expression == _INVENTORY_TAB_PROBE
    page_match = re.search(r"pageNum=([1-9][0-9]{0,2})", expression)
    if page_match is None:
        return False
    page = int(page_match.group(1))
    return page <= MAX_PAGES and expression == _inventory_page_expression(page)




class PrivateWebInventoryReadCancelled(RuntimeError):
    """No new CDP inventory transport may be admitted after a stop."""


class PrivateWebInventoryReadSetupError(RuntimeError):
    """Signal-safe CDP admission could not be established."""


@contextmanager
def _cancellable_transport_entry(stop_requested: Callable[[], bool] | None):
    """Atomically gate one outbound CDP send without masking the response wait.

    The signal mask covers only the cancellation predicate and socket.send.
    CDP response waits stay interruptible; the original mask is always restored.
    Unsupported platforms/threads fail closed as setup failures, not shutdowns.
    """
    if stop_requested is None:
        yield
        return
    if not callable(stop_requested):
        raise TypeError("stop_requested must be callable")
    if current_thread() is not main_thread() or not callable(getattr(signal, "pthread_sigmask", None)):
        raise PrivateWebCdpReadSetupError("signal-safe transport unavailable")
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, _STOP_SIGNALS)
    try:
        # Check queued signals as well as an already-run Python handler.
        # No handler for these signals can run in this main-thread window
        # in the synchronous periodic CLI. This is the read admission point.
        if stop_requested() or (_STOP_SIGNALS & signal.sigpending()):
            raise PrivateWebCdpReadCancelled("inventory transport cancelled")
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


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
        preconnected_client: _CdpClient | None = None,
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
        # Avoid evaluating a custom factory's __bool__ before admission.
        self._client_factory = (
            client_factory if client_factory is not None else
            lambda: _LoopbackCdpClient(
                f"http://127.0.0.1:{cdp_port}",
                timeout_seconds=self._timeout,
            )
        )
        # A stop-aware read cannot invoke an effectful lazy client factory
        # before it installs the cancellation binder. A preconnected client
        # is an isolated test seam, not an entitlement for a real transport.
        self._preconnected_client = preconnected_client
        self._factory_is_internal = client_factory is None
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = Lock()
        self._closed = False

    @staticmethod
    def _value(client: _CdpClient, expression: str, *, await_promise: bool = False) -> object:
        # Also guard this directly callable helper and injected clients.
        _require_private_web_cdp_authority()
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
        """Admit ONLY an already-open management tab; never navigate an editor.

        Opening the inventory page through Page.navigate could replace a
        writer's unsaved editor tab even if its CDP WebSocket is separate.
        This passive probe does not set DOM/global state, create a tab or send
        a navigation command. The subsequent _page() checks its origin and
        path again before its read-only fetch.
        """
        _require_private_web_cdp_authority()
        current = self._value(client, _INVENTORY_TAB_PROBE)
        if not (
            isinstance(current, dict)
            and current.get("readyState") == "complete"
            and current.get("origin") == _ORIGIN
            and current.get("path") == _MANAGER_PATH
        ):
            raise PrivateWebCdpError("inventory_wrong_target")

    @staticmethod
    def _page(client: _CdpClient, page: int) -> ReadResult[dict[str, Any]]:
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= MAX_PAGES:
            return ReadResult.failure(ReadStatus.PARSE_ERROR, error="invalid_page_number")
        expression = _inventory_page_expression(page)
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

    def read_ads(
        self, *, stop_requested: Callable[[], bool] | None = None,
    ) -> ReadResult[tuple[AdSnapshot, ...]]:
        with self._lock:
            if self._closed:
                return ReadResult.failure(
                    ReadStatus.TRANSPORT_ERROR, error="cdp_inventory_closed",
                )
            client: _CdpClient | None = None
            try:
                if stop_requested is not None:
                    if not callable(stop_requested):
                        raise PrivateWebInventoryReadSetupError("invalid stop callback")
                    if stop_requested():
                        raise PrivateWebInventoryReadCancelled("inventory transport cancelled")
                # An injected factory can itself open external transport.
                # Admission must happen before ANY client factory call.
                _require_private_web_cdp_authority()
                if stop_requested is None:
                    client = self._client_factory()
                else:
                    if self._preconnected_client is None:
                        if not self._factory_is_internal:
                            # Callers' arbitrary factories may cause uncontrolled
                            # browser effects before cancellation is installed.
                            raise PrivateWebInventoryReadSetupError(
                                "cancellable CDP connection not composed"
                            )
                        # The native factory opens ONLY its loopback discovery
                        # and WebSocket before the first platform read. A stop
                        # arriving during connection cannot authorize the
                        # following evaluate: admission is rechecked on send.
                        with _cancellable_transport_entry(stop_requested):
                            client = self._client_factory()
                    else:
                        client = self._preconnected_client
                        self._preconnected_client = None
                    binder = getattr(client, "bind_cancellable_reads", None)
                    if not callable(binder):
                        raise PrivateWebInventoryReadSetupError(
                            "signal-safe client binding unavailable"
                        )
                    binder(
                        stop_requested=stop_requested,
                        send_guard=lambda: _cancellable_transport_entry(stop_requested),
                    )
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
            except (PrivateWebInventoryReadCancelled, PrivateWebInventoryReadSetupError):
                raise
            except PrivateWebCdpReadCancelled:
                raise PrivateWebInventoryReadCancelled("inventory transport cancelled") from None
            except PrivateWebCdpReadSetupError:
                raise PrivateWebInventoryReadSetupError("signal-safe CDP setup failed") from None
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
            pending = self._preconnected_client
            self._preconnected_client = None
            if pending is not None:
                # A never-admitted injected client may implement an effectful
                # close(). Refuse ALL such operations without true authority.
                try:
                    _require_private_web_cdp_authority()
                except PrivateWebCdpError:
                    return
                try:
                    pending.close()
                except Exception:
                    pass