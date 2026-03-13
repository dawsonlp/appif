# Design Document: Gmail Connector

**Author**: Architect
**Date**: 2026-02-19
**Status**: Draft
**Service**: Gmail (via Gmail API)

---

## 1. Problem Statement

Agents need to participate in email conversations — receiving messages from Gmail mailboxes and sending replies — without any knowledge of Gmail's API, OAuth flows, or MIME encoding. Today this requires a human at a mail client or purpose-built integration code tightly coupled to Google's platform.

This connector replaces that coupling. It provides a transport adapter that ingests Gmail events, normalizes them into the canonical messaging shape, and delivers outbound messages — all behind the same stable interface used by every other connector in the system, whether the upstream is Slack, Teams, or anything else.

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
| **Authentication & authorization** | Manage OAuth 2.0 credential lifecycle — consent, token refresh, storage. Expose credential health through status. |
| **Realtime event ingestion** | Receive push notifications when new mail arrives and convert each relevant message into a `MessageEvent`. |
| **Backfill** | Retrieve historical messages within a requested time range and emit them through the same listener pipeline. |
| **Outbound delivery** | Accept a `ConversationRef` + `MessageContent`, construct a properly formatted email, and send or draft it. |
| **Thread mapping** | Map Gmail's thread model onto `ConversationRef` so callers can reply in-thread without knowing Gmail internals. |
| **Identity resolution** | Resolve email addresses to `Identity` objects with stable IDs and display names. |
| **Attachment handling** | Normalize inbound attachments into the canonical attachment model; encode outbound attachments for delivery. |
| **Rate-limit management** | Respect Gmail API quotas internally; retry transient failures; surface persistent failures as typed errors. |
| **Lifecycle management** | Expose connection state (`DISCONNECTED → CONNECTING → CONNECTED → ERROR`) and health checks. |

### Out of Scope (Not Connector Responsibilities)

| Concern | Where It Lives |
|---------|----------------|
| Deciding which emails matter | Upstream agent / workflow |
| Classifying or triaging messages | Domain layer above connector |
| Storing email history or search index | Persistence layer |
| Managing Google Cloud Pub/Sub infrastructure | Infrastructure provisioning (Terraform / IaC) |
| Calendar, Contacts, Drive, or other Google APIs | Separate connectors if needed |
| Spam filtering or content moderation | External concern |
| User-facing OAuth consent UI | Separate auth utility; connector consumes tokens |

---

## 4. Canonical Event Model

Every inbound Gmail message arrives as a `MessageEvent`. The connector maps Gmail's native representation into this shape; no Gmail-specific types leak through the public API.

### MessageEvent

| Field | Gmail Mapping |
|-------|---------------|
| `message_id` | Gmail message ID (globally unique within Google) |
| `connector` | `"gmail"` |
| `account_id` | The authenticated mailbox address (e.g. `user@domain.com`) |
| `conversation_ref` | See §5 below |
| `author` | Resolved from the `From` header |
| `timestamp` | `internalDate` from Gmail, converted to UTC `datetime` |
| `content.text` | Extracted plain-text body (or converted from HTML if no plain-text part exists) |
| `content.attachments` | List of attachment descriptors (filename, MIME type, size, reference) |
| `metadata` | Connector-specific extras: labels, snippet, header fields (all opaque to callers) |

### Identity

| Field | Gmail Mapping |
|-------|---------------|
| `id` | Email address (stable, unique per mailbox) |
| `display_name` | Parsed display name from the `From` header, falling back to the local part of the address |
| `connector` | `"gmail"` |

---

## 5. Conversation Routing

Gmail's thread model maps to `ConversationRef` as follows:

| ConversationRef Field | Value |
|-----------------------|-------|
| `connector` | `"gmail"` |
| `account_id` | Authenticated mailbox address |
| `type` | `"email_thread"` |
| `opaque_id` | Dictionary containing: Gmail thread ID, message ID of the specific message, and sufficient header references (`In-Reply-To`, `References`) to construct a valid threaded reply |

### Thread Semantics

- **Gmail threads** are groups of messages sharing a thread ID. A single thread may span multiple subjects if Gmail groups them (e.g. by `References` / `In-Reply-To` headers).
- The connector treats one Gmail thread as one conversation. If a caller sends a reply to a `ConversationRef`, the connector places it in the correct thread.
- **New conversations**: When a caller provides a `ConversationRef` with no thread context (or a special "new conversation" target), the connector starts a new email thread.
- The `opaque_id` is connector-internal. Callers pass it back verbatim on `send()` and never inspect its contents.

---

## 6. Public API

The Gmail connector implements the `Connector` protocol exactly. No additional public methods.

### Lifecycle

| Method | Behavior |
|--------|----------|
| `connect()` | Validates credentials, establishes push notification subscription (or starts polling), transitions to `CONNECTED`. Raises `NotAuthorized` if credentials are invalid or expired beyond recovery. |
| `disconnect()` | Tears down push notification subscription, cancels any polling, releases resources, transitions to `DISCONNECTED`. |
| `get_status()` | Returns current `ConnectorStatus`. Includes credential health. |

### Discovery

| Method | Behavior |
|--------|----------|
| `list_accounts()` | Returns the authenticated mailbox(es). Typically one account per connector instance. |
| `list_targets(account_id)` | Returns available send targets. For Gmail, this is conceptually unbounded (any email address). The connector may return recently-contacted addresses or configured address-book entries as convenience targets. Returns an empty list if no practical enumeration is possible. |

### Inbound

| Method | Behavior |
|--------|----------|
| `register_listener(listener)` | Adds a `MessageListener`. Connector delivers all inbound messages to all registered listeners. |
| `unregister_listener(listener)` | Removes a previously registered listener. |
| `backfill(account_id, scope)` | Retrieves historical messages matching the scope (date range, label/folder filter) and delivers them through registered listeners. |

### Outbound

| Method | Behavior |
|--------|----------|
| `send(conversation, content)` | Sends or drafts a message. If `conversation` references an existing thread, the message is a reply placed in that thread. If `conversation` references a new target, the message starts a new thread. Returns a `SendReceipt` with the new message ID and thread ID. |

### Capabilities

| Method | Behavior |
|--------|----------|
| `get_capabilities()` | Returns the connector's declared capabilities (see §7). |

---

## 7. Capabilities Declaration

| Capability | Value | Rationale |
|------------|-------|-----------|
| `supports_realtime` | `False` | v1 uses polling, which provides near-realtime but not event-driven delivery. Upgrades to `True` when Google Cloud Pub/Sub push is implemented (see §19). |
| `supports_backfill` | `True` | Gmail API supports historical message retrieval by date range and label. |
| `supports_threads` | `True` | Gmail has native thread grouping. |
| `supports_reply` | `True` | Connector can place a message in an existing thread. |
| `supports_auto_send` | `True` | Connector can send email directly without human intervention. |
| `delivery_mode` | `"AUTOMATIC"` | Default: messages are sent immediately. See §7.1 for ASSISTED mode. |

### 7.1 Draft Support (ASSISTED Delivery Mode)

The connector must support an alternative delivery mode where outbound messages are saved as drafts rather than sent immediately. This enables human-in-the-loop workflows where an agent composes a reply and a human reviews before sending.

- The delivery mode is a connector-level configuration, not a per-message flag.
- When configured for `"ASSISTED"` mode, `send()` creates a draft in the correct thread and returns a `SendReceipt` referencing the draft.
- The `SendReceipt` metadata should include enough information for an external process to locate and send the draft.
- Switching between AUTOMATIC and ASSISTED mode requires reconfiguration, not runtime toggling.

---

## 8. Authentication

### Requirements

Gmail requires OAuth 2.0 for API access. Unlike Slack's static bot tokens, Gmail credentials have a more complex lifecycle:

1. **Initial consent**: A one-time authorization flow grants the application access to a user's mailbox. This produces a refresh token.
2. **Token refresh**: Access tokens expire (typically 1 hour). The connector must transparently refresh them using the stored refresh token.
3. **Token revocation**: Users or administrators can revoke access at any time. The connector must detect this and transition to an error state.
4. **Scope management**: The connector requires specific OAuth scopes for reading, sending, and managing push notifications.

### Auth Protocol

The connector defines a pluggable auth boundary (analogous to Slack's `SlackAuth` protocol):

- **Input**: The auth provider supplies valid credentials on demand.
- **Output**: A short-lived access token sufficient to call the Gmail API.
- **Contract**: The auth provider handles refresh, storage, and initial consent. The connector calls it when it needs a token and trusts the result.
- **Failure**: If the auth provider cannot produce a valid token, the connector raises `NotAuthorized`.

### Default Implementation

The default auth provider reads pre-obtained credentials (client ID, client secret, refresh token) from the environment and handles token refresh internally. The initial consent flow (browser-based OAuth redirect) is performed once out-of-band, and the resulting refresh token is stored as a credential.

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `APPIF_GMAIL_CLIENT_ID` | OAuth 2.0 client ID |
| `APPIF_GMAIL_CLIENT_SECRET` | OAuth 2.0 client secret |
| `APPIF_GMAIL_REFRESH_TOKEN` | Long-lived refresh token from initial consent |
| `APPIF_GMAIL_ACCOUNT` | Target mailbox address (for status and identity) |

All credentials stored in `~/.env`, loaded via environment.

---

## 9. Realtime Event Ingestion

### Strategy: Polling with History ID

The connector polls Gmail at a configurable interval, using Gmail's history ID as a monotonically increasing cursor. Each poll retrieves messages that have arrived since the last known history ID, normalizes them, and dispatches to registered listeners.

#### Polling Loop

1. On `connect()`, the connector queries Gmail for the current history ID and records it.
2. A background polling loop runs at a configurable interval (default: 30 seconds).
3. Each cycle calls the Gmail history endpoint for changes since the last history ID.
4. New messages are fetched, normalized to `MessageEvent`, and dispatched to all registered listeners.
5. The history ID is advanced after successful processing.

#### History ID Tracking

Gmail provides a monotonically increasing history ID. The connector uses this as its cursor:

- Stored in memory (volatile — lost on restart).
- On restart, the connector picks up from the current state (no guaranteed delivery of messages missed during downtime). Backfill covers the gap if needed.
- History ID is per-mailbox, tracked per account.
- Updated atomically to prevent concurrent poll cycles from producing stale cursors.

#### Polling Interval

- Configurable via environment variable (`APPIF_GMAIL_POLL_INTERVAL_SECONDS`, default: 30).
- Minimum enforced interval to respect Gmail API quotas.

### Filtering

The connector must support configurable inbound filtering:

- **Label filter**: Only watch specific labels (e.g., INBOX, a custom label). Default: INBOX.
- **Exclude sent**: Do not emit events for messages the connector itself sent (prevent echo loops).

---

## 10. Backfill

| Aspect | Behavior |
|--------|----------|
| **Trigger** | Explicit call to `backfill(account_id, scope)` |
| **Scope** | Date range (required), label/folder filter (optional) |
| **Delivery** | Messages delivered through the same listener pipeline as realtime events |
| **Ordering** | Oldest-first within the requested range |
| **Deduplication** | Listeners must be idempotent. The connector does not guarantee that a message emitted during backfill was not also emitted in realtime. |
| **Rate limiting** | Backfill respects Gmail API quotas. Large ranges are paged internally. |
| **Progress** | No progress callback in the current design. The connector completes or raises an error. |

---

## 11. Outbound Message Construction

When `send()` is called:

1. The connector resolves the target from `ConversationRef`:
   - **Reply**: Extract thread ID and header references from `opaque_id`. Construct a message that Gmail will thread correctly (matching `In-Reply-To` and `References` headers).
   - **New thread**: Use the target address from `ConversationRef`. Construct a fresh message with a new subject.

2. The connector builds a properly formatted email message:
   - `text` from `MessageContent` becomes the message body.
   - Attachments from `MessageContent` are encoded and attached.
   - Standard headers (`From`, `To`, `Subject`, `In-Reply-To`, `References`, `Date`, `Message-ID`) are set.

3. Depending on delivery mode:
   - **AUTOMATIC**: The message is sent immediately via the Gmail send endpoint.
   - **ASSISTED**: The message is saved as a draft in the correct thread.

4. A `SendReceipt` is returned containing the new message ID, thread ID, and delivery mode used.

### Subject Handling

- **Replies**: The connector preserves the original thread's subject (with `Re:` prefix if not already present).
- **New threads**: The caller must provide a subject. The connector may accept it via `MessageContent.metadata` or a dedicated field. If no subject is provided, the connector uses a sensible default or raises an error (design decision for technical design phase).

---

## 12. Attachment Handling

### Domain Model: Attachment Type

Attachments are represented by an `Attachment` domain type in `domain/messaging/models.py`. This is a cross-cutting domain model change that benefits all connectors:

| Field | Type | Description |
|-------|------|-------------|
| `filename` | `str` | Original filename |
| `mime_type` | `str` | MIME content type |
| `size_bytes` | `int | None` | Size in bytes (if known) |
| `content_ref` | `str` | Opaque reference resolvable by the originating connector (e.g. Gmail attachment ID, Slack file URL) |
| `content` | `bytes | None` | Optionally populated content — eager for small attachments, `None` for lazy-loaded large attachments |

The `content_ref` is connector-specific. To resolve lazy attachments, the connector exposes a `resolve_attachment(content_ref: str) -> bytes` method. This is a connector-specific extension, **not** part of the `Connector` protocol (since not all connectors need it).

`MessageContent.attachments` becomes `list[Attachment]`.

### Inbound

- The connector populates `Attachment` objects with metadata and `content_ref`.
- `content` is `None` by default (lazy). Small attachments below a configurable threshold may be eagerly loaded.
- This prevents large attachments from consuming memory when the listener may not need them.

### Outbound

- Outbound `Attachment` objects must have `content` populated (not lazy).
- The connector encodes attachments appropriately for email delivery.
- Gmail imposes a 25 MB total message size limit. The connector must validate this before attempting to send and raise `ConnectorError` if exceeded.

---

## 13. Error Mapping

All Gmail API errors are caught internally and mapped to the connector's typed error hierarchy. Gmail-specific error details (HTTP status codes, error reasons) never appear in the public interface.

| Gmail Condition | Connector Error |
|----------------|-----------------|
| Invalid/expired/revoked credentials | `NotAuthorized` |
| Insufficient OAuth scopes | `NotAuthorized` |
| Recipient address invalid or rejected | `TargetUnavailable` |
| Rate limit exceeded (HTTP 429) | `TransientFailure` (with retry) |
| Backend error (HTTP 500/503) | `TransientFailure` (with retry) |
| Quota exhausted (daily send limit) | `ConnectorError` with descriptive message |
| Message too large | `ConnectorError` with descriptive message |
| Operation not supported by config | `NotSupported` |

### Retry Policy

Transient failures (rate limits, server errors) are retried internally with exponential backoff. The connector defines:

- Maximum retry count
- Maximum total retry duration
- Backoff multiplier

If retries are exhausted, the error surfaces to the caller as `TransientFailure`.

---

## 14. Rate Limit and Quota Management

Gmail API enforces per-user and per-project quotas:

| Quota | Approximate Limit |
|-------|-------------------|
| API calls per user per second | 250 quota units |
| Daily sending limit (consumer) | 500 messages |
| Daily sending limit (Workspace) | 2,000 messages |
| Message size | 25 MB |

### Strategy

- The connector tracks quota consumption internally.
- Batch operations (backfill) use pagination and throttle to stay within limits.
- Outbound sends are not batched; each `send()` call is one API call.
- When approaching rate limits, the connector applies internal backpressure (delay) before surfacing errors.
- Daily send limits are documented but not enforced by the connector — Gmail itself rejects messages beyond the limit, and the connector maps this to an appropriate error.

---

## 15. Threading and Concurrency

### Internal Threading Model

| Concern | Constraint |
|---------|-----------|
| **Event dispatch** | Listeners are invoked asynchronously. The connector must not block its event ingestion loop waiting for a listener to return. |
| **Listener isolation** | A slow or failing listener must not affect other listeners or the connector's ability to receive events. |
| **Token refresh** | Must be thread-safe. Multiple concurrent API calls must not trigger redundant refresh requests. |
| **History ID** | Updated atomically. Concurrent notifications must not produce a stale cursor. |
| **Watch renewal** | Managed by a background task. Must not interfere with normal event processing. |

### Shutdown

On `disconnect()`:

1. Stop accepting new inbound events.
2. Cancel any pending push subscription or polling timer.
3. Drain in-flight listener calls (with a timeout).
4. Release all resources.
5. Transition to `DISCONNECTED`.

---

## 16. Multiple Accounts

The connector supports one mailbox per instance. To monitor multiple mailboxes, instantiate multiple connector instances. This keeps credential isolation simple and avoids cross-account state contamination.

---

## 17. Constraints and Non-Negotiable Decisions

1. **Gmail API only** — IMAP is not used. The Gmail API provides structured access to threads, labels, history, and push notifications. IMAP does not support push notifications and has weaker threading semantics.

2. **No credential UI** — The connector does not implement OAuth consent screens or redirect handlers. Initial consent is performed out-of-band. The connector consumes pre-obtained refresh tokens.

3. **No platform types in public API** — Gmail message objects, label objects, and HTTP responses never appear in the `Connector` protocol surface. All normalization happens inside the connector boundary.

4. **Listeners are fire-and-forget** — The connector delivers events at-least-once with no acknowledgment. Listeners must be idempotent.

5. **One mailbox per instance** — Cross-account operations are out of scope for a single connector instance.

6. **History gaps on restart** — The connector does not persist its history cursor. Messages arriving during downtime may be missed unless backfill is used after restart.

---

## 18. Trade-offs and Rationale

| Decision | Alternative Considered | Rationale |
|----------|----------------------|-----------|
| **Gmail API over IMAP** | IMAP (wider compatibility) | Gmail API provides structured threads, labels, push notifications, and better quota management. IMAP lacks push and has unreliable threading. |
| **Polling over Pub/Sub for v1** | Google Cloud Pub/Sub push (near-realtime) | Polling requires zero external infrastructure — no GCP project, Pub/Sub topic, or IAM configuration. This keeps the connector self-contained and testable. Pub/Sub is a future enhancement (see §19) that upgrades `supports_realtime` to `True`. |
| **Volatile history cursor** | Persisted cursor in database | Keeps the connector stateless and infrastructure-free. Backfill covers restart gaps. Persistence can be added later if needed. |
| **One mailbox per instance** | Multi-mailbox with account switching | Simplifies credential management, error isolation, and concurrency. Multi-mailbox can be composed by instantiating multiple connectors. |
| **Lazy attachment loading** | Eager download of all attachments | Email attachments can be large. Lazy loading prevents memory pressure when attachments are not needed. |
| **ASSISTED mode as configuration** | Per-message send-or-draft flag | Keeps the `send()` API simple and consistent. The human-in-the-loop decision is an operational choice, not a per-call decision. |
| **Subject via metadata** | Dedicated `subject` field on MessageContent | MessageContent is connector-agnostic. Adding `subject` would leak email semantics. Metadata keeps the model clean; technical design resolves the exact mechanism. |
| **No daily send limit enforcement** | Connector-side send counter | Gmail enforces its own limits. Duplicating enforcement adds complexity and drift risk. The connector maps the rejection to a typed error. |

---

## 19. Future Considerations (Not in Initial Scope)

These are recognized needs that are explicitly deferred:

- **Google Cloud Pub/Sub push**: Replace polling with event-driven delivery via Pub/Sub watch notifications. Requires external infrastructure (GCP project, topic, subscription, IAM). Upgrades `supports_realtime` to `True`. May require a webhook HTTP endpoint or pull subscription depending on deployment model.
- **Persistent history cursor**: Survives restarts without backfill. Requires a storage dependency.
- **Label management**: Creating, applying, or removing labels as part of connector operations.
- **Multi-mailbox orchestration**: A supervisor that manages multiple connector instances.
- **Webhook endpoint for Pub/Sub push**: An HTTP server that receives Pub/Sub push deliveries (as opposed to pull subscription). May be needed for deployment in serverless environments.
- **Calendar/Contacts integration**: Separate connector(s) for other Google Workspace APIs.
- **Shared mailbox / delegation support**: Google Workspace delegation model for shared inboxes.

---

## 20. HTTP Readiness

The `Connector` protocol is defined in terms of method calls, not transport. This connector can be wrapped in an HTTP API in the future:

| Connector Method | HTTP Equivalent |
|-----------------|-----------------|
| `connect()` | `POST /connectors/gmail/connect` |
| `disconnect()` | `POST /connectors/gmail/disconnect` |
| `get_status()` | `GET /connectors/gmail/status` |
| `list_accounts()` | `GET /connectors/gmail/accounts` |
| `list_targets(account_id)` | `GET /connectors/gmail/accounts/{id}/targets` |
| `send(conversation, content)` | `POST /connectors/gmail/send` |
| `backfill(account_id, scope)` | `POST /connectors/gmail/backfill` |
| Listener events | SSE stream or webhook callback |

The HTTP layer is infrastructure. This design does not prescribe it.