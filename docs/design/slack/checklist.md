# Slack Connector — Development Checklist

**Author**: Senior Engineer
**Date**: 2026-02-18
**Status**: Complete
**Prerequisite**: Architect's design document (`design.md`)

---

## Overview

This checklist breaks the Slack connector design into implementable tasks, ordered by dependency. Each phase builds on the previous. No phase begins until its predecessor is accepted.

The first two phases establish the **shared messaging connector domain** — types and interfaces that are connector-agnostic. Slack-specific code begins in Phase 3.

---

## Phase 1: Shared Messaging Domain Models

These types are connector-agnostic. They live in the domain layer, separate from the existing content-extraction models (`Article`, `Edition`, `Section`).

- [x] Create `src/appif/domain/messaging/` package
- [x] Define `MessageContent` frozen dataclass — `text: str`, `attachments: list`
- [x] Define `Identity` frozen dataclass — `id: str`, `display_name: str`, `connector: str`
- [x] Define `ConversationRef` frozen dataclass — `connector: str`, `account_id: str`, `type: str`, `opaque_id: dict`
- [x] Define `MessageEvent` frozen dataclass — `message_id`, `connector`, `account_id`, `conversation_ref`, `author`, `timestamp`, `content`, `metadata`
- [x] Define `SendReceipt` frozen dataclass — `external_id: str`, `timestamp: datetime`
- [x] Define `ConnectorCapabilities` frozen dataclass — `supports_realtime`, `supports_backfill`, `supports_threads`, `supports_reply`, `supports_auto_send`, `delivery_mode`
- [x] Define `ConnectorStatus` — lifecycle states (disconnected, connecting, connected, error)
- [x] Define `Account` and `Target` types for discovery results
- [x] Define `BackfillScope` type — account, channel/conversation scope, time range
- [x] Write unit tests for all domain models (construction, immutability, equality)

**Acceptance**: All messaging domain types exist, are frozen, and pass unit tests. No I/O, no Slack imports.

---

## Phase 2: Shared Connector Interface & Error Hierarchy

The public `Connector` protocol and typed errors. Also connector-agnostic.

- [x] Define `MessageListener` protocol — `on_message(event: MessageEvent) -> None`
- [x] Define `Connector` protocol with all public methods:
  - [x] `connect() -> None`
  - [x] `disconnect() -> None`
  - [x] `get_status() -> ConnectorStatus`
  - [x] `list_accounts() -> list[Account]`
  - [x] `list_targets(account_id: str) -> list[Target]`
  - [x] `register_listener(listener: MessageListener) -> None`
  - [x] `unregister_listener(listener: MessageListener) -> None`
  - [x] `send(conversation: ConversationRef, content: MessageContent) -> SendReceipt`
  - [x] `backfill(account_id: str, scope: BackfillScope) -> None`
  - [x] `get_capabilities() -> ConnectorCapabilities`
- [x] Define connector error hierarchy in `src/appif/domain/messaging/errors.py`:
  - [x] `ConnectorError` (base)
  - [x] `NotAuthorized`
  - [x] `NotSupported`
  - [x] `TargetUnavailable`
  - [x] `TransientFailure`
- [x] Write unit tests for error hierarchy (inheritance, instantiation, string representation)

**Acceptance**: Connector protocol and error types exist. A fake/stub connector can be written against the protocol without any platform SDK.

---

## Phase 3: Slack Adapter — Credential Loading & Configuration

- [x] Add `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET` to `.env.example`
- [x] Create `src/appif/adapters/slack/` package
- [x] Implement Slack credential loading from `~/.env` (reuse existing `infrastructure/credentials.py` pattern)
- [x] Validate required tokens are present on connector construction; raise `NotAuthorized` if missing
- [x] Write unit tests for credential validation (missing token, empty token, valid token)

**Acceptance**: `SlackConnector` can be constructed with valid credentials. Missing credentials raise `NotAuthorized` before any network call.

---

## Phase 4: Slack Adapter — Lifecycle (connect / disconnect / status)

- [x] Implement `connect()` — initialize Bolt app, start Socket Mode, transition status to `connected`
- [x] Implement `disconnect()` — tear down Socket Mode, clean up resources, transition status to `disconnected`
- [x] Implement `get_status()` — return current `ConnectorStatus`
- [x] Handle connection failures — map Bolt/SDK exceptions to `NotAuthorized` or `TransientFailure`
- [x] Ensure connector controls its own thread/event loop (no shared loop with upstream)
- [ ] Write integration test: connect with valid token, verify status, disconnect, verify status
- [x] Write unit test: connect with invalid token raises `NotAuthorized`

**Acceptance**: Connector connects to Slack via Socket Mode, reports accurate status, disconnects cleanly. No Slack types leak outside the adapter.

---

## Phase 5: Slack Adapter — Discovery (list_accounts / list_targets)

- [x] Implement `list_accounts()` — return configured workspace(s) as `Account` objects
- [x] Implement `list_targets(account_id)` — call `conversations.list`, map to `Target` objects
- [x] Handle pagination for large workspace channel lists
- [x] Handle rate limits on `conversations.list` — retry with backoff
- [x] Map SDK errors to connector errors (`NotAuthorized`, `TransientFailure`)
- [ ] Write integration test: list targets returns channels the bot can see

**Acceptance**: Discovery methods return normalized `Account` and `Target` types. No Slack SDK types in return values.

---

## Phase 6: Slack Adapter — Inbound Event Normalization

- [x] Implement internal Slack event → `MessageEvent` mapper:
  - [x] Map `message_id` from Slack's `client_msg_id` or `ts`
  - [x] Map `connector` to `"slack"`
  - [x] Map `account_id` from workspace/team ID
  - [x] Build `ConversationRef` with channel ID and optional thread `ts` in `opaque_id`
  - [x] Determine `ConversationRef.type`: `"channel"`, `"thread"`, `"dm"` based on conversation type
  - [x] Resolve `author` — call `users.info`, cache results, build `Identity`
  - [x] Map `timestamp` from Slack `ts` to `datetime`
  - [x] Map `content` — extract text, handle attachments/files
  - [x] Populate `metadata` with raw Slack event fields (subtype, edited, etc.)
- [x] Handle message subtypes: regular messages, bot messages, edited messages, file shares
- [x] Filter out connector's own messages (don't echo bot's outbound back as inbound)
- [x] Write unit tests for mapper with sample Slack event payloads (channel message, DM, thread reply, edited message, file share)

**Acceptance**: Every supported Slack event type maps to a valid `MessageEvent`. Unit tests cover all supported subtypes. No Slack types in the output.

---

## Phase 7: Slack Adapter — Listener Dispatch

- [x] Implement `register_listener(listener)` — add to internal listener set
- [x] Implement `unregister_listener(listener)` — remove from internal listener set
- [x] Implement internal dispatch: normalized events → all registered listeners
- [x] Dispatch via internal queue or executor (fire-and-forget, non-blocking)
- [x] Isolate listener failures — one listener crashing does not affect others or the event pipeline
- [x] Guarantee per-conversation ordering in dispatch
- [x] Log listener exceptions internally (structured logging, do not propagate)
- [x] Wire Bolt event handler → normalizer → dispatcher pipeline
- [x] Write unit test: register listener, emit event, verify listener receives `MessageEvent`
- [x] Write unit test: crashing listener does not prevent other listeners from receiving events
- [x] Write unit test: unregistered listener stops receiving events

**Acceptance**: Listeners receive normalized `MessageEvent` objects. Slow/crashing listeners do not stall ingestion. Ordering is per-conversation.

---

## Phase 8: Slack Adapter — Outbound (send)

- [x] Implement `send(conversation_ref, content)`:
  - [x] Extract channel ID and optional thread `ts` from `ConversationRef.opaque_id`
  - [x] Call `chat.postMessage` via slack_sdk
  - [x] Return `SendReceipt` with Slack's `ts` and timestamp
- [x] Handle rate limits — retry with backoff, raise `TransientFailure` if exhausted
- [x] Handle channel not found / not a member — raise `TargetUnavailable`
- [x] Handle auth failures — raise `NotAuthorized`
- [ ] Write integration test: send message to test channel, verify receipt
- [x] Write unit test: send with invalid `ConversationRef` raises `TargetUnavailable`
- [x] Write unit test: rate limit response triggers retry then `TransientFailure`

**Acceptance**: `send()` delivers messages, returns `SendReceipt`. Errors are typed connector errors, never SDK exceptions.

---

## Phase 9: Slack Adapter — Backfill

- [x] Implement `backfill(account_id, scope)`:
  - [x] Call `conversations.history` (and `conversations.replies` for threaded scope)
  - [x] Normalize each historical message through the same mapper (Phase 6)
  - [x] Dispatch to registered listeners (same pipeline as realtime)
- [x] Handle pagination (Slack cursor-based pagination)
- [x] Handle rate limits — backoff and retry
- [x] Respect `BackfillScope` time range and conversation filters
- [ ] Write integration test: backfill a channel, verify listeners receive historical `MessageEvent` objects in order

**Acceptance**: Backfill retrieves history and emits `MessageEvent` through the listener pipeline. Identical shape to realtime events.

---

## Phase 10: Slack Adapter — Capabilities

- [x] Implement `get_capabilities()` returning `ConnectorCapabilities`:
  - [x] `supports_realtime = True`
  - [x] `supports_backfill = True`
  - [x] `supports_threads = True`
  - [x] `supports_reply = True`
  - [x] `supports_auto_send = True`
  - [x] `delivery_mode = "AUTOMATIC"`
- [x] Write unit test: capabilities match expected values

**Acceptance**: Trivial — but explicitly tested so upstream code can branch on capabilities.

---

## Phase 11: User-Resolution Cache

- [x] Implement internal user cache (Slack user ID → `Identity`)
- [x] Populate on first lookup via `users.info`
- [x] TTL-based expiration (configurable, sensible default)
- [x] Handle cache misses gracefully (re-fetch, don't fail)
- [ ] Write unit test: cache hit avoids second lookup; cache miss triggers fetch

**Acceptance**: Repeated messages from the same user do not trigger repeated `users.info` calls.

---

## Phase 12: Rate-Limit & Retry Strategy

- [x] Centralize rate-limit handling for all Slack API calls
- [x] Respect `Retry-After` header from Slack responses
- [x] Exponential backoff with jitter for transient failures
- [x] Maximum retry count (configurable)
- [x] After retries exhausted, raise `TransientFailure` with context
- [ ] Write unit test: simulated rate-limit triggers backoff, eventual success
- [ ] Write unit test: retries exhausted raises `TransientFailure`

**Acceptance**: All outbound Slack API calls route through the retry/rate-limit layer. No raw SDK retry behavior leaks.

---

## Phase 13: Dependency & Packaging

- [x] Add `slack-bolt` and `slack-sdk` to `pyproject.toml` under `[project.optional-dependencies]` slack group
- [ ] Verify `pipx install '.[slack]'` installs the Slack connector and its dependencies
- [x] Ensure Slack adapter is importable only when extras are installed (graceful `ImportError` if slack-sdk missing)

**Acceptance**: `uv pip install -e ".[slack]"` provides a working Slack connector. Without the extra, importing the adapter raises a clear error.

---

## Phase 14: Integration Testing (End-to-End)

- [ ] Create `tests/integration/test_slack_connector.py`
- [ ] Test full lifecycle: construct → connect → register listener → receive message → send reply → backfill → disconnect
- [ ] Test against a real Slack workspace (test workspace with bot token)
- [ ] Document test workspace setup in `tests/integration/README.md`
- [ ] Verify zero Slack SDK types appear in any assertion — all checks against canonical domain types

> **Note**: Integration tests require a real Slack workspace and valid tokens. Deferred until workspace is provisioned.

**Acceptance**: Full round-trip works against a real Slack workspace. All assertions use domain types only.

---

## Phase 15: Documentation & .env.example

- [x] Update `readme.md` — add Slack to the supported services table
- [x] Update `.env.example` with `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`
- [x] Add Slack setup instructions to README (Slack app creation, required scopes, Socket Mode setup)
- [x] Document required Slack bot scopes:
  - [x] `channels:history`, `channels:read` (public channels)
  - [x] `groups:history`, `groups:read` (private channels)
  - [x] `im:history`, `im:read` (DMs)
  - [x] `mpim:history`, `mpim:read` (group DMs)
  - [x] `chat:write` (send messages)
  - [x] `users:read` (resolve user identity)
  - [x] `connections:write` (Socket Mode)

**Acceptance**: A new developer can set up the Slack connector from README instructions alone.