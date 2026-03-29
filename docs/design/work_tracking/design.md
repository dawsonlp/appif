# Design Document: Work Tracking Domain

**Author**: Architect
**Date**: 2026-02-28
**Status**: Draft
**Domain**: Work Tracking (Jira, GitHub Issues, Linear, Azure DevOps)

---

## 1. Problem Statement

Agents that solve problems need to read work items, create sub-items, post
status updates, track relationships, and transition workflow states. Today
there is no normalized interface for this. Each agent would need to integrate
directly with a work tracking platform's API, coupling agent logic to a
specific vendor.

This domain provides a stable, platform-agnostic interface to work tracking
systems. An agent speaks in domain terms. Platform specifics are encapsulated
in adapters. Swapping from Jira to GitHub Issues requires a configuration
change, not a code change.

---

## 2. Core Principle

**The work tracking domain is a request-response adapter.**

It does not interpret priority, infer relationships, or decide workflow
transitions. It does perform **platform translation** -- mapping domain
vocabulary to platform-specific strings and mechanics. This includes issue
type resolution, link type mapping, and error translation.

The domain:

- Reads normalized work items from an external system
- Creates, updates, and transitions work items
- Manages relationships between items
- Searches for items by structured criteria
- Translates domain vocabulary to platform-specific representations

Nothing more.

---

## 3. Relationship to Messaging Domain

The messaging domain handles bidirectional, event-driven transport (Slack,
Email, Teams). The work tracking domain handles request-response operations
against project management systems.

The two domains share architectural principles but differ in interaction
pattern:

| Aspect | Messaging | Work Tracking |
|--------|-----------|---------------|
| Interaction | Event-driven (listeners) | Request-response (calls) |
| Lifecycle | Long-lived connection | Per-request or pooled |
| Concurrency | Connector owns event loop | Caller-driven |
| Canonical type | `MessageEvent` | `WorkItem` |

They share:

- Frozen dataclasses for all domain types
- Protocol classes for adapter interfaces
- Platform adapters under `appif.adapters.<platform>/`
- Domain-scoped error hierarchies
- No platform SDK types in the domain model

---

## 4. Scope

### In Scope

| Responsibility | Description |
|----------------|-------------|
| **Read work items** | Retrieve normalized items by key |
| **Create work items** | Create items with type, description, assignee, labels |
| **Create sub-items** | Create child items using platform parent-child mechanisms |
| **Post comments** | Add comments to existing items |
| **Transition status** | Discover and execute workflow transitions |
| **Link items** | Create typed relationships between items |
| **Search items** | Query by project, status, assignee, labels, free text |
| **List projects** | Return all accessible projects on the platform |
| **Get project details** | Retrieve a single project by key |
| **Create projects** | Create new projects with key, name, type, lead |
| **Delete projects** | Remove a project by key (irreversible) |
| **Multi-instance management** | Register, list, remove platform instances at runtime |
| **Upload attachments** | Attach caller-provided file content to a work item (W13, added 2026-03-29) |
| **Authentication** | Per-instance credential management |
| **Error translation** | Map platform errors to domain exceptions |

### Out of Scope

| Concern | Why excluded |
|---------|-------------|
| Event-driven monitoring | Different interaction pattern; may reuse Connector model later |
| Attachment delete/replace/versioning | Write-side management beyond upload; upload is supported (W13) |
| Sprint/iteration management | Administrative, not agent workflow |
| Board/view management | UI concern |
| User management | Administrative |

---

## 5. Canonical Domain Model

All types are platform-agnostic. No Jira field names, GitHub API conventions,
or Linear-specific concepts appear in the domain model. All types are frozen
dataclasses.

### WorkItem

The normalized representation of a tracked work item.

| Field | Type | Description |
|-------|------|-------------|
| `key` | `str` | Platform-scoped human-readable identifier (e.g., "PROJ-123") |
| `id` | `str` | Platform-scoped internal identifier |
| `title` | `str` | Summary/title |
| `description` | `str` | Body text (plain text or markup) |
| `status` | `str` | Current workflow status name |
| `priority` | `str or None` | Priority level name |
| `item_type` | `str` | Type: task, story, bug, epic, etc. |
| `labels` | `tuple[str, ...]` | Tags/labels |
| `assignee` | `ItemAuthor or None` | Assigned person |
| `reporter` | `ItemAuthor or None` | Person who created the item |
| `created` | `datetime` | Creation timestamp |
| `updated` | `datetime` | Last modification timestamp |
| `parent_key` | `str or None` | Parent item key if this is a sub-item |
| `sub_item_keys` | `tuple[str, ...]` | Child item keys |
| `links` | `tuple[ItemLink, ...]` | Relationships to other items |
| `comments` | `tuple[ItemComment, ...]` | Comments, ordered by time |
| `attachments` | `tuple[ItemAttachment, ...]` | File attachments with metadata |

### ItemAuthor

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Platform-scoped user identifier |
| `display_name` | `str` | Human-readable name |

### ItemComment

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Comment identifier |
| `author` | `ItemAuthor` | Who posted the comment |
| `body` | `str` | Comment text |
| `created` | `datetime` | When the comment was posted |

### ItemLink

| Field | Type | Description |
|-------|------|-------------|
| `link_type` | `LinkType` | Normalized relationship type |
| `target_key` | `str` | Key of the related item |

### LinkType

Normalized to a small vocabulary that maps across platforms:

| Value | Meaning |
|-------|---------|
| `BLOCKS` | This item blocks the target |
| `BLOCKED_BY` | This item is blocked by the target |
| `RELATES_TO` | General relationship |
| `DUPLICATES` | This item duplicates the target |
| `DUPLICATED_BY` | This item is duplicated by the target |
| `PARENT_OF` | This item is parent of the target |
| `CHILD_OF` | This item is child of the target |

### ItemCategory

> **Change**: Added in v0.4.0. See [ADR-001](../../adr/001-adapter-resolved-issue-types.md).

Domain-level vocabulary for work item types. Callers express intent using
these values. Adapters translate to platform-specific type names.

| Value | Meaning | Jira mapping (typical) |
|-------|---------|------------------------|
| `TASK` | A unit of work | "Task" |
| `SUBTASK` | A child unit under a parent item | Project's subtask type (auto-discovered) |
| `STORY` | A user-facing feature or requirement | "Story" (fallback: "Task") |
| `BUG` | A defect report | "Bug" (fallback: "Task") |
| `EPIC` | A large body of work containing other items | "Epic" (fallback: "Task") |

This follows the same architectural pattern as `LinkType`: domain vocabulary
in, platform strings out. The adapter handles resolution and fallback.

### ItemAttachment

> **Added**: v0.6.0.

Metadata for a file attached to a work item. Contains descriptive
information only -- no platform URLs or download mechanics leak through
this type. Use `WorkTracker.download_attachment()` to retrieve file content.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Opaque, stable attachment identifier |
| `filename` | `str` | Original filename (e.g. "requirements.docx") |
| `mime_type` | `str` | MIME type (e.g. "application/pdf") |
| `size_bytes` | `int` | File size in bytes |
| `created` | `datetime` | When the attachment was added |
| `author` | `ItemAuthor or None` | Who uploaded the attachment |

### AttachmentContent

> **Added**: v0.6.0.

Result of downloading an attachment. Contains the complete file bytes and
associated metadata. Future versions may introduce a streaming variant via
a separate method; this type always represents fully-buffered content.

| Field | Type | Description |
|-------|------|-------------|
| `metadata` | `ItemAttachment` | Attachment metadata |
| `data` | `bytes` | Complete file content |

### ItemIdentifier

Returned after item creation.

| Field | Type | Description |
|-------|------|-------------|
| `key` | `str` | Human-readable key |
| `id` | `str` | Internal identifier |

### CreateItemRequest

> **Change**: Modified in v0.4.0. See [ADR-001](../../adr/001-adapter-resolved-issue-types.md).

Input for item creation. Not a domain entity -- a command object.
Implemented as a Pydantic `BaseModel` (frozen) for input validation
and JSON schema generation. Domain entities remain frozen dataclasses.

| Field | Type | Description |
|-------|------|-------------|
| `project` | `str` | Project/container key |
| `title` | `str` | Summary |
| `description` | `str` | Body text |
| `item_type` | `ItemCategory` | Domain-level item category (required) |
| `parent_key` | `str or None` | Parent item for sub-item creation |
| `labels` | `tuple[str, ...]` | Tags |
| `priority` | `str or None` | Priority level |
| `assignee_id` | `str or None` | User ID to assign |

**Validation rules:**

- `item_type` is required and must be a valid `ItemCategory` value
- When `item_type` is `SUBTASK`, `parent_key` is required
- The adapter translates `ItemCategory` to the platform-specific type string

**Why Pydantic for command objects:**

Command objects are user-facing input. Pydantic provides enum validation,
cross-field validation, clear error messages, and JSON schema generation
(`model_json_schema()`) for OpenAPI and LLM tool definitions. Domain
entities (WorkItem, ItemComment, etc.) remain frozen dataclasses because
they are adapter-produced output that is already normalized.

### TransitionInfo

An available workflow transition.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Transition identifier |
| `name` | `str` | Human-readable transition name |

### SearchCriteria

Structured search parameters.

| Field | Type | Description |
|-------|------|-------------|
| `project` | `str or None` | Filter by project |
| `status` | `str or None` | Filter by status |
| `assignee_id` | `str or None` | Filter by assignee |
| `labels` | `tuple[str, ...]` | Filter by labels |
| `query` | `str or None` | Free-text search |

### SearchResult

Paginated search response.

| Field | Type | Description |
|-------|------|-------------|
| `items` | `tuple[WorkItem, ...]` | Matching items |
| `total` | `int` | Total matches (may exceed returned count) |
| `offset` | `int` | Starting position of this page |
| `limit` | `int` | Page size |

### ProjectInfo

> **Added**: v0.7.0.

Metadata about a project in the work tracking platform. Returned by
`list_projects()` and `get_project()`.

| Field | Type | Description |
|-------|------|-------------|
| `key` | `str` | Project key (e.g. "PROJ") |
| `name` | `str` | Human-readable project name |
| `description` | `str` | Project description (may be empty) |
| `lead` | `ItemAuthor or None` | Project lead |
| `project_type` | `str` | Platform-specific project type (e.g. "software") |
| `url` | `str` | API URL for the project |

### CreateProjectRequest

> **Added**: v0.7.0.

Input for project creation. A command object (Pydantic `BaseModel`, frozen).

| Field | Type | Description |
|-------|------|-------------|
| `key` | `str` | Project key (2-10 uppercase alphanumeric, starts with letter) |
| `name` | `str` | Project name |
| `project_type` | `str` | Platform project type (default: "software") |
| `description` | `str` | Project description (default: "") |
| `lead_account_id` | `str or None` | Platform user ID for the project lead |

**Validation rules:**

- `key` must be 2-10 uppercase alphanumeric characters starting with a letter
- `name` is required

### InstanceInfo

Metadata about a registered platform instance (returned by the registry).

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Unique instance name |
| `platform` | `str` | Platform type: "jira", "github", etc. |
| `server_url` | `str` | Server base URL |
| `is_default` | `bool` | Whether this is the default instance |

---

## 6. Protocol Interfaces

Two protocol interfaces separate administrative concerns from operational
concerns. Agents use `WorkTracker`. Orchestrators and setup code use
`InstanceRegistry`.

### InstanceRegistry (Administrative)

Manages which platform instances are available.

| Method | Description |
|--------|-------------|
| `register(name, platform, server_url, credentials)` | Register a new instance with a unique name |
| `unregister(name)` | Remove an instance and clean up its state |
| `list_instances()` | Return all registered instances (no credentials) |
| `set_default(name)` | Designate an instance as the default |
| `get_default()` | Return the default instance name, or None |

**Rules:**

- `register()` with a name that already exists raises an error
- `unregister()` the default instance clears the default
- Credentials are stored in memory only, never persisted by the library
- Each registered instance has independent auth, rate limiting, connection state

### WorkTracker (Operational)

The operational interface agents use. Every method takes an optional
`instance` parameter. If omitted, the default instance is used. If no
default is set and `instance` is omitted, an error is raised.

| Method | Description |
|--------|-------------|
| `get_item(key, instance?)` | Retrieve a work item by key |
| `create_item(request, instance?)` | Create a new work item |
| `add_comment(key, body, instance?)` | Add a comment to a work item |
| `get_transitions(key, instance?)` | List available transitions |
| `transition(key, transition_name, instance?)` | Execute a workflow transition |
| `link_items(from_key, to_key, link_type, instance?)` | Create a relationship |
| `search(criteria, offset?, limit?, instance?)` | Search for work items |
| `download_attachment(attachment_id, instance?)` | Download attachment content by ID |
| `attach_file(key, filename, content, instance?)` | Upload file content as an attachment to a work item (W13) |
| `list_projects(instance?)` | Return all accessible projects |
| `get_project(key, instance?)` | Retrieve a single project by key |
| `create_project(request, instance?)` | Create a new project |
| `delete_project(key, instance?)` | Delete a project (irreversible) |

**Design rules:**

- All inputs and outputs are domain types (frozen dataclasses, enums)
- No platform SDK types appear in signatures
- Methods are synchronous (request-response); async variants may be added later
- The `instance` parameter is a string name matching a registered instance

---

## 7. Error Model

The domain defines its own exception hierarchy. Platform-specific errors are
caught by adapters and translated to these types. Agents never catch platform
SDK exceptions.

| Error | When raised |
|-------|-------------|
| `WorkTrackingError` | Base class for all work tracking errors |
| `ItemNotFound` | Requested item does not exist |
| `PermissionDenied` | Authentication or authorization failure |
| `InvalidTransition` | Requested transition is not available for the item's current state |
| `ConnectionFailure` | Network error, server unreachable |
| `RateLimited` | Too many requests; includes retry-after if available |
| `InstanceNotFound` | Referenced instance name is not registered |
| `NoDefaultInstance` | Operation omitted instance and no default is set |
| `InstanceAlreadyRegistered` | Attempted to register a name that already exists |
| `ProjectNotFound` | Requested project does not exist |

All errors carry an `instance` field identifying which backend raised the
error (or None for registry-level errors).

---

## 8. Multi-Instance Architecture

### Conceptual Model

```
InstanceRegistry          WorkTracker
(admin protocol)          (operational protocol)
       |                        |
       v                        v
  +------------------------------------+
  |        WorkTrackingService          |
  |  (single object implements both)    |
  |                                     |
  |  instances: {name -> adapter}       |
  |  default: name or None              |
  +------------------------------------+
       |              |
       v              v
   JiraAdapter   JiraAdapter
   (client A)    (internal)
```

The `WorkTrackingService` is a thin routing layer. It implements both
protocols. Internally it maintains a dict of named adapter instances.
Each operation resolves the instance name, looks up the adapter, and
delegates.

This class lives in `src/appif/domain/work_tracking/service.py` alongside
the domain types. There is no separate `service/` package -- it is a
simple dict + default pointer, not a layer.

### Instance Resolution

1. If `instance` is provided, look it up in the registry
2. If `instance` is None, use the default
3. If no default is set, raise `NoDefaultInstance`
4. If the name is not found, raise `InstanceNotFound`

### No Internal Adapter Protocol

There is no formal internal adapter interface. Each platform adapter (e.g.,
`JiraAdapter`) is a concrete class with methods matching the `WorkTracker`
operations (minus the `instance` parameter). The `WorkTrackingService` calls
these methods directly.

When a second platform adapter is added, extract a common protocol if the
pattern warrants it. Until then, a formal interface for one implementation
is premature abstraction.

---

## 9. Credential Configuration

### Environment Variable Pattern (Startup)

For the default/startup instance, credentials come from environment
variables following the existing appif pattern:

| Variable | Description |
|----------|-------------|
| `APPIF_JIRA_SERVER_URL` | Jira server base URL |
| `APPIF_JIRA_USERNAME` | Username (typically email) |
| `APPIF_JIRA_API_TOKEN` | Personal API token |

If these variables are present at initialization, an instance named
`"default"` is auto-registered and set as the default.

### Runtime Registration

Additional instances are registered programmatically:

```
registry.register(
    name="client_a",
    platform="jira",
    server_url="https://client-a.atlassian.net",
    credentials={"username": "...", "api_token": "..."},
)
```

Credentials are passed as a dictionary. The adapter validates that required
keys are present and raises `PermissionDenied` on invalid or missing
credentials.

### Security

- Credentials are stored in memory only
- `list_instances()` never returns credentials
- Credentials are not logged
- Instance removal clears credentials from memory

---

## 10. Constraints and Non-Negotiable Decisions

1. **The domain model is platform-agnostic.** No Jira, GitHub, Linear, or
   Azure DevOps types, field names, or conventions appear in domain types or
   protocol interfaces.

2. **Two protocol interfaces separate admin from operations.** `InstanceRegistry`
   manages instances. `WorkTracker` performs work tracking operations. They may
   be implemented by the same object but are semantically distinct.

3. **All domain types are frozen dataclasses.** Immutable value objects, no
   mutable state, no platform SDK types.

4. **Errors are typed and domain-scoped.** Platform exceptions never escape
   the adapter boundary.

5. **Multi-instance is first-class.** The architecture supports N simultaneous
   backends from day one. Single-instance is just N=1 with a default.

6. **Request-response, not event-driven.** Unlike messaging connectors, work
   tracking operations are synchronous calls. There is no listener model, no
   event loop owned by the adapter.

7. **Adapters are fully encapsulated.** Zero platform SDK imports or types
   appear in any code outside the adapter package. If the Jira library is
   replaced with raw HTTP calls, no code outside `appif.adapters.jira/`
   changes.

8. **Consistency with messaging domain conventions.** Same file structure,
   same naming patterns, same separation of domain/ports/errors.

---

## 11. What This Buys You

| Benefit | How |
|---------|-----|
| Jira / GitHub Issues / Linear all look identical to agents | Shared canonical model + shared protocol interface |
| Client instances are first-class | Register at runtime, no config files, no restarts |
| Jira library can be replaced without rewriting agents | All Jira specifics internal to the adapter |
| Agents test without platform access | Mock the protocol, inject domain types |
| New platforms add without changing agents | Implement the adapter interface, register an instance |
| Admin and operational concerns don't mix | Separate protocols, clean boundaries |

### Mental Model

```
InstanceRegistry = Admin (which backends exist)
WorkTracker      = Operations (read, write, transition work items)
Adapter          = Platform translation (Jira JSON <-> domain types)