"""HTTP retry / back-off layer for Microsoft Graph calls (httpx-based).

The Teams adapter talks to Graph over ``httpx`` directly (like the Outlook
connector). This module wraps GET/POST with retry on transient failures
(429 / 5xx, respecting ``Retry-After``) and maps terminal HTTP errors onto
the domain error hierarchy. Auth (401/403) and not-found (404) are raised
immediately without retry.
"""

from __future__ import annotations

import logging
import time

import httpx

from appif.domain.messaging.errors import ConnectorError, NotAuthorized, TargetUnavailable, TransientFailure

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "teams"

_AUTH_STATUSES = {401, 403}
_NOT_FOUND_STATUSES = {404}
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


def graph_request(
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
            # Network-level error — treat as transient and back off
            backoff = min(2**attempt, 60)
            logger.warning("teams.http_error", extra={"attempt": attempt, "error": str(exc), "backoff": backoff})
            time.sleep(backoff)
            last_status = None
            continue

        status = response.status_code
        if response.is_success:
            return response

        if status in _AUTH_STATUSES:
            raise NotAuthorized(_CONNECTOR_NAME, reason=_body_snippet(response))
        if status in _NOT_FOUND_STATUSES:
            raise TargetUnavailable(_CONNECTOR_NAME, target=url, reason=_body_snippet(response))

        if status == 429:
            retry_after = _retry_after(response)
            logger.warning("teams.rate_limited", extra={"retry_after": retry_after, "attempt": attempt})
            time.sleep(retry_after)
            last_status = status
            continue
        if status in _TRANSIENT_STATUSES:
            backoff = min(2**attempt, 60)
            logger.warning("teams.transient_error", extra={"status": status, "attempt": attempt, "backoff": backoff})
            time.sleep(backoff)
            last_status = status
            continue

        # Other 4xx — terminal
        raise ConnectorError(_CONNECTOR_NAME, f"HTTP {status}: {_body_snippet(response)}")

    raise TransientFailure(_CONNECTOR_NAME, reason=f"Max retries ({max_retries}) exceeded (last status {last_status})")


def graph_get(url: str, **kwargs) -> httpx.Response:
    return graph_request("GET", url, **kwargs)


def graph_post(url: str, **kwargs) -> httpx.Response:
    return graph_request("POST", url, **kwargs)


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
