"""Unit tests for JiraAdapter.attach_file().

Tests validation rules, successful upload with response normalization,
and error translation. No I/O -- the Jira client session is mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from requests import HTTPError, Response

from appif.adapters.jira.adapter import JiraAdapter
from appif.domain.work_tracking.errors import (
    ItemNotFound,
    PermissionDenied,
    WorkTrackingError,
)
from appif.domain.work_tracking.models import ItemAttachment

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

JIRA_UPLOAD_RESPONSE = [
    {
        "id": "10042",
        "filename": "requirements.md",
        "mimeType": "text/markdown",
        "size": 47,
        "created": "2026-03-29T10:30:00.000+0000",
        "author": {"accountId": "abc123", "displayName": "Test User"},
        "content": "https://jira.example.com/rest/api/2/attachment/content/10042",
    }
]


@pytest.fixture()
def adapter():
    """Create a JiraAdapter with a mocked Jira client."""
    with patch("appif.adapters.jira.adapter.create_jira_client") as mock_create:
        mock_client = MagicMock()
        mock_create.return_value = mock_client
        adp = JiraAdapter(
            server_url="https://jira.example.com",
            credentials={"username": "user", "api_token": "token"},
            instance_name="test",
        )
        return adp


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestAttachFileValidation:
    """Input validation before any platform call."""

    def test_attach_file_empty_filename_raises(self, adapter):
        with pytest.raises(WorkTrackingError, match="filename must not be empty"):
            adapter.attach_file("PROJ-1", "", b"some content")

    def test_attach_file_whitespace_filename_raises(self, adapter):
        with pytest.raises(WorkTrackingError, match="filename must not be empty"):
            adapter.attach_file("PROJ-1", "   ", b"some content")

    def test_attach_file_empty_content_raises(self, adapter):
        with pytest.raises(WorkTrackingError, match="requirements.md"):
            adapter.attach_file("PROJ-1", "requirements.md", b"")


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


class TestAttachFileSuccess:
    """Successful upload returns ItemAttachment with correct fields."""

    def test_attach_file_success_returns_item_attachment(self, adapter):
        mock_response = MagicMock()
        mock_response.json.return_value = JIRA_UPLOAD_RESPONSE
        mock_response.raise_for_status = MagicMock()
        adapter._client._session.post.return_value = mock_response

        result = adapter.attach_file("PROJ-1", "requirements.md", b"# Requirements\n\nContent here.")

        assert isinstance(result, ItemAttachment)
        assert result.id == "10042"
        assert result.filename == "requirements.md"
        assert result.mime_type == "text/markdown"
        assert result.size_bytes == 47
        assert result.author is not None
        assert result.author.display_name == "Test User"


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


class TestAttachFileErrorTranslation:
    """Platform HTTP errors map to domain exceptions."""

    def _make_http_error(self, status_code: int) -> HTTPError:
        response = Response()
        response.status_code = status_code
        error = HTTPError(response=response)
        return error

    def test_attach_file_404_raises_item_not_found(self, adapter):
        adapter._client._session.post.side_effect = self._make_http_error(404)

        with pytest.raises(ItemNotFound):
            adapter.attach_file("PROJ-999", "file.txt", b"content")

    def test_attach_file_403_raises_permission_denied(self, adapter):
        adapter._client._session.post.side_effect = self._make_http_error(403)

        with pytest.raises(PermissionDenied):
            adapter.attach_file("PROJ-1", "file.txt", b"content")
