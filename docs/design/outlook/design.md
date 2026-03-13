# Design Document: Outlook / Microsoft 365 Connector

**Author**: Architect
**Date**: 2026-02-21
**Status**: Draft
**Service**: Outlook / Microsoft 365 (via Microsoft Graph API)

---

## 1. Problem Statement

Agents need to participate in email conversations — receiving messages from Outlook mailboxes and sending replies — without any knowledge of Microsoft's Graph API, OAuth flows, or MIME encoding. Today this requires a human at a mail client or purpose-built integration code tightly coupled to Microsoft's platform.

This connector replaces that coupling. It provides a transport adapter that ingests Outlook mail events, normalizes them into the canonical messaging shape, and delivers outbound messages — all behind the same stable interface used by every other connector in the system, whether the upstream is Slack, Gmail, or anything else.

---

## 2. Core Principle

**A connector is a transport adapter.**

It does not reason, store meaning, or decide importance.

A connector:

- Connects to an external system
- Emits normalized inbound events
- Delivers outbound messages
- Exposes capabilities and lifecycle state

Nothing more.

---

## 3. Scope

### In Scope (Connector Responsibilities)

| Responsibility | Description |
|----------------|-------------|
| **Authentication & authorization** | Manage OAuth 2.0 credential lifecycle with Microsoft Identity Platform — consent, token refresh, storage. Expose credential health through status. |
| **Realtime event ingestion** | Detect new mail arrival and convert each relevant message into a `MessageEvent`. |
| **Backfill** | Retrieve historical messages within a requested time range and emit them through the same listener pipeline. |
| **Outbound delivery** | Accept a `ConversationRef` + `MessageContent`, construct a properly formatted email, and send or draft it. |
| **Thread mapping** | Map Outlook's conversation model onto `ConversationRef` so callers can reply in-thread without knowing Outlook internals. |
| **Identity resolution** | Resolve email addresses to `Identity` objects with stable IDs and display names. |
| **Attachment handling** | Normalize inbound attachments into the canonical attachment model; encode outbound attachments for delivery. |
| **Rate-limit management** | Respect Microsoft Graph API throttling internally; retry transient failures; surface persistent failures as typed errors. |
| **Lifecycle management** | Expose connection state (`DISCONNECTED → CONNECTING → CONNECTED → ERROR`) and health checks. |

### Out of Scope (Not Connector Responsibilities)

| Concern | Where It Lives |
|---------|----------------|
| Deciding which emails matter | Upstream agent / workflow |
| Classifying or triaging messages | Domain layer above connector |
| Storing email history or search index | Persistence layer |
| Managing Azure AD tenant or app registration infrastructure | Infrastructure provisioning / Azure Portal |
| Calendar, Contacts, OneDrive, Teams, or other Microsoft 365 APIs | Separate connectors if needed |
| Spam filtering or content moderation | External concern |
| User-facing OAuth consent UI | Separate auth utility; connector consumes tokens |

---

## 4. External System Model: Microsoft Graph Mail API

### Overview

Microsoft Graph is the unified API for accessing Microsoft 365 services. Mail operations are exposed under the `/me/messages` and `/me/mailFolders` endpoints. Key characteristics relevant to this connector:

### Mailbox Model

- A mailbox belongs to a Microsoft 365 user (personal Microsoft account or organizational Azure AD account).
- Mail is organized into **folders** (Inbox, Sent Items, Drafts, etc.) rather than labels. Folders form a tree hierarchy.
- Microsoft 365 organizational accounts may have **shared mailboxes** — out of scope for v1 but recognized as a future need.

### Conversation Model

- Outlook groups related messages into **conversations** identified by a `conversationId`.
- Each message also has a unique `id` and belongs to exactly one folder.
- Conversations span folders (a sent reply and its received response share the same `conversationId` even though they live in different folders).
- The `conversationId` is assigned by the server and is stable across the life of the conversation.

### Change Detection

Microsoft Graph provides two mechanisms for detecting new mail:

1. **Change notifications (webhooks)**: The application creates a subscription on a resource (e.g., `/me/mailFolders('Inbox')/messages`). When a change occurs, Graph sends an HTTP POST to a registered callback URL. Subscriptions expire and must be renewed periodically (maximum lifetime varies by resource; mail subscriptions last up to ~4230 minutes / ~3 days for organizational accounts).

2. **Delta queries**: The application requests changes since a prior sync state using a `deltaLink`. This returns all messages that have been created, modified, or deleted since the last query. Delta queries work without any external infrastructure — no webhook endpoint required.

### API Style

- RESTful JSON over HTTPS.
- OData query parameters for filtering, selecting fields, ordering, and pagination (`$filter`, `$select`, `$orderby`, `$top`, `$skip`).
- Batch API available for combining multiple requests (up to 20 per batch).
- Rich mail properties: body (text and HTML), subject, sender, recipients (to, cc, bcc), importance, categories, flags, attachments.

---

## 5. Canonical Event Model

Every inbound Outlook message arrives as a `MessageEvent`. The connector maps Microsoft Graph's native representation into this shape; no Graph-specific types leak through the public API.

### MessageEvent

| Field | Outlook Mapping |
|-------|-----------------|
| `message_id` | Graph message `id` (globally unique within Microsoft Graph) |
| `connector` | `"outlook"` |
| `account_id` | The authenticated mailbox address (e.g. `user@organization.com`) |
| `conversation_ref` | See §6 below |
| `author` | Resolved from the `from` property of the message |
| `timestamp` | `receivedDateTime` from Graph, converted to UTC `datetime` |
| `content.text` | Extracted plain-text body (Graph provides `body.content` with `body.contentType` of `"text"` or `"html"`; connector extracts or converts to plain text) |
| `content.attachments` | List of attachment descriptors (filename, MIME type, size, reference) |
| `metadata` | Connector-specific extras: folder, importance, categories, internet message headers (all opaque to callers) |

### Identity

| Field | Outlook Mapping |
|-------|-----------------|
| `id` | Email address (stable, unique per mailbox) |
| `display_name` | The `name` field from the `emailAddress` object in the `from` property |
| `connector` | `"outlook"` |

---

## 6. Conversation Routing

Outlook's conversation model maps to `ConversationRef` as follows:

| ConversationRef Field | Value |
|-----------------------|-------|
| `connector` | `"outlook"` |
| `account_id` | Authenticated mailbox address |
| `type` | `"email_thread"` |
| `opaque_id` | Dictionary containing: Outlook conversation ID, message ID of the specific message, and the subject line (for reply construction) |

### Thread Semantics

- **Outlook conversations** are groups of messages sharing a `conversationId`. A conversation is typically one subject thread, though Outlook's grouping may differ from other mail clients in edge cases.
- The connector treats one Outlook conversation as one conversation. If a caller sends a reply to a `ConversationRef`, the connector places it in the correct conversation thread.
- **New conversations**: When a caller provides a `ConversationRef` with no conversation context (or a special "new conversation" target), the connector starts a new email thread.
- **Replies**: The Graph API supports replying to a specific message (`/messages/{id}/reply` or `/messages/{id}/createReply`). The connector uses the message ID from `opaque_id` to target the correct message within the conversation.
- The `opaque_id` is connector-internal. Callers pass it back verbatim on `send()` and never inspect its contents.

---

## 7. Public API

The Outlook connector implements the `Connector` protocol exactly. No additional public methods.

### Lifecycle

| Method | Behavior |
|--------|----------|
| `connect()` | Validates credentials, initializes change detection (delta query baseline or subscription), transitions to `CONNECTED`. Raises `NotAuthorized` if credentials are invalid or expired beyond recovery. |
| `disconnect()` | Tears down any active subscriptions, cancels polling, releases resources, transitions to `DISCONNECTED`. |
| `get_status()` | Returns current `ConnectorStatus`. Includes credential health. |

### Discovery

| Method | Behavior |
|--------|----------|
| `list_accounts()` | Returns the authenticated mailbox(es). Typically one account per connector instance. |
| `list_targets(account_id)` | Returns available send targets. For Outlook, this is conceptually unbounded (any email address). The connector may return recently-contacted addresses or configured contacts as convenience targets. Returns an empty list if no practical enumeration is possible. |

### Inbound

| Method | Behavior |
|--------|----------|
| `register_listener(listener)` | Adds a `MessageListener`. Connector delivers all inbound messages to all registered listeners. |
| `unregister_listener(listener)` | Removes a previously registered listener. |
| `backfill(account_id, scope)` | Retrieves historical messages matching the scope (date range, folder filter) and delivers them through registered listeners. |

### Outbound

| Method | Behavior |
|--------|----------|
| `send(conversation, content)` | Sends or drafts a message. If `conversation` references an existing thread, the message is a reply placed in that conversation. If `conversation` references a new target, the message starts a new thread. Returns a `SendReceipt` with the new message ID and conversation ID. |

### Capabilities

| Method | Behavior |
|--------|----------|
| `get_capabilities()` | Returns the connector's declared capabilities (see §8). |

---

## 8. Capabilities Declaration

| Capability | Value | Rationale |
|------------|-------|-----------|
| `supports_realtime` | `False` | v1 uses delta-query polling, which provides near-realtime but not event-driven delivery. Upgrades to `True` when change notification subscriptions are implemented (see §19). |
| `supports_backfill` | `True` | Graph API supports historical message retrieval by date range and folder. |
| `supports_threads` | `True` | Outlook has native conversation grouping. |
| `supports_reply` | `True` | Connector can reply to a specific message within a conversation. |
| `supports_auto_send` | `True` | Connector can send email directly without human intervention. |
| `delivery_mode` | `"AUTOMATIC"` | Default: messages are sent immediately. See §8.1 for ASSISTED mode. |

### 8.1 Draft Support (ASSISTED Delivery Mode)

The connector must support an alternative delivery mode where outbound messages are saved as drafts rather than sent immediately. This enables human-in-the-loop workflows where an agent composes a reply and a human reviews before sending.

- The delivery mode is a connector-level configuration, not a per-message flag.
- When configured for `"ASSISTED"` mode, `send()` creates a draft in the correct conversation and returns a `SendReceipt` referencing the draft.
- The Graph API provides native draft support: `POST /me/messages` creates a draft, `POST /me/messages/{id}/send` sends it.
- The `SendReceipt` metadata should include enough information for an external process to locate and send the draft.
- Switching between AUTOMATIC and ASSISTED mode requires reconfiguration, not runtime toggling.

---

## 9. Authentication

### Requirements

Microsoft Graph requires OAuth 2.0 for API access. Authentication flows through the **Microsoft Identity Platform** (Azure AD v2.0 endpoints). Key lifecycle considerations:

1. **App registration**: A one-time registration in the Azure portal creates an application with a client ID and client secret. This is an out-of-band infrastructure step.
2. **Initial consent**: A one-time authorization flow grants the application access to a user's mailbox. This produces an authorization code exchangeable for access and refresh tokens.
3. **Token refresh**: Access tokens expire (typically 60–90 minutes). The connector must transparently refresh them using the stored refresh token.
4. **Token revocation**: Users or administrators can revoke access at any time. Conditional access policies in organizational tenants may also block access. The connector must detect this and transition to an error state.
5. **Scope management**: The connector requires specific Microsoft Graph permission scopes for reading mail, sending mail, and managing subscriptions.
6. **Account types**: Microsoft Identity Platform supports personal Microsoft accounts, organizational (Azure AD) accounts, and multi-tenant configurations. Token lifetime and refresh behavior differ between account types.

### Required Permission Scopes

| Scope | Purpose |
|-------|---------|
| `Mail.Read` | Read messages in all mail folders |
| `Mail.Send` | Send mail on behalf of the user |
| `Mail.ReadWrite` | Create drafts, manage message properties |
| `User.Read` | Read the authenticated user's profile (for identity resolution) |

Additional scopes for future capabilities (not required in v1):
- `MailboxSettings.Read` — for mailbox configuration
- Subscription scopes if change notifications are added later

### Auth Protocol

The connector defines a pluggable auth boundary (analogous to the Gmail connector's auth protocol):

- **Input**: The auth provider supplies valid credentials on demand.
- **Output**: A short-lived access token sufficient to call the Microsoft Graph API.
- **Contract**: The auth provider handles refresh, storage, and initial consent. The connector calls it when it needs a token and trusts the result.
- **Failure**: If the auth provider cannot produce a valid token, the connector raises `NotAuthorized`.

### Default Implementation

The default auth provider reads pre-obtained credentials (client ID, client secret, tenant ID, refresh token) from the environment and handles token refresh internally. The initial consent flow (browser-based OAuth redirect) is performed once out-of-band, and the resulting refresh token is stored as a credential.

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `APPIF_OUTLOOK_CLIENT_ID` | Azure AD application (client) ID |
| `APPIF_OUTLOOK_CLIENT_SECRET` | Azure AD client secret |
| `APPIF_OUTLOOK_TENANT_ID` | Azure AD tenant ID (or `"common"` for multi-tenant / personal accounts) |
| `APPIF_OUTLOOK_REFRESH_TOKEN` | Long-lived refresh token from initial consent |
| `APPIF_OUTLOOK_ACCOUNT` | Target mailbox address (for status and identity) |

All credentials stored in `~/.env`, loaded via environment.

---

## 10. Realtime Event Ingestion

### Strategy: Delta Query Polling

The connector polls Microsoft Graph at a configurable interval, using **delta queries** as the change detection mechanism. Delta queries return all messages created, modified, or deleted since the last sync, using a server-provided `deltaLink` as a cursor.

#### Polling Loop

1. On `connect()`, the connector performs an initial delta query against the target folder (default: Inbox) to establish the baseline `deltaLink`.
2. A background polling loop runs at a configurable interval (default: 30 seconds).
3. Each cycle calls the `deltaLink` URL to retrieve changes since the last sync.
4. New messages are fetched, normalized to `MessageEvent`, and dispatched to all registered listeners.
5. The `deltaLink` is updated after successful processing.

#### Delta Link Tracking

Microsoft Graph provides a `deltaLink` URL that encodes the sync state:

- Stored in memory (volatile — lost on restart).
- On restart, the connector re-initializes from the current state (no guaranteed delivery of messages missed during downtime). Backfill covers the gap if needed.
- Delta link is per-folder, per-account.
- Updated atomically to prevent concurrent poll cycles from producing stale state.

#### Polling Interval

- Configurable via environment variable (`APPIF_OUTLOOK_POLL_INTERVAL_SECONDS`, default: 30).
- Minimum enforced interval to respect Graph API throttling.

### Filtering

The connector must support configurable inbound filtering:

- **Folder filter**: Only watch specific folders (e.g., Inbox, a custom folder). Default: Inbox.
- **Exclude sent**: Do not emit events for messages the connector itself sent (prevent echo loops).

### Why Delta Queries Over Change Notifications for v1

Change notifications (webhooks) require a publicly accessible HTTPS endpoint to receive callbacks from Microsoft Graph. For a desktop/local application:

- No publicly routable URL is available without tunneling infrastructure.
- Subscription management adds complexity (creation, renewal every ~3 days, validation handshake).
- Delta queries provide equivalent correctness — they detect the same set of changes — with zero external infrastructure.

Change notifications are a future enhancement (see §19) for deployment scenarios where a webhook endpoint is available.

---

## 11. Backfill

| Aspect | Behavior |
|--------|----------|
| **Trigger** | Explicit call to `backfill(account_id, scope)` |
| **Scope** | Date range (required), folder filter (optional) |
| **Delivery** | Messages delivered through the same listener pipeline as realtime events |
| **Ordering** | Oldest-first within the requested range |
| **Deduplication** | Listeners must be idempotent. The connector does not guarantee that a message emitted during backfill was not also emitted in realtime. |
| **Rate limiting** | Backfill respects Graph API throttling. Large ranges are paged internally using `$top` and `$skip` or `@odata.nextLink`. |
| **Progress** | No progress callback in the current design. The connector completes or raises an error. |

### Backfill Query Strategy

Microsoft Graph supports filtering messages by `receivedDateTime`:

- `$filter=receivedDateTime ge {start} and receivedDateTime le {end}`
- `$orderby=receivedDateTime asc`
- Results are paged; the connector follows `@odata.nextLink` until exhausted.
- Folder scoping is achieved by querying `/me/mailFolders('{folderId}')/messages` rather than `/me/messages`.

---

## 12. Outbound Message Construction

When `send()` is called:

1. The connector resolves the target from `ConversationRef`:
   - **Reply**: Extract the conversation ID and message ID from `opaque_id`. Use the Graph reply endpoint to create a reply to the specific message.
   - **New thread**: Use the target address from `ConversationRef`. Construct a fresh message with a new subject.

2. The connector builds a properly formatted message via the Graph API:
   - `text` from `MessageContent` becomes the message body.
   - Attachments from `MessageContent` are attached via the Graph attachments endpoint.
   - Standard properties (`subject`, `toRecipients`, `body`, `importance`) are set.

3. Depending on delivery mode:
   - **AUTOMATIC**: The message is sent immediately via `POST /me/sendMail` (new) or `POST /me/messages/{id}/reply` (reply) with the send action.
   - **ASSISTED**: The message is saved as a draft. For replies, `POST /me/messages/{id}/createReply` creates a draft reply; for new messages, `POST /me/messages` creates a draft. The draft can be sent later via `POST /me/messages/{draftId}/send`.

4. A `SendReceipt` is returned containing the new message ID, conversation ID, and delivery mode used.

### Subject Handling

- **Replies**: The Graph API handles subject propagation automatically when using the reply endpoints. The original subject is preserved with `RE:` prefix.
- **New threads**: The caller must provide a subject. The connector may accept it via `MessageContent.metadata` or a dedicated mechanism. If no subject is provided, the connector uses a sensible default or raises an error (design decision for technical design phase).

---

## 13. Attachment Handling

### Domain Model

Attachments use the canonical `Attachment` type from `domain/messaging/models.py` (shared across all connectors). See the Gmail design document §12 for the full type definition.

### Inbound

- The connector populates `Attachment` objects with metadata and `content_ref`.
- Microsoft Graph provides attachment metadata (name, size, content type) in the message response and attachment content via a separate endpoint (`/messages/{id}/attachments/{attachmentId}`).
- `content` is `None` by default (lazy). Small attachments below a configurable threshold may be eagerly loaded.
- Graph API supports file attachments, item attachments (e.g., attached emails), and reference attachments (OneDrive links). The connector handles file attachments in v1; item and reference attachments are noted in metadata but not fully resolved.

### Outbound

- Outbound `Attachment` objects must have `content` populated (not lazy).
- The connector encodes attachments via the Graph API's attachment creation endpoint.
- Microsoft Graph imposes a **4 MB limit per request** for inline attachments. Attachments larger than 4 MB require an upload session (`POST /me/messages/{id}/attachments/createUploadSession`). The connector must handle both cases transparently.
- The overall message size limit is **150 MB** (including encoding overhead). The connector must validate this before attempting to send and raise `ConnectorError` if exceeded.

---

## 14. Error Mapping

All Microsoft Graph API errors are caught internally and mapped to the connector's typed error hierarchy. Graph-specific error details (HTTP status codes, error codes, inner error objects) never appear in the public interface.

| Graph Condition | Connector Error |
|----------------|-----------------|
| Invalid/expired/revoked credentials | `NotAuthorized` |
| Insufficient permission scopes | `NotAuthorized` |
| Conditional access policy blocked | `NotAuthorized` |
| Recipient address invalid or rejected | `TargetUnavailable` |
| Mailbox not found or inaccessible | `TargetUnavailable` |
| Throttling (HTTP 429) | `TransientFailure` (with retry, respecting `Retry-After` header) |
| Service unavailable (HTTP 503) | `TransientFailure` (with retry) |
| Backend timeout (HTTP 504) | `TransientFailure` (with retry) |
| Quota exhausted | `ConnectorError` with descriptive message |
| Message or attachment too large | `ConnectorError` with descriptive message |
| Operation not supported by config | `NotSupported` |

### Retry Policy

Transient failures (throttling, server errors) are retried internally with exponential backoff. The connector defines:

- Maximum retry count
- Maximum total retry duration
- Backoff multiplier
- Respect for `Retry-After` header when present (Graph API provides this on 429 responses)

If retries are exhausted, the error surfaces to the caller as `TransientFailure`.

---

## 15. Rate Limit and Throttling Management

Microsoft Graph uses a **service-specific throttling model** rather than a simple per-second quota. Throttling behavior varies by service and tenant:

| Aspect | Behavior |
|--------|----------|
| **Throttling signal** | HTTP 429 with `Retry-After` header |
| **Per-app limits** | Vary by tenant size and service (mail is more restrictive than directory) |
| **Per-mailbox limits** | Apply to operations on a specific mailbox |
| **Batch request limits** | Maximum 20 requests per batch |
| **Mail-specific** | Organizational tenants may have admin-configured sending limits |
| **Attachment upload** | Large attachment uploads (>4 MB) use upload sessions with their own throttling |

### Strategy

- The connector respects `Retry-After` headers from 429 responses.
- Batch operations (backfill) use pagination and throttle request rate to avoid triggering limits.
- Outbound sends are not batched; each `send()` call maps to one or more API calls.
- When throttled, the connector applies internal backpressure (delay) before surfacing errors.
- Sending limits are enforced by Microsoft 365 at the tenant/mailbox level. The connector maps rejections to appropriate errors rather than duplicating enforcement.

---

## 16. Threading and Concurrency

### Internal Threading Model

| Concern | Constraint |
|---------|-----------|
| **Event dispatch** | Listeners are invoked asynchronously. The connector must not block its event ingestion loop waiting for a listener to return. |
| **Listener isolation** | A slow or failing listener must not affect other listeners or the connector's ability to receive events. |
| **Token refresh** | Must be thread-safe. Multiple concurrent API calls must not trigger redundant refresh requests. |
| **Delta link** | Updated atomically. Concurrent poll cycles must not produce stale state. |
| **Subscription renewal** | (Future) Managed by a background task. Must not interfere with normal event processing. |

### Shutdown

On `disconnect()`:

1. Stop accepting new inbound events.
2. Cancel any pending polling timer or active subscription.
3. Drain in-flight listener calls (with a timeout).
4. Release all resources.
5. Transition to `DISCONNECTED`.

---

## 17. Multiple Accounts

The connector supports one mailbox per instance. To monitor multiple mailboxes, instantiate multiple connector instances. This keeps credential isolation simple and avoids cross-account state contamination.

This is especially important for Microsoft 365 where organizational accounts and personal Microsoft accounts have different token endpoints, refresh behaviors, and permission models.

---

## 18. Constraints and Non-Negotiable Decisions

1. **Microsoft Graph API only** — EWS (Exchange Web Services), IMAP, and legacy Outlook REST API are not used. Microsoft Graph is the current and forward-looking API for Microsoft 365 mail access. EWS is in maintenance mode; IMAP lacks push, structured conversations, and rich metadata.

2. **No credential UI** — The connector does not implement OAuth consent screens or redirect handlers. Initial consent is performed out-of-band. The connector consumes pre-obtained refresh tokens.

3. **No platform types in public API** — Graph message objects, OData responses, and HTTP error payloads never appear in the `Connector` protocol surface. All normalization happens inside the connector boundary.

4. **Listeners are fire-and-forget** — The connector delivers events at-least-once with no acknowledgment. Listeners must be idempotent.

5. **One mailbox per instance** — Cross-account operations are out of scope for a single connector instance.

6. **Delta state lost on restart** — The connector does not persist its delta link. Messages arriving during downtime may be missed unless backfill is used after restart.

7. **Azure AD app registration required** — The connector assumes an Azure AD app registration already exists. Creating or configuring the app registration is an out-of-band infrastructure step documented in the setup guide, not performed by the connector.

---

## 19. Trade-offs and Rationale

| Decision | Alternative Considered | Rationale |
|----------|----------------------|-----------|
| **Microsoft Graph over EWS** | EWS (wider legacy compatibility) | Graph is Microsoft's strategic API. EWS is in maintenance mode with no new features. Graph provides cleaner REST semantics, better documentation, and native support for modern auth. |
| **Microsoft Graph over IMAP** | IMAP (protocol-level compatibility) | IMAP lacks structured conversations, push notifications, rich metadata (categories, importance), and has weaker authentication. Graph provides all of these natively. |
| **Delta query polling over change notifications for v1** | Change notifications / webhooks (true realtime) | Delta queries require zero external infrastructure — no public HTTPS endpoint, no tunneling, no subscription management. For a local/desktop application, this is the only self-contained option. Change notifications are a future enhancement (§20) for server deployments. |
| **Volatile delta link** | Persisted delta state in database | Keeps the connector stateless and infrastructure-free. Backfill covers restart gaps. Persistence can be added later if needed. |
| **One mailbox per instance** | Multi-mailbox with account switching | Simplifies credential management, error isolation, and concurrency. Multi-mailbox can be composed by instantiating multiple connectors. Especially important given differences between personal and organizational account types. |
| **Lazy attachment loading** | Eager download of all attachments | Email attachments can be large (up to 150 MB). Lazy loading prevents memory pressure when attachments are not needed. Graph's separate attachment endpoint makes lazy loading natural. |
| **ASSISTED mode as configuration** | Per-message send-or-draft flag | Keeps the `send()` API simple and consistent. The human-in-the-loop decision is an operational choice, not a per-call decision. |
| **Subject via metadata** | Dedicated `subject` field on MessageContent | MessageContent is connector-agnostic. Adding `subject` would leak email semantics. Metadata keeps the model clean; technical design resolves the exact mechanism. |
| **File attachments only in v1** | Full support for item attachments and reference attachments (OneDrive links) | Item attachments (attached emails) and reference attachments (OneDrive links) add significant complexity. File attachments cover the dominant use case. Other types are noted in metadata for future support. |

---

## 20. Future Considerations (Not in Initial Scope)

These are recognized needs that are explicitly deferred:

- **Change notifications (webhooks)**: Replace delta-query polling with event-driven delivery via Graph subscription webhooks. Requires a publicly accessible HTTPS endpoint. Upgrades `supports_realtime` to `True`. Subscriptions must be renewed periodically (~3 days for mail). Requires a validation handshake endpoint.
- **Persistent delta state**: Survives restarts without backfill. Requires a storage dependency.
- **Folder management**: Creating, moving, or managing mail folders as part of connector operations.
- **Categories and flags**: Applying Outlook categories or follow-up flags to processed messages.
- **Item and reference attachments**: Full resolution of attached emails and OneDrive link attachments.
- **Shared mailboxes**: Accessing organizational shared mailboxes via delegated or application permissions.
- **Multi-mailbox orchestration**: A supervisor that manages multiple connector instances.
- **Application permissions (daemon mode)**: Using client credential flow instead of delegated (user) permissions for server-to-server access without a user context.
- **Calendar/Contacts/Teams integration**: Separate connector(s) for other Microsoft 365 APIs.
- **Focused Inbox awareness**: Distinguishing between Focused and Other inbox views.
- **Batch API optimization**: Using Graph's batch endpoint to combine multiple operations in a single HTTP request for improved throughput during backfill.

---

## 21. HTTP Readiness

The `Connector` protocol is defined in terms of method calls, not transport. This connector can be wrapped in an HTTP API in the future:

| Connector Method | HTTP Equivalent |
|-----------------|-----------------|
| `connect()` | `POST /connectors/outlook/connect` |
| `disconnect()` | `POST /connectors/outlook/disconnect` |
| `get_status()` | `GET /connectors/outlook/status` |
| `list_accounts()` | `GET /connectors/outlook/accounts` |
| `list_targets(account_id)` | `GET /connectors/outlook/accounts/{id}/targets` |
| `send(conversation, content)` | `POST /connectors/outlook/send` |
| `backfill(account_id, scope)` | `POST /connectors/outlook/backfill` |
| Listener events | SSE stream or webhook callback |

The HTTP layer is infrastructure. This design does not prescribe it.