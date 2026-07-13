"""Unit tests for the shared Microsoft Graph HTTP retry/back-off layer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from appif.adapters._graph.http import graph_request
from appif.domain.messaging.errors import ConnectorError, NotAuthorized, TargetUnavailable, TransientFailure


def _response(status_code, *, text="", headers=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    resp.text = text
    resp.headers = headers or {}
    return resp


def _call(mock_request, **kwargs):
    return graph_request("outlook", "GET", "https://graph/test", headers={"Authorization": "Bearer x"}, **kwargs)


class TestTerminalStatuses:
    """Auth / not-found / gone map to typed errors and are never retried."""

    @patch("appif.adapters._graph.http.httpx.request")
    def test_401_maps_to_not_authorized(self, mock_request):
        mock_request.return_value = _response(401, text="Unauthorized")
        with pytest.raises(NotAuthorized):
            _call(mock_request)
        assert mock_request.call_count == 1

    @patch("appif.adapters._graph.http.httpx.request")
    def test_403_maps_to_not_authorized(self, mock_request):
        mock_request.return_value = _response(403, text="Forbidden")
        with pytest.raises(NotAuthorized):
            _call(mock_request)
        assert mock_request.call_count == 1

    @patch("appif.adapters._graph.http.httpx.request")
    def test_404_maps_to_target_unavailable(self, mock_request):
        mock_request.return_value = _response(404, text="Not Found")
        with pytest.raises(TargetUnavailable):
            _call(mock_request)
        assert mock_request.call_count == 1

    @patch("appif.adapters._graph.http.httpx.request")
    def test_410_gone_maps_to_target_unavailable(self, mock_request):
        """410 (expired delta) is terminal and surfaces as TargetUnavailable."""
        mock_request.return_value = _response(410, text="Gone")
        with pytest.raises(TargetUnavailable):
            _call(mock_request)
        assert mock_request.call_count == 1

    @patch("appif.adapters._graph.http.httpx.request")
    def test_unknown_4xx_maps_to_connector_error(self, mock_request):
        mock_request.return_value = _response(418, text="I'm a teapot")
        with pytest.raises(ConnectorError) as exc_info:
            _call(mock_request)
        assert not isinstance(exc_info.value, (NotAuthorized, TargetUnavailable, TransientFailure))
        assert mock_request.call_count == 1


class TestRetryBehavior:
    """Transient statuses back off and retry; success short-circuits."""

    @patch("appif.adapters._graph.http.httpx.request")
    def test_success_on_first_attempt(self, mock_request):
        ok = _response(200)
        mock_request.return_value = ok
        assert _call(mock_request) is ok
        assert mock_request.call_count == 1

    @patch("appif.adapters._graph.http.time.sleep")
    @patch("appif.adapters._graph.http.httpx.request")
    def test_429_retries_respecting_retry_after(self, mock_request, mock_sleep):
        ok = _response(200)
        mock_request.side_effect = [_response(429, headers={"Retry-After": "3"}), ok]
        assert _call(mock_request, max_retries=5) is ok
        mock_sleep.assert_called_once_with(3)

    @patch("appif.adapters._graph.http.time.sleep")
    @patch("appif.adapters._graph.http.httpx.request")
    def test_transient_then_success(self, mock_request, mock_sleep):
        ok = _response(200)
        mock_request.side_effect = [_response(503), _response(500), ok]
        assert _call(mock_request, max_retries=5) is ok
        assert mock_request.call_count == 3

    @patch("appif.adapters._graph.http.time.sleep")
    @patch("appif.adapters._graph.http.httpx.request")
    def test_exhausted_retries_raise_transient_failure(self, mock_request, mock_sleep):
        mock_request.return_value = _response(503)
        with pytest.raises(TransientFailure, match="Max retries"):
            _call(mock_request, max_retries=5)
        assert mock_request.call_count == 5

    @patch("appif.adapters._graph.http.time.sleep")
    @patch("appif.adapters._graph.http.httpx.request")
    def test_network_error_is_retried(self, mock_request, mock_sleep):
        ok = _response(200)
        mock_request.side_effect = [httpx.ConnectError("boom"), ok]
        assert _call(mock_request, max_retries=5) is ok
        assert mock_request.call_count == 2
