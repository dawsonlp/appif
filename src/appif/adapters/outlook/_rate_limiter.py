"""Retry / back-off layer for Microsoft Graph API calls.

Provides a ``graph_retry`` tenacity decorator and a ``map_graph_error``
function that translates ``ODataError`` into the domain error hierarchy.
"""

from __future__ import annotations

import logging
import time

from appif.domain.messaging.errors import ConnectorError, NotAuthorized, TargetUnavailable, TransientFailure

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "outlook"

# HTTP status codes that should not be retried
_AUTH_STATUSES = {401, 403}
_NOT_FOUND_STATUSES = {404}
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


def map_graph_error(exc: Exception) -> ConnectorError:
    """Map a Graph SDK exception to a typed connector error.

    Handles ``ODataError`` from msgraph-sdk and falls back to
    ``ConnectorError`` for unexpected exception types.
    """
    status = _extract_status(exc)
    message = _extract_message(exc)

    # If we can't determine a status, treat as generic error
    if status is None:
        return ConnectorError(_CONNECTOR_NAME, message)

    if status in _AUTH_STATUSES:
        return NotAuthorized(_CONNECTOR_NAME, reason=message)
    if status in _NOT_FOUND_STATUSES:
        return TargetUnavailable(_CONNECTOR_NAME, target="unknown", reason=message)
    if status in _TRANSIENT_STATUSES:
        return TransientFailure(_CONNECTOR_NAME, reason=message)

    return ConnectorError(_CONNECTOR_NAME, message)


def call_with_retry(api_callable, *, max_retries: int = 5, **kwargs):
    """Execute a Graph API call with retry on transient failures.

    Respects ``Retry-After`` headers on 429 responses. Auth errors
    (401/403) are raised immediately without retry.

    Parameters
    ----------
    api_callable:
        A callable that performs the Graph API request.
    max_retries:
        Maximum number of attempts.
    **kwargs:
        Forwarded to the callable.

    Returns
    -------
    The API response on success.

    Raises
    ------
    NotAuthorized
        If authentication fails (401/403).
    TargetUnavailable
        If the target resource is not found (404).
    TransientFailure
        After max_retries transient failures.
    ConnectorError
        For unexpected errors.
    """
    # Lazy import
    try:
        from msgraph.generated.models.o_data_errors.o_data_error import ODataError
    except ImportError:
        ODataError = None  # noqa: F841

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            return api_callable(**kwargs)
        except Exception as exc:
            status = _extract_status(exc)

            # Auth errors — never retry
            if status in _AUTH_STATUSES:
                raise map_graph_error(exc) from exc

            # Not found — never retry
            if status in _NOT_FOUND_STATUSES:
                raise map_graph_error(exc) from exc

            # Rate limit — respect Retry-After
            if status == 429:
                retry_after = _extract_retry_after(exc)
                logger.warning(
                    "outlook.rate_limited",
                    extra={"retry_after": retry_after, "attempt": attempt},
                )
                time.sleep(retry_after)
                last_error = exc
                continue

            # Other transient errors
            if status in _TRANSIENT_STATUSES:
                backoff = min(2**attempt, 60)
                logger.warning(
                    "outlook.transient_error",
                    extra={"status": status, "attempt": attempt, "backoff": backoff},
                )
                time.sleep(backoff)
                last_error = exc
                continue

            # Unknown error — no retry
            raise map_graph_error(exc) from exc

    raise TransientFailure(
        _CONNECTOR_NAME,
        reason=f"Max retries ({max_retries}) exceeded: {last_error}",
    ) from last_error


def _extract_status(exc: Exception) -> int | None:
    """Best-effort extraction of HTTP status from an ODataError."""
    # ODataError has a response_status_code attribute
    if hasattr(exc, "response_status_code"):
        return exc.response_status_code
    # Some versions use error.code with HTTP status
    if hasattr(exc, "error") and hasattr(exc.error, "code"):
        try:
            return int(exc.error.code)
        except (ValueError, TypeError):
            pass
    return None


def _extract_message(exc: Exception) -> str:
    """Best-effort extraction of error message from an ODataError."""
    if hasattr(exc, "error") and hasattr(exc.error, "message"):
        return exc.error.message or str(exc)
    return str(exc)


def _extract_retry_after(exc: Exception) -> int:
    """Extract Retry-After value from response headers, default to 1s."""
    # ODataError may carry response headers
    if hasattr(exc, "response_headers") and exc.response_headers:
        retry_val = exc.response_headers.get("Retry-After")
        if retry_val:
            try:
                return int(retry_val)
            except (ValueError, TypeError):
                pass
    return 1
