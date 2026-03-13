# Technical Design: Work Tracking Domain + Jira Adapter

**Author**: Senior Engineer
**Date**: 2026-02-28
**Status**: Draft
**Prerequisite**: [Design Document](design.md), [Requirements](requirements.md)

---

## 1. Overview

This document bridges the architect's design to implementation. It covers:

- Domain layer implementation (models, errors, ports)
- The `WorkTrackingService` that implements both protocols
- The Jira adapter as the first platform backend
- Library choices, error mapping, and testing strategy

---

## 2. Technology Choices

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Jira client | `atlassian-python-api` | Mature, handles auth, pagination, field mapping. Lighter alternative (raw httpx) considered but the library saves significant boilerplate for field discovery, JQL, transitions. |
| HTTP fallback | `httpx` | Used only if jira lib proves insufficient for an edge case. Not a primary dependency. |
| Credentials | `python-dotenv` (existing) | Consistent with messaging domain pattern. Load from `~/.env`. |
| Concurrency | Plain dict for instance registry | Sequential access pattern. No async needed for request-response. Add locking if concurrent access proves necessary. |

### Why `atlassian-python-api` over raw HTTP

The Jira REST API has significant complexity around:
- Field ID discovery (custom fields have opaque IDs like `customfield_10014`)
- JQL query building and pagination
- Transition discovery and execution
- Issue link type resolution
- Attachment download (authenticated session reuse)

The library handles all of this. Raw httpx would require reimplementing
pagination, field mapping, and error handling from scratch with no added value.

---

## 3. File Structure

```
src/appif/
    domain/
        work_tracking/
            __init__.py          # Public exports
            models.py            # Frozen dataclasses (WorkItem, etc.)
            errors.py            # Exception hierarchy
            ports.py             # InstanceRegistry + WorkTracker protocols
            service.py           # WorkTrackingService (thin routing layer)
    adapters/
        jira/
            __init__.py          # Public exports
            _auth.py             # Credential validation, JIRA client creation
            _normalizer.py       # Jira issue JSON -> domain WorkItem
            adapter.py           # JiraAdapter (concrete class, includes retry)

tests/
    unit/
        test_work_tracking_models.py
        test_work_tracking_errors.py
        test_jira_normalizer.py
        test_jira_adapter.py
        test_work_tracking_service.py
    integration/
        test_jira_integration.py
```

**Simplifications vs. messaging adapter:**

- No separate `_rate_limiter.py` -- the `jira` library handles basic retries;
  error translation lives in `adapter.py` as a simple helper function
- No `_adapter.py` / `adapter.py` split -- one concrete class, no internal
  protocol
- No separate `service/` package -- `service.py` is a thin file alongside
  the domain types
- No `_auth.py` unit test -- credential validation is trivial (check two
  dict keys); tested as part of adapter tests

---

## 4. Domain Layer Implementation

### 4.1 models.py

All frozen dataclasses following messaging domain conventions. Key decisions:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class LinkType(Enum):
    BLOCKS = "blocks"
    BLOCKED_BY = "blocked_by"
    RELATES_TO = "relates_to"
    DUPLICATES = "duplicates"
    DUPLICATED_BY = "duplicated_by"
    PARENT_OF = "parent_of"
    CHILD_OF = "child_of"


@dataclass(frozen=True)
class ItemAuthor:
    id: str
    display_name: str


@dataclass(frozen=True)
class ItemComment:
    id: str
    author: ItemAuthor
    body: str
    created: datetime


@dataclass(frozen=True)
class ItemLink:
    link_type: LinkType
    target_key: str


@dataclass(frozen=True)
class ItemAttachment:
    """Metadata only -- no platform URLs. Use download_attachment() for content."""
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    created: datetime
    author: ItemAuthor | None = None


@dataclass(frozen=True)
class AttachmentContent:
    """Complete download result. Future streaming via separate method."""
    metadata: ItemAttachment
    data: bytes


@dataclass(frozen=True)
class WorkItem:
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


@dataclass(frozen=True)
class ItemIdentifier:
    key: str
    id: str


@dataclass(frozen=True)
class CreateItemRequest:
    project: str
    title: str
    item_type: str
    description: str = ""
    parent_key: str | None = None
    labels: tuple[str, ...] = ()
    priority: str | None = None
    assignee_id: str | None = None


@dataclass(frozen=True)
class TransitionInfo:
    id: str
    name: str


@dataclass(frozen=True)
class SearchCriteria:
    project: str | None = None
    status: str | None = None
    assignee_id: str | None = None
    labels: tuple[str, ...] = ()
    query: str | None = None


@dataclass(frozen=True)
class SearchResult:
    items: tuple[WorkItem, ...] = ()
    total: int = 0
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True)
class InstanceInfo:
    name: str
    platform: str
    server_url: str
    is_default: bool = False
```

**Design decisions:**

- `WorkItem` uses `tuple` for collection fields (immutable, hashable)
- Required fields first, optional fields with defaults after
- `CreateItemRequest` requires `project`, `title`, `item_type`; everything
  else is optional
- `SearchCriteria` is all-optional (empty criteria returns everything in
  the project)
- `ItemAttachment` exposes metadata only; platform content URLs are never
  exposed. Callers use `download_attachment(id)` for file content.
- `AttachmentContent` returns complete bytes. Future streaming support will
  be a separate method (e.g. `stream_attachment()`) to avoid breaking
  existing callers.

### 4.2 errors.py

Follows the messaging domain pattern. All errors carry an `instance` field.

```python
class WorkTrackingError(Exception):
    """Base error for all work tracking failures."""

    def __init__(self, message: str = "", instance: str | None = None):
        self.instance = instance
        prefix = f"[{instance}] " if instance else ""
        super().__init__(f"{prefix}{message}" if message else f"{prefix}work tracking error")


class ItemNotFound(WorkTrackingError):
    def __init__(self, key: str, instance: str | None = None):
        self.key = key
        super().__init__(f"item not found: {key}", instance)


class PermissionDenied(WorkTrackingError):
    def __init__(self, reason: str = "", instance: str | None = None):
        self.reason = reason
        msg = "permission denied"
        if reason:
            msg += f": {reason}"
        super().__init__(msg, instance)


class InvalidTransition(WorkTrackingError):
    def __init__(self, key: str, transition: str, instance: str | None = None):
        self.key = key
        self.transition = transition
        super().__init__(
            f"invalid transition '{transition}' for item {key}", instance
        )


class ConnectionFailure(WorkTrackingError):
    def __init__(self, reason: str = "", instance: str | None = None):
        self.reason = reason
        msg = "connection failure"
        if reason:
            msg += f": {reason}"
        super().__init__(msg, instance)


class RateLimited(WorkTrackingError):
    def __init__(self, retry_after: float | None = None, instance: str | None = None):
        self.retry_after = retry_after
        msg = "rate limited"
        if retry_after is not None:
            msg += f" (retry after {retry_after}s)"
        super().__init__(msg, instance)


class InstanceNotFound(WorkTrackingError):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"instance not found: {name}")


class NoDefaultInstance(WorkTrackingError):
    def __init__(self):
        super().__init__("no default instance configured")


class InstanceAlreadyRegistered(WorkTrackingError):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"instance already registered: {name}")
```

### 4.3 ports.py

Two Protocol classes. The `WorkTracker` protocol uses `instance: str | None = None`
for the optional instance parameter.

```python
from typing import Protocol

class InstanceRegistry(Protocol):
    def register(
        self,
        name: str,
        platform: str,
        server_url: str,
        credentials: dict[str, str],
    ) -> None: ...

    def unregister(self, name: str) -> None: ...

    def list_instances(self) -> list[InstanceInfo]: ...

    def set_default(self, name: str) -> None: ...

    def get_default(self) -> str | None: ...


class WorkTracker(Protocol):
    def get_item(self, key: str, *, instance: str | None = None) -> WorkItem: ...

    def create_item(
        self, request: CreateItemRequest, *, instance: str | None = None
    ) -> ItemIdentifier: ...

    def add_comment(
        self, key: str, body: str, *, instance: str | None = None
    ) -> ItemComment: ...

    def get_transitions(
        self, key: str, *, instance: str | None = None
    ) -> list[TransitionInfo]: ...

    def transition(
        self, key: str, transition_name: str, *, instance: str | None = None
    ) -> None: ...

    def link_items(
        self,
        from_key: str,
        to_key: str,
        link_type: LinkType,
        *,
        instance: str | None = None,
    ) -> None: ...

    def search(
        self,
        criteria: SearchCriteria,
        *,
        offset: int = 0,
        limit: int = 50,
        instance: str | None = None,
    ) -> SearchResult: ...

    def download_attachment(
        self,
        attachment_id: str,
        *,
        instance: str | None = None,
    ) -> AttachmentContent: ...
```

**Design decisions:**

- `instance` is keyword-only to prevent accidental positional use
- Methods return domain types, never platform types
- `add_comment` returns the created `ItemComment` (with server-assigned ID
  and timestamp)
- `transition()` returns None (side effect only)
- `search()` pagination defaults to offset=0, limit=50
- `download_attachment()` returns `AttachmentContent` with complete bytes;
  the `attachment_id` comes from `ItemAttachment.id` on a retrieved `WorkItem`

---

## 5. WorkTrackingService Implementation

Lives in `src/appif/domain/work_tracking/service.py`. Implements both
`InstanceRegistry` and `WorkTracker`. This is a thin routing layer, not a
separate architectural layer.

### Instance Resolution Logic

```python
def _resolve(self, instance: str | None) -> JiraAdapter:
    name = instance or self._default
    if name is None:
        raise NoDefaultInstance()
    adapter = self._instances.get(name)
    if adapter is None:
        raise InstanceNotFound(name)
    return adapter
```

### Platform Dispatch

When `register()` is called with `platform="jira"`, the service creates a
`JiraAdapter`. Platform dispatch is a simple `if/elif`:

```python
if platform == "jira":
    adapter = JiraAdapter(server_url, credentials)
else:
    raise WorkTrackingError(f"unsupported platform: {platform}")
```

No factory dict, no plugin registry. When a second platform is added,
refactor if the pattern warrants it.

### Thread Safety

Not implemented initially. Agents call operations sequentially. If
concurrent access becomes a real need, add a `threading.Lock` at that
point.

---

## 6. Jira Adapter Internals

### 6.1 _auth.py

Validates credentials and creates a `jira.JIRA` client instance.

**Required credentials keys:**
- `username` -- Jira username (typically email for Cloud)
- `api_token` -- Personal API token
- Server URL is passed separately

```python
def create_jira_client(server_url: str, credentials: dict[str, str]) -> jira.JIRA:
    username = credentials.get("username")
    api_token = credentials.get("api_token")
    if not username or not api_token:
        raise PermissionDenied("missing username or api_token in credentials")
    return jira.JIRA(
        server=server_url,
        basic_auth=(username, api_token),
    )
```

### 6.2 _normalizer.py

Maps Jira issue objects to domain `WorkItem`. This is the core translation layer.

**Key mappings:**

| Jira field | Domain field | Notes |
|------------|-------------|-------|
| `issue.key` | `key` | Direct |
| `issue.id` | `id` | Direct (string) |
| `issue.fields.summary` | `title` | Direct |
| `issue.fields.description` | `description` | Convert ADF to plain text, or use raw if string |
| `issue.fields.status.name` | `status` | Status display name |
| `issue.fields.priority.name` | `priority` | May be None |
| `issue.fields.issuetype.name` | `item_type` | Lowercased |
| `issue.fields.labels` | `labels` | Direct list to tuple |
| `issue.fields.assignee` | `assignee` | Map to `ItemAuthor` or None |
| `issue.fields.reporter` | `reporter` | Map to `ItemAuthor` or None |
| `issue.fields.created` | `created` | Parse ISO 8601 |
| `issue.fields.updated` | `updated` | Parse ISO 8601 |
| `issue.fields.parent.key` | `parent_key` | May not exist |
| `issue.fields.subtasks[].key` | `sub_item_keys` | List of child keys |
| `issue.fields.issuelinks` | `links` | Map link types (see below) |
| `issue.fields.comment.comments` | `comments` | Map each to `ItemComment` |
| `issue.fields.attachment` | `attachments` | Map each to `ItemAttachment` |

**Link type mapping:**

Jira link types are freeform strings. The normalizer maps known patterns:

| Jira link type name | Direction | Domain LinkType |
|--------------------|-----------|-----------------|
| "Blocks" | outward | `BLOCKS` |
| "Blocks" | inward | `BLOCKED_BY` |
| "Duplicate" | outward | `DUPLICATES` |
| "Duplicate" | inward | `DUPLICATED_BY` |
| "Relates" | any | `RELATES_TO` |
| (unknown) | any | `RELATES_TO` (fallback) |

Parent/child links are derived from `parent` and `subtasks` fields, not
from issue links.

**Attachment mapping:**

| Jira field | Domain field | Notes |
|------------|-------------|-------|
| `attachment[].id` | `ItemAttachment.id` | String |
| `attachment[].filename` | `filename` | Direct |
| `attachment[].mimeType` | `mime_type` | Default: "application/octet-stream" |
| `attachment[].size` | `size_bytes` | Coerced to int |
| `attachment[].created` | `created` | Parse ISO 8601 |
| `attachment[].author` | `author` | Map to `ItemAuthor` or None |
| `attachment[].content` | (excluded) | Platform URL; use `download_attachment()` |

### 6.3 adapter.py

The `JiraAdapter` is a single concrete class. It holds a `jira.JIRA` client
and delegates normalization to `_normalizer`. Error translation is a helper
function in the same file (no separate rate limiter module).

**Error translation** maps HTTP status codes to domain exceptions:
- 401/403 -> `PermissionDenied`
- 404 -> `ItemNotFound`
- 429 -> `RateLimited`
- 5xx / network errors -> `ConnectionFailure`

The `jira` library handles basic retries internally. If additional retry
logic becomes necessary, add it here rather than in a separate module.

**Key methods:**

- `get_item(key)` -- `jira_client.issue(key, expand="renderedFields,names")`,
  then normalize
- `create_item(request)` -- Build fields dict, call `jira_client.create_issue()`,
  handle parent via `parent` field (for sub-tasks) or epic link
  (for epic children)
- `add_comment(key, body)` -- `jira_client.add_comment(key, body)`, normalize
  the returned comment
- `get_transitions(key)` -- `jira_client.transitions(key)`, map to
  `TransitionInfo` list
- `transition(key, name)` -- Find transition ID by name, call
  `jira_client.transition_issue(key, transition_id)`
- `link_items(from_key, to_key, link_type)` -- Map `LinkType` back to Jira
  link type name, call `jira_client.create_issue_link()`
- `search(criteria, offset, limit)` -- Build JQL from `SearchCriteria`,
  call `jira_client.search_issues(jql, startAt=offset, maxResults=limit)`,
  normalize each result
- `download_attachment(attachment_id)` -- Fetch attachment metadata via
  `GET /rest/api/2/attachment/{id}`, normalize to `ItemAttachment`, then
  fetch the content bytes from the `content` URL using the adapter's
  authenticated session. Returns `AttachmentContent(metadata, data)`.
  Raises `ItemNotFound` if the attachment ID does not exist.

**JQL building:**

```python
def _build_jql(criteria: SearchCriteria) -> str:
    clauses = []
    if criteria.project:
        clauses.append(f'project = "{criteria.project}"')
    if criteria.status:
        clauses.append(f'status = "{criteria.status}"')
    if criteria.assignee_id:
        clauses.append(f'assignee = "{criteria.assignee_id}"')
    if criteria.labels:
        for label in criteria.labels:
            clauses.append(f'labels = "{label}"')
    if criteria.query:
        clauses.append(f'text ~ "{criteria.query}"')
    return " AND ".join(clauses) if clauses else "ORDER BY created DESC"
```

---

## 7. Environment Variable Auto-Registration

At `WorkTrackingService` initialization, check for env vars:

```python
APPIF_JIRA_SERVER_URL = os.environ.get("APPIF_JIRA_SERVER_URL")
APPIF_JIRA_USERNAME = os.environ.get("APPIF_JIRA_USERNAME")
APPIF_JIRA_API_TOKEN = os.environ.get("APPIF_JIRA_API_TOKEN")
```

If all three are present, auto-register:

```python
self.register(
    name="default",
    platform="jira",
    server_url=APPIF_JIRA_SERVER_URL,
    credentials={"username": APPIF_JIRA_USERNAME, "api_token": APPIF_JIRA_API_TOKEN},
)
self.set_default("default")
```

This keeps the zero-configuration startup path working for the single-instance
case.

---

## 8. Testing Strategy

### 8.1 Unit Tests (no I/O)

**test_work_tracking_models.py:**
- Frozen dataclass construction with all fields
- Default values (empty tuples, None optionals)
- Immutability (cannot assign to frozen fields)
- LinkType enum values

**test_work_tracking_errors.py:**
- Each error type constructs with expected message format
- `instance` field is populated correctly
- Error hierarchy (all inherit from `WorkTrackingError`)

**test_jira_normalizer.py:**
- Normalize a complete Jira issue dict to `WorkItem`
- Handle missing optional fields (no assignee, no parent, no links)
- Map each Jira link type to correct `LinkType`
- Unknown link types fall back to `RELATES_TO`
- Comments are ordered by time
- ISO 8601 date parsing
- `normalize_attachment`: full fields, missing fields, defaults, size coercion
- Platform content URL excluded from domain object
- `normalize_project`: Jira project dict -> `ProjectInfo` domain type
  - Fields: key, name, description (None -> ""), lead (via `_to_author`), projectTypeKey, self URL
  - Handles missing lead, missing description, Jira Server `key` fallback for user IDs
- Issue with attachments populates `WorkItem.attachments`
- Issue without attachments or empty list produces empty tuple

**test_jira_adapter.py:**
- Mock `jira.JIRA` client
- `get_item` calls `client.issue()` and normalizes
- `create_item` builds correct fields dict
- `transition` finds transition by name, raises `InvalidTransition` if not found
- `search` builds correct JQL from criteria
- Error mapping: 404 -> `ItemNotFound`, 403 -> `PermissionDenied`, etc.

**test_work_tracking_service.py:**
- Register/unregister instances
- Default instance management
- Instance resolution: explicit, default, missing, no default
- Operations delegate to correct adapter

### 8.2 Integration Tests (requires live Jira)

**test_jira_integration.py** (marked with `@pytest.mark.integration`):
- Read a known issue
- Create an issue and verify it exists
- Add a comment and verify it appears
- Transition an issue and verify new status
- Search by project
- Link two issues

These require `APPIF_JIRA_SERVER_URL`, `APPIF_JIRA_USERNAME`, and
`APPIF_JIRA_API_TOKEN` to be set, plus a test project in Jira.

---

## 9. Dependencies

Add to `pyproject.toml`:

```toml
dependencies = [
    # ... existing deps ...
    "jira",
]
```

The `jira` library transitively brings in `requests`, `defusedxml`, and
`packaging`. These are well-established, stable dependencies.

---

## 10. Configuration Updates

### .env.example additions

```
# Jira Work Tracking (default instance)
APPIF_JIRA_SERVER_URL=https://your-domain.atlassian.net
APPIF_JIRA_USERNAME=your-email@example.com
APPIF_JIRA_API_TOKEN=your-api-token
```

### ADAPTERS.md addition

Document the Jira adapter alongside existing Gmail, Outlook, and Slack
entries with:
- Environment variables required
- Supported operations
- Multi-instance registration example
- Known limitations

---

## 11a. Project Operations (v0.7.0)

### Domain Types

- `ProjectInfo`: frozen dataclass with key, name, description, lead, project_type, url
- `CreateProjectRequest`: Pydantic BaseModel (frozen) with key validation (2-10 uppercase alphanumeric)
- `ProjectNotFound`: error subclass for project-specific 404s

### Jira Adapter Methods

| Method | Jira API | Notes |
|--------|----------|-------|
| `list_projects()` | `GET /rest/api/2/project` via `self._client.projects()` | Returns list of ProjectInfo |
| `get_project(key)` | `GET /rest/api/2/project/{key}` via `self._client.project(key)` | Raises ProjectNotFound on 404 |
| `create_project(request)` | `POST /rest/api/2/project` via `self._client.post()` | Fetches full details after create |
| `delete_project(key)` | `DELETE /rest/api/2/project/{key}` via `self._client.delete()` | Irreversible; requires admin permissions |

### Error Translation

Project operations use `_translate_project_error()` which maps 404 to `ProjectNotFound`
(not `ItemNotFound`), keeping error semantics accurate. Other HTTP errors follow the
same pattern as `_translate_error()`.

### Service Routing

Four thin passthrough methods on `WorkTrackingService`, following the existing
`_resolve(instance).method()` pattern.

---

## 11. Construction Order

Following RULES.md:

1. Domain models (`models.py`) -- no I/O
2. Domain errors (`errors.py`) -- no I/O
3. Domain ports (`ports.py`) -- protocol definitions
4. Domain `__init__.py` -- public exports
5. Domain unit tests (`test_work_tracking_models.py`, `test_work_tracking_errors.py`)
6. Jira `_auth.py` -- credential validation
7. Jira `_normalizer.py` -- JSON to domain mapping
8. Jira `adapter.py` -- concrete adapter with error translation
9. Jira unit tests (`test_jira_normalizer.py`, `test_jira_adapter.py`)
10. `WorkTrackingService` (`service.py`) -- thin routing layer
11. Service unit tests (`test_work_tracking_service.py`)
12. Integration tests (gated by marker)
13. `pyproject.toml`, `.env.example`, `ADAPTERS.md` updates
