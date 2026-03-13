"""Unit tests for work tracking domain models.

Tests ItemCategory enum and CreateItemRequest Pydantic validation.
No I/O, no mocks -- pure domain object tests.
"""

import pytest
from pydantic import ValidationError

from appif.domain.work_tracking.models import (
    CreateItemRequest,
    CreateProjectRequest,
    ItemCategory,
)

# ---------------------------------------------------------------------------
# ItemCategory enum
# ---------------------------------------------------------------------------


class TestItemCategory:
    """ItemCategory enum members and values."""

    def test_members_exist(self):
        assert ItemCategory.TASK is not None
        assert ItemCategory.SUBTASK is not None
        assert ItemCategory.STORY is not None
        assert ItemCategory.BUG is not None
        assert ItemCategory.EPIC is not None

    def test_values(self):
        assert ItemCategory.TASK.value == "task"
        assert ItemCategory.SUBTASK.value == "subtask"
        assert ItemCategory.STORY.value == "story"
        assert ItemCategory.BUG.value == "bug"
        assert ItemCategory.EPIC.value == "epic"

    def test_member_count(self):
        assert len(ItemCategory) == 5


# ---------------------------------------------------------------------------
# CreateItemRequest -- valid construction
# ---------------------------------------------------------------------------


class TestCreateItemRequestValid:
    """CreateItemRequest accepts valid inputs."""

    def test_task_minimal(self):
        req = CreateItemRequest(project="PROJ", title="Do something", item_type=ItemCategory.TASK)
        assert req.project == "PROJ"
        assert req.title == "Do something"
        assert req.item_type is ItemCategory.TASK
        assert req.description == ""
        assert req.parent_key is None
        assert req.labels == ()
        assert req.priority is None
        assert req.assignee_id is None

    def test_task_with_all_fields(self):
        req = CreateItemRequest(
            project="PROJ",
            title="Full item",
            item_type=ItemCategory.TASK,
            description="Details here",
            parent_key=None,
            labels=("backend", "urgent"),
            priority="High",
            assignee_id="user-123",
        )
        assert req.description == "Details here"
        assert req.labels == ("backend", "urgent")
        assert req.priority == "High"
        assert req.assignee_id == "user-123"

    def test_subtask_with_parent_key(self):
        req = CreateItemRequest(
            project="PROJ",
            title="Child item",
            item_type=ItemCategory.SUBTASK,
            parent_key="PROJ-10",
        )
        assert req.item_type is ItemCategory.SUBTASK
        assert req.parent_key == "PROJ-10"

    def test_story(self):
        req = CreateItemRequest(project="PROJ", title="User story", item_type=ItemCategory.STORY)
        assert req.item_type is ItemCategory.STORY

    def test_bug(self):
        req = CreateItemRequest(project="PROJ", title="Defect", item_type=ItemCategory.BUG)
        assert req.item_type is ItemCategory.BUG

    def test_epic(self):
        req = CreateItemRequest(project="PROJ", title="Big initiative", item_type=ItemCategory.EPIC)
        assert req.item_type is ItemCategory.EPIC

    def test_non_subtask_with_parent_key_allowed(self):
        """parent_key is permissive for non-SUBTASK categories."""
        req = CreateItemRequest(
            project="PROJ",
            title="Task with parent",
            item_type=ItemCategory.TASK,
            parent_key="PROJ-5",
        )
        assert req.parent_key == "PROJ-5"

    @pytest.mark.parametrize("category", list(ItemCategory))
    def test_all_categories_accepted(self, category):
        kwargs = {"project": "PROJ", "title": "Item", "item_type": category}
        if category is ItemCategory.SUBTASK:
            kwargs["parent_key"] = "PROJ-1"
        req = CreateItemRequest(**kwargs)
        assert req.item_type is category

    def test_accepts_enum_by_value_string(self):
        """Pydantic coerces string values to enum members."""
        req = CreateItemRequest(project="PROJ", title="Item", item_type="task")
        assert req.item_type is ItemCategory.TASK


# ---------------------------------------------------------------------------
# CreateItemRequest -- validation errors
# ---------------------------------------------------------------------------


class TestCreateItemRequestValidation:
    """CreateItemRequest rejects invalid inputs."""

    def test_rejects_arbitrary_string_for_item_type(self):
        with pytest.raises(ValidationError) as exc_info:
            CreateItemRequest(project="PROJ", title="Item", item_type="Sub-task")
        errors = exc_info.value.errors()
        assert any("item_type" in str(e.get("loc", "")) for e in errors)

    def test_rejects_none_item_type(self):
        with pytest.raises(ValidationError):
            CreateItemRequest(project="PROJ", title="Item", item_type=None)

    def test_subtask_without_parent_key_raises(self):
        with pytest.raises(ValidationError, match="parent_key is required when item_type is SUBTASK"):
            CreateItemRequest(project="PROJ", title="Child", item_type=ItemCategory.SUBTASK)

    def test_subtask_with_empty_parent_key_raises(self):
        with pytest.raises(ValidationError, match="parent_key is required when item_type is SUBTASK"):
            CreateItemRequest(project="PROJ", title="Child", item_type=ItemCategory.SUBTASK, parent_key="")

    def test_missing_project_raises(self):
        with pytest.raises(ValidationError):
            CreateItemRequest(title="Item", item_type=ItemCategory.TASK)

    def test_missing_title_raises(self):
        with pytest.raises(ValidationError):
            CreateItemRequest(project="PROJ", item_type=ItemCategory.TASK)


# ---------------------------------------------------------------------------
# CreateItemRequest -- immutability
# ---------------------------------------------------------------------------


class TestCreateItemRequestImmutability:
    """CreateItemRequest is frozen (immutable)."""

    def test_cannot_set_project(self):
        req = CreateItemRequest(project="PROJ", title="Item", item_type=ItemCategory.TASK)
        with pytest.raises(ValidationError):
            req.project = "OTHER"

    def test_cannot_set_item_type(self):
        req = CreateItemRequest(project="PROJ", title="Item", item_type=ItemCategory.TASK)
        with pytest.raises(ValidationError):
            req.item_type = ItemCategory.BUG


# ---------------------------------------------------------------------------
# CreateProjectRequest -- valid construction and defaults
# ---------------------------------------------------------------------------


class TestCreateProjectRequestValid:
    """CreateProjectRequest accepts valid inputs and sets correct defaults."""

    def test_minimal(self):
        req = CreateProjectRequest(key="PROJ", name="My Project")
        assert req.key == "PROJ"
        assert req.name == "My Project"
        assert req.project_type == "software"
        assert req.description == ""
        assert req.lead_account_id is None

    def test_key_with_digits(self):
        """Digits are allowed after the first letter."""
        req = CreateProjectRequest(key="PR01", name="With Digits")
        assert req.key == "PR01"


# ---------------------------------------------------------------------------
# CreateProjectRequest -- key validation (real domain risk)
# ---------------------------------------------------------------------------


class TestCreateProjectRequestValidation:
    """CreateProjectRequest key validation enforces Jira project key rules."""

    def test_lowercase_key_rejected(self):
        with pytest.raises(ValidationError, match="uppercase alphanumeric"):
            CreateProjectRequest(key="proj", name="Test")

    def test_single_char_key_rejected(self):
        with pytest.raises(ValidationError, match="uppercase alphanumeric"):
            CreateProjectRequest(key="P", name="Test")

    def test_key_over_ten_chars_rejected(self):
        with pytest.raises(ValidationError, match="uppercase alphanumeric"):
            CreateProjectRequest(key="ABCDEFGHIJK", name="Test")

    def test_key_starting_with_digit_rejected(self):
        with pytest.raises(ValidationError, match="uppercase alphanumeric"):
            CreateProjectRequest(key="1PROJ", name="Test")

    def test_key_with_special_chars_rejected(self):
        with pytest.raises(ValidationError, match="uppercase alphanumeric"):
            CreateProjectRequest(key="PR-OJ", name="Test")
