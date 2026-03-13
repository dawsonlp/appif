# Technical Design: Gmail Connector Implementation

**Author**: Senior Engineer
**Date**: 2026-02-19
**Status**: Draft
**Prerequisite**: Architect's design document (`design.md`)

---

## Overview

This document defines the phased implementation plan for the Gmail connector. Each phase has clear deliverables with checkboxes. No phase begins until its predecessor is accepted. Phases follow the construction order: domain objects → domain tests → infrastructure modules → connector assembly → integration tests.

### Technology Choices

| Concern | Library / Tool | Rationale |
|---------|---------------|-----------|
| Gmail API client | `google-api-python-client` (v2.x) | Official Google API client. Already declared in `pyproject.toml` extras. |
| OAuth 2.0 credentials | `google-auth` + `google-auth-oauthlib` | Official Google auth libraries. Handle token refresh, credential serialization. Already in `pyproject.toml` extras. |
| HTTP transport | `google-auth-httplib2` or `google-api-python-client` built-in | Bundled with the Google client; no separate HTTP library needed for API calls. |
| MIME message construction | `email.message` (stdlib) | Python stdlib. RFC 2822 compliant. No external dependency. |
| MIME parsing (inbound) | `email.policy`, `email.parser` (stdlib) | Stdlib handles multipart parsing, charset decoding, attachment extraction. |
| HTML → text fallback | `beautifulsoup4` + `lxml` (already in project) | Extract readable text from HTML email bodies when no plain-text part exists. |
| Retry with backoff | `tenacity` (new dependency) | Declarative retry policies. Cleaner than hand-rolled loops. Well-maintained, zero transitive deps. |
| Structured logging | `structlog` (already in project) | Consistent with project-wide logging. |
| Environment config | `python-dotenv` (already in project) | Load `~/.env` credentials. |
| Testing | `pytest`, `pytest-mock`, `hypothesis` | Unit, integration, property tests per project standards. |

### New Dependency

Add `tenacity` to `pyproject.toml` core dependencies (used across connectors, not Gmail-specific):

```
tenacity = ">=9.0"
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `APPIF_GMAIL_CLIENT_ID` | Yes | — | OAuth 2.0 client ID (used by consent script and as fallback) |
| `APPIF_GMAIL_CLIENT_SECRET` | Yes | — | OAuth 2.0 client secret (used by consent script and as fallback) |
| `APPIF_GMAIL_ACCOUNT` | Yes | — | Target mailbox address (e.g. `user@example.com`) |
| `APPIF_GMAIL_CREDENTIALS_DIR` | No | `~/.config/appif/gmail` | Directory containing per-account credential JSON files |
| `APPIF_GMAIL_POLL_INTERVAL_SECONDS` | No | `30` | Polling interval in seconds |
| `APPIF_GMAIL_LABEL_FILTER` | No | `INBOX` | Comma-separated label IDs to watch |
| `APPIF_GMAIL_DELIVERY_MODE` | No | `AUTOMATIC` | `AUTOMATIC` or `ASSISTED` (draft mode) |

### Credential Storage (Changed from Original Design)

**Original design**: stored `APPIF_GMAIL_REFRESH_TOKEN` in `~/.env` alongside client credentials.

**Updated approach**: credentials are stored as per-account JSON files in `~/.config/appif/gmail/<account>.json`, produced by the consent script (`scripts/gmail_consent.py`). This change was made because:

1. **Consumer Gmail tokens expire after 7 days** when the Google Cloud app is in "Testing" mode. Storing the refresh token in `~/.env` means re-running consent requires editing `~/.env` each time. File-based storage lets the consent script overwrite the file directly.
2. **Multi-account support**: one file per account, named by email address, enables connecting multiple Gmail accounts without env var naming collisions.
3. **The `google-auth` library** natively supports `Credentials.from_authorized_user_file()`, which reads this exact JSON format.
4. **Token refresh persistence**: when `google-auth` refreshes an access token, the connector can write the updated token back to the file, avoiding unnecessary re-authorization.

The credential JSON file contains: `token`, `refresh_token`, `token_uri`, `client_id`, `client_secret`, `scopes`. See `docs/design/gmail/setup.md` for the full format.

### File Layout

```
src/appif/adapters/gmail/
├── __init__.py              # Public exports: GmailConnector, GmailAuth
├── connector.py             # GmailConnector class
├── _auth.py                 # GmailAuth protocol + RefreshTokenAuth
├── _normalizer.py           # Gmail message dict → MessageEvent
├── _message_builder.py      # MessageContent → RFC 2822 MIME message
├── _poller.py               # History ID polling loop
└── _rate_limiter.py         # Retry decorator + error mapping
```

---

## Phase 0: Prerequisites and Credential Setup

**Goal**: Obtain working OAuth 2.0 credentials and validate Gmail API access before writing any connector code.

### Checklist

- [ ] Create a Google Cloud project (or use existing) with Gmail API enabled
- [ ] Configure OAuth consent screen (internal or external depending on Workspace vs consumer)
- [ ] Create OAuth 2.0 client ID (type: Desktop application)
- [ ] Document the required OAuth scopes:
  - `https://www.googleapis.com/auth/gmail.readonly` (read messages, history)
  - `https://www.googleapis.com/auth/gmail.send` (send messages)
  - `https://www.googleapis.com/auth/gmail.compose` (create drafts)
  - `https://www.googleapis.com/auth/gmail.modify` (modify labels — needed for history tracking)
- [ ] Write a one-time consent script (`scripts/gmail_consent.py`) that:
  - Runs the OAuth installed-app flow using `google-auth-oauthlib`
  - Opens a browser for user consent
  - Prints the refresh token for the user to save to `~/.env`
  - Does NOT become part of the connector runtime — it is a setup utility
- [ ] Validate credentials manually: use the refresh token to call `users.getProfile` and confirm the mailbox address matches `APPIF_GMAIL_ACCOUNT`
- [ ] Update `.env.example` with all `APPIF_GMAIL_*` variables (commented out, with descriptions)
- [ ] Write `docs/design/gmail/setup.md` documenting the full credential acquisition flow

### Acceptance Criteria

A refresh token in `~/.env` that can be used to call the Gmail API and retrieve the authenticated user's profile.

---

## Phase 1: Domain Model Update — Attachment Type

**Goal**: Add the `Attachment` dataclass to the shared domain model per design.md §12.

### Checklist

- [ ] Add `Attachment` frozen dataclass to `src/appif/domain/messaging/models.py`:
  - `filename: str`
  - `mime_type: str`
  - `size_bytes: int | None = None`
  - `content_ref: str = ""`
  - `content: bytes | None = None`
- [ ] Change `MessageContent.attachments` type annotation from `list` to `list[Attachment]`
  - Default factory remains `field(default_factory=list)`
- [ ] Update `__init__.py` exports to include `Attachment`
- [ ] Verify existing Slack normalizer still works (it currently constructs `MessageContent` with an empty attachment list — no breaking change)
- [ ] Write unit tests for `Attachment` construction and immutability
- [ ] Update existing `test_messaging_models.py` to cover the new type

### Acceptance Criteria

`Attachment` is importable from `domain.messaging.models`. All existing tests pass. `MessageContent.attachments` is typed `list[Attachment]`.

---

## Phase 2: Auth Module (`_auth.py`)

**Goal**: Implement the pluggable auth boundary for Gmail OAuth 2.0.

### Component: `GmailAuth` Protocol

Defines the contract that any auth provider must satisfy:

- Property `credentials` → returns a `google.oauth2.credentials.Credentials` object (or equivalent) suitable for building a Gmail API service client
- Method `validate()` → confirms credentials are present and not permanently revoked; raises `NotAuthorized` on failure
- Property `account` → returns the mailbox address string

### Component: `FileCredentialAuth` (Default Implementation)

Renamed from `RefreshTokenAuth` to reflect the file-based credential approach.

- Reads `APPIF_GMAIL_ACCOUNT` from environment (required — identifies which credential file to load)
- Reads `APPIF_GMAIL_CREDENTIALS_DIR` from environment (optional — defaults to `~/.config/appif/gmail`)
- Loads credentials from `{credentials_dir}/{account}.json` using `google.oauth2.credentials.Credentials.from_authorized_user_file()`
- The credential JSON file is produced by `scripts/gmail_consent.py` (Phase 0)
- Token refresh is handled automatically by the `google-auth` library when the credentials object is used with an authorized HTTP transport
- **Token persistence**: after a successful token refresh, write the updated credentials back to the JSON file so the new access token and potentially rotated refresh token are preserved
- Thread safety: the `google-auth` `Credentials` object handles refresh internally with a lock. File writes use an atomic rename pattern (write to temp file, then rename).
- `validate()` checks:
  1. `APPIF_GMAIL_ACCOUNT` env var is present and non-empty
  2. The credential file exists at the expected path
  3. The credential file contains valid JSON with `refresh_token`, `client_id`, `client_secret` keys
  4. Does NOT make an API call (that happens at `connect()` time)

### Checklist

- [ ] Define `GmailAuth` protocol in `src/appif/adapters/gmail/_auth.py`
- [ ] Implement `FileCredentialAuth` class
- [ ] Load `APPIF_GMAIL_ACCOUNT` and `APPIF_GMAIL_CREDENTIALS_DIR` from env via `dotenv.load_dotenv(Path.home() / ".env")`
- [ ] Load credentials from `{credentials_dir}/{account}.json` using `Credentials.from_authorized_user_file()`
- [ ] Implement `save_credentials()` method for token persistence after refresh
- [ ] `validate()` raises `NotAuthorized("gmail", "missing APPIF_GMAIL_ACCOUNT")` if account not set
- [ ] `validate()` raises `NotAuthorized("gmail", "credential file not found: ...")` if file missing
- [ ] `validate()` raises `NotAuthorized("gmail", "invalid credential file: ...")` if JSON malformed or missing required keys
- [ ] Write unit tests: valid file → credentials constructed; missing file → `NotAuthorized`; missing env → `NotAuthorized`
- [ ] Test that `credentials` property returns an object with `token`, `refresh_token`, `client_id`, `client_secret` attributes
- [ ] Test token persistence: after simulated refresh, file is updated

### Acceptance Criteria

`FileCredentialAuth` produces a valid `Credentials` object from a JSON file. Missing file or env var raises `NotAuthorized`. No API calls made during construction. Token refresh updates are persisted to the credential file.

---

## Phase 3: Rate Limiter and Error Mapping (`_rate_limiter.py`)

**Goal**: Centralize Gmail API error handling and retry logic.

### Component: `gmail_retry` Decorator

A `tenacity` retry decorator configured for Gmail API error patterns:

- **Retry on**: `HttpError` with status 429 (rate limit), 500, 503 (server errors)
- **Do not retry on**: 401 (auth — raise `NotAuthorized`), 403 (scope/permission — raise `NotAuthorized`), 400 (bad request — raise `ConnectorError`), 404 (not found — raise `TargetUnavailable`)
- **Backoff**: Exponential, starting at 1 second, max 60 seconds
- **Max attempts**: 5
- **Max total wait**: 120 seconds
- **On retry exhaustion**: Raise `TransientFailure("gmail", ...)`

### Component: `map_gmail_error` Function

Converts `googleapiclient.errors.HttpError` to the appropriate typed connector error. Used both by the retry decorator (to decide retry-vs-raise) and directly in code paths that don't use the decorator.

| HTTP Status | Connector Error |
|-------------|-----------------|
| 401 | `NotAuthorized` |
| 403 | `NotAuthorized` (scope/permission) |
| 404 | `TargetUnavailable` |
| 429 | `TransientFailure` |
| 400 | `ConnectorError` |
| 500, 503 | `TransientFailure` |
| Other | `ConnectorError` |

Additionally, check the error reason string for specific Gmail conditions:
- `dailyLimitExceeded` → `ConnectorError` with "daily send limit exceeded"
- `userRateLimitExceeded` → `TransientFailure` (retryable)

### Checklist

- [ ] Add `tenacity` to `pyproject.toml` dependencies
- [ ] Implement `gmail_retry` as a configurable `tenacity.retry` decorator in `src/appif/adapters/gmail/_rate_limiter.py`
- [ ] Implement `map_gmail_error(error: HttpError) -> ConnectorError` function
- [ ] Ensure the decorator uses a custom `retry_error_callback` that wraps the final error in `TransientFailure`
- [ ] Write unit tests with mocked `HttpError` objects for each status code → expected connector error
- [ ] Write unit tests verifying retry count and backoff behavior (use `tenacity`'s test utilities or mock `sleep`)
- [ ] Confirm that non-`HttpError` exceptions (network errors, timeouts) are caught and wrapped in `TransientFailure`

### Acceptance Criteria

`@gmail_retry` retries transient errors with backoff. Non-transient errors map to the correct typed error immediately. No Gmail-specific exception types escape this module.

---

## Phase 4: Normalizer (`_normalizer.py`)

**Goal**: Convert Gmail API message dicts into canonical `MessageEvent` objects.

### Component: `normalize_message` Function

Signature: `normalize_message(message: dict, account_id: str) -> MessageEvent | None`

Input: A Gmail API message resource (from `users.messages.get` with `format=full`).

Processing:
1. **Filter**: Return `None` if the message was sent by `account_id` itself (echo suppression). Detect by checking `From` header against `account_id`.
2. **Author**: Parse `From` header using `email.utils.parseaddr`. Construct `Identity(id=email, display_name=name, connector="gmail")`. If no display name, use the local part of the email.
3. **Timestamp**: Convert `internalDate` (milliseconds since epoch) to `datetime` in UTC.
4. **Body extraction**: Walk the MIME payload tree:
   - Prefer `text/plain` part
   - Fall back to `text/html` → strip tags using `BeautifulSoup(html, "lxml").get_text()`
   - Handle `multipart/alternative` (pick plain text from alternatives)
   - Handle base64 encoding (Gmail returns bodies as URL-safe base64)
5. **Attachments**: For each part where `filename` is present in the part headers:
   - Extract `filename`, `mimeType`, `body.size`
   - Construct `Attachment(filename=..., mime_type=..., size_bytes=..., content_ref=body.attachmentId, content=None)`
6. **ConversationRef**: Build from message metadata:
   - `connector="gmail"`, `account_id=account_id`, `type="email_thread"`
   - `opaque_id={"thread_id": msg["threadId"], "message_id": msg["id"], "in_reply_to": <In-Reply-To header>, "references": <References header>, "subject": <Subject header>, "to": <To header>}`
7. **Metadata**: Populate with `labels` (from `labelIds`), `snippet`, and any other headers useful for downstream processing.

### Component: `extract_body` Helper

Encapsulates MIME tree walking and body extraction. Separated for independent testability.

### Component: `extract_attachments` Helper

Encapsulates attachment descriptor extraction from MIME parts.

### Checklist

- [ ] Implement `normalize_message` in `src/appif/adapters/gmail/_normalizer.py`
- [ ] Implement `extract_body(payload: dict) -> str` helper
- [ ] Implement `extract_attachments(payload: dict) -> list[Attachment]` helper
- [ ] Parse `From` header using `email.utils.parseaddr` — handle edge cases (missing name, encoded RFC 2047 names)
- [ ] Handle base64url decoding of body data (`base64.urlsafe_b64decode`)
- [ ] Echo suppression: return `None` when `From` matches `account_id`
- [ ] Store `subject` and `to` in `opaque_id` — needed by `_message_builder` for reply construction
- [ ] Write unit tests with representative Gmail API message fixtures:
  - Simple plain-text message
  - HTML-only message (no plain-text part)
  - Multipart/alternative message
  - Message with attachments
  - Message with inline images (should not appear as attachments)
  - Echo message (sent by self) → returns `None`
  - Message with RFC 2047 encoded From header
- [ ] Property test: any valid `normalize_message` output has non-empty `message_id`, `connector == "gmail"`, valid `timestamp`

### Acceptance Criteria

`normalize_message` converts any Gmail API message dict into a valid `MessageEvent` or `None`. No Gmail-specific types in output. Body extraction handles all common MIME structures.

---

## Phase 5: Message Builder (`_message_builder.py`)

**Goal**: Convert outbound `MessageContent` + `ConversationRef` into an RFC 2822 email message suitable for the Gmail API.

### Component: `build_message` Function

Signature: `build_message(conversation: ConversationRef, content: MessageContent, from_address: str) -> str`

Returns: Base64url-encoded string of the complete RFC 2822 message (the format Gmail's `messages.send` expects as the `raw` field).

Processing:
1. **Determine reply vs new thread**:
   - If `conversation.opaque_id` contains `thread_id` and `message_id` → this is a reply
   - Otherwise → new thread
2. **Headers for reply**:
   - `In-Reply-To`: the `message_id` from `opaque_id` (the RFC 2822 Message-ID, not the Gmail ID — extract from opaque_id `in_reply_to` or construct)
   - `References`: append to existing references chain from `opaque_id`
   - `Subject`: `Re: <original subject>` (from `opaque_id["subject"]`), unless already prefixed
   - `To`: the original sender's address (from `opaque_id["to"]` or derived)
3. **Headers for new thread**:
   - `Subject`: from `content.metadata.get("subject")` — raise `ConnectorError("gmail", "subject required for new thread")` if missing
   - `To`: from `conversation.opaque_id["to"]` — the recipient address
4. **Common headers**: `From`, `Date` (RFC 2822 format), `MIME-Version: 1.0`
5. **Body**: Set as `text/plain; charset=utf-8`
6. **Attachments**: If `content.attachments` is non-empty, construct a `multipart/mixed` message:
   - First part: `text/plain` body
   - Subsequent parts: each attachment with appropriate `Content-Type`, `Content-Disposition: attachment; filename=...`, base64 `Content-Transfer-Encoding`
7. **Size validation**: Calculate total message size. If > 25 MB, raise `ConnectorError("gmail", "message exceeds 25 MB limit")`
8. **Encode**: Base64url-encode the entire message bytes

### Subject Convention

The metadata key for subject on new outbound messages is `"subject"`:
- `MessageContent(text="...", metadata={"subject": "Meeting tomorrow"})`
- Missing key on a new-thread send → `ConnectorError`
- Ignored on replies (subject derived from thread)

### Checklist

- [ ] Implement `build_message` in `src/appif/adapters/gmail/_message_builder.py`
- [ ] Use `email.message.EmailMessage` for construction (modern stdlib API)
- [ ] Handle plain-text-only messages (no attachments)
- [ ] Handle multipart/mixed messages (text + attachments)
- [ ] Validate total message size ≤ 25 MB before encoding
- [ ] Reply construction: set `In-Reply-To`, `References`, derive `Subject` and `To` from `opaque_id`
- [ ] New thread construction: require `subject` in metadata, require `to` in `opaque_id`
- [ ] Base64url encode final output (Gmail API raw format)
- [ ] Write unit tests:
  - Plain text reply → correct threading headers
  - New thread → correct subject, no threading headers
  - Message with attachments → multipart structure
  - Missing subject on new thread → `ConnectorError`
  - Message exceeding 25 MB → `ConnectorError`
  - Unicode body → proper UTF-8 encoding
- [ ] Property test: `build_message` output is always valid base64url, and decoding it yields parseable RFC 2822

### Acceptance Criteria

`build_message` produces Gmail-API-compatible base64url-encoded RFC 2822 messages. Threading headers are correct for replies. Size limits enforced. No Gmail SDK types in the interface.

---

## Phase 6: Poller (`_poller.py`)

**Goal**: Implement the background polling loop that checks Gmail for new messages and dispatches to listeners.

### Component: `GmailPoller` Class

Manages the polling lifecycle:

- **State**: `_history_id: int | None`, `_running: bool`, `_poll_thread: threading.Thread | None`
- **Dependencies** (injected): Gmail API service object, `account_id`, label filter list, poll interval, a callback `on_new_messages(messages: list[dict]) -> None`
- **Start**: Records current history ID via `users.getProfile()` → `historyId`. Spawns a daemon thread running the poll loop.
- **Poll cycle**:
  1. Call `users.history.list(userId="me", startHistoryId=self._history_id, historyTypes=["messageAdded"], labelId=<filter>)`
  2. Page through results if necessary
  3. Collect new message IDs from `messagesAdded` entries
  4. Fetch each full message via `users.messages.get(userId="me", id=msg_id, format="full")`
  5. Invoke callback with the list of full message dicts
  6. Update `_history_id` to the latest from the response
  7. Handle `404` on history (history ID expired) → reset to current profile history ID, log warning
- **Stop**: Set `_running = False`, join the poll thread with a timeout.

### Threading Considerations

- The poll loop runs on a single daemon thread — no concurrent polls
- `_history_id` is updated only by the poll thread — no concurrent writes
- `_running` is a `threading.Event` for clean shutdown signaling
- API calls use the `@gmail_retry` decorator from Phase 3
- The callback (`on_new_messages`) is invoked on the poll thread; the connector is responsible for dispatching to listeners asynchronously (Phase 7)

### Label Filtering

- `APPIF_GMAIL_LABEL_FILTER` parsed as comma-separated label IDs
- Default: `["INBOX"]`
- Passed to `history.list` as `labelId` parameter (Gmail API accepts one label per call; if multiple labels, make multiple history calls per cycle)

### Echo Suppression

- Handled by the normalizer (Phase 4), not the poller
- The poller fetches all messages matching the label filter; the normalizer filters out self-sent messages

### Checklist

- [ ] Implement `GmailPoller` in `src/appif/adapters/gmail/_poller.py`
- [ ] Start: query `users.getProfile` for initial `historyId`
- [ ] Poll loop: `users.history.list` → collect message IDs → `users.messages.get` for each
- [ ] Handle pagination in history list responses (`nextPageToken`)
- [ ] Handle expired history ID (404) → reset to current profile `historyId`, log warning
- [ ] Use `threading.Event` for shutdown signaling (`_stop_event.wait(interval)` for interruptible sleep)
- [ ] Apply `@gmail_retry` to all API calls
- [ ] Support configurable label filter
- [ ] Stop: signal event, join thread with timeout
- [ ] Write unit tests with mocked Gmail API service:
  - Normal poll cycle: history returns new messages → callback invoked
  - Empty poll cycle: no new messages → callback not invoked
  - Expired history ID → reset and retry
  - Paginated history → all pages processed
  - Stop signal → thread exits cleanly
- [ ] Integration test (live API): start poller, send a test email, verify callback fires within 2× poll interval

### Acceptance Criteria

`GmailPoller` runs a background thread that detects new messages via history ID polling and invokes a callback. Clean start/stop. Handles history expiration gracefully. All API errors retried or mapped.

---

## Phase 7: Connector (`connector.py`)

**Goal**: Assemble all components into `GmailConnector` implementing the `Connector` protocol.

### Component: `GmailConnector` Class

Constructor parameters:
- `auth: GmailAuth = None` — defaults to `FileCredentialAuth()` if not provided
- `delivery_mode: str = None` — defaults to `APPIF_GMAIL_DELIVERY_MODE` env var or `"AUTOMATIC"`
- `poll_interval: int = None` — defaults to `APPIF_GMAIL_POLL_INTERVAL_SECONDS` env var or `30`

Internal state:
- `_status: ConnectorStatus` — lifecycle state machine
- `_service` — Gmail API service object (built from auth credentials)
- `_poller: GmailPoller | None`
- `_listeners: list[MessageListener]` — registered listeners
- `_listeners_lock: threading.Lock` — guards listener list
- `_executor: ThreadPoolExecutor` — for async listener dispatch
- `_delivery_mode: Literal["AUTOMATIC", "ASSISTED"]`

### Lifecycle: `connect()`

1. Call `auth.validate()` — raises `NotAuthorized` if credentials missing
2. Build Gmail API service: `build("gmail", "v1", credentials=auth.credentials)`
3. Validate credentials by calling `users.getProfile(userId="me")` — raises `NotAuthorized` on 401/403
4. Confirm profile email matches `auth.account`
5. Create and start `GmailPoller` with `on_new_messages` callback
6. Set status to `CONNECTED`

### Lifecycle: `disconnect()`

1. Stop the poller
2. Shutdown the thread pool executor (wait for in-flight listener calls, timeout 10s)
3. Set `_service = None`
4. Set status to `DISCONNECTED`

### Listener Dispatch: `_on_new_messages` Callback

Invoked by the poller on its thread:

1. For each message dict, call `normalize_message(msg, account_id)` from Phase 4
2. Skip `None` results (filtered messages)
3. For each `MessageEvent`, dispatch to all registered listeners:
   - Acquire `_listeners_lock`, copy the listener list
   - For each listener, submit `listener.on_message(event)` to the thread pool executor
   - Wrap each call in try/except — log errors, never propagate to other listeners

### Discovery: `list_accounts()`

Returns `[Account(id=auth.account, connector="gmail", name=auth.account)]`

### Discovery: `list_targets()`

Returns `[]` (email targets are unbounded per design.md §6).

### Outbound: `send(conversation, content)`

1. Build RFC 2822 message via `build_message(conversation, content, from_address=auth.account)` from Phase 5
2. Depending on `_delivery_mode`:
   - `"AUTOMATIC"`: Call `users.messages.send(userId="me", body={"raw": encoded, "threadId": thread_id})` with `@gmail_retry`
   - `"ASSISTED"`: Call `users.drafts.create(userId="me", body={"message": {"raw": encoded, "threadId": thread_id}})` with `@gmail_retry`
3. Construct and return `SendReceipt`:
   - `message_id`: from API response
   - `connector`: `"gmail"`
   - `timestamp`: now (UTC)
   - `metadata`: `{"thread_id": ..., "delivery_mode": ..., "draft_id": ... (if ASSISTED)}`

### Backfill: `backfill(account_id, scope)`

1. Validate `account_id` matches the connector's account
2. Build query string from `BackfillScope`:
   - `after:YYYY/MM/DD before:YYYY/MM/DD` for date range
   - `label:LABEL` for label filter
3. Page through `users.messages.list(userId="me", q=query)` with `@gmail_retry`
4. Fetch each full message via `users.messages.get(userId="me", id=msg_id, format="full")`
5. Normalize and dispatch to listeners (same path as realtime)
6. Process oldest-first (reverse the default Gmail order)
7. Respect rate limits via `@gmail_retry` and internal pacing

### Capabilities: `get_capabilities()`

Returns:
```
ConnectorCapabilities(
    supports_realtime=False,
    supports_backfill=True,
    supports_threads=True,
    supports_reply=True,
    supports_auto_send=True,
    delivery_mode=self._delivery_mode,
)
```

### Attachment Resolution: `resolve_attachment(content_ref)`

This is a connector-specific method (NOT part of the `Connector` protocol):

1. Call `users.messages.attachments.get(userId="me", messageId=<msg_id>, id=content_ref)` with `@gmail_retry`
2. Base64url-decode the response data
3. Return `bytes`

Note: `content_ref` is the Gmail attachment ID. The message ID needed for the API call must be stored alongside — either embedded in `content_ref` as a composite key (e.g. `message_id:attachment_id`) or available from the `ConversationRef`. Technical decision: use composite key format `{message_id}:{attachment_id}` in `content_ref`.

### Checklist

- [ ] Implement `GmailConnector` class in `src/appif/adapters/gmail/connector.py`
- [ ] Constructor: accept optional `auth`, `delivery_mode`, `poll_interval`; default from env
- [ ] `connect()`: validate auth → build service → verify profile → start poller → set CONNECTED
- [ ] `disconnect()`: stop poller → shutdown executor → set DISCONNECTED
- [ ] `get_status()`: return current `ConnectorStatus`
- [ ] `register_listener()` / `unregister_listener()`: thread-safe with `_listeners_lock`
- [ ] `_on_new_messages`: normalize → dispatch to listeners via thread pool
- [ ] Listener dispatch: each listener in its own executor task, errors logged not propagated
- [ ] `list_accounts()`: return single account
- [ ] `list_targets()`: return empty list
- [ ] `send()`: build message → send or draft depending on mode → return `SendReceipt`
- [ ] `send()` reply path: include `threadId` in API call body
- [ ] `send()` ASSISTED path: create draft instead of sending
- [ ] `backfill()`: query → page → fetch → normalize → dispatch
- [ ] `backfill()` oldest-first ordering
- [ ] `get_capabilities()`: return declared capabilities
- [ ] `resolve_attachment()`: fetch attachment bytes by composite `content_ref`
- [ ] Export `GmailConnector` and `GmailAuth` from `__init__.py`
- [ ] Write unit tests with mocked Gmail API service:
  - `connect()` success → status is CONNECTED
  - `connect()` with bad credentials → `NotAuthorized`
  - `disconnect()` → status is DISCONNECTED, poller stopped
  - `send()` AUTOMATIC → `messages.send` called
  - `send()` ASSISTED → `drafts.create` called
  - `send()` reply → `threadId` included
  - `backfill()` → messages fetched and dispatched to listeners
  - Listener error does not crash connector
  - `resolve_attachment()` → returns decoded bytes
- [ ] Integration test (live API): connect → send a test email → verify it arrives

### Acceptance Criteria

`GmailConnector` satisfies the `Connector` protocol. All lifecycle transitions work. Listeners receive normalized events. Send/draft works in both modes. Backfill retrieves historical messages. All errors are typed connector errors.

---

## Phase 8: Testing

**Goal**: Comprehensive test coverage following project testing philosophy.

### Unit Tests (No I/O)

| Module | Key Test Cases |
|--------|---------------|
| `_auth.py` | Valid env → credentials; missing env → `NotAuthorized`; all four vars required |
| `_normalizer.py` | Plain text, HTML-only, multipart, attachments, inline images, echo suppression, RFC 2047 headers |
| `_message_builder.py` | Reply headers, new thread headers, attachments, size limit, base64url output, unicode |
| `_rate_limiter.py` | Each HTTP status → correct error type; retry count; backoff timing; non-HTTP errors |
| `_poller.py` | Normal cycle, empty cycle, expired history, pagination, shutdown |
| `connector.py` | Connect/disconnect lifecycle, send modes, backfill dispatch, listener isolation |

### Integration Tests (Live Gmail API)

- [ ] `test_gmail_auth_live`: Refresh token produces valid access token; `getProfile` returns expected account
- [ ] `test_gmail_send_live`: Send a test email to self; verify it arrives via API query
- [ ] `test_gmail_draft_live`: Create draft; verify it appears in drafts list
- [ ] `test_gmail_poll_live`: Start connector, send email from another source, verify listener receives event within 2× poll interval
- [ ] `test_gmail_backfill_live`: Backfill last 1 hour; verify known recent messages appear
- [ ] `test_gmail_attachment_live`: Send email with attachment; verify `resolve_attachment` returns correct bytes

### Property Tests (Hypothesis)

- [ ] `normalize_message` output (when not None) always has: non-empty `message_id`, `connector == "gmail"`, valid UTC `timestamp`, non-empty `content.text`
- [ ] `build_message` output is always valid base64url; decoding yields parseable RFC 2822
- [ ] `build_message` for replies always includes `In-Reply-To` and `References` headers
- [ ] `map_gmail_error` never raises an untyped exception — output is always a `ConnectorError` subclass
- [ ] Attachment `content_ref` round-trip: composite key `{msg_id}:{att_id}` can be split back into components

### Test File Layout

```
tests/
├── unit/
│   ├── test_gmail_auth.py
│   ├── test_gmail_normalizer.py
│   ├── test_gmail_message_builder.py
│   ├── test_gmail_rate_limiter.py
│   ├── test_gmail_poller.py
│   └── test_gmail_connector.py
└── integration/
    └── test_gmail_integration.py
```

### Checklist

- [ ] All unit test files created and passing
- [ ] All integration test files created (gated by env var presence — skip if `APPIF_GMAIL_REFRESH_TOKEN` not set)
- [ ] Property tests created and passing
- [ ] No test imports Gmail SDK types directly (tests use fixtures and mocks)
- [ ] Coverage: all public functions and all error paths covered

### Acceptance Criteria

Full test suite passes locally. Integration tests pass against a live Gmail account. Property tests validate invariants across random inputs.

---

## Phase 9: CLI and Package Integration

**Goal**: Wire the Gmail connector into the project's CLI and verify package configuration.

### Checklist

- [ ] Verify `pyproject.toml` gmail extras include all required dependencies:
  - `google-api-python-client`
  - `google-auth-oauthlib`
  - `google-auth-httplib2`
- [ ] Add `tenacity` to core dependencies in `pyproject.toml`
- [ ] Add CLI commands to `src/cli/` (if applicable — e.g. `appif gmail status`, `appif gmail send`)
- [ ] Update `.env.example` with all `APPIF_GMAIL_*` variables
- [ ] Update `readme.md` with Gmail connector section (installation, setup reference, basic usage)
- [ ] Verify `uv pip install -e ".[gmail]"` installs all dependencies cleanly
- [ ] Verify `from appif.adapters.gmail import GmailConnector, GmailAuth` works after install
- [ ] Run full test suite: `pytest tests/unit/ -v`

### Acceptance Criteria

Gmail connector is installable via `uv pip install -e ".[gmail]"`. CLI commands (if added) work. All documentation updated. Full unit test suite green.

---

## Component Interaction Summary

```
┌─────────────────────────────────────────────────────────┐
│                    GmailConnector                        │
│                    (connector.py)                        │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐ │
│  │ GmailAuth│  │ GmailPoll│  │  ThreadPoolExecutor   │ │
│  │ (_auth)  │  │ (_poller) │  │  (listener dispatch)  │ │
│  └────┬─────┘  └────┬─────┘  └───────────┬───────────┘ │
│       │              │                    │             │
│       │   ┌──────────▼─────────┐          │             │
│       │   │   Gmail API calls  │          │             │
│       │   │   (@gmail_retry)   │          │             │
│       │   └──────────┬─────────┘          │             │
│       │              │                    │             │
│       │   ┌──────────▼─────────┐   ┌─────▼──────────┐  │
│       │   │    _normalizer     │   │  MessageListener│  │
│       │   │ (msg → MessageEvent)│   │  .on_message()  │  │
│       │   └────────────────────┘   └────────────────┘  │
│       │                                                 │
│       │   ┌────────────────────┐                       │
│       │   │  _message_builder  │                       │
│       │   │ (MessageContent →  │                       │
│       │   │  RFC 2822 email)   │                       │
│       │   └────────────────────┘                       │
└───────┴─────────────────────────────────────────────────┘
```

### Data Flow: Inbound (Polling)

1. `GmailPoller` calls `history.list` → gets new message IDs
2. `GmailPoller` calls `messages.get` → gets full message dicts
3. `GmailPoller` invokes `_on_new_messages` callback on connector
4. Connector calls `normalize_message` for each → `MessageEvent | None`
5. Connector submits `listener.on_message(event)` to thread pool for each listener

### Data Flow: Outbound (Send)

1. Caller invokes `connector.send(conversation_ref, message_content)`
2. Connector calls `build_message` → base64url RFC 2822 string
3. Connector calls `messages.send` or `drafts.create` (with `@gmail_retry`)
4. Connector returns `SendReceipt`

### Data Flow: Backfill

1. Caller invokes `connector.backfill(account_id, scope)`
2. Connector builds query string from scope
3. Connector pages through `messages.list` → message IDs
4. For each page: fetch full messages → normalize → dispatch to listeners
5. Process oldest-first

---

## Open Decisions for Implementation Phase

These are decisions the implementing engineer makes during coding:

1. **Thread pool size**: Default worker count for `ThreadPoolExecutor`. Suggest `max_workers=4` — sufficient for listener dispatch without exhausting resources.
2. **Backfill page size**: Number of messages per `messages.list` call. Gmail default is 100, max is 500. Suggest 100 for balanced throughput/quota usage.
3. **Poller error handling**: If a poll cycle fails entirely (e.g., network outage), should the poller retry immediately, wait one interval, or apply exponential backoff? Suggest: wait one interval, log the error, continue.
4. **History ID 404 recovery**: When history is expired, the poller resets to current. Should it also trigger an automatic backfill for the gap? Suggest: no — log a warning, let the caller decide to backfill.
5. **`content_ref` composite key separator**: Using `:` as separator (`{msg_id}:{att_id}`). If either ID ever contains `:`, this breaks. Gmail IDs are alphanumeric, so `:` is safe. Document the assumption.
