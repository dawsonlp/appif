# Work Tracking Domain: Requirements

## Context

appif provides normalized access to external platforms behind connector-agnostic
domain models. The messaging domain (Gmail, Outlook, Slack) follows this pattern
successfully. Agents import appif domain types and never see platform-specific
APIs.

Work tracking systems (Jira, GitHub Issues, Linear, Azure DevOps) represent a
second class of external platform that agents need to interact with. The
interaction pattern differs from messaging -- it is request-response rather than
event-driven -- but the principle is identical: agents should speak in domain
terms, and platform specifics should be encapsulated in adapters.

## Problem Statement

Agents that solve problems need to read work items, create sub-items, post
status updates, and track relationships between items. Today there is no
normalized interface for this. Each agent would need to integrate directly with
the work tracking platform's API, coupling agent logic to a specific vendor.

We need a work tracking domain in appif that gives agents a stable,
platform-agnostic interface to work tracking systems, starting with Jira.

## Users

- **The Solver agent** -- reads Jira tickets, creates sub-tickets, posts
  understanding and results as comments, transitions ticket status
- **Future agents** -- any agent that needs to interact with tracked work
- **The comms_assessor** -- may eventually watch for work item events alongside
  communications

## Requirements

### W1: Read Work Items

The system must retrieve a work item by its key/identifier and return a
normalized representation that includes:

- Identifier (key and internal ID)
- Title/summary
- Description (as plain text or structured content)
- Current status
- Priority
- Labels/tags
- Assignee
- Reporter
- Comments (ordered by time)
- Links to other work items (with link type)
- Parent item reference (if a sub-item)
- Sub-item references (if a parent)

### W2: Create Work Items

The system must create new work items with at minimum:

- Title/summary
- Description
- Item type (task, story, bug, epic, etc.)
- Parent item reference (for creating sub-items)
- Labels/tags
- Priority
- Assignee

The system must return the created item's identifier.

### W3: Create Sub-Items

The system must create work items that are explicitly children of an existing
item, using whatever parent-child mechanism the underlying platform supports.
The relationship must be visible from both the parent and the child.

### W4: Post Comments

The system must add comments to existing work items. Comments must support:

- Plain text content
- A way to identify the comment author/source (the agent posting it)

### W5: Transition Status

The system must move work items through their workflow. The system must be
able to:

- Discover available transitions for a given item
- Execute a transition by name

### W6: Link Items

The system must create relationships between work items. Link types should
be normalized to a small vocabulary that maps across platforms:

- blocks / blocked_by
- relates_to
- duplicates / duplicated_by
- parent_of / child_of

### W7: Search Items

The system must search for work items by:

- Project/container
- Status
- Assignee
- Free-text query
- Labels/tags

Results must be paginated.

### W8: Platform Agnosticism

All domain types must be platform-agnostic. No Jira field names, GitHub
API conventions, or Linear-specific concepts may appear in the domain model
or the protocol interface.

An agent using the work tracking domain must be able to switch from Jira to
GitHub Issues by changing configuration only -- zero code changes in the
agent.

### W9: Authentication

Each adapter must handle its own authentication. Credentials are loaded from
environment variables following the existing appif pattern. The first
adapter (Jira) will use a personal API token (`JIRA_AGENTMIMIR_API_TOKEN`)
and server URL from the environment.

### W10: Error Handling

The domain must define its own exception types for common failure modes:

- Item not found
- Permission denied
- Invalid transition
- Connection failure
- Rate limiting

Platform-specific errors must be translated to these domain exceptions.
Agents never catch platform SDK exceptions -- they catch
appif work tracking domain exceptions.

### W12: Multi-Instance Support

The system must support simultaneous connections to multiple work tracking
platform instances (e.g., internal Jira, client A's Jira, client B's Jira).

**Instance Registry (administrative protocol):**

- Register a new instance at runtime with a unique name, server URL,
  platform type, and credentials
- List all registered instances (names and server URLs, not credentials)
- Remove a registered instance and clean up its state
- Optionally designate a default instance

**Operational routing:**

- All work tracking operations accept an instance parameter identifying
  which backend to use
- If instance is omitted and a default is set, use the default
- If instance is omitted and no default exists, raise an error

**Isolation:**

- Each instance has independent authentication, rate limiting, and
  connection state
- Failure on one instance does not affect others

**Startup convenience:**

- Instances can be pre-registered from environment variables at
  initialization (the common single-instance case remains zero-friction)
- Additional instances are registered programmatically at runtime
- Instances are ephemeral (not persisted across restarts; the orchestrator
  re-registers on startup)

### W11: Consistency with Messaging Domain

The work tracking domain must follow the same conventions as the existing
messaging domain:

- Frozen dataclasses for all domain types
- Protocol class for the adapter interface
- Platform-specific adapters under `appif.adapters.<platform>/`
- No platform SDK types in the domain model

### W13: Upload Attachments

The system must attach files to existing work items. The caller provides:

- The work item key
- The file content (as bytes) and the filename to use on the platform

The system must return the resulting attachment metadata for the newly
created attachment.

The operation must support the same multi-instance routing as all other
WorkTracker operations (optional `instance` parameter, default instance
fallback).

Platform-specific upload mechanics (multipart encoding, CSRF headers, size
limits) are adapter concerns and must not leak into the domain interface.

> **Added**: 2026-03-29. Promoted from Out of Scope to fulfill RADEMO1
> requirement D2.5. See `cli03_product_evaluation.md` for rationale.

## Out of Scope

- Event-driven work item monitoring (watching for ticket changes in
  real-time). This may be added later as a separate concern, potentially
  reusing the existing Connector event pattern.
- Attachment delete/replace/versioning
- Sprint/iteration management
- Board/view management
- User management
