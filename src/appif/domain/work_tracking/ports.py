"""Work tracking protocol interfaces.

Three protocols, split by role in the hexagonal architecture:

- ``WorkTrackerBackend`` is the **driven port** — the per-instance operations a
  platform adapter (e.g. the Jira adapter) implements. It has no ``instance``
  routing; each backend represents one connected instance.
- ``WorkTracker`` and ``InstanceRegistry`` are the **driver-side** interfaces
  the application uses: ``WorkTracker`` adds ``instance`` routing over a set of
  backends, and ``InstanceRegistry`` manages that set. ``WorkTrackingService``
  implements both and holds the registered backends.

The domain depends only on ``WorkTrackerBackend``; concrete adapters are wired
in by a composition factory in the adapter layer (see ADR-002), so the domain
never imports an adapter.
"""

from __future__ import annotations

from typing import Protocol

from appif.domain.work_tracking.models import (
    AttachmentContent,
    CreateItemRequest,
    CreateProjectRequest,
    InstanceInfo,
    IssueTypeInfo,
    ItemAttachment,
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


class WorkTrackerBackend(Protocol):
    """Driven port: the per-instance operations a platform adapter implements.

    One backend == one connected instance, so there is no ``instance`` routing
    here (that belongs to :class:`WorkTracker`). ``platform`` and ``server_url``
    let the registry describe the instance without knowing the concrete type.
    """

    @property
    def platform(self) -> str: ...

    @property
    def server_url(self) -> str: ...

    def get_item(self, key: str) -> WorkItem: ...

    def create_item(self, request: CreateItemRequest) -> ItemIdentifier: ...

    def add_comment(self, key: str, body: str) -> ItemComment: ...

    def get_transitions(self, key: str) -> list[TransitionInfo]: ...

    def transition(self, key: str, transition_name: str) -> None: ...

    def link_items(self, from_key: str, to_key: str, link_type: LinkType) -> None: ...

    def search(self, criteria: SearchCriteria, offset: int = 0, limit: int = 50) -> SearchResult: ...

    def get_project_issue_types(self, project: str) -> list[IssueTypeInfo]: ...

    def get_link_types(self) -> list[LinkTypeInfo]: ...

    def download_attachment(self, attachment_id: str) -> AttachmentContent: ...

    def attach_file(self, key: str, filename: str, content: bytes) -> ItemAttachment: ...

    def list_projects(self) -> list[ProjectInfo]: ...

    def get_project(self, key: str) -> ProjectInfo: ...

    def create_project(self, request: CreateProjectRequest) -> ProjectInfo: ...

    def delete_project(self, key: str) -> None: ...


class InstanceRegistry(Protocol):
    """Administrative (driver-side) interface for managing platform instances."""

    def register(self, name: str, backend: WorkTrackerBackend, *, make_default: bool = False) -> None:
        """Register a work tracker backend under a unique name."""
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

    def attach_file(
        self,
        key: str,
        filename: str,
        content: bytes,
        *,
        instance: str | None = None,
    ) -> ItemAttachment:
        """Attach a file to a work item.

        Parameters
        ----------
        key:
            Work item key (e.g. ``"PROJ-123"``).
        filename:
            Filename to use on the platform (e.g. ``"requirements.md"``).
        content:
            Complete file content as bytes. Must be non-empty.
        instance:
            Optional instance name. Uses default if omitted.

        Returns
        -------
        ItemAttachment
            Metadata for the newly created attachment, including the
            platform-assigned ``id``, confirmed ``filename``,
            ``mime_type``, ``size_bytes``, ``created``, and ``author``.

        Raises
        ------
        WorkTrackingError
            If filename is empty or content is empty.
        ItemNotFound
            If the work item does not exist.
        PermissionDenied
            If the caller lacks permission to attach files.
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
