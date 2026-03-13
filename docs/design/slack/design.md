# Design Document: Slack Connector

**Author**: Architect
**Date**: 2026-03-07
**Status**: Draft (revised per requirements v2.0)
**Service**: Slack

---

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 2.1 | 2026-03-07 | Added synchronous interface constraint (Section 10, Section 15 item 11). Driven by sync/async design review against RULES.md and Python development guidelines. |
| 2.0 | 2026-03-07 | Revised per requirements v2.0. Added identity model (one connector = one identity), capability model, OAuth scopes per identity type. Rewritten credential configuration for single-identity construction. |
| 1.0 | 2026-02-18 | Initial draft |

---

## 1. Problem Statement

Agents need to participate in Slack conversations — receiving messages from workspaces and sending replies — without any knowledge of Slack's API, SDK, or transport mechanics. Today this requires a human at a keyboard or purpose-built bot code tightly coupled to the Slack platform.

This connector replaces that coupling. It provides a transport adapter that ingests Slack events, normalizes them into a canonical shape, and delivers outbound messages — all behind a stable interface that is identical regardless of whether the upstream system is Slack, Teams, Email, or anything else.

---

## 2. Core Principles

**A connector is a transport adapter.**

It does not reason, store meaning, or decide importance.

A connector:

- Connects to an external system
- Emits normalized inbound events
- Delivers outbound messages
- Exposes capabilities and lifecycle state

Nothing more.

**A connector authenticates as exactly one identity.**

Give it a bot token and it is the bot. Give it a user token and it is the user. The connector does not combine identities, switch between tokens, or try to be two things. Capabilities are a consequence of identity, not configuration.

If a consumer needs multiple perspectives on the same workspace, it constructs multiple connectors. That composition choice belongs to the consumer, not the connector.

---

## 3. Scope

### In Scope (Connector Responsibilities)

| Responsibility | Description |
|----------------|-------------|
| **Single-identity authentication** | Authenticate as exactly one entity using the provided credential |
| **Realtime event ingestion** | Receive messages via Socket Mode as they arrive (when transport credentials are available) |
| **Historical backfill** | Retrieve conversation history on demand |
| **Normalization** | Transform Slack events into canonical `MessageEvent` shape |
| **Message delivery** | Send messages and replies to channels, threads, and DMs |
| **Retry and rate-limit handling** | Respect Slack rate limits, retry transient failures |
| **Capability advertisement** | Report what this connector can do, computed from identity and transport availability |
| **Failure surfacing** | Expose typed errors for authentication failures, unavailable targets, transient issues |

### Out of Scope (Lives Upstream)

| Concern | Why excluded |
|---------|-------------|
| Classify ask vs. inform | Interpretation, not transport |
| Track obligations | Memory / reasoning layer |
| Store long-term memory | Persistence layer |
| Infer intent | Agent reasoning |
| Auto-respond | Policy decision, not transport |
| Thread summarization | Content processing |
| Channel management (create, archive, rename) | Administrative mutation — not transport |
| User/group management | Administrative mutation |
| Multi-identity composition | Consumer's concern — construct multiple connectors |
| Identity switching within a connector | Violates one-identity principle |
| Slash commands, interactive components, modals | Separate interaction surface; may be a future connector extension |
| File upload/download | Not in scope for this requirements cycle |

---

## 4. Canonical Event Model

Every inbound message — Slack, Email, Teams, or any future platform — must arrive in this shape. This model is **connector-agnostic**. It is defined here as part of the first connector implementation but applies to all connectors.

### MessageEvent

The top-level event received by listeners.

| Field | Type | Description |
|-------|------|-------------|
| `message_id` | `str` | Connector-scoped unique identifier |
| `connector` | `str` | Source connector name: `"slack"`, `"email"`, `"teams"` |
| `account_id` | `str` | Workspace ID (Slack), mailbox (Email), tenant ID (Teams) |
| `conversation_ref` | `ConversationRef` | Opaque routing key for replies |
| `author` | `Identity` | Who sent the message |
| `timestamp` | `datetime` | When the message was sent (source timestamp) |
| `content` | `MessageContent` | The message body and attachments |
| `metadata` | `dict` | Raw or lightly-normalized platform facts (not interpreted) |

All fields are immutable. `MessageEvent` is a frozen value object.

### ConversationRef

An opaque routing key. The upstream system uses this to reply — it never inspects or constructs it.

| Field | Type | Description |
|-------|------|-------------|
| `connector` | `str` | Which connector owns this reference |
| `account_id` | `str` | Workspace / mailbox / tenant |
| `type` | `str` | `"channel"`, `"thread"`, `"dm"`, `"email_thread"` |
| `opaque_id` | `dict` | Connector-owned routing data; opaque to consumers |

**Rule**: If something is not needed to reply, it does not belong in `ConversationRef`.

For Slack, `opaque_id` will internally contain channel ID and optionally thread timestamp. This structure is private to the Slack connector — consumers never read or write it.

### Identity

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Platform-scoped user identifier |
| `display_name` | `str` | Human-readable name |
| `connector` | `str` | Which connector resolved this identity |

### MessageContent

| Field | Type | Description |
|-------|------|-------------|
| `text` | `str` | Plain text content (Slack markdown stripped or preserved per connector policy) |
| `attachments` | `list` | File references or structured attachment metadata |

### SendReceipt

Returned after a successful outbound `send()`.

| Field | Type | Description |
|-------|------|-------------|
| `external_id` | `str` | Platform-assigned message identifier |
| `timestamp` | `datetime` | Platform-acknowledged send time |

No guarantees beyond "attempted and acknowledged by platform."

### ConnectorCapabilities

Each connector advertises what it can do. Agent logic branches on capabilities, not connector type. Values are computed by the connector from its identity and transport availability — they are not externally configured.

| Field | Type | Description |
|-------|------|-------------|
| `supports_realtime` | `bool` | Can receive events as they occur |
| `supports_backfill` | `bool` | Can retrieve historical messages |
| `supports_threads` | `bool` | Supports threaded conversations |
| `supports_reply` | `bool` | Can send replies to conversations |
| `supports_auto_send` | `bool` | Can send messages without human intervention |
| `delivery_mode` | `Literal["AUTOMATIC", "ASSISTED", "MANUAL"]` | How messages are delivered |

---

## 5. Public Connector Interface

This is the entire surface area any upstream system sees. Nothing here mentions Slack, Bolt, or SDKs.

### Lifecycle

| Method | Description |
|--------|-------------|
| `connect()` | Authenticate and begin receiving events |
| `disconnect()` | Tear down connections, stop event ingestion |
| `get_status()` | Return current lifecycle state (`ConnectorStatus`) |

### Discovery

| Method | Description |
|--------|-------------|
| `list_accounts()` | List configured workspaces / accounts |
| `list_targets(account_id)` | List available channels, DMs, groups within an account |

### Inbound (Listener Registration)

| Method | Description |
|--------|-------------|
| `register_listener(listener)` | Subscribe a `MessageListener` to receive events |
| `unregister_listener(listener)` | Remove a previously registered listener |

### Outbound

| Method | Description |
|--------|-------------|
| `send(conversation_ref, content)` | Send a message to the conversation identified by the ref. Returns `SendReceipt`. |

The caller does not specify "Slack vs. Email." It replies using the `ConversationRef` it received. The connector resolves routing internally.

### Durability

| Method | Description |
|--------|-------------|
| `backfill(account_id, scope)` | Retrieve historical messages for specified scope. Emitted to registered listeners. |

Realtime and backfill are explicitly separate. Realtime events flow through listeners automatically. Backfill is an explicit call — on startup, on schedule, or during failure recovery. This avoids false confidence about message completeness.

### Capability Introspection

| Method | Description |
|--------|-------------|
| `get_capabilities()` | Return `ConnectorCapabilities` for this connector |

Capabilities are queryable at any time, including before `connect()`. They reflect the connector's structural abilities based on its identity and available transport credentials.

---

## 6. Listener Model

### Interface

Listeners implement a single method:

| Method | Description |
|--------|-------------|
| `on_message(event: MessageEvent)` | Called when an inbound message arrives |

### Design Rules

| Rule | Rationale |
|------|-----------|
| **Fire-and-forget** | Connector must not block on listener execution |
| **At-least-once delivery** | Listener code must be idempotent |
| **No return values** | Interpretation happens elsewhere; connector does not act on listener output |
| **No backpressure coupling** | Connector queues internally if a listener is slow |

### Internal Dispatch

Inside the connector, events arrive from the Slack platform, are normalized into `MessageEvent`, and dispatched to listeners via an internal queue or executor. This keeps ingestion resilient — a slow or crashing listener cannot stall the event pipeline.

Ordering guarantees are **per conversation**, not global.

---

## 7. Identity Model

### One Connector, One Identity

A connector authenticates as exactly one entity. The credential provided at construction determines the identity. There is no mechanism to switch identities, combine tokens, or operate as multiple entities.

Slack supports two identity types relevant to this connector:

| Identity type | Token prefix | The connector is... |
|---------------|-------------|---------------------|
| **Bot** | `xoxb-` | The app's bot user. Sees channels it has been added to. Sends as the app. |
| **User** | `xoxp-` | A specific human user. Sees everything that user can see. Sends as that user. |

Both identity types use the same connector interface. The authenticated identity determines:

- **Visibility**: Which channels, DMs, and messages are accessible
- **Attribution**: Who messages appear to be from
- **Permissions**: Which operations succeed vs. raise `NotAuthorized`

These differences are inherent to the identity. They are not modes, configurations, or feature flags.

### Token Classification

Slack uses three token types. This design classifies them by role:

| Token | Prefix | Classification | Purpose |
|-------|--------|---------------|---------|
| Bot OAuth token | `xoxb-` | **Identity** | Authenticates as the app's bot user |
| User OAuth token | `xoxp-` | **Identity** | Authenticates as a specific human user |
| App-level token | `xapp-` | **Transport** | Enables Socket Mode (WebSocket) for real-time event delivery |

**Identity tokens** determine who the connector is. Exactly one is required at construction.

**Transport tokens** determine how events are delivered. The app-level token enables Socket Mode. Its absence does not change who the connector is — it reduces what the connector can do (real-time event delivery becomes unavailable).

### Construction Rule

A connector requires exactly one identity token. The app-level token is optional and affects only transport capability:

- **Identity token present, app-level token present**: Full capability. Real-time events delivered via Socket Mode.
- **Identity token present, app-level token absent**: Degraded capability. `supports_realtime` is `False`. Backfill, send, discovery, and all other operations work normally.
- **No identity token**: Construction fails. A connector without an identity is meaningless.

### Multi-Identity Composition

If a consumer needs both perspectives — read as user, send as bot — it constructs two separate connectors. Each connector is independently constructed, independently authenticated, and independently managed. The connector has no knowledge of this composition; it is the consumer's architectural choice.

---

## 8. Capability Model

### Capabilities Are Computed, Not Configured

The connector computes its own capabilities from two inputs:

1. **Identity type** — what the authenticated entity can do on the platform
2. **Transport availability** — whether real-time event delivery infrastructure is present

There is no external capability override. A consumer cannot force `supports_realtime=True` on a connector that lacks the transport credential. The connector reports what it can actually do.

### Capability Determination for Slack

For the Slack connector, capability computation follows these rules:

| Capability | Determined by | Rule |
|------------|--------------|------|
| `supports_realtime` | Transport | `True` if app-level token (`xapp-`) is available; `False` otherwise |
| `supports_backfill` | Identity | `True` for all supported identity types (both have history access) |
| `supports_threads` | Platform | `True` (Slack supports threading natively) |
| `supports_reply` | Identity | `True` for all supported identity types (both can post messages) |
| `supports_auto_send` | Identity | `True` for all supported identity types |
| `delivery_mode` | Platform | `"AUTOMATIC"` (Slack delivers events without human intervention) |

### Identity Differences Are Not Capability Differences

Both bot and user identities support the same structural operations (backfill, send, list channels, thread replies). The differences between them — visibility, attribution, permission scope — manifest as:

- **Different results** from `list_targets()` (user sees more channels than bot)
- **Different results** from `backfill()` (different channels accessible)
- **Different message attribution** (bot name vs. user name)
- **Runtime errors** (`NotAuthorized`) for operations the specific identity cannot perform in a specific context

These are identity-scoped outcomes, not capability flags. The capability model answers "can this connector structurally perform this operation?" The identity determines the scope within which those operations succeed.

### Queryable Before Connect

Capabilities are available immediately after construction. A consumer can inspect `get_capabilities()` before calling `connect()` to understand what the connector supports and make decisions accordingly. This is possible because capabilities are derived from construction inputs (identity token type, transport token presence), not from runtime state.

---

## 9. Error Model

The connector raises only connector-level errors. Platform-specific exceptions are caught internally and mapped to typed connector errors.

| Error | When raised |
|-------|-------------|
| `ConnectorError` | Base class for all connector errors |
| `NotAuthorized` | Authentication failure, expired token, revoked access, insufficient permissions for the requested operation |
| `NotSupported` | Requested operation not available for this connector (e.g., real-time when no app-level token) |
| `TargetUnavailable` | Channel/DM/workspace not reachable |
| `TransientFailure` | Temporary failure (rate limit, network timeout) — safe to retry |

**Never raised outward**: HTTP status codes, SDK exceptions, Slack API error strings. These are logged internally for diagnostics but never leak through the public interface.

### Error and Identity Interaction

When an operation fails because the authenticated identity lacks permission (e.g., a bot trying to read a private channel it has not been added to), the connector raises `NotAuthorized`. When an operation is structurally unavailable (e.g., real-time streaming without an app-level token), the connector raises `NotSupported`. This distinction allows consumers to differentiate between "you cannot do this here" and "this connector cannot do this at all."

---

## 10. Concurrency and Ordering

### Public Interface Is Synchronous

The `Connector` protocol and `MessageListener` protocol define **synchronous** interfaces. All public methods are blocking calls (`def`, not `async def`). This is a design-time decision driven by caller needs, not implementation convenience.

**Rationale.** All known callers of the connector interface are synchronous: CLI commands, scripts, batch agents, and orchestrators that block and wait. Per the project's sync/async design principle (RULES.md), interfaces serve their callers. Synchronous interfaces impose the lowest cost on consumers. Asynchronous callers can trivially wrap sync code via `asyncio.to_thread()`; the reverse — sync callers wrapping async code — is error-prone and risks event loop nesting failures.

**Internal concurrency is an implementation detail.** Connectors that require background I/O (Socket Mode WebSocket connections, polling loops) handle this internally using threads. The threading choice does not leak into the public interface. Listeners receive callbacks on the connector's internal dispatch thread, not on a caller-managed event loop.

This constraint applies to **all** messaging connectors, not just Slack. The Gmail and Outlook connectors already follow this pattern.

### Ordering and Isolation

| Constraint | Description |
|------------|-------------|
| Connector controls its own threads | No shared event loop with upstream |
| Listener callbacks are isolated | One listener's failure does not affect another |
| Connector must survive slow or crashing listeners | Internal queue absorbs backpressure |
| Ordering guarantees are per conversation, not global | Messages within a channel/thread arrive in order; no cross-conversation ordering promise |

---

## 11. Slack-Specific Internals (Private Boundary)

Everything below is internal to the Slack connector implementation. It is documented here to establish architectural boundaries, not to prescribe implementation. Zero Slack types, imports, or semantics leak through the public interface.

### Technology Stack (Internal)

| Component | Purpose |
|-----------|---------|
| **Bolt for Python** | Socket Mode, event routing |
| **slack_sdk** | `conversations.list`, `conversations.history`, `chat.postMessage`, `users.info` |

### Internal Responsibilities

| Concern | Internal component |
|---------|--------------------|
| Identity authentication | Validate token, determine identity type from token prefix |
| Realtime event ingestion | Socket Mode handler (requires app-level token) |
| Event normalization | Internal mapper: Slack event to `MessageEvent` |
| Message delivery | `chat.postMessage` via slack_sdk |
| User resolution | `users.info` calls, cached internally |
| Channel/target listing | `conversations.list` via slack_sdk |
| History retrieval | `conversations.history` / `conversations.replies` via slack_sdk |
| Rate-limit handling | Internal retry with backoff, respecting `Retry-After` headers |

### OAuth Scopes by Identity Type

The following scopes are required for each identity type to support the full connector interface. These are minimum scopes — the connector may function with a subset, but some operations will raise `NotAuthorized`.

#### Bot token scopes

| Scope | Required for |
|-------|-------------|
| `channels:read` | List public channels |
| `channels:history` | Read message history in public channels |
| `groups:read` | List private channels the bot is a member of |
| `groups:history` | Read message history in private channels |
| `im:read` | List direct message conversations |
| `im:history` | Read direct message history |
| `mpim:read` | List group direct message conversations |
| `mpim:history` | Read group direct message history |
| `chat:write` | Send messages |
| `users:read` | Resolve user identities |

#### User token scopes

| Scope | Required for |
|-------|-------------|
| `channels:read` | List public channels |
| `channels:history` | Read message history in public channels |
| `groups:read` | List private channels the user is a member of |
| `groups:history` | Read message history in private channels |
| `im:read` | List direct message conversations |
| `im:history` | Read direct message history |
| `mpim:read` | List group direct message conversations |
| `mpim:history` | Read group direct message history |
| `chat:write` | Send messages as the user |
| `users:read` | Resolve user identities |

#### App-level token (Socket Mode)

The app-level token is not an OAuth token. It is generated from the app configuration page and requires `connections:write` to be enabled on the app manifest. It is used solely for the Socket Mode WebSocket connection.

### Replaceability Test

If Bolt is deleted tomorrow and replaced with raw HTTP + WebSocket, the public `Connector` interface does not change. No upstream code requires modification.

---

## 12. Credential Configuration

### Construction Inputs

| Input | Required | Classification | Description |
|-------|----------|---------------|-------------|
| Identity token | **Yes** | Identity | Bot token (`xoxb-`) or user token (`xoxp-`). Determines who the connector is. |
| App-level token | No | Transport | App-level token (`xapp-`). Enables Socket Mode for real-time events. |

### Environment Variables

| Environment Variable | Description |
|----------------------|-------------|
| `APPIF_SLACK_IDENTITY_TOKEN` | Identity token — bot (`xoxb-`) or user (`xoxp-`). Determines who the connector is. |
| `APPIF_SLACK_APP_TOKEN` | App-level token for Socket Mode (`xapp-`). Optional; enables real-time event delivery. |

Exactly one identity token is provided per connector instance. If a consumer needs both bot and user perspectives, it constructs two connectors with separate configurations.

Credentials are loaded from `~/.env` using `python-dotenv`, consistent with the existing project pattern. The project-root `.env.example` documents expected keys. Actual values are never committed.

### Degradation Behavior

| Credential combination | Result |
|------------------------|--------|
| Identity token + app-level token | Full capability |
| Identity token only | `supports_realtime=False`; all other operations work |
| No identity token | Construction fails |
| App-level token only | Construction fails (transport without identity is meaningless) |

---

## 13. Serialization Readiness

To keep future HTTP exposure trivial:

| Constraint | Rationale |
|------------|-----------|
| All public inputs/outputs are serializable | JSON-ready without custom encoders |
| No closures in public API | Stateless interface |
| No reliance on shared mutable state | Safe for concurrent access |
| Listener registration is explicit and reversible | Clean lifecycle management |

---

## 14. Relationship to Existing Domain Models

The current domain models (`Article`, `Edition`, `Section` in `models.py`) serve **content-extraction connectors** — the Economist, Foreign Affairs, Irish Times. These are viewer-layer adapters that retrieve and structure published content.

The messaging connector domain (`MessageEvent`, `ConversationRef`, `MessageContent`, `Identity`, `SendReceipt`, `ConnectorCapabilities`) serves a fundamentally different purpose: **bidirectional transport**. These types will live in their own domain area, separate from the content-extraction models.

The `Connector` interface defined in this document is the shared abstraction for all messaging connectors (Slack, Teams, Email). Content-extraction adapters continue to implement the existing `ContentSource` port.

---

## 15. Constraints and Non-Negotiable Decisions

1. **The connector is a transport adapter.** It does not interpret, classify, or auto-respond. All intelligence lives upstream.

2. **One connector, one identity.** A connector authenticates as exactly one entity. It does not combine tokens, switch identities, or operate as multiple entities. This is not configurable.

3. **Capabilities are a consequence, not a configuration.** The connector computes its capabilities from its identity and transport availability. There is no external override or capability injection.

4. **The app-level token is transport, not identity.** It enables real-time event delivery via Socket Mode. Its presence or absence changes what the connector can do, not who it is.

5. **The canonical event model is shared.** `MessageEvent` and its component types are not Slack-specific. Any future messaging connector produces the same shapes.

6. **Slack internals are fully encapsulated.** Zero Slack SDK imports or types appear in any code outside the Slack adapter package.

7. **Listeners are fire-and-forget sinks.** The connector dispatches and moves on. Listener failures are the listener's problem.

8. **Realtime and backfill are separate.** No implicit "catch up" behavior. Backfill is an explicit operation.

9. **Errors are typed and connector-scoped.** Platform exceptions never escape.

10. **The public interface is the same for every messaging connector.** You can swap Slack for Teams by changing the connector instance, not the calling code.

11. **The public interface is synchronous.** All `Connector` protocol methods and `MessageListener.on_message()` are blocking calls. Internal concurrency (WebSocket connections, pollers) is an implementation detail managed by threading. This follows the project's sync/async design principle: interfaces serve their callers, and all known callers are synchronous.

---

## 16. What This Buys You

| Benefit | How |
|---------|-----|
| Slack / Teams / Email all look identical upstream | Shared canonical event model + shared connector interface |
| Bolt can be replaced without rewriting the agent | All Slack specifics are internal to the adapter |
| Any listener plugs in as a generic sink | Listener model is identity-agnostic |
| Bot and user perspectives compose cleanly | Two connectors, same interface, consumer's choice |
| Capabilities are always honest | Computed from construction inputs, not hardcoded or wished into existence |
| Auto-respond logic sits cleanly above the connector | Connector does not decide; it transports |
| Testable in isolation | Fake listeners, no platform dependency for interface tests |

### Mental Model

```
Connector = I/O
Listeners = Sinks
Identity = One token, one entity
Capabilities = Consequence of identity + transport
Composition = Consumer's problem, not connector's