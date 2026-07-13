"""Shared HTTP retry / back-off layer for Microsoft Graph calls (httpx-based).

Both the Outlook and Teams connectors talk to Graph over ``httpx`` directly.
This module wraps GET/POST with retry on transient failures (429 / 5xx,
respecting ``Retry-After``) and maps terminal HTTP errors onto the domain
error hierarchy. Auth (401/403) and not-found / gone (404/410) are raised
immediately without retry.

The functions take the ``connector`` name as their first argument so the raised
errors and log events are attributed to the calling adapter. Each adapter binds
that name once in its own thin ``_rate_limiter`` module.
"""

from __future__ import annotations

import logging
import time

import httpx

from appif.domain.messaging.errors import ConnectorError, NotAuthorized, TargetUnavailable, TransientFailure

logger = logging.getLogger(__name__)

_AUTH_STATUSES = {401, 403}
# 404 Not Found and 410 Gone (e.g. an expired delta token) are both terminal
# for the requested URL — the caller re-seeds rather than retrying.
_NOT_FOUND_STATUSES = {404, 410}
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


def graph_request(
    connector: str,
    method: str,
    url: str,
    *,
    headers: dict,
    params: dict | None = None,
    json: dict | None = None,
    timeout: float = 30.0,
    max_retries: int = 5,
) -> httpx.Response:
    """Perform a Graph HTTP request with retry on transient failures.

    Returns the successful ``httpx.Response``. Raises a typed
    :class:`~appif.domain.messaging.errors.ConnectorError` subclass on
    terminal failures.
    """
    last_status: int | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = httpx.request(method, url, headers=headers, params=params, json=json, timeout=timeout)
        except httpx.HTTPError as exc:
            # Network-level error — treat as transient and back off.
            backoff = min(2**attempt, 60)
            logger.warning(f"{connector}.http_error", extra={"attempt": attempt, "error": str(exc), "backoff": backoff})
            time.sleep(backoff)
            last_status = None
            continue

        status = response.status_code
        if response.is_success:
            return response

        if status in _AUTH_STATUSES:
            raise NotAuthorized(connector, reason=_body_snippet(response))
        if status in _NOT_FOUND_STATUSES:
            raise TargetUnavailable(connector, target=url, reason=_body_snippet(response))

        if status == 429:
            retry_after = _retry_after(response)
            logger.warning(f"{connector}.rate_limited", extra={"retry_after": retry_after, "attempt": attempt})
            time.sleep(retry_after)
            last_status = status
            continue
        if status in _TRANSIENT_STATUSES:
            backoff = min(2**attempt, 60)
            logger.warning(f"{connector}.transient_error", extra={"status": status, "attempt": attempt})
            time.sleep(backoff)
            last_status = status
            continue

        # Other 4xx — terminal.
        raise ConnectorError(connector, f"HTTP {status}: {_body_snippet(response)}")

    raise TransientFailure(connector, reason=f"Max retries ({max_retries}) exceeded (last status {last_status})")


def graph_get(connector: str, url: str, **kwargs) -> httpx.Response:
    return graph_request(connector, "GET", url, **kwargs)


def graph_post(connector: str, url: str, **kwargs) -> httpx.Response:
    return graph_request(connector, "POST", url, **kwargs)


def _retry_after(response: httpx.Response) -> int:
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return int(raw)
        except (ValueError, TypeError):
            pass
    return 1


def _body_snippet(response: httpx.Response, limit: int = 200) -> str:
    try:
        return response.text[:limit]
    except Exception:
        return f"HTTP {response.status_code}"
