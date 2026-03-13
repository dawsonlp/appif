"""Retry / back-off layer for Gmail API calls.

Provides a ``gmail_retry`` tenacity decorator and a ``map_gmail_error``
function that translates ``HttpError`` into the domain error hierarchy.
"""

from __future__ import annotations

import logging

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)

from appif.domain.messaging.errors import ConnectorError, NotAuthorized, TargetUnavailable, TransientFailure

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "gmail"

# HTTP status codes
_AUTH_STATUSES = {401, 403}
_NOT_FOUND_STATUSES = {404}
_TRANSIENT_STATUSES = {429, 500, 503}

# Error reasons that are transient despite arriving as 400
_TRANSIENT_REASONS = {"failedPrecondition"}


def map_gmail_error(exc: Exception) -> ConnectorError:
    """Map a ``googleapiclient.errors.HttpError`` to a typed connector error.

    Also handles non-HTTP exceptions by wrapping them in ``ConnectorError``.
    """
    status = _extract_status(exc)
    message = _extract_message(exc)
    reason = _extract_reason(exc)

    if status is None:
        # Non-HTTP error (network, timeout, etc.)
        return TransientFailure(_CONNECTOR_NAME, reason=message)

    # Check specific Gmail error reasons
    if reason == "dailyLimitExceeded":
        return ConnectorError(_CONNECTOR_NAME, "daily send limit exceeded")
    if reason == "userRateLimitExceeded":
        return TransientFailure(_CONNECTOR_NAME, reason="user rate limit exceeded")
    if reason in _TRANSIENT_REASONS:
        return TransientFailure(_CONNECTOR_NAME, reason=message)

    if status in _AUTH_STATUSES:
        return NotAuthorized(_CONNECTOR_NAME, reason=message)
    if status in _NOT_FOUND_STATUSES:
        return TargetUnavailable(_CONNECTOR_NAME, target="unknown", reason=message)
    if status in _TRANSIENT_STATUSES:
        return TransientFailure(_CONNECTOR_NAME, reason=message)
    if status == 400:
        return ConnectorError(_CONNECTOR_NAME, message)

    return ConnectorError(_CONNECTOR_NAME, message)


def _is_retryable(exc: BaseException) -> bool:
    """Determine whether an exception should trigger a retry."""
    # Already-mapped domain errors should not be retried (except TransientFailure)
    if isinstance(exc, ConnectorError) and not isinstance(exc, TransientFailure):
        return False
    status = _extract_status(exc)
    if status is None:
        # Network errors are retryable
        return True
    if status in _TRANSIENT_STATUSES:
        return True
    # Some 400 errors are transient (e.g. "Precondition check failed")
    if status == 400 and _extract_reason(exc) in _TRANSIENT_REASONS:
        return True
    return False


def _on_retry_exhausted(retry_state) -> None:
    """Called when all retries are exhausted — raise TransientFailure."""
    exc = retry_state.outcome.exception()
    raise TransientFailure(
        _CONNECTOR_NAME,
        reason=f"max retries exceeded: {exc}",
    ) from exc


# Tenacity retry decorator configured for Gmail API error patterns
gmail_retry = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    stop=stop_after_attempt(5) | stop_after_delay(120),
    retry_error_callback=_on_retry_exhausted,
    reraise=False,
)


def call_with_retry(fn, *args, **kwargs):
    """Execute a Gmail API call with retry on transient failures.

    Non-retryable errors are mapped to typed connector errors immediately.

    Parameters
    ----------
    fn:
        Callable that performs the Gmail API request.
    *args, **kwargs:
        Forwarded to the callable.

    Returns
    -------
    The API response on success.

    Raises
    ------
    NotAuthorized, TargetUnavailable, TransientFailure, ConnectorError
    """

    @gmail_retry
    def _inner():
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            status = _extract_status(exc)
            reason = _extract_reason(exc)
            # Non-retryable errors — map and raise immediately
            if status is not None and status not in _TRANSIENT_STATUSES:
                # But let transient-reason 400s through for retry
                if status == 400 and reason in _TRANSIENT_REASONS:
                    raise  # let tenacity retry this
                raise map_gmail_error(exc) from exc
            raise

    return _inner()


# ── Internal helpers ──────────────────────────────────────────


def _extract_status(exc: BaseException) -> int | None:
    """Best-effort extraction of HTTP status from an HttpError."""
    # googleapiclient.errors.HttpError has a .status_code or .resp.status
    if hasattr(exc, "status_code"):
        return exc.status_code
    if hasattr(exc, "resp") and hasattr(exc.resp, "status"):
        try:
            return int(exc.resp.status)
        except (ValueError, TypeError):
            pass
    # Some errors carry .status directly
    if hasattr(exc, "status"):
        try:
            return int(exc.status)
        except (ValueError, TypeError):
            pass
    return None


def _extract_message(exc: BaseException) -> str:
    """Best-effort extraction of error message."""
    if hasattr(exc, "reason"):
        return str(exc.reason)
    return str(exc)


def _extract_reason(exc: BaseException) -> str:
    """Extract the error reason string from a Gmail HttpError.

    Gmail API errors often include an ``errors`` list with ``reason``
    fields like ``dailyLimitExceeded`` or ``userRateLimitExceeded``.
    """
    if hasattr(exc, "error_details"):
        for detail in exc.error_details or []:
            if isinstance(detail, dict) and "reason" in detail:
                return detail["reason"]
    # Try parsing the content body
    if hasattr(exc, "content"):
        try:
            import json

            body = json.loads(exc.content)
            errors = body.get("error", {}).get("errors", [])
            if errors:
                return errors[0].get("reason", "")
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
    return ""
