"""Unit tests for the Outlook rate limiter module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from appif.adapters.outlook._rate_limiter import call_with_retry, map_graph_error
from appif.domain.messaging.errors import ConnectorError, NotAuthorized, TargetUnavailable, TransientFailure


def _make_odata_error(status_code, message="test error"):
    """Create a mock ODataError-like exception."""
    exc = Exception(message)
    exc.response_status_code = status_code
    exc.error = MagicMock()
    exc.error.code = str(status_code)
    exc.error.message = message
    exc.response_headers = {}
    return exc


class TestMapGraphError:
    """Tests for map_graph_error."""

    def test_401_maps_to_not_authorized(self):
        exc = _make_odata_error(401, "Unauthorized")
        result = map_graph_error(exc)
        assert isinstance(result, NotAuthorized)

    def test_403_maps_to_not_authorized(self):
        exc = _make_odata_error(403, "Forbidden")
        result = map_graph_error(exc)
        assert isinstance(result, NotAuthorized)

    def test_404_maps_to_target_unavailable(self):
        exc = _make_odata_error(404, "Not Found")
        result = map_graph_error(exc)
        assert isinstance(result, TargetUnavailable)

    def test_429_maps_to_transient_failure(self):
        exc = _make_odata_error(429, "Too Many Requests")
        result = map_graph_error(exc)
        assert isinstance(result, TransientFailure)

    def test_500_maps_to_transient_failure(self):
        exc = _make_odata_error(500, "Internal Server Error")
        result = map_graph_error(exc)
        assert isinstance(result, TransientFailure)

    def test_unknown_status_maps_to_connector_error(self):
        exc = _make_odata_error(418, "I'm a teapot")
        result = map_graph_error(exc)
        assert isinstance(result, ConnectorError)
        assert not isinstance(result, (NotAuthorized, TargetUnavailable, TransientFailure))

    def test_non_odata_exception_maps_to_connector_error(self):
        exc = RuntimeError("something broke")
        result = map_graph_error(exc)
        assert isinstance(result, ConnectorError)


class TestCallWithRetry:
    """Tests for call_with_retry."""

    def test_success_on_first_attempt(self):
        fn = MagicMock(return_value="ok")
        result = call_with_retry(fn)
        assert result == "ok"
        fn.assert_called_once()

    def test_401_raises_not_authorized_immediately(self):
        """Auth errors are NOT retried."""
        exc = _make_odata_error(401, "Unauthorized")
        fn = MagicMock(side_effect=exc)

        with pytest.raises(NotAuthorized):
            call_with_retry(fn, max_retries=3)

        # Should only be called once (no retry)
        assert fn.call_count == 1

    def test_404_raises_target_unavailable_immediately(self):
        exc = _make_odata_error(404, "Not Found")
        fn = MagicMock(side_effect=exc)

        with pytest.raises(TargetUnavailable):
            call_with_retry(fn, max_retries=3)

        assert fn.call_count == 1

    @patch("appif.adapters.outlook._rate_limiter.time.sleep")
    def test_429_retries_with_retry_after(self, mock_sleep):
        """429 with Retry-After header → correct sleep before retry."""
        exc = _make_odata_error(429, "Too Many Requests")
        exc.response_headers = {"Retry-After": "3"}

        fn = MagicMock(side_effect=[exc, "ok"])

        result = call_with_retry(fn, max_retries=5)
        assert result == "ok"
        mock_sleep.assert_called_once_with(3)

    @patch("appif.adapters.outlook._rate_limiter.time.sleep")
    def test_five_consecutive_503_raises_transient_failure(self, mock_sleep):
        """5 × 503 → TransientFailure."""
        exc = _make_odata_error(503, "Service Unavailable")
        fn = MagicMock(side_effect=[exc] * 5)

        with pytest.raises(TransientFailure, match="Max retries"):
            call_with_retry(fn, max_retries=5)

        assert fn.call_count == 5

    @patch("appif.adapters.outlook._rate_limiter.time.sleep")
    def test_transient_then_success(self, mock_sleep):
        """Transient error followed by success → returns result."""
        exc = _make_odata_error(500, "Internal Server Error")
        fn = MagicMock(side_effect=[exc, exc, "ok"])

        result = call_with_retry(fn, max_retries=5)
        assert result == "ok"
        assert fn.call_count == 3
