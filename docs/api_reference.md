# API Reference

Complete API reference for `appif` version 1.0.0.

---

## Messaging Domain

### Connector Protocol

**Import**: `from appif.domain.messaging.ports import Connector`

All messaging adapters (`GmailConnector`, `OutlookConnector`, `SlackConnector`)
implement this protocol.

#### Lifecycle

| Method | Signature | Description |
|--------|-----------|-------------|
| `connect` | `() -> None` | Authenticate and begin receiving events |
| `disconnect` | `() -> None` | Tear down connections and stop event ingestion |
| `get_status` | `() -> ConnectorStatus` | Return current lifecycle state |

#### Discovery

| Method | Signature | Description |
|--------|-----------|-------------|
| `list_accounts` | `() -> list[Account]` | List configured workspaces / accounts |
| `list_targets` | `(account_id: str) -> list[Target]` | List channels, DMs, groups within an account |

#### Inbound

| Method | Signature | Description |
|--------|-----------|-------------|
| `register_listener` | `(listener: MessageListener) -> None` | Subscribe to inbound message events |
| `unregister_listener` | `(listener: MessageListener) -> None` | Remove a previously registered listener |

#### Outbound

| Method | Signature | Description |
|--------|-----------|-------------|
| `send` | `(conversation: ConversationRef, content: MessageContent) -> SendReceipt` | Send a message to a conversation |

#### Durability

| Method | Signature | Description |
|--------|-----------|-------------|
| `backfill` | `(account_id: str, scope: BackfillScope) -> None` | Retrieve historical messages and emit to listeners |

#### Introspection

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_capabilities` | `() -> ConnectorCapabilities` | Return what this connector supports |

### MessageListener Protocol

**Import**: `from appif.domain.messaging.ports import MessageListener`

| Method | Signature | Description |
|--------|-----------|-------------|
| `on_message` | `(event: MessageEvent) -> None` | Fire-and-forget callback for inbound messages |

Design rules: at-least-once delivery, no return values, no backpressure coupling.

### Messaging Adapters

#### GmailConnector

**Import**: `from appif.adapters.gmail import GmailConnector`

Delivery mode: AUTOMATIC (background polling) + ASSISTED (backfill).
Auth: OAuth 2.0 with file-based tokens. Setup: [Gmail Setup Guide](design/gmail/setup.md)

#### OutlookConnector

**Import**: `from appif.adapters.outlook import OutlookConnector`

Delivery mode: AUTOMATIC (delta-query polling) + ASSISTED (backfill).
Auth: OAuth 2.0 via MSAL. Setup: [Outlook Setup Guide](design/outlook/setup.md)

#### SlackConnector

**Import**: `from appif.adapters.slack import SlackConnector`

Delivery mode: AUTOMATIC (Socket Mode real-time).
Auth: Bot token (`xoxb-`) or User token (`xoxp-`), optional App-level token (`xapp-`).
Setup: [Slack Setup Guide](design/slack/setup.md)

---

## Messaging Domain Models

**Import**: `from appif.domain.messaging.models import ...`

### Identity

Person who authored a message, resolved by the connector.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Platform user ID |
| `display_name` | `str` | Human-readable name |
| `connector` | `str` | Which connector resolved this identity |

### MessageContent

Body of a message (outbound or inbound content).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | `str` | required | Message text |
| `attachments` | `list[Attachment]` | `[]` | File attachments |

### Attachment

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `filename` | `str` | required | File name |
| `content_type` | `str` | required | MIME type |
| `size_bytes` | `int \| None` | `None` | File size |
| `content_ref` | `str \| None` | `None` | Connector-specific opaque reference for lazy download |
| `data` | `bytes \| None` | `None` | Raw content when available inline |

### ConversationRef

Opaque routing key for replies. Upstream systems use this to reply --
they never inspect or construct the `opaque_id`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `connector` | `str` | required | Owning connector name |
| `account_id` | `str` | required | Account within the connector |
| `type` | `str` | required | `"channel"`, `"thread"`, `"dm"`, `"email_thread"` |
| `opaque_id` | `dict` | `{}` | Connector-specific routing data |

### MessageEvent

Canonical inbound message event received by listeners.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `message_id` | `str` | required | Unique message identifier |
| `connector` | `str` | required | Source connector |
| `account_id` | `str` | required | Source account |
| `conversation_ref` | `ConversationRef` | required | Reply routing key |
| `author` | `Identity` | required | Message author |
| `timestamp` | `datetime` | required | When the message was sent |
| `content` | `MessageContent` | required | Message body and attachments |
| `metadata` | `dict` | `{}` | Connector-specific metadata |

### SendReceipt

Acknowledgement returned after a successful send.

| Field | Type | Description |
|-------|------|-------------|
| `external_id` | `str` | Platform message ID |
| `timestamp` | `datetime` | Delivery timestamp |

### ConnectorCapabilities

| Field | Type | Description |
|-------|------|-------------|
| `supports_realtime` | `bool` | Can receive messages in real-time |
| `supports_backfill` | `bool` | Can retrieve historical messages |
| `supports_threads` | `bool` | Supports threaded conversations |
| `supports_reply` | `bool` | Can reply to existing threads |
| `supports_auto_send` | `bool` | Can send without human approval |
| `delivery_mode` | `Literal["AUTOMATIC", "ASSISTED", "MANUAL"]` | How messages arrive |

### ConnectorStatus (enum)

Values: `DISCONNECTED`, `CONNECTING`, `CONNECTED`, `ERROR`

### Account

| Field | Type | Description |
|-------|------|-------------|
| `account_id` | `str` | Unique account identifier |
| `display_name` | `str` | Human-readable name |
| `connector` | `str` | Owning connector |

### Target

| Field | Type | Description |
|-------|------|-------------|
| `target_id` | `str` | Destination identifier |
| `display_name` | `str` | Human-readable name |
| `type` | `str` | `"channel"`, `"dm"`, `"group"`, etc. |
| `account_id` | `str` | Parent account |

### BackfillScope

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `conversation_ids` | `tuple[str, ...]` | `()` | Specific conversations to backfill |
| `oldest` | `datetime \| None` | `None` | Earliest message time |
| `latest` | `datetime \| None` | `None` | Latest message time |

---

## Messaging Errors

**Import**: `from appif.domain.messaging.errors import ...`

| Error | Description | Key Attributes |
|-------|-------------|----------------|
| `ConnectorError` | Base class for all connector errors | `connector: str` |
| `NotAuthorized` | Credentials missing, expired, or revoked | `reason: str` |
| `NotSupported` | Requested operation not available | `operation: str` |
| `TargetUnavailable` | Destination not reachable | `target: str`, `reason: str` |
| `TransientFailure` | Temporary error, safe to retry | `reason: str`, `retry_after: float \| None` |

---

## Work Tracking Domain

### InstanceRegistry Protocol

**Import**: `from appif.domain.work_tracking.ports import InstanceRegistry`

Administrative interface for managing platform instances.

| Method | Signature | Description |
|--------|-----------|-------------|
| `register` | `(name, platform, server_url, credentials) -> None` | Register a new instance |
| `unregister` | `(name) -> None` | Remove an instance |
| `list_instances` | `() -> list[InstanceInfo]` | List all registered instances (no credentials) |
| `set_default` | `(name) -> None` | Designate an instance as the default |
| `get_default` | `() -> str \| None` | Return the default instance name |

### WorkTracker Protocol

**Import**: `from appif.domain.work_tracking.ports import WorkTracker`

Operational interface for work tracking. Every method accepts an optional
`instance: str | None` keyword argument. If omitted, the default instance
is used.

#### Work Items

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_item` | `(key) -> WorkItem` | Retrieve a work item by key |
| `create_item` | `(request: CreateItemRequest) -> ItemIdentifier` | Create a new work item |
| `add_comment` | `(key, body) -> ItemComment` | Add a comment to a work item |

#### Transitions

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_transitions` | `(key) -> list[TransitionInfo]` | List available workflow transitions |
| `transition` | `(key, transition_name) -> None` | Execute a workflow transition by name |

#### Links

| Method | Signature | Description |
|--------|-----------|-------------|
| `link_items` | `(from_key, to_key, link_type: LinkType) -> None` | Create a typed relationship |

#### Search

| Method | Signature | Description |
|--------|-----------|-------------|
| `search` | `(criteria: SearchCriteria, *, offset=0, limit=50) -> SearchResult` | Search for work items |

#### Discovery

| Method | Signature | Description |
|--------|-----------|-------------|
| `get_project_issue_types` | `(project) -> list[IssueTypeInfo]` | Issue types available in a project |
| `get_link_types` | `() -> list[LinkTypeInfo]` | Link types available on the platform |

#### Attachments

| Method | Signature | Description |
|--------|-----------|-------------|
| `download_attachment` | `(attachment_id) -> AttachmentContent` | Download attachment content by ID |

#### Projects

| Method | Signature | Description |
|--------|-----------|-------------|
| `list_projects` | `() -> list[ProjectInfo]` | All accessible projects |
| `get_project` | `(key) -> ProjectInfo` | Single project by key |
| `create_project` | `(request: CreateProjectRequest) -> ProjectInfo` | Create a new project |
| `delete_project` | `(key) -> None` | Delete a project (irreversible) |

### WorkTrackingService

**Import**: `from appif.domain.work_tracking.service import WorkTrackingService`

Implements both `InstanceRegistry` and `WorkTracker`. Routes operations
to the correct adapter. Auto-loads Jira instances from
`~/.config/appif/jira/config.yaml` at construction.

```python
service = WorkTrackingService()  # auto_load=True by default
service = WorkTrackingService(auto_load=False)  # skip YAML loading
```

---

## Work Tracking Domain Models

**Import**: `from appif.domain.work_tracking.models import ...`

### WorkItem

Normalized representation of a tracked work item (frozen dataclass).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `key` | `str` | required | Item key (e.g. `"PROJ-123"`) |
| `id` | `str` | required | Platform internal ID |
| `title` | `str` | required | Summary/title |
| `description` | `str` | required | Description body |
| `status` | `str` | required | Current status name |
| `item_type` | `str` | required | Issue type name |
| `created` | `datetime` | required | Creation timestamp |
| `updated` | `datetime` | required | Last update timestamp |
| `priority` | `str \| None` | `None` | Priority name |
| `labels` | `tuple[str, ...]` | `()` | Labels |
| `assignee` | `ItemAuthor \| None` | `None` | Assigned person |
| `reporter` | `ItemAuthor \| None` | `None` | Reporter |
| `parent_key` | `str \| None` | `None` | Parent item key |
| `sub_item_keys` | `tuple[str, ...]` | `()` | Child item keys |
| `links` | `tuple[ItemLink, ...]` | `()` | Relationships to other items |
| `comments` | `tuple[ItemComment, ...]` | `()` | Comments |
| `attachments` | `tuple[ItemAttachment, ...]` | `()` | File attachments |

### ItemAuthor

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Platform user ID |
| `display_name` | `str` | Human-readable name |

### ItemComment

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Comment ID |
| `author` | `ItemAuthor` | Comment author |
| `body` | `str` | Comment text |
| `created` | `datetime` | Creation timestamp |

### ItemLink

| Field | Type | Description |
|-------|------|-------------|
| `link_type` | `LinkType` | Relationship type |
| `target_key` | `str` | Linked item key |

### ItemAttachment

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | required | Attachment ID (use with `download_attachment`) |
| `filename` | `str` | required | File name |
| `mime_type` | `str` | required | MIME type |
| `size_bytes` | `int` | required | File size |
| `created` | `datetime` | required | Upload timestamp |
| `author` | `ItemAuthor \| None` | `None` | Uploader |

### AttachmentContent

| Field | Type | Description |
|-------|------|-------------|
| `metadata` | `ItemAttachment` | Attachment metadata |
| `data` | `bytes` | Complete file content |

### ItemCategory (enum)

Domain-level work item categories. Callers express intent; adapters
resolve to platform-specific type strings.

| Value | Description |
|-------|-------------|
| `TASK` | Standard work item (universal default) |
| `SUBTASK` | Child item (requires `parent_key` on `CreateItemRequest`) |
| `STORY` | User story (falls back to TASK if unsupported) |
| `BUG` | Defect report (falls back to TASK if unsupported) |
| `EPIC` | Large body of work (falls back to TASK if unsupported) |

### CreateItemRequest (Pydantic BaseModel, frozen)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `project` | `str` | required | Project key |
| `title` | `str` | required | Item title |
| `item_type` | `ItemCategory` | required | Category (adapter resolves to platform type) |
| `description` | `str` | `""` | Description |
| `parent_key` | `str \| None` | `None` | Parent item key (required for SUBTASK) |
| `labels` | `tuple[str, ...]` | `()` | Labels |
| `priority` | `str \| None` | `None` | Priority name |
| `assignee_id` | `str \| None` | `None` | Assignee platform ID |

### ItemIdentifier

Returned after item creation.

| Field | Type | Description |
|-------|------|-------------|
| `key` | `str` | Item key (e.g. `"PROJ-123"`) |
| `id` | `str` | Platform internal ID |

### TransitionInfo

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Platform transition ID |
| `name` | `str` | Human-readable name |

### SearchCriteria (frozen dataclass)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `project` | `str \| None` | `None` | Project key filter |
| `status` | `str \| None` | `None` | Status name filter |
| `assignee_id` | `str \| None` | `None` | Assignee filter |
| `labels` | `tuple[str, ...]` | `()` | Labels filter |
| `query` | `str \| None` | `None` | Raw query string (JQL for Jira) |

### SearchResult

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `items` | `tuple[WorkItem, ...]` | `()` | Matching items |
| `total` | `int` | `0` | Total matches (may exceed returned items) |
| `offset` | `int` | `0` | Starting offset |
| `limit` | `int` | `50` | Page size |

### LinkType (enum)

| Value | Description |
|-------|-------------|
| `BLOCKS` | This item blocks another |
| `BLOCKED_BY` | This item is blocked by another |
| `RELATES_TO` | General relationship |
| `DUPLICATES` | This item duplicates another |
| `DUPLICATED_BY` | This item is duplicated by another |
| `PARENT_OF` | This item is parent of another |
| `CHILD_OF` | This item is child of another |

### IssueTypeInfo

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | Type name (e.g. `"Task"`, `"Bug"`) |
| `subtask` | `bool` | required | Whether this is a subtask type |
| `description` | `str` | `""` | Type description |

### LinkTypeInfo

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Link type name |
| `inward` | `str` | Inward description (e.g. `"is blocked by"`) |
| `outward` | `str` | Outward description (e.g. `"blocks"`) |

### ProjectInfo

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `key` | `str` | required | Project key |
| `name` | `str` | required | Project name |
| `description` | `str` | `""` | Description |
| `lead` | `ItemAuthor \| None` | `None` | Project lead |
| `project_type` | `str` | `""` | Project type (e.g. `"software"`) |
| `url` | `str` | `""` | Project URL |

### CreateProjectRequest (Pydantic BaseModel, frozen)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `key` | `str` | required | 2-10 uppercase alphanumeric, starts with letter |
| `name` | `str` | required | Project name |
| `project_type` | `str` | `"software"` | Project type |
| `description` | `str` | `""` | Description |
| `lead_account_id` | `str \| None` | `None` | Lead user platform ID |

### InstanceInfo

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | Instance name |
| `platform` | `str` | required | Platform identifier (e.g. `"jira"`) |
| `server_url` | `str` | required | Server URL |
| `is_default` | `bool` | `False` | Whether this is the default instance |

---

## Work Tracking Errors

**Import**: `from appif.domain.work_tracking.errors import ...`

| Error | Description | Key Attributes |
|-------|-------------|----------------|
| `WorkTrackingError` | Base class | `instance: str \| None` |
| `ItemNotFound` | Work item does not exist | `key: str` |
| `ProjectNotFound` | Project does not exist | `key: str` |
| `PermissionDenied` | Authorization failure | `reason: str` |
| `InvalidTransition` | Transition not available for current state | `key: str`, `transition: str` |
| `ConnectionFailure` | Network error or server unreachable | `reason: str` |
| `RateLimited` | Too many requests | `retry_after: float \| None` |
| `InstanceNotFound` | Instance name not registered | `name: str` |
| `NoDefaultInstance` | No default instance configured | -- |
| `InstanceAlreadyRegistered` | Duplicate instance name | `name: str` |
