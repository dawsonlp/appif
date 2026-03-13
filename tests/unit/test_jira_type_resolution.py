"""Unit tests for JiraAdapter type resolution logic.

Tests _resolve_issue_type(), _get_project_types(), and the per-project
type cache. Uses mocks to isolate from real Jira API calls.
"""

from unittest.mock import MagicMock, patch

from appif.domain.work_tracking.models import (
    CreateItemRequest,
    IssueTypeInfo,
    ItemCategory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(type_cache: dict | None = None):
    """Create a JiraAdapter with a mocked Jira client."""
    with patch("appif.adapters.jira.adapter.create_jira_client") as mock_create:
        mock_create.return_value = MagicMock()
        from appif.adapters.jira.adapter import JiraAdapter

        adapter = JiraAdapter(
            server_url="https://test.atlassian.net",
            credentials={"username": "test", "api_token": "token"},
            instance_name="test",
        )
    if type_cache is not None:
        adapter._type_cache = type_cache
    return adapter


def _standard_types() -> list[IssueTypeInfo]:
    """Typical Jira project with Task, Story, Bug, Epic, Sub-task."""
    return [
        IssueTypeInfo(name="Task", subtask=False),
        IssueTypeInfo(name="Story", subtask=False),
        IssueTypeInfo(name="Bug", subtask=False),
        IssueTypeInfo(name="Epic", subtask=False),
        IssueTypeInfo(name="Sub-task", subtask=True),
    ]


def _minimal_types() -> list[IssueTypeInfo]:
    """Project with only Task -- no Story, Bug, Epic, or subtask type."""
    return [
        IssueTypeInfo(name="Task", subtask=False),
    ]


def _request(category: ItemCategory, project: str = "PROJ", parent_key: str | None = None) -> CreateItemRequest:
    kwargs = {"project": project, "title": "Test item", "item_type": category}
    if parent_key:
        kwargs["parent_key"] = parent_key
    return CreateItemRequest(**kwargs)


# ---------------------------------------------------------------------------
# _resolve_issue_type -- standard project
# ---------------------------------------------------------------------------


class TestResolveIssueTypeStandard:
    """Resolution when all standard types are available."""

    def test_task_resolves_to_task(self):
        adapter = _make_adapter(type_cache={"PROJ": _standard_types()})
        assert adapter._resolve_issue_type(_request(ItemCategory.TASK)) == "Task"

    def test_story_resolves_to_story(self):
        adapter = _make_adapter(type_cache={"PROJ": _standard_types()})
        assert adapter._resolve_issue_type(_request(ItemCategory.STORY)) == "Story"

    def test_bug_resolves_to_bug(self):
        adapter = _make_adapter(type_cache={"PROJ": _standard_types()})
        assert adapter._resolve_issue_type(_request(ItemCategory.BUG)) == "Bug"

    def test_epic_resolves_to_epic(self):
        adapter = _make_adapter(type_cache={"PROJ": _standard_types()})
        assert adapter._resolve_issue_type(_request(ItemCategory.EPIC)) == "Epic"

    def test_subtask_resolves_to_subtask_type(self):
        adapter = _make_adapter(type_cache={"PROJ": _standard_types()})
        result = adapter._resolve_issue_type(_request(ItemCategory.SUBTASK, parent_key="PROJ-1"))
        assert result == "Sub-task"


# ---------------------------------------------------------------------------
# _resolve_issue_type -- fallback behavior
# ---------------------------------------------------------------------------


class TestResolveIssueTypeFallback:
    """Fallback when preferred types are not available."""

    def test_story_falls_back_to_task(self):
        adapter = _make_adapter(type_cache={"PROJ": _minimal_types()})
        assert adapter._resolve_issue_type(_request(ItemCategory.STORY)) == "Task"

    def test_bug_falls_back_to_task(self):
        adapter = _make_adapter(type_cache={"PROJ": _minimal_types()})
        assert adapter._resolve_issue_type(_request(ItemCategory.BUG)) == "Task"

    def test_epic_falls_back_to_task(self):
        adapter = _make_adapter(type_cache={"PROJ": _minimal_types()})
        assert adapter._resolve_issue_type(_request(ItemCategory.EPIC)) == "Task"

    def test_subtask_returns_none_when_no_subtask_type(self):
        adapter = _make_adapter(type_cache={"PROJ": _minimal_types()})
        result = adapter._resolve_issue_type(_request(ItemCategory.SUBTASK, parent_key="PROJ-1"))
        assert result is None


# ---------------------------------------------------------------------------
# _get_project_types -- caching
# ---------------------------------------------------------------------------


class TestProjectTypeCache:
    """Per-project type cache behavior."""

    def test_cache_populated_on_first_call(self):
        adapter = _make_adapter()
        # Mock get_project_issue_types to return standard types
        adapter.get_project_issue_types = MagicMock(return_value=_standard_types())

        result = adapter._get_project_types("PROJ")
        assert len(result) == 5
        assert "PROJ" in adapter._type_cache
        adapter.get_project_issue_types.assert_called_once_with("PROJ")

    def test_cache_reused_on_second_call(self):
        adapter = _make_adapter()
        adapter.get_project_issue_types = MagicMock(return_value=_standard_types())

        adapter._get_project_types("PROJ")
        adapter._get_project_types("PROJ")

        # Only one API call despite two lookups
        adapter.get_project_issue_types.assert_called_once()

    def test_different_projects_cached_separately(self):
        adapter = _make_adapter()
        adapter.get_project_issue_types = MagicMock(side_effect=[_standard_types(), _minimal_types()])

        result_a = adapter._get_project_types("PROJ_A")
        result_b = adapter._get_project_types("PROJ_B")

        assert len(result_a) == 5
        assert len(result_b) == 1
        assert adapter.get_project_issue_types.call_count == 2

    def test_pre_populated_cache_skips_api(self):
        adapter = _make_adapter(type_cache={"PROJ": _standard_types()})
        adapter.get_project_issue_types = MagicMock()

        result = adapter._get_project_types("PROJ")
        assert len(result) == 5
        adapter.get_project_issue_types.assert_not_called()


# ---------------------------------------------------------------------------
# Case-insensitive matching
# ---------------------------------------------------------------------------


class TestCaseInsensitiveResolution:
    """Type name matching is case-insensitive."""

    def test_task_matches_case_insensitive(self):
        types = [IssueTypeInfo(name="task", subtask=False)]
        adapter = _make_adapter(type_cache={"PROJ": types})
        assert adapter._resolve_issue_type(_request(ItemCategory.TASK)) == "task"

    def test_subtask_flag_checked_not_name(self):
        """SUBTASK resolution uses the subtask flag, not the name."""
        types = [
            IssueTypeInfo(name="Task", subtask=False),
            IssueTypeInfo(name="Child Issue", subtask=True),
        ]
        adapter = _make_adapter(type_cache={"PROJ": types})
        result = adapter._resolve_issue_type(_request(ItemCategory.SUBTASK, parent_key="PROJ-1"))
        assert result == "Child Issue"
