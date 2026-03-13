# Adapters

**Last Updated**: 2026-03-07

Documentation of the working messaging adapters in the `appif` project.

---

## Table of Contents

- [Overview](#overview)
- [Domain Protocol](#domain-protocol)
- [Adapter Summary](#adapter-summary)
- [Gmail Adapter](#gmail-adapter)
- [Outlook Adapter](#outlook-adapter)
- [Slack Adapter](#slack-adapter)
- [Installation](#installation)
- [Environment Variables](#environment-variables)
- [Running Tests](#running-tests)

---

## Overview

Three messaging adapters are implemented: **Gmail**, **Outlook**, and **Slack**. All implement the same `Connector` protocol defined in the domain layer, providing a platform-agnostic interface for sending, receiving, and backfilling messages across different platforms.

Each adapter encapsulates all platform-specific logic (authentication, API calls, message formatting, polling) behind the domain protocol. Upstream code interacts only with domain types — never with Gmail API, Graph API, or Slack SDK types directly.

---

## Domain Protocol

All adapters implement the contracts defined in `src/appif/domain/messaging/`.

### Connector Protocol (`appif.domain.messaging.ports.Connector`)

| Method | Signature | Purpose |
|--------|-----------|---------|
| `connect` | `(account_id: str) → None` | Authenticate and start receiving messages |
| `disconnect` | `() → None` | Tear down connections, stop polling |
| `get_status` | `() → ConnectorStatus` | Current state (DISCONNECTED, CONNECTED, ERROR) |
| `list_accounts` | `() → list[Account]` | Discovered accounts with valid credentials |
| `list_targets` | `(account: Account) → list[Target]` | Addressable destinations (email addresses, channels) |
| `register_listener` | `(listener: MessageListener) → None` | Subscribe to inbound message events |
| `unregister_listener` | `(listener: MessageListener) → None` | Remove a listener |
| `send` | `(target: Target, content: MessageContent) → SendReceipt` | Deliver a message |
| `backfill` | `(scope: BackfillScope) → list[MessageEvent]` | Retrieve historical messages |
| `get_capabilities` | `() → ConnectorCapabilities` | Platform feature declaration |

### MessageListener Protocol (`appif.domain.messaging.ports.MessageListener`)

| Method | Signature | Purpose |
|--------|-----------|---------|
| `on_message` | `(event: MessageEvent) → None` | Fire-and-forget callback for inbound messages |

Design rules: at-least-once delivery, no return values, no backpressure coupling.

### Domain Models (`appif.domain.messaging.models`)

| Model | Purpose |
|-------|---------|
| `MessageEvent` | Canonical inbound message (sender, body, conversation, timestamp, attachments) |
| `MessageContent` | Outbound message payload (text, optional subject, optional conversation, attachments) |
| `Identity` | Platform-agnostic user identity (id, display name, email) |
| `ConversationRef` | Thread/conversation reference (platform, channel, thread_id, opaque_id) |
| `SendReceipt` | Proof of delivery (platform message ID, timestamp) |
| `Account` | A credential set (id, display name, platform) |
| `Target` | Addressable destination (address, display name, target type) |
| `BackfillScope` | Backfill parameters (account, target, since, until, max_count) |
| `ConnectorCapabilities` | Feature flags (delivery mode, threading, attachments, reactions, editing) |
| `ConnectorStatus` | Connection state enum (DISCONNECTED, CONNECTING, CONNECTED, ERROR) |
| `Attachment` | File attachment (filename, content_type, size, content_url, content_bytes) |
| `DeliveryMode` | Enum: AUTOMATIC (push), ASSISTED (poll-on-demand) |

### Domain Errors (`appif.domain.messaging.errors`)

| Error | Meaning |
|-------|---------|
| `ConnectorError` | Base class for all connector errors |
| `NotAuthorized` | Credentials missing, expired, or revoked |
| `TargetUnavailable` | Destination doesn't exist or isn't reachable |
| `TransientFailure` | Temporary error, safe to retry |
| `NotSupported` | Requested operation not available on this platform |

---

## Adapter Summary

| Feature | Gmail | Outlook | Slack |
|---------|-------|---------|-------|
| **Platform** | Gmail API (Google) | Graph API (Microsoft 365) | Slack API (Bolt + Socket Mode) |
| **Delivery Mode** | AUTOMATIC + ASSISTED | AUTOMATIC + ASSISTED | AUTOMATIC |
| **Inbound Method** | `history.list` polling | Delta-query polling | Socket Mode (real-time) |
| **Threading** | ✅ RFC 2822 headers | ✅ Graph `conversationId` | ✅ Slack thread_ts |
| **Attachments** | ✅ Send and receive | ✅ Send and receive | ✅ Send and receive |
| **Reactions** | ❌ | ❌ | ✅ |
| **Editing** | ❌ | ❌ | ✅ |
| **Auth Method** | OAuth 2.0 (file-based tokens) | OAuth 2.0 via MSAL | Bot token (`xoxb-`) or User token (`xoxp-`) + optional App token (`xapp-`) |
| **Credential Storage** | `~/.config/appif/gmail/<account>.json` | `~/.config/appif/outlook/<account>.json` | Environment variables |
| **Multi-account** | ✅ | ✅ | Single workspace |
| **Consent Script** | `scripts/gmail_consent.py` | `scripts/outlook_consent.py` | N/A (token from Slack app) |
| **Unit Tests** | 90 tests (6 files) | 66 tests (6 files) | Tests require `slack_bolt` |
| **Optional Dep Group** | `gmail` | `outlook` | `slack` |

---

## Gmail Adapter

**Location**: `src/appif/adapters/gmail/`

### Module Structure

| Module | Responsibility |
|--------|---------------|
| `connector.py` | `GmailConnector` — implements `Connector` protocol, orchestrates all other modules |
| `_auth.py` | `GmailAuth` protocol + `FileCredentialAuth` — loads OAuth tokens from `~/.config/appif/gmail/<account>.json` |
| `_normalizer.py` | Gmail API message dict → `MessageEvent` — MIME tree walking, HTML stripping, echo suppression, attachment extraction |
| `_message_builder.py` | `MessageContent` → base64url-encoded RFC 2822 message — reply threading via `In-Reply-To`/`References` headers, 25MB size limit |
| `_poller.py` | `GmailPoller` — daemon thread using `history.list` with `startHistoryId` for incremental inbound message detection |
| `_rate_limiter.py` | `call_with_retry` (tenacity-based) + `map_gmail_error` mapping `HttpError` codes to domain errors |
| `__init__.py` | Exports `GmailConnector`, `GmailAuth`, `FileCredentialAuth` |

### Capabilities

- **Delivery**: AUTOMATIC (background polling) and ASSISTED (on-demand via backfill)
- **Send**: New threads and reply-to-thread (subject routing via `ConversationRef.opaque_id`)
- **Backfill**: Fetches messages from a mailbox with date range and count limits
- **Attachments**: Resolved lazily via `attachment_resolver` callback (content fetched from Gmail API on demand)
- **Echo suppression**: Normalizer filters out messages sent by the connected account
- **Subject routing**: Uses email subject as `ConversationRef.opaque_id` for thread correlation

### Credential Setup

1. Create a Google Cloud project and enable the Gmail API
2. Configure OAuth consent screen (Internal for Workspace, External for consumer)
3. Create OAuth client ID (Desktop application type)
4. Set environment variables:
   ```bash
   APPIF_GMAIL_CLIENT_ID=<your-client-id>
   APPIF_GMAIL_CLIENT_SECRET=<your-client-secret>
   ```
5. Run consent flow: `python scripts/gmail_consent.py <account-name>`
6. Token saved to `~/.config/appif/gmail/<account-name>.json`

Full setup guide: [`docs/design/gmail/setup.md`](docs/design/gmail/setup.md)

### Unit Tests (90 tests)

| File | Tests | Covers |
|------|-------|--------|
| `test_gmail_auth.py` | 12 | Token loading, account discovery, error paths |
| `test_gmail_rate_limiter.py` | 19 | Retry logic, error mapping (403, 404, 429, 500, etc.) |
| `test_gmail_normalizer.py` | 19 | MIME parsing, HTML stripping, echo suppression, attachments |
| `test_gmail_message_builder.py` | 11 | RFC 2822 construction, threading headers, size limits |
| `test_gmail_poller.py` | 9 | History-based polling, listener dispatch, error recovery |
| `test_gmail_connector.py` | 20 | Full connector lifecycle, send, backfill, capabilities |

---

## Outlook Adapter

**Location**: `src/appif/adapters/outlook/`

### Module Structure

| Module | Responsibility |
|--------|---------------|
| `connector.py` | `OutlookConnector` — implements `Connector` protocol, orchestrates all other modules |
| `_auth.py` | `MsalAuth` + `MsalTokenCredential` — MSAL-based OAuth with file-persisted token cache |
| `_normalizer.py` | Graph API message dict → `MessageEvent` — HTML→text conversion, echo suppression, attachment composite keys |
| `_message_builder.py` | `MessageContent` → Graph API JSON request body — reply threading, recipient formatting |
| `_poller.py` | `OutlookPoller` — daemon thread using Graph delta queries for incremental mail detection |
| `_rate_limiter.py` | `call_with_retry` (tenacity-based) + `map_graph_error` mapping HTTP status codes to domain errors |
| `__init__.py` | Exports `OutlookConnector` |

### Capabilities

- **Delivery**: AUTOMATIC (background delta-query polling) and ASSISTED (on-demand via backfill)
- **Send**: New threads and reply-to-thread via Graph API
- **Backfill**: Fetches messages using Graph API list endpoint with date filters
- **Attachments**: Send and receive with composite key references
- **Echo suppression**: Normalizer filters messages sent by the connected account
- **Threading**: Uses Graph API `conversationId` for thread correlation

### Credential Setup

1. Register an application in Azure AD (Microsoft Entra ID)
2. Add API permissions: `Mail.ReadWrite`, `Mail.Send`, `User.Read` (delegated)
3. Configure redirect URI: `http://localhost`
4. Set environment variables:
   ```bash
   APPIF_OUTLOOK_CLIENT_ID=<your-client-id>
   APPIF_OUTLOOK_TENANT_ID=<your-tenant-id>
   ```
5. Run consent flow: `python scripts/outlook_consent.py <account-name>`
6. Token cache saved to `~/.config/appif/outlook/<account-name>.json`

Full setup guide: [`docs/design/outlook/setup.md`](docs/design/outlook/setup.md)

### Unit Tests (66 tests)

| File | Tests | Covers |
|------|-------|--------|
| `test_outlook_auth.py` | — | MSAL token acquisition, cache persistence, error handling |
| `test_outlook_rate_limiter.py` | — | Retry logic, Graph API error mapping |
| `test_outlook_normalizer.py` | — | Graph message parsing, HTML stripping, echo suppression |
| `test_outlook_message_builder.py` | — | Graph JSON construction, threading, recipients |
| `test_outlook_poller.py` | — | Delta-query polling, listener dispatch, error recovery |
| `test_outlook_connector.py` | — | Full connector lifecycle, send, backfill, capabilities |

---

## Slack Adapter

**Location**: `src/appif/adapters/slack/`

### Module Structure

| Module | Responsibility |
|--------|---------------|
| `connector.py` | `SlackConnector` — implements `Connector` protocol using Bolt for Socket Mode |
| `_auth.py` | `SlackAuth` protocol + `StaticTokenAuth` — bot token and app-level token from environment |
| `_normalizer.py` | Slack event dict → `MessageEvent` — user resolution, channel mapping, thread handling |
| `_rate_limiter.py` | `call_with_retry` (tenacity-based) + Slack-specific error mapping |
| `_user_cache.py` | `UserCache` — caches `users.info` lookups to avoid redundant API calls |
| `__init__.py` | Exports `SlackConnector`, `SlackAuth`, `StaticTokenAuth` |

### Capabilities

- **Delivery**: AUTOMATIC only (real-time via Socket Mode)
- **Send**: Messages to channels and threads
- **Threading**: Native Slack `thread_ts` support
- **Attachments**: Send and receive
- **Reactions**: ✅ Supported
- **Editing**: ✅ Supported
- **User resolution**: Cached `users.info` lookups for display name and email

### Credential Setup

1. Create a Slack app at [api.slack.com/apps](https://api.slack.com/apps)
2. Add Bot Token Scopes: `channels:history`, `channels:read`, `chat:write`, `users:read`
3. Optionally add User Token Scopes: `channels:history`, `channels:read`, `chat:write`, `users:read`
4. Optionally enable Socket Mode and generate an App-Level Token (`connections:write` scope)
5. Install the app to your workspace
6. Set environment variables:
   ```bash
   APPIF_SLACK_BOT_OAUTH_TOKEN=xoxb-...
   APPIF_SLACK_USER_OAUTH_TOKEN=xoxp-...
   APPIF_SLACK_BOT_APP_LEVEL_TOKEN=xapp-...
   ```

Full setup guide: [`docs/design/slack/setup.md`](docs/design/slack/setup.md)

### Unit Tests

| File | Covers |
|------|--------|
| `test_slack_connector.py` | Auth validation, token classification, capabilities, connector lifecycle, send |
| `test_slack_normalizer.py` | Event normalization, user resolution, echo suppression, thread handling |

Slack tests run as part of the standard test suite: `pytest tests/unit -v`

---

## Installation

All adapter dependencies are included in the core package -- no optional extras needed.

### As a library dependency

```bash
pip install appif
```

### Development (includes test and lint tooling)

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Dependency groups (from `pyproject.toml`)

| Extra | Packages |
|-------|----------|
| (core) | All adapter dependencies: `google-api-python-client`, `google-auth-oauthlib`, `msgraph-sdk`, `azure-identity`, `msal`, `slack-sdk`, `slack-bolt`, `atlassian-python-api`, etc. |
| `dev` | `pytest`, `pytest-asyncio`, `ruff`, `black`, `mypy` |

---

## Environment Variables

All variables use the `APPIF_` prefix. Store secrets in `~/.env` (loaded via `python-dotenv`).

### Gmail

| Variable | Purpose |
|----------|---------|
| `APPIF_GMAIL_CLIENT_ID` | OAuth client ID from Google Cloud Console |
| `APPIF_GMAIL_CLIENT_SECRET` | OAuth client secret |
| `APPIF_GMAIL_POLL_INTERVAL` | Polling interval in seconds (default: 30) |

### Outlook

| Variable | Purpose |
|----------|---------|
| `APPIF_OUTLOOK_CLIENT_ID` | Azure AD application (client) ID |
| `APPIF_OUTLOOK_TENANT_ID` | Azure AD directory (tenant) ID |
| `APPIF_OUTLOOK_POLL_INTERVAL` | Polling interval in seconds (default: 30) |

### Slack

| Variable | Purpose |
|----------|---------|
| `APPIF_SLACK_BOT_OAUTH_TOKEN` | Bot user OAuth token (`xoxb-...`) |
| `APPIF_SLACK_USER_OAUTH_TOKEN` | User OAuth token (`xoxp-...`) — for connecting as yourself |
| `APPIF_SLACK_BOT_APP_LEVEL_TOKEN` | App-level token for Socket Mode (`xapp-...`, optional) |

See [`.env.example`](.env.example) for the full template.

---

## Running Tests

### All unit tests

```bash
pytest tests/unit -v
```

Expected: **323 tests passing** (Gmail + Outlook + Slack + Jira + domain/infrastructure tests).

### Individual adapter tests

```bash
# Gmail only
pytest tests/unit/test_gmail_*.py -v

# Outlook only
pytest tests/unit/test_outlook_*.py -v

# Slack only (requires slack_bolt)
pytest tests/unit/test_slack_*.py -v
```

### Lint and type check

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/
```

---

## Architecture Notes

### Shared Patterns

All three adapters follow the same internal module decomposition:

```
src/appif/adapters/<platform>/
├── __init__.py          # Public exports
├── connector.py         # Connector protocol implementation
├── _auth.py             # Authentication (protocol + implementation)
├── _normalizer.py       # Platform message -> MessageEvent
├── _message_builder.py  # MessageContent -> platform request (email adapters)
├── _poller.py           # Inbound message detection (email adapters)
└── _rate_limiter.py     # Retry + platform error -> domain error mapping
```

Slack differs slightly: no `_message_builder.py` or `_poller.py` (uses Socket Mode push instead of polling), and adds `_user_cache.py` for user lookups.

### Design Decisions

- **Module-level imports**: Each module imports its dependencies at the top level. Test files mock at the module boundary for full isolation.
- **Domain error bypass**: Retry logic in `_rate_limiter.py` raises domain errors (`NotAuthorized`, `TargetUnavailable`) immediately without retry; only `TransientFailure`-eligible errors are retried.
- **Subject routing**: Email adapters use the email subject line as `ConversationRef.opaque_id`, enabling thread correlation across platforms.
- **Credential files**: Gmail and Outlook store OAuth tokens as JSON files under `~/.config/appif/<platform>/<account>.json`, supporting multi-account operation.
- **Lazy attachment resolution**: Gmail resolves attachment content on-demand via a callback; Outlook and Slack include attachment metadata inline.

### Related Documentation

| Document | Location |
|----------|----------|
| Gmail design | [`docs/design/gmail/design.md`](docs/design/gmail/design.md) |
| Gmail technical design | [`docs/design/gmail/technical_design.md`](docs/design/gmail/technical_design.md) |
| Gmail setup | [`docs/design/gmail/setup.md`](docs/design/gmail/setup.md) |
| Outlook design | [`docs/design/outlook/design.md`](docs/design/outlook/design.md) |
| Outlook technical design | [`docs/design/outlook/technical_design.md`](docs/design/outlook/technical_design.md) |
| Outlook setup | [`docs/design/outlook/setup.md`](docs/design/outlook/setup.md) |
| Slack design | [`docs/design/slack/design.md`](docs/design/slack/design.md) |
| Slack setup | [`docs/design/slack/setup.md`](docs/design/slack/setup.md) |

---

## Jira Adapter (Work Tracking)

**Location**: `src/appif/adapters/jira/`

The Jira adapter implements a new **work tracking** domain (`src/appif/domain/work_tracking/`) separate from the messaging domain. It provides CRUD operations for Jira issues with multi-instance support.

### Domain Protocol

Defined in `src/appif/domain/work_tracking/ports.py`:

| Protocol | Methods |
|----------|---------|
| `InstanceRegistry` | `register`, `unregister`, `list_instances`, `set_default`, `get_default` |
| `WorkTracker` | `get_item`, `create_item`, `add_comment`, `get_transitions`, `transition`, `link_items`, `search` |

### Domain Models (`appif.domain.work_tracking.models`)

| Model | Purpose |
|-------|---------|
| `WorkItem` | Canonical work item (key, title, description, status, type, priority, labels, assignee, reporter, links, comments) |
| `CreateItemRequest` | Parameters for creating a new item (project, title, type, description, labels, priority) |
| `ItemIdentifier` | Lightweight reference returned on creation (key + id) |
| `ItemComment` | Comment with author, body, and timestamp |
| `ItemLink` | Typed relationship between items (blocks, relates_to, duplicates, etc.) |
| `TransitionInfo` | Available workflow transition (id + name) |
| `SearchCriteria` | Query parameters (project, status, assignee, labels, text query) |
| `SearchResult` | Paginated search response (items, total, offset, limit) |
| `InstanceInfo` | Registered instance metadata (name, platform, URL, is_default) |
| `ItemAuthor` | User identity (id + display name) |
| `LinkType` | Enum: BLOCKS, BLOCKED_BY, RELATES_TO, DUPLICATES, DUPLICATED_BY, PARENT_OF, CHILD_OF |

### Module Structure

| Module | Responsibility |
|--------|---------------|
| `adapter.py` | `JiraAdapter` -- implements all work tracking operations against a single Jira instance |
| `_auth.py` | YAML config loading + `atlassian.Jira` client creation |
| `_normalizer.py` | Jira REST API dicts to domain `WorkItem`, `ItemComment`, `TransitionInfo` |
| `__init__.py` | Public exports |

### Service Layer

`WorkTrackingService` (`src/appif/domain/work_tracking/service.py`) implements both `InstanceRegistry` and `WorkTracker` protocols. It:

- Loads instances from YAML config at startup
- Routes operations to the correct adapter by instance name
- Supports a default instance for convenience

### Supported Operations

| Operation | Method | Description |
|-----------|--------|-------------|
| Get item | `get_item(key)` | Retrieve full work item with comments and links |
| Create item | `create_item(request)` | Create task/story/bug with labels, priority, parent |
| Add comment | `add_comment(key, body)` | Add comment, returns created comment with ID |
| Get transitions | `get_transitions(key)` | List available workflow transitions |
| Transition | `transition(key, name)` | Execute workflow transition by name |
| Link items | `link_items(from, to, type)` | Create typed link (blocks, relates, duplicates) |
| Search | `search(criteria)` | JQL-based search with pagination |

### Configuration

YAML config at `~/.config/appif/jira/config.yaml` (override with `APPIF_JIRA_CONFIG` env var):

```yaml
instances:
  personal:
    jira:
      url: https://your-domain.atlassian.net
      username: your-email@example.com
      api_token: your-api-token
  work:
    jira:
      url: https://company.atlassian.net
      username: you@company.com
      api_token: another-token

default: personal
```

Supports the same nested format as the `jira-helper` MCP server config. Multiple instances can be registered and switched between at runtime.

### Library

Uses `atlassian-python-api` (v4.0+) -- actively maintained, dict-based API, covers Jira + Confluence + Bitbucket.

### Integration Tests

```bash
pytest tests/integration/test_jira_integration.py -v
```

Exercises the full CRUD lifecycle against a live Jira Cloud instance:
- Create tickets, retrieve and verify fields
- Add comments, verify they appear on re-fetch
- Create links between items, verify relationship
- Transition workflow status
- Search by project and labels

Test tickets are recorded for cleanup: `python scripts/jira_cleanup.py`

### Related Documentation

| Document | Location |
|----------|----------|
| Requirements | [`docs/design/work_tracking/requirements.md`](docs/design/work_tracking/requirements.md) |
| Design | [`docs/design/work_tracking/design.md`](docs/design/work_tracking/design.md) |
| Technical design | [`docs/design/work_tracking/technical_design.md`](docs/design/work_tracking/technical_design.md) |
| Checklist | [`docs/design/work_tracking/checklist.md`](docs/design/work_tracking/checklist.md) |
