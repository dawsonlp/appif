"""Canonical work tracking domain models.

These types are platform-agnostic. No Jira field names, GitHub API
conventions, or Linear-specific concepts appear here. Every work item --
Jira, GitHub Issues, Linear, or any future platform -- arrives in this
shape. No platform SDK types appear here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

# ---------------------------------------------------------------------------
# Link types
# ---------------------------------------------------------------------------


class LinkType(Enum):
    """Normalized relationship types between work items."""

    BLOCKS = "blocks"
    BLOCKED_BY = "blocked_by"
    RELATES_TO = "relates_to"
    DUPLICATES = "duplicates"
    DUPLICATED_BY = "duplicated_by"
    PARENT_OF = "parent_of"
    CHILD_OF = "child_of"


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemAuthor:
    """A person associated with a work item (assignee, reporter, commenter)."""

    id: str
    display_name: str


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemComment:
    """A comment on a work item."""

    id: str
    author: ItemAuthor
    body: str
    created: datetime


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemLink:
    """A typed relationship from this item to another."""

    link_type: LinkType
    target_key: str


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemAttachment:
    """Metadata for a file attached to a work item.

    Contains descriptive information only -- no platform URLs or
    download mechanics leak through this type. Use
    ``WorkTracker.download_attachment()`` to retrieve the file content
    by ``id``.
    """

    id: str
    filename: str
    mime_type: str
    size_bytes: int
    created: datetime
    author: ItemAuthor | None = None


@dataclass(frozen=True)
class AttachmentContent:
    """Downloaded attachment content with metadata.

    The ``data`` field contains the complete file content as bytes.
    Future versions may introduce a streaming variant via a separate
    method; this type will always represent fully-buffered content.
    """

    metadata: ItemAttachment
    data: bytes


# ---------------------------------------------------------------------------
# Work item
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkItem:
    """Normalized representation of a tracked work item."""

    key: str
    id: str
    title: str
    description: str
    status: str
    item_type: str
    created: datetime
    updated: datetime
    priority: str | None = None
    labels: tuple[str, ...] = ()
    assignee: ItemAuthor | None = None
    reporter: ItemAuthor | None = None
    parent_key: str | None = None
    sub_item_keys: tuple[str, ...] = ()
    links: tuple[ItemLink, ...] = ()
    comments: tuple[ItemComment, ...] = ()
    attachments: tuple[ItemAttachment, ...] = ()


# ---------------------------------------------------------------------------
# Item categories
# ---------------------------------------------------------------------------


class ItemCategory(Enum):
    """Domain-level work item categories.

    Callers express intent using these categories. Platform adapters
    resolve each category to the correct platform-specific type string
    (e.g. TASK -> "Task" in Jira, "issue" in GitHub).

    Members
    -------
    TASK
        A standard work item. The universal default; every platform
        supports this concept.
    SUBTASK
        A child work item scoped under a parent. Requires ``parent_key``
        on ``CreateItemRequest``. The adapter discovers the correct
        subtask type for the target project.
    STORY
        A user story describing desired functionality from the user's
        perspective. Falls back to TASK if the platform or project does
        not support stories.
    BUG
        A defect report. Falls back to TASK if the platform or project
        does not support a dedicated bug type.
    EPIC
        A large body of work spanning multiple items. Falls back to TASK
        if the platform or project does not support epics.
    """

    TASK = "task"
    SUBTASK = "subtask"
    STORY = "story"
    BUG = "bug"
    EPIC = "epic"


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemIdentifier:
    """Identifier returned after item creation."""

    key: str
    id: str


class CreateItemRequest(BaseModel):
    """Input for creating a new work item.

    Not a domain entity -- a command object. Uses Pydantic for
    enum validation, cross-field rules, and JSON schema generation.

    Validation rules
    ----------------
    - ``item_type`` must be an ``ItemCategory`` enum member.
    - ``parent_key`` is required when ``item_type`` is ``SUBTASK``.
    - ``parent_key`` is permitted (but optional) for other categories
      to support platforms where any item can have a parent.
    """

    model_config = ConfigDict(frozen=True)

    project: str
    title: str
    item_type: ItemCategory
    description: str = ""
    parent_key: str | None = None
    labels: tuple[str, ...] = ()
    priority: str | None = None
    assignee_id: str | None = None

    @model_validator(mode="after")
    def _subtask_requires_parent(self) -> CreateItemRequest:
        if self.item_type is ItemCategory.SUBTASK and not self.parent_key:
            raise ValueError("parent_key is required when item_type is SUBTASK")
        return self


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionInfo:
    """An available workflow transition for a work item."""

    id: str
    name: str


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchCriteria:
    """Structured search parameters for work items."""

    project: str | None = None
    status: str | None = None
    assignee_id: str | None = None
    labels: tuple[str, ...] = ()
    query: str | None = None


@dataclass(frozen=True)
class SearchResult:
    """Paginated search response."""

    items: tuple[WorkItem, ...] = ()
    total: int = 0
    offset: int = 0
    limit: int = 50


# ---------------------------------------------------------------------------
# Issue type discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IssueTypeInfo:
    """Metadata about an issue type available in a project."""

    name: str
    subtask: bool
    description: str = ""


# ---------------------------------------------------------------------------
# Link type discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkTypeInfo:
    """Metadata about a link type available in the platform."""

    name: str
    inward: str
    outward: str


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectInfo:
    """Metadata about a project in the work tracking platform."""

    key: str
    name: str
    description: str = ""
    lead: ItemAuthor | None = None
    project_type: str = ""
    url: str = ""


class CreateProjectRequest(BaseModel):
    """Input for creating a new project.

    Not a domain entity -- a command object. Uses Pydantic for
    validation and JSON schema generation.

    Validation rules
    ----------------
    - ``key`` must be uppercase alphanumeric (2-10 characters).
    - ``name`` is required and non-empty.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    project_type: str = "software"
    description: str = ""
    lead_account_id: str | None = None

    @model_validator(mode="after")
    def _validate_key(self) -> CreateProjectRequest:
        import re

        if not re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", self.key):
            raise ValueError("key must be 2-10 uppercase alphanumeric characters starting with a letter")
        return self


# ---------------------------------------------------------------------------
# Instance registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstanceInfo:
    """Metadata about a registered platform instance."""

    name: str
    platform: str
    server_url: str
    is_default: bool = False
