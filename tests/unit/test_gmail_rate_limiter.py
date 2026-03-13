"""Unit tests for the Gmail rate limiter module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from appif.domain.messaging.errors import ConnectorError, NotAuthorized, TargetUnavailable, TransientFailure


def _make_http_error(status: int, reason: str = "error", content: bytes | None = None, error_reason: str = ""):
    """Create a mock HttpError with the given status and optional error reason."""
    mock = MagicMock()
    mock.resp = MagicMock()
    mock.resp.status = status
    mock.reason = reason
    mock.status_code = status
    if error_reason:
        content = json.dumps({"error": {"errors": [{"reason": error_reason}]}}).encode()
    mock.content = content or b"{}"
    mock.error_details = None
    return mock


class TestMapGmailError:
    """Tests for map_gmail_error status → connector error mapping."""

    def test_401_maps_to_not_authorized(self):
        from appif.adapters.gmail._rate_limiter import map_gmail_error

        exc = _make_http_error(401, "Unauthorized")
        result = map_gmail_error(exc)
        assert isinstance(result, NotAuthorized)
        assert result.connector == "gmail"

    def test_403_maps_to_not_authorized(self):
        from appif.adapters.gmail._rate_limiter import map_gmail_error

        exc = _make_http_error(403, "Forbidden")
        result = map_gmail_error(exc)
        assert isinstance(result, NotAuthorized)

    def test_404_maps_to_target_unavailable(self):
        from appif.adapters.gmail._rate_limiter import map_gmail_error

        exc = _make_http_error(404, "Not Found")
        result = map_gmail_error(exc)
        assert isinstance(result, TargetUnavailable)

    def test_429_maps_to_transient_failure(self):
        from appif.adapters.gmail._rate_limiter import map_gmail_error

        exc = _make_http_error(429, "Rate Limit Exceeded")
        result = map_gmail_error(exc)
        assert isinstance(result, TransientFailure)

    def test_500_maps_to_transient_failure(self):
        from appif.adapters.gmail._rate_limiter import map_gmail_error

        exc = _make_http_error(500, "Internal Server Error")
        result = map_gmail_error(exc)
        assert isinstance(result, TransientFailure)

    def test_503_maps_to_transient_failure(self):
        from appif.adapters.gmail._rate_limiter import map_gmail_error

        exc = _make_http_error(503, "Service Unavailable")
        result = map_gmail_error(exc)
        assert isinstance(result, TransientFailure)

    def test_400_maps_to_connector_error(self):
        from appif.adapters.gmail._rate_limiter import map_gmail_error

        exc = _make_http_error(400, "Bad Request")
        result = map_gmail_error(exc)
        assert isinstance(result, ConnectorError)
        assert not isinstance(result, (NotAuthorized, TargetUnavailable, TransientFailure))

    def test_400_failed_precondition_maps_to_transient_failure(self):
        from appif.adapters.gmail._rate_limiter import map_gmail_error

        exc = _make_http_error(400, "Precondition check failed.", error_reason="failedPrecondition")
        result = map_gmail_error(exc)
        assert isinstance(result, TransientFailure)
        assert result.connector == "gmail"

    def test_unknown_status_maps_to_connector_error(self):
        from appif.adapters.gmail._rate_limiter import map_gmail_error

        exc = _make_http_error(418, "I'm a teapot")
        result = map_gmail_error(exc)
        assert isinstance(result, ConnectorError)

    def test_non_http_error_maps_to_transient_failure(self):
        from appif.adapters.gmail._rate_limiter import map_gmail_error

        exc = ConnectionError("network down")
        result = map_gmail_error(exc)
        assert isinstance(result, TransientFailure)

    def test_daily_limit_exceeded_maps_to_connector_error(self):
        from appif.adapters.gmail._rate_limiter import map_gmail_error

        content = json.dumps({"error": {"errors": [{"reason": "dailyLimitExceeded"}]}}).encode()
        exc = _make_http_error(403, "Forbidden", content=content)
        exc.error_details = None
        result = map_gmail_error(exc)
        assert isinstance(result, ConnectorError)
        assert "daily send limit" in str(result)

    def test_user_rate_limit_exceeded_maps_to_transient(self):
        from appif.adapters.gmail._rate_limiter import map_gmail_error

        content = json.dumps({"error": {"errors": [{"reason": "userRateLimitExceeded"}]}}).encode()
        exc = _make_http_error(429, "Rate Limited", content=content)
        exc.error_details = None
        result = map_gmail_error(exc)
        assert isinstance(result, TransientFailure)


class TestIsRetryable:
    """Tests for the _is_retryable function."""

    def test_429_is_retryable(self):
        from appif.adapters.gmail._rate_limiter import _is_retryable

        assert _is_retryable(_make_http_error(429)) is True

    def test_500_is_retryable(self):
        from appif.adapters.gmail._rate_limiter import _is_retryable

        assert _is_retryable(_make_http_error(500)) is True

    def test_503_is_retryable(self):
        from appif.adapters.gmail._rate_limiter import _is_retryable

        assert _is_retryable(_make_http_error(503)) is True

    def test_401_is_not_retryable(self):
        from appif.adapters.gmail._rate_limiter import _is_retryable

        assert _is_retryable(_make_http_error(401)) is False

    def test_403_is_not_retryable(self):
        from appif.adapters.gmail._rate_limiter import _is_retryable

        assert _is_retryable(_make_http_error(403)) is False

    def test_404_is_not_retryable(self):
        from appif.adapters.gmail._rate_limiter import _is_retryable

        assert _is_retryable(_make_http_error(404)) is False

    def test_400_plain_is_not_retryable(self):
        from appif.adapters.gmail._rate_limiter import _is_retryable

        assert _is_retryable(_make_http_error(400)) is False

    def test_400_failed_precondition_is_retryable(self):
        from appif.adapters.gmail._rate_limiter import _is_retryable

        exc = _make_http_error(400, "Precondition check failed.", error_reason="failedPrecondition")
        assert _is_retryable(exc) is True

    def test_network_error_is_retryable(self):
        from appif.adapters.gmail._rate_limiter import _is_retryable

        assert _is_retryable(ConnectionError("network")) is True


class TestCallWithRetry:
    """Tests for the call_with_retry function."""

    def test_success_returns_result(self):
        from appif.adapters.gmail._rate_limiter import call_with_retry

        result = call_with_retry(lambda: {"ok": True})
        assert result == {"ok": True}

    def test_non_retryable_error_raises_immediately(self):
        from unittest.mock import MagicMock

        from googleapiclient.errors import HttpError

        from appif.adapters.gmail._rate_limiter import call_with_retry

        resp = MagicMock()
        resp.status = 401
        resp.reason = "Unauthorized"
        http_err = HttpError(resp, b'{"error": {"message": "Unauthorized"}}')

        def fail():
            raise http_err

        with pytest.raises(NotAuthorized):
            call_with_retry(fail)

    def test_transient_error_retries_then_raises(self):
        from appif.adapters.gmail._rate_limiter import call_with_retry

        call_count = 0

        def always_fail():
            nonlocal call_count
            call_count += 1
            exc = _make_http_error(503, "Unavailable")
            raise exc

        with pytest.raises(TransientFailure, match="max retries"):
            call_with_retry(always_fail)

        assert call_count > 1  # Retried at least once

    def test_failed_precondition_retries_then_raises(self):
        from appif.adapters.gmail._rate_limiter import call_with_retry

        call_count = 0

        def always_fail_precondition():
            nonlocal call_count
            call_count += 1
            exc = _make_http_error(400, "Precondition check failed.", error_reason="failedPrecondition")
            raise exc

        with pytest.raises(TransientFailure, match="max retries"):
            call_with_retry(always_fail_precondition)

        assert call_count > 1  # Retried at least once

    def test_failed_precondition_succeeds_on_retry(self):
        from appif.adapters.gmail._rate_limiter import call_with_retry

        call_count = 0

        def fail_once_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                exc = _make_http_error(400, "Precondition check failed.", error_reason="failedPrecondition")
                raise exc
            return {"ok": True}

        result = call_with_retry(fail_once_then_succeed)
        assert result == {"ok": True}
        assert call_count == 2
