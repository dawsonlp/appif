"""WorkTrackingService -- thin routing layer.

Implements both ``InstanceRegistry`` and ``WorkTracker`` protocols.
Routes operations to the correct adapter based on instance name.
Loads configuration from YAML at startup.
"""

from __future__ import annotations

from appif.adapters.jira._auth import load_config
from appif.adapters.jira.adapter import JiraAdapter
from appif.domain.work_tracking.errors import (
    InstanceAlreadyRegistered,
    InstanceNotFound,
    NoDefaultInstance,
    WorkTrackingError,
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


class WorkTrackingService:
    """Routes work tracking operations to registered platform adapters.

    At construction, loads Jira instances from the YAML config file
    (``~/.config/appif/jira/config.yaml`` or ``APPIF_JIRA_CONFIG``).
    Additional instances can be registered programmatically.
    """

    def __init__(self, *, auto_load: bool = True):
        self._adapters: dict[str, JiraAdapter] = {}
        self._platforms: dict[str, str] = {}  # name -> platform
        self._default: str | None = None

        if auto_load:
            self._load_from_config()

    def _load_from_config(self) -> None:
        """Auto-register instances from the YAML config file."""
        config = load_config()
        instances = config.get("instances", {})
        default_name = config.get("default")

        for name, instance_cfg in instances.items():
            # Support both flat format and nested jira/confluence format
            if "jira" in instance_cfg:
                jira_cfg = instance_cfg["jira"]
            else:
                jira_cfg = instance_cfg

            url = jira_cfg.get("url", "")
            username = jira_cfg.get("username", "")
            api_token = jira_cfg.get("api_token", "")

            if url and username and api_token:
                try:
                    self.register(
                        name=name,
                        platform="jira",
                        server_url=url,
                        credentials={"username": username, "api_token": api_token},
                    )
                except Exception:
                    # Log but don't fail startup for one bad instance
                    pass

        if default_name and default_name in self._adapters:
            self.set_default(default_name)
        elif len(self._adapters) == 1:
            # Auto-default when there's exactly one instance
            self.set_default(next(iter(self._adapters)))

    # -- InstanceRegistry --------------------------------------------------

    def register(
        self,
        name: str,
        platform: str,
        server_url: str,
        credentials: dict[str, str],
    ) -> None:
        """Register a new instance with a unique name."""
        if name in self._adapters:
            raise InstanceAlreadyRegistered(name)

        if platform == "jira":
            adapter = JiraAdapter(server_url, credentials, instance_name=name)
        else:
            raise WorkTrackingError(f"unsupported platform: {platform}")

        self._adapters[name] = adapter
        self._platforms[name] = platform

    def unregister(self, name: str) -> None:
        """Remove an instance and clean up its state."""
        if name not in self._adapters:
            raise InstanceNotFound(name)
        del self._adapters[name]
        del self._platforms[name]
        if self._default == name:
            self._default = None

    def list_instances(self) -> list[InstanceInfo]:
        """Return all registered instances (no credentials)."""
        return [
            InstanceInfo(
                name=name,
                platform=self._platforms[name],
                server_url=adapter.server_url,
                is_default=(name == self._default),
            )
            for name, adapter in self._adapters.items()
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

    def _resolve(self, instance: str | None) -> JiraAdapter:
        """Resolve an instance name to an adapter."""
        name = instance or self._default
        if name is None:
            raise NoDefaultInstance()
        adapter = self._adapters.get(name)
        if adapter is None:
            raise InstanceNotFound(name)
        return adapter

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
