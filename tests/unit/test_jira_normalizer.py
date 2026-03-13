"""Unit tests for Jira normalizer functions."""

from appif.adapters.jira._normalizer import (
    normalize_attachment,
    normalize_issue,
    normalize_issue_type,
    normalize_link_type_info,
    normalize_project,
)
from appif.domain.work_tracking.models import IssueTypeInfo, ItemAttachment, LinkTypeInfo

# ---------------------------------------------------------------------------
# normalize_issue_type
# ---------------------------------------------------------------------------


class TestNormalizeIssueType:
    def test_standard_type(self):
        raw = {"name": "Task", "subtask": False, "description": "A regular task"}
        result = normalize_issue_type(raw)
        assert result == IssueTypeInfo(name="Task", subtask=False, description="A regular task")

    def test_subtask_type(self):
        raw = {"name": "Sub-task", "subtask": True, "description": "A subtask"}
        result = normalize_issue_type(raw)
        assert result == IssueTypeInfo(name="Sub-task", subtask=True, description="A subtask")

    def test_missing_fields_use_defaults(self):
        raw = {}
        result = normalize_issue_type(raw)
        assert result == IssueTypeInfo(name="", subtask=False, description="")

    def test_extra_fields_ignored(self):
        raw = {"name": "Epic", "subtask": False, "description": "", "id": "10000", "iconUrl": "http://..."}
        result = normalize_issue_type(raw)
        assert result.name == "Epic"
        assert result.subtask is False

    def test_subtask_truthy_values(self):
        """Jira may return subtask as various truthy values."""
        for val in (True, 1, "yes"):
            raw = {"name": "Sub-task", "subtask": val}
            result = normalize_issue_type(raw)
            assert result.subtask is True


# ---------------------------------------------------------------------------
# normalize_link_type_info
# ---------------------------------------------------------------------------


class TestNormalizeLinkTypeInfo:
    def test_blocks_link_type(self):
        raw = {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"}
        result = normalize_link_type_info(raw)
        assert result == LinkTypeInfo(name="Blocks", inward="is blocked by", outward="blocks")

    def test_relates_link_type(self):
        raw = {"name": "Relates", "inward": "relates to", "outward": "relates to"}
        result = normalize_link_type_info(raw)
        assert result == LinkTypeInfo(name="Relates", inward="relates to", outward="relates to")

    def test_duplicate_link_type(self):
        raw = {"name": "Duplicate", "inward": "is duplicated by", "outward": "duplicates"}
        result = normalize_link_type_info(raw)
        assert result.name == "Duplicate"
        assert result.inward == "is duplicated by"
        assert result.outward == "duplicates"

    def test_missing_fields_use_defaults(self):
        raw = {}
        result = normalize_link_type_info(raw)
        assert result == LinkTypeInfo(name="", inward="", outward="")

    def test_extra_fields_ignored(self):
        raw = {"name": "Cloners", "inward": "is cloned by", "outward": "clones", "id": "10001", "self": "http://..."}
        result = normalize_link_type_info(raw)
        assert result.name == "Cloners"
        assert result.inward == "is cloned by"
        assert result.outward == "clones"


# ---------------------------------------------------------------------------
# normalize_attachment
# ---------------------------------------------------------------------------


class TestNormalizeAttachment:
    def test_full_attachment(self):
        raw = {
            "id": "10042",
            "filename": "requirements.docx",
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size": 46080,
            "created": "2026-03-01T14:00:00.000+0000",
            "author": {"accountId": "abc123", "displayName": "Jane Doe"},
            "content": "https://jira.example.com/rest/api/3/attachment/content/10042",
        }
        result = normalize_attachment(raw)
        assert result.id == "10042"
        assert result.filename == "requirements.docx"
        assert result.mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert result.size_bytes == 46080
        assert result.created.year == 2026
        assert result.author is not None
        assert result.author.display_name == "Jane Doe"

    def test_platform_url_not_exposed(self):
        """The content URL must NOT appear on the domain object."""
        raw = {
            "id": "10042",
            "filename": "test.pdf",
            "mimeType": "application/pdf",
            "size": 1024,
            "content": "https://jira.example.com/rest/api/3/attachment/content/10042",
        }
        result = normalize_attachment(raw)
        assert not hasattr(result, "content")
        assert not hasattr(result, "url")
        assert not hasattr(result, "download_url")

    def test_missing_fields_use_defaults(self):
        raw = {}
        result = normalize_attachment(raw)
        assert result.id == ""
        assert result.filename == ""
        assert result.mime_type == "application/octet-stream"
        assert result.size_bytes == 0
        assert result.author is None

    def test_extra_fields_ignored(self):
        raw = {
            "id": "10042",
            "filename": "test.txt",
            "mimeType": "text/plain",
            "size": 100,
            "content": "https://example.com",
            "self": "https://example.com/rest/api/2/attachment/10042",
            "thumbnail": "https://example.com/thumbnail",
        }
        result = normalize_attachment(raw)
        assert result.id == "10042"
        assert isinstance(result, ItemAttachment)

    def test_size_coerced_to_int(self):
        raw = {"id": "1", "filename": "f.txt", "mimeType": "text/plain", "size": "2048"}
        result = normalize_attachment(raw)
        assert result.size_bytes == 2048


# ---------------------------------------------------------------------------
# normalize_issue -- attachment extraction
# ---------------------------------------------------------------------------


class TestNormalizeIssueAttachments:
    def test_issue_with_attachments(self):
        issue = {
            "key": "PROJ-1",
            "id": "10001",
            "fields": {
                "summary": "Test",
                "description": None,
                "status": {"name": "To Do"},
                "issuetype": {"name": "Task"},
                "created": "2026-03-01T12:00:00.000+0000",
                "updated": "2026-03-01T12:00:00.000+0000",
                "attachment": [
                    {"id": "101", "filename": "a.txt", "mimeType": "text/plain", "size": 10},
                    {"id": "102", "filename": "b.pdf", "mimeType": "application/pdf", "size": 2048},
                ],
            },
        }
        item = normalize_issue(issue)
        assert len(item.attachments) == 2
        assert item.attachments[0].filename == "a.txt"
        assert item.attachments[1].filename == "b.pdf"

    def test_issue_without_attachments(self):
        issue = {
            "key": "PROJ-2",
            "id": "10002",
            "fields": {
                "summary": "No attachments",
                "description": None,
                "status": {"name": "To Do"},
                "issuetype": {"name": "Task"},
                "created": "2026-03-01T12:00:00.000+0000",
                "updated": "2026-03-01T12:00:00.000+0000",
            },
        }
        item = normalize_issue(issue)
        assert item.attachments == ()

    def test_issue_with_empty_attachment_list(self):
        issue = {
            "key": "PROJ-3",
            "id": "10003",
            "fields": {
                "summary": "Empty list",
                "description": None,
                "status": {"name": "Done"},
                "issuetype": {"name": "Bug"},
                "created": "2026-03-01T12:00:00.000+0000",
                "updated": "2026-03-01T12:00:00.000+0000",
                "attachment": [],
            },
        }
        item = normalize_issue(issue)
        assert item.attachments == ()


# ---------------------------------------------------------------------------
# normalize_project
# ---------------------------------------------------------------------------


class TestNormalizeProject:
    def test_full_project(self):
        raw = {
            "key": "PROJ",
            "name": "My Project",
            "description": "A test project",
            "lead": {"accountId": "abc123", "displayName": "Jane Lead"},
            "projectTypeKey": "software",
            "self": "https://jira.example.com/rest/api/2/project/10001",
        }
        result = normalize_project(raw)
        assert result.key == "PROJ"
        assert result.name == "My Project"
        assert result.description == "A test project"
        assert result.lead is not None
        assert result.lead.id == "abc123"
        assert result.lead.display_name == "Jane Lead"
        assert result.project_type == "software"
        assert result.url == "https://jira.example.com/rest/api/2/project/10001"

    def test_minimal_project(self):
        raw = {"key": "MIN", "name": "Minimal"}
        result = normalize_project(raw)
        assert result.key == "MIN"
        assert result.name == "Minimal"
        assert result.description == ""
        assert result.lead is None
        assert result.project_type == ""
        assert result.url == ""

    def test_null_description(self):
        raw = {"key": "ND", "name": "No Desc", "description": None}
        result = normalize_project(raw)
        assert result.description == ""

    def test_lead_with_key_fallback(self):
        """Jira Server uses 'key' instead of 'accountId' for users."""
        raw = {
            "key": "SRV",
            "name": "Server Project",
            "lead": {"key": "jdoe", "displayName": "John Doe"},
        }
        result = normalize_project(raw)
        assert result.lead is not None
        assert result.lead.id == "jdoe"
        assert result.lead.display_name == "John Doe"
