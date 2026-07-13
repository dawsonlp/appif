"""WorkTrackingService -- multi-instance routing over work tracker backends.

Implements the ``InstanceRegistry`` and ``WorkTracker`` driver-side protocols.
It holds a set of registered :class:`WorkTrackerBackend` instances (the driven
port) and routes each operation to the one named by ``instance`` (or the
default).

Architectural note (see ADR-002): only one work-tracking platform (Jira) is
implemented today, so a swappable backend port + a routing service is more
structure than a single hard-wired adapter would need -- a deliberate trade
against KISS. We keep it because the *more important* goal is preserving the
hexagonal shape: the domain depends only on the ``WorkTrackerBackend`` port and
never imports an adapter. Concrete adapters are constructed and registered by a
composition factory in the adapter layer (``appif.adapters.jira`` provides
``create_work_tracking_service``). That keeps the dependency arrow pointing
inward, keeps the domain type-checkable in isolation, and means a second
platform (or a fake backend in tests) plugs in without touching this file.
"""

from __future__ import annotations

from appif.domain.work_tracking.errors import (
    InstanceAlreadyRegistered,
    InstanceNotFound,
    NoDefaultInstance,
)
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
from appif.domain.work_tracking.ports import WorkTrackerBackend


class WorkTrackingService:
    """Routes work tracking operations to registered backends by instance name.

    Construct it empty and register backends, or use a platform composition
    factory (e.g. ``appif.adapters.jira.create_work_tracking_service``) to wire
    and register them for you.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, WorkTrackerBackend] = {}
        self._default: str | None = None

    # -- InstanceRegistry --------------------------------------------------

    def register(self, name: str, backend: WorkTrackerBackend, *, make_default: bool = False) -> None:
        """Register a work tracker backend under a unique name.

        The first backend registered becomes the default automatically; pass
        ``make_default=True`` to force a later one to become the default.
        """
        if name in self._adapters:
            raise InstanceAlreadyRegistered(name)
        self._adapters[name] = backend
        if make_default or self._default is None:
            self._default = name

    def unregister(self, name: str) -> None:
        """Remove an instance and clean up its state."""
        if name not in self._adapters:
            raise InstanceNotFound(name)
        del self._adapters[name]
        if self._default == name:
            self._default = None

    def list_instances(self) -> list[InstanceInfo]:
        """Return all registered instances (no credentials)."""
        return [
            InstanceInfo(
                name=name,
                platform=backend.platform,
                server_url=backend.server_url,
                is_default=(name == self._default),
            )
            for name, backend in self._adapters.items()
        ]

    def set_default(self, name: str) -> None:
        """Designate an instance as the default."""
        if name not in self._adapters:
            raise InstanceNotFound(name)
        self._default = name

    def get_default(self) -> str | None:
        """Return the default instance name, or None."""
        return self._default

    # -- Instance resolution -----------------------------------------------

    def _resolve(self, instance: str | None) -> WorkTrackerBackend:
        """Resolve an instance name to a registered backend."""
        name = instance or self._default
        if name is None:
            raise NoDefaultInstance()
        backend = self._adapters.get(name)
        if backend is None:
            raise InstanceNotFound(name)
        return backend

    # -- WorkTracker -------------------------------------------------------

    def get_item(self, key: str, *, instance: str | None = None) -> WorkItem:
        return self._resolve(instance).get_item(key)

    def create_item(self, request: CreateItemRequest, *, instance: str | None = None) -> ItemIdentifier:
        return self._resolve(instance).create_item(request)

    def add_comment(self, key: str, body: str, *, instance: str | None = None) -> ItemComment:
        return self._resolve(instance).add_comment(key, body)

    def get_transitions(self, key: str, *, instance: str | None = None) -> list[TransitionInfo]:
        return self._resolve(instance).get_transitions(key)

    def transition(self, key: str, transition_name: str, *, instance: str | None = None) -> None:
        self._resolve(instance).transition(key, transition_name)

    def link_items(
        self,
        from_key: str,
        to_key: str,
        link_type: LinkType,
        *,
        instance: str | None = None,
    ) -> None:
        self._resolve(instance).link_items(from_key, to_key, link_type)

    def search(
        self,
        criteria: SearchCriteria,
        *,
        offset: int = 0,
        limit: int = 50,
        instance: str | None = None,
    ) -> SearchResult:
        return self._resolve(instance).search(criteria, offset, limit)

    def get_project_issue_types(self, project: str, *, instance: str | None = None) -> list[IssueTypeInfo]:
        return self._resolve(instance).get_project_issue_types(project)

    def get_link_types(self, *, instance: str | None = None) -> list[LinkTypeInfo]:
        return self._resolve(instance).get_link_types()

    def download_attachment(
        self,
        attachment_id: str,
        *,
        instance: str | None = None,
    ) -> AttachmentContent:
        return self._resolve(instance).download_attachment(attachment_id)

    def attach_file(
        self,
        key: str,
        filename: str,
        content: bytes,
        *,
        instance: str | None = None,
    ) -> ItemAttachment:
        return self._resolve(instance).attach_file(key, filename, content)

    def list_projects(self, *, instance: str | None = None) -> list[ProjectInfo]:
        return self._resolve(instance).list_projects()

    def get_project(self, key: str, *, instance: str | None = None) -> ProjectInfo:
        return self._resolve(instance).get_project(key)

    def create_project(
        self,
        request: CreateProjectRequest,
        *,
        instance: str | None = None,
    ) -> ProjectInfo:
        return self._resolve(instance).create_project(request)

    def delete_project(self, key: str, *, instance: str | None = None) -> None:
        self._resolve(instance).delete_project(key)
