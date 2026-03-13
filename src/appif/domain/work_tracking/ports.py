"""Work tracking protocol interfaces.

Two protocols separate administrative concerns from operational concerns.
Agents use ``WorkTracker``. Orchestrators and setup code use
``InstanceRegistry``. Both may be implemented by the same object.
"""

from __future__ import annotations

from typing import Protocol

from appif.domain.work_tracking.models import (
    AttachmentContent,
    CreateItemRequest,
    CreateProjectRequest,
    InstanceInfo,
    IssueTypeInfo,
    ItemComment,
    ItemIdentifier,
    LinkType,
    LinkTypeInfo,
    ProjectInfo,
    SearchCriteria,
    SearchResult,
    TransitionInfo,
    WorkItem,
)


class InstanceRegistry(Protocol):
    """Administrative interface for managing platform instances."""

    def register(
        self,
        name: str,
        platform: str,
        server_url: str,
        credentials: dict[str, str],
    ) -> None:
        """Register a new instance with a unique name."""
        ...

    def unregister(self, name: str) -> None:
        """Remove an instance and clean up its state."""
        ...

    def list_instances(self) -> list[InstanceInfo]:
        """Return all registered instances (no credentials)."""
        ...

    def set_default(self, name: str) -> None:
        """Designate an instance as the default."""
        ...

    def get_default(self) -> str | None:
        """Return the default instance name, or None."""
        ...


class WorkTracker(Protocol):
    """Operational interface for work tracking.

    Every method takes an optional ``instance`` keyword argument. If
    omitted, the default instance is used. If no default is set, an
    error is raised.
    """

    def get_item(self, key: str, *, instance: str | None = None) -> WorkItem:
        """Retrieve a work item by key."""
        ...

    def create_item(self, request: CreateItemRequest, *, instance: str | None = None) -> ItemIdentifier:
        """Create a new work item."""
        ...

    def add_comment(self, key: str, body: str, *, instance: str | None = None) -> ItemComment:
        """Add a comment to a work item."""
        ...

    def get_transitions(self, key: str, *, instance: str | None = None) -> list[TransitionInfo]:
        """List available workflow transitions for a work item."""
        ...

    def transition(self, key: str, transition_name: str, *, instance: str | None = None) -> None:
        """Execute a workflow transition by name."""
        ...

    def link_items(
        self,
        from_key: str,
        to_key: str,
        link_type: LinkType,
        *,
        instance: str | None = None,
    ) -> None:
        """Create a typed relationship between two work items."""
        ...

    def search(
        self,
        criteria: SearchCriteria,
        *,
        offset: int = 0,
        limit: int = 50,
        instance: str | None = None,
    ) -> SearchResult:
        """Search for work items matching the given criteria."""
        ...

    def get_project_issue_types(self, project: str, *, instance: str | None = None) -> list[IssueTypeInfo]:
        """Return the issue types available in a project."""
        ...

    def get_link_types(self, *, instance: str | None = None) -> list[LinkTypeInfo]:
        """Return the link types available on the platform."""
        ...

    def download_attachment(
        self,
        attachment_id: str,
        *,
        instance: str | None = None,
    ) -> AttachmentContent:
        """Download the content of an attachment by its ID.

        Returns an ``AttachmentContent`` containing the complete file
        bytes and associated metadata. The ``attachment_id`` corresponds
        to ``ItemAttachment.id`` from a retrieved ``WorkItem``.

        Future versions may introduce a streaming variant via a separate
        method; this method will always return fully-buffered content.
        """
        ...

    def list_projects(self, *, instance: str | None = None) -> list[ProjectInfo]:
        """Return all projects accessible on the platform."""
        ...

    def get_project(self, key: str, *, instance: str | None = None) -> ProjectInfo:
        """Retrieve a single project by key."""
        ...

    def create_project(
        self,
        request: CreateProjectRequest,
        *,
        instance: str | None = None,
    ) -> ProjectInfo:
        """Create a new project and return its details."""
        ...

    def delete_project(self, key: str, *, instance: str | None = None) -> None:
        """Delete a project by key. This operation is irreversible."""
        ...
