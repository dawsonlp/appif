"""Centralized rate-limit and retry handling for Slack API calls.

All outbound Slack API calls route through this layer. It respects
``Retry-After`` headers and implements exponential backoff with jitter.
SDK exceptions are caught here and mapped to connector errors.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

from slack_sdk.errors import SlackApiError

from appif.domain.messaging.errors import NotAuthorized, TargetUnavailable, TransientFailure

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Slack API error codes that map to specific connector errors
_AUTH_ERRORS = frozenset(
    {"not_authed", "invalid_auth", "account_inactive", "token_revoked", "token_expired", "missing_scope"}
)
_TARGET_ERRORS = frozenset({"channel_not_found", "not_in_channel", "is_archived", "user_not_found"})
_RATE_LIMIT_ERRORS = frozenset({"ratelimited"})


def call_with_retry(
    fn: Callable[..., T],
    *args,
    connector_name: str = "slack",
    max_retries: int = 3,
    base_delay: float = 1.0,
    **kwargs,
) -> T:
    """Execute a Slack API call with retry and error mapping.

    Retries on rate-limit and transient failures with exponential
    backoff + jitter. Maps SDK exceptions to typed connector errors.
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            result = fn(*args, **kwargs)

            # slack_sdk methods return a SlackResponse with an 'error' field
            if hasattr(result, "data") and isinstance(result.data, dict):
                error_code = result.data.get("error")
                if error_code:
                    _raise_for_error_code(connector_name, error_code, str(result.data))

            return result

        except SlackApiError as exc:
            error_code = exc.response.data.get("error", "") if hasattr(exc, "response") else ""
            retry_after = _extract_retry_after(exc)

            # Auth errors — no retry
            if error_code in _AUTH_ERRORS:
                raise NotAuthorized(connector_name, reason=error_code) from exc

            # Target errors — no retry
            if error_code in _TARGET_ERRORS:
                raise TargetUnavailable(connector_name, target=error_code, reason=str(exc)) from exc

            # Rate limit — retry with Retry-After
            if error_code in _RATE_LIMIT_ERRORS or retry_after is not None:
                delay = retry_after if retry_after is not None else _backoff_delay(attempt, base_delay)
                if attempt < max_retries:
                    logger.warning(
                        "slack_rate_limited",
                        extra={"attempt": attempt + 1, "delay": delay, "error": error_code},
                    )
                    time.sleep(delay)
                    last_error = exc
                    continue
                raise TransientFailure(
                    connector_name, reason=f"rate limited after {max_retries + 1} attempts", retry_after=retry_after
                ) from exc

            # Other SDK errors — retry with backoff
            if attempt < max_retries:
                delay = _backoff_delay(attempt, base_delay)
                logger.warning(
                    "slack_api_error_retrying",
                    extra={"attempt": attempt + 1, "delay": delay, "error": str(exc)},
                )
                time.sleep(delay)
                last_error = exc
                continue

            raise TransientFailure(connector_name, reason=str(exc)) from exc

        except Exception as exc:
            # Non-SDK exceptions (network errors, etc.)
            if attempt < max_retries:
                delay = _backoff_delay(attempt, base_delay)
                logger.warning(
                    "slack_transient_error_retrying",
                    extra={"attempt": attempt + 1, "delay": delay, "error": str(exc)},
                )
                time.sleep(delay)
                last_error = exc
                continue

            raise TransientFailure(connector_name, reason=str(exc)) from exc

    # Should not reach here, but safety net
    raise TransientFailure(connector_name, reason=f"exhausted retries: {last_error}")


def _raise_for_error_code(connector_name: str, error_code: str, detail: str):
    """Map a Slack error code to a connector error."""
    if error_code in _AUTH_ERRORS:
        raise NotAuthorized(connector_name, reason=error_code)
    if error_code in _TARGET_ERRORS:
        raise TargetUnavailable(connector_name, target=error_code, reason=detail)
    if error_code in _RATE_LIMIT_ERRORS:
        raise TransientFailure(connector_name, reason=error_code)


def _extract_retry_after(exc: SlackApiError) -> float | None:
    """Extract Retry-After header from a Slack API error response."""
    if hasattr(exc, "response") and hasattr(exc.response, "headers"):
        retry_after = exc.response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
    return None


def _backoff_delay(attempt: int, base_delay: float) -> float:
    """Exponential backoff with jitter."""
    delay = base_delay * (2**attempt)
    jitter = random.uniform(0, delay * 0.5)
    return delay + jitter
