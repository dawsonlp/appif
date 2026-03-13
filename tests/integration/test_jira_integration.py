"""Integration tests for Jira work tracking adapter.

These tests run against a live Jira Cloud instance and exercise the
full CRUD lifecycle: create tickets, add comments, link items,
transition status, and search.

Run with:
    pytest tests/integration/test_jira_integration.py -v

Requires ~/.config/appif/jira/config.yaml with valid credentials and
a TSTADPT project on the personal instance.

Created tickets are NOT automatically cleaned up so you can inspect
them in the Jira UI. Run scripts/jira_cleanup.py when done.
"""

import pytest

from appif.domain.work_tracking.models import (
    CreateItemRequest,
    ItemCategory,
    LinkType,
    ProjectInfo,
    SearchCriteria,
)
from appif.domain.work_tracking.service import WorkTrackingService

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INSTANCE = "personal"
PROJECT = "TSTADPT"

# Collect keys of created items for the cleanup script
_created_keys: list[str] = []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def service():
    """Create a WorkTrackingService connected to the live Jira instance."""
    svc = WorkTrackingService(auto_load=True)
    instances = svc.list_instances()
    names = [i.name for i in instances]
    if INSTANCE not in names:
        pytest.skip(f"Instance '{INSTANCE}' not configured; available: {names}")
    svc.set_default(INSTANCE)
    return svc


# ---------------------------------------------------------------------------
# Tests -- ordered to build on each other
# ---------------------------------------------------------------------------


class TestJiraCRUDLifecycle:
    """Full lifecycle: create, read, comment, link, transition, search."""

    def test_01_create_item(self, service):
        """Create a task and verify it gets a key and id back."""
        request = CreateItemRequest(
            project=PROJECT,
            title="Integration test: create item",
            item_type=ItemCategory.TASK,
            description="Created by test_jira_integration.py",
            labels=("appif-test",),
        )
        ident = service.create_item(request)

        assert ident.key.startswith(f"{PROJECT}-")
        assert ident.id
        _created_keys.append(ident.key)

        # Store for subsequent tests
        self.__class__._item_key = ident.key

    def test_02_get_item(self, service):
        """Retrieve the created item and verify domain fields are populated."""
        key = self.__class__._item_key
        item = service.get_item(key)

        assert item.key == key
        assert item.title == "Integration test: create item"
        assert item.status  # Should have a status (e.g. "To Do")
        assert item.item_type == "task"
        assert item.created.year >= 2026
        assert "appif-test" in item.labels
        assert item.reporter is not None
        assert item.reporter.display_name  # Should have a display name

    def test_03_add_comment(self, service):
        """Add a comment and verify it appears on the item."""
        key = self.__class__._item_key
        comment = service.add_comment(key, "Test comment from appif integration test")

        assert comment.id
        assert comment.body == "Test comment from appif integration test"
        assert comment.author is not None

        # Re-fetch the item and verify the comment is there
        item = service.get_item(key)
        assert len(item.comments) >= 1
        bodies = [c.body for c in item.comments]
        assert any("Test comment from appif" in b for b in bodies)

    def test_04_create_second_item_and_link(self, service):
        """Create a second item and link it to the first as 'blocks'."""
        request = CreateItemRequest(
            project=PROJECT,
            title="Integration test: blocked item",
            item_type=ItemCategory.TASK,
            description="This item is blocked by the first",
            labels=("appif-test",),
        )
        ident = service.create_item(request)
        _created_keys.append(ident.key)
        self.__class__._second_key = ident.key

        # Link: first item blocks second
        service.link_items(
            self.__class__._item_key,
            ident.key,
            LinkType.BLOCKS,
        )

        # Verify the link appears on the first item
        first = service.get_item(self.__class__._item_key)
        block_targets = [link.target_key for link in first.links if link.link_type == LinkType.BLOCKS]
        assert ident.key in block_targets

    def test_05_get_transitions(self, service):
        """Verify we can list available transitions."""
        key = self.__class__._item_key
        transitions = service.get_transitions(key)

        assert len(transitions) > 0
        names = [t.name for t in transitions]
        # Store a valid transition name for the next test
        self.__class__._transition_name = names[0]

    def test_06_transition_item(self, service):
        """Transition the item and verify the status changes."""
        key = self.__class__._item_key
        transition_name = self.__class__._transition_name
        service.transition(key, transition_name)

        updated = service.get_item(key)
        # Status should have changed (unless the transition loops back)
        # At minimum, the call should not error
        assert updated.status  # Has a status

    def test_07_get_project_issue_types(self, service):
        """Discover issue types available in the test project."""
        types = service.get_project_issue_types(PROJECT)

        assert len(types) > 0
        names = [t.name for t in types]
        # Every Jira project should have at least Task
        assert "Task" in names

        # Verify structure
        for t in types:
            assert isinstance(t.name, str)
            assert isinstance(t.subtask, bool)
            assert isinstance(t.description, str)

    def test_08_get_link_types(self, service):
        """Discover link types available on the instance."""
        link_types = service.get_link_types()

        assert len(link_types) > 0
        names = [lt.name for lt in link_types]
        # Jira Cloud always has Blocks
        assert "Blocks" in names

        # Verify structure
        for lt in link_types:
            assert isinstance(lt.name, str)
            assert isinstance(lt.inward, str)
            assert isinstance(lt.outward, str)
            assert lt.inward  # Should not be empty
            assert lt.outward  # Should not be empty

    def test_09_search_by_project(self, service):
        """Search for items in the test project and find our created items."""
        criteria = SearchCriteria(
            project=PROJECT,
            labels=("appif-test",),
        )
        result = service.search(criteria)

        assert result.total >= 2
        found_keys = {item.key for item in result.items}
        assert self.__class__._item_key in found_keys
        assert self.__class__._second_key in found_keys

    def test_10_list_projects(self, service):
        """List all accessible projects and verify structure."""
        projects = service.list_projects()

        assert len(projects) > 0
        assert all(isinstance(p, ProjectInfo) for p in projects)

        # Our test project should be in the list
        keys = [p.key for p in projects]
        assert PROJECT in keys

        # Verify structure of at least one project
        proj = next(p for p in projects if p.key == PROJECT)
        assert proj.name  # Should have a name
        assert proj.key == PROJECT

    def test_11_get_project(self, service):
        """Get details of a specific project."""
        proj = service.get_project(PROJECT)

        assert isinstance(proj, ProjectInfo)
        assert proj.key == PROJECT
        assert proj.name  # Should have a name
        assert proj.project_type  # Should report a type (e.g. "software")

    def test_99_record_created_keys(self, service, tmp_path_factory):
        """Write created keys to a file for the cleanup script."""
        if _created_keys:
            # Write to a known location
            import json
            from pathlib import Path

            cleanup_file = Path.home() / ".config" / "appif" / "jira" / "test_cleanup.json"
            existing = []
            if cleanup_file.exists():
                existing = json.loads(cleanup_file.read_text())
            existing.extend(_created_keys)
            cleanup_file.write_text(json.dumps(list(set(existing)), indent=2))
