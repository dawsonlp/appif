"""Jira adapter -- concrete implementation using atlassian-python-api.

Each ``JiraAdapter`` instance owns a single ``atlassian.Jira`` client
connected to one Jira Cloud (or Server) instance.
"""

from __future__ import annotations

import io
import logging

from atlassian import Jira
from requests import HTTPError

from appif.adapters.jira._auth import create_jira_client
from appif.adapters.jira._normalizer import (
    normalize_attachment,
    normalize_comment,
    normalize_issue,
    normalize_issue_type,
    normalize_link_type_info,
    normalize_project,
    normalize_transition,
)
from appif.domain.work_tracking.errors import (
    ConnectionFailure,
    InvalidTransition,
    ItemNotFound,
    PermissionDenied,
    ProjectNotFound,
    RateLimited,
    WorkTrackingError,
)
from appif.domain.work_tracking.models import (
    AttachmentContent,
    CreateItemRequest,
    CreateProjectRequest,
    IssueTypeInfo,
    ItemAttachment,
    ItemCategory,
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

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Link type reverse mapping (domain -> Jira)
# ---------------------------------------------------------------------------

_DOMAIN_TO_JIRA_LINK: dict[LinkType, str] = {
    LinkType.BLOCKS: "Blocks",
    LinkType.BLOCKED_BY: "Blocks",
    LinkType.RELATES_TO: "Relates",
    LinkType.DUPLICATES: "Duplicate",
    LinkType.DUPLICATED_BY: "Duplicate",
    LinkType.PARENT_OF: "Relates",
    LinkType.CHILD_OF: "Relates",
}


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


def _translate_error(exc: Exception, instance: str | None = None) -> WorkTrackingError:
    """Map an HTTP or API error to the appropriate domain exception."""
    status = None
    text = str(exc)

    if isinstance(exc, HTTPError) and exc.response is not None:
        status = exc.response.status_code
    elif hasattr(exc, "status_code"):
        status = exc.status_code

    if status == 404:
        return ItemNotFound(key=text, instance=instance)
    if status in (401, 403):
        return PermissionDenied(reason=text, instance=instance)
    if status == 429:
        return RateLimited(instance=instance)
    if status and status >= 500:
        return ConnectionFailure(reason=text, instance=instance)
    return WorkTrackingError(text, instance=instance)


def _translate_project_error(exc: Exception, key: str, instance: str | None = None) -> WorkTrackingError:
    """Map an HTTP error to a project-specific domain exception."""
    status = None
    text = str(exc)

    if isinstance(exc, HTTPError) and exc.response is not None:
        status = exc.response.status_code
    elif hasattr(exc, "status_code"):
        status = exc.status_code

    if status == 404:
        return ProjectNotFound(key=key, instance=instance)
    if status in (401, 403):
        return PermissionDenied(reason=text, instance=instance)
    if status == 429:
        return RateLimited(instance=instance)
    if status and status >= 500:
        return ConnectionFailure(reason=text, instance=instance)
    return WorkTrackingError(text, instance=instance)


# ---------------------------------------------------------------------------
# JQL builder
# ---------------------------------------------------------------------------


def _build_jql(criteria: SearchCriteria) -> str:
    """Build a JQL query string from structured search criteria."""
    clauses: list[str] = []
    if criteria.project:
        clauses.append(f'project = "{criteria.project}"')
    if criteria.status:
        clauses.append(f'status = "{criteria.status}"')
    if criteria.assignee_id:
        clauses.append(f'assignee = "{criteria.assignee_id}"')
    for label in criteria.labels:
        clauses.append(f'labels = "{label}"')
    if criteria.query:
        clauses.append(f'text ~ "{criteria.query}"')
    if clauses:
        return " AND ".join(clauses) + " ORDER BY created DESC"
    return "ORDER BY created DESC"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class JiraAdapter:
    """Concrete adapter for a single Jira instance.

    Parameters
    ----------
    server_url:
        The Jira instance URL.
    credentials:
        Dict with ``username`` and ``api_token`` keys.
    instance_name:
        Logical name for error messages.
    """

    # Default type names per category (Jira conventions)
    _CATEGORY_TYPE_NAMES: dict[ItemCategory, str] = {
        ItemCategory.TASK: "Task",
        ItemCategory.STORY: "Story",
        ItemCategory.BUG: "Bug",
        ItemCategory.EPIC: "Epic",
        # SUBTASK resolved dynamically via createmeta
    }

    def __init__(
        self,
        server_url: str,
        credentials: dict[str, str],
        instance_name: str | None = None,
    ):
        self._server_url = server_url
        self._instance_name = instance_name
        self._client: Jira = create_jira_client(server_url, credentials)
        self._type_cache: dict[str, list[IssueTypeInfo]] = {}

    @property
    def server_url(self) -> str:
        return self._server_url

    # -- Operations --------------------------------------------------------

    def get_item(self, key: str) -> WorkItem:
        """Retrieve a work item by key."""
        try:
            issue = self._client.issue(key)
            if isinstance(issue, str) or not issue:
                raise ItemNotFound(key=key, instance=self._instance_name)
            return normalize_issue(issue)
        except (ItemNotFound, WorkTrackingError):
            raise
        except Exception as exc:
            raise _translate_error(exc, self._instance_name) from exc

    # -- Type resolution ---------------------------------------------------

    def _get_project_types(self, project_key: str) -> list[IssueTypeInfo]:
        """Return issue types for a project, using cache when available."""
        if project_key in self._type_cache:
            return self._type_cache[project_key]
        types = self.get_project_issue_types(project_key)
        self._type_cache[project_key] = types
        log.debug("Cached %d issue types for project %s", len(types), project_key)
        return types

    def _resolve_issue_type(self, request: CreateItemRequest) -> str | None:
        """Map an ItemCategory to a Jira issue type name.

        Returns the resolved type name, or ``None`` for SUBTASK when
        the project has no subtask type (signals the fallback strategy).
        """
        category = request.item_type
        project_types = self._get_project_types(request.project)
        available_names = {t.name.lower(): t.name for t in project_types}

        if category is ItemCategory.SUBTASK:
            # Find the project's subtask type by metadata flag
            for t in project_types:
                if t.subtask:
                    log.debug("Resolved SUBTASK -> %r for project %s", t.name, request.project)
                    return t.name
            # No subtask type available -- signal fallback
            log.info(
                "Project %s has no subtask type; will create Task + CHILD_OF link",
                request.project,
            )
            return None

        # Standard categories: look for exact match, fall back to Task
        preferred = self._CATEGORY_TYPE_NAMES.get(category, "Task")
        if preferred.lower() in available_names:
            resolved = available_names[preferred.lower()]
            log.debug("Resolved %s -> %r for project %s", category.name, resolved, request.project)
            return resolved

        # Fallback to Task
        log.info(
            "Type %r not available in project %s; falling back to Task",
            preferred,
            request.project,
        )
        return available_names.get("task", "Task")

    # -- CRUD operations ---------------------------------------------------

    def create_item(self, request: CreateItemRequest) -> ItemIdentifier:
        """Create a new work item and return its identifier.

        Resolves ``request.item_type`` (an ``ItemCategory``) to the
        correct Jira issue type name. When SUBTASK is requested but
        the project has no subtask type, creates a Task and adds a
        CHILD_OF link to ``request.parent_key``.
        """
        resolved_type = self._resolve_issue_type(request)
        use_link_fallback = resolved_type is None

        if use_link_fallback:
            # SUBTASK fallback: create as Task, link later
            resolved_type = "Task"

        fields: dict = {
            "project": {"key": request.project},
            "summary": request.title,
            "issuetype": {"name": resolved_type},
        }
        if request.description:
            fields["description"] = request.description
        if request.priority:
            fields["priority"] = {"name": request.priority}
        if request.labels:
            fields["labels"] = list(request.labels)
        if request.parent_key and not use_link_fallback:
            fields["parent"] = {"key": request.parent_key}

        try:
            result = self._client.create_issue(fields=fields)
            issue_key = result.get("key", "")
            issue_id = str(result.get("id", ""))

            if request.assignee_id:
                self._client.assign_issue(issue_key, request.assignee_id)

            # SUBTASK fallback: add CHILD_OF link to parent
            if use_link_fallback and request.parent_key:
                log.info(
                    "Adding CHILD_OF link from %s to parent %s (subtask fallback)",
                    issue_key,
                    request.parent_key,
                )
                self.link_items(issue_key, request.parent_key, LinkType.CHILD_OF)

            return ItemIdentifier(key=issue_key, id=issue_id)
        except WorkTrackingError:
            raise
        except Exception as exc:
            raise _translate_error(exc, self._instance_name) from exc

    def download_attachment(self, attachment_id: str) -> AttachmentContent:
        """Download the content of an attachment by its ID.

        Uses the Jira REST API to fetch attachment metadata and content.
        Returns an ``AttachmentContent`` with the complete file bytes
        and associated ``ItemAttachment`` metadata.
        """
        try:
            # Fetch attachment metadata
            raw_meta = self._client.get(f"rest/api/2/attachment/{attachment_id}")
            if not isinstance(raw_meta, dict):
                raise ItemNotFound(key=attachment_id, instance=self._instance_name)

            metadata = normalize_attachment(raw_meta)

            # Fetch the actual content bytes
            content_url = raw_meta.get("content", "")
            if not content_url:
                raise WorkTrackingError(
                    f"Attachment {attachment_id} has no content URL",
                    instance=self._instance_name,
                )

            response = self._client._session.get(content_url)
            response.raise_for_status()

            log.debug(
                "Downloaded attachment %s (%s, %d bytes)",
                attachment_id,
                metadata.filename,
                len(response.content),
            )

            return AttachmentContent(metadata=metadata, data=response.content)
        except (ItemNotFound, WorkTrackingError):
            raise
        except Exception as exc:
            raise _translate_error(exc, self._instance_name) from exc

    def attach_file(self, key: str, filename: str, content: bytes) -> ItemAttachment:
        """Attach a file to a work item.

        Uses Jira's POST /rest/api/2/issue/{key}/attachments endpoint
        with multipart/form-data encoding. Returns metadata for the
        newly created attachment.
        """
        if not filename or not filename.strip():
            raise WorkTrackingError(
                f"cannot attach file to {key}: filename must not be empty",
                instance=self._instance_name,
            )
        if not content:
            raise WorkTrackingError(
                f"cannot attach empty file: {filename}",
                instance=self._instance_name,
            )

        try:
            url = f"{self._server_url}/rest/api/2/issue/{key}/attachments"
            response = self._client._session.post(
                url,
                headers={"X-Atlassian-Token": "no-check"},
                files={"file": (filename, io.BytesIO(content))},
            )
            response.raise_for_status()

            attachments = response.json()
            if not attachments or not isinstance(attachments, list):
                raise WorkTrackingError(
                    f"unexpected response when attaching {filename} to {key}",
                    instance=self._instance_name,
                )

            log.debug(
                "Attached %s to %s (%d bytes)",
                filename,
                key,
                len(content),
            )

            return normalize_attachment(attachments[0])
        except (ItemNotFound, WorkTrackingError):
            raise
        except Exception as exc:
            raise _translate_error(exc, self._instance_name) from exc

    def get_project_issue_types(self, project: str) -> list[IssueTypeInfo]:
        """Return the issue types available in a project."""
        try:
            # Try createmeta endpoint (works on Jira Cloud and Server)
            createmeta_url = f"rest/api/2/issue/createmeta?projectKeys={project}&expand=projects.issuetypes"
            createmeta = self._client.get(createmeta_url)
            issue_types = []
            if isinstance(createmeta, dict):
                projects = createmeta.get("projects", [])
                if projects:
                    issue_types = projects[0].get("issuetypes", [])
            return [normalize_issue_type(it) for it in issue_types]
        except WorkTrackingError:
            raise
        except Exception as exc:
            raise _translate_error(exc, self._instance_name) from exc

    def get_link_types(self) -> list[LinkTypeInfo]:
        """Return the link types available on this Jira instance."""
        try:
            result = self._client.get("rest/api/2/issueLinkType")
            link_types = []
            if isinstance(result, dict):
                link_types = result.get("issueLinkTypes", [])
            return [normalize_link_type_info(lt) for lt in link_types]
        except WorkTrackingError:
            raise
        except Exception as exc:
            raise _translate_error(exc, self._instance_name) from exc

    def add_comment(self, key: str, body: str) -> ItemComment:
        """Add a comment to a work item."""
        try:
            # atlassian-python-api's issue_add_comment returns the comment dict
            raw = self._client.issue_add_comment(key, body)
            return normalize_comment(raw)
        except WorkTrackingError:
            raise
        except Exception as exc:
            raise _translate_error(exc, self._instance_name) from exc

    def get_transitions(self, key: str) -> list[TransitionInfo]:
        """List available workflow transitions for a work item."""
        try:
            result = self._client.get_issue_transitions(key)
            transitions = result.get("transitions", []) if isinstance(result, dict) else result
            return [normalize_transition(t) for t in transitions]
        except WorkTrackingError:
            raise
        except Exception as exc:
            raise _translate_error(exc, self._instance_name) from exc

    def transition(self, key: str, transition_name: str) -> None:
        """Execute a workflow transition by name."""
        try:
            result = self._client.get_issue_transitions(key)
            transitions = result.get("transitions", []) if isinstance(result, dict) else result

            target_id = None
            for t in transitions:
                if t.get("name", "").lower() == transition_name.lower():
                    target_id = t.get("id")
                    break

            if target_id is None:
                raise InvalidTransition(
                    key=key,
                    transition=transition_name,
                    instance=self._instance_name,
                )

            self._client.set_issue_status(key, transition_name)
        except (InvalidTransition, WorkTrackingError):
            raise
        except Exception as exc:
            raise _translate_error(exc, self._instance_name) from exc

    def link_items(self, from_key: str, to_key: str, link_type: LinkType) -> None:
        """Create a typed relationship between two work items."""
        if link_type in (LinkType.BLOCKED_BY, LinkType.DUPLICATED_BY):
            jira_link_name = _DOMAIN_TO_JIRA_LINK[link_type]
            actual_from, actual_to = to_key, from_key
        else:
            jira_link_name = _DOMAIN_TO_JIRA_LINK.get(link_type, "Relates")
            actual_from, actual_to = from_key, to_key

        try:
            self._client.create_issue_link(
                {
                    "type": {"name": jira_link_name},
                    "outwardIssue": {"key": actual_to},
                    "inwardIssue": {"key": actual_from},
                }
            )
        except WorkTrackingError:
            raise
        except Exception as exc:
            raise _translate_error(exc, self._instance_name) from exc

    def search(
        self,
        criteria: SearchCriteria,
        offset: int = 0,
        limit: int = 50,
    ) -> SearchResult:
        """Search for work items matching the given criteria."""
        jql = _build_jql(criteria)
        try:
            result = self._client.jql(jql, start=offset, limit=limit)
            issues = result.get("issues", [])
            total = result.get("total", 0) or len(issues)
            items = tuple(normalize_issue(issue) for issue in issues)
            return SearchResult(
                items=items,
                total=total,
                offset=offset,
                limit=limit,
            )
        except WorkTrackingError:
            raise
        except Exception as exc:
            raise _translate_error(exc, self._instance_name) from exc

    # -- Project operations ------------------------------------------------

    def list_projects(self) -> list[ProjectInfo]:
        """Return all projects accessible on this Jira instance."""
        try:
            raw_projects = self._client.projects()
            if not isinstance(raw_projects, list):
                return []
            return [normalize_project(p) for p in raw_projects]
        except WorkTrackingError:
            raise
        except Exception as exc:
            raise _translate_error(exc, self._instance_name) from exc

    def get_project(self, key: str) -> ProjectInfo:
        """Retrieve a single project by key."""
        try:
            raw = self._client.project(key)
            if isinstance(raw, str) or not raw:
                raise ProjectNotFound(key=key, instance=self._instance_name)
            return normalize_project(raw)
        except (ProjectNotFound, WorkTrackingError):
            raise
        except Exception as exc:
            raise _translate_project_error(exc, key, self._instance_name) from exc

    def create_project(self, request: CreateProjectRequest) -> ProjectInfo:
        """Create a new project and return its details."""
        try:
            payload = {
                "key": request.key,
                "name": request.name,
                "projectTypeKey": request.project_type,
            }
            if request.description:
                payload["description"] = request.description
            if request.lead_account_id:
                payload["leadAccountId"] = request.lead_account_id

            result = self._client.post("rest/api/2/project", data=payload)

            # Jira returns minimal info on create; fetch full details
            created_key = ""
            if isinstance(result, dict):
                created_key = result.get("key", request.key)
            else:
                created_key = request.key

            return self.get_project(created_key)
        except (ProjectNotFound, WorkTrackingError):
            raise
        except Exception as exc:
            raise _translate_project_error(exc, request.key, self._instance_name) from exc

    def delete_project(self, key: str) -> None:
        """Delete a project by key. This operation is irreversible."""
        try:
            self._client.delete(f"rest/api/2/project/{key}")
            log.info("Deleted project %s on instance %s", key, self._instance_name)
        except WorkTrackingError:
            raise
        except Exception as exc:
            raise _translate_project_error(exc, key, self._instance_name) from exc
