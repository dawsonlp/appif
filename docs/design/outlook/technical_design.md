# Technical Design: Outlook / Microsoft 365 Connector Implementation

**Author**: Senior Engineer
**Date**: 2026-02-21
**Status**: Draft
**Prerequisite**: Architect's design document (`design.md`)

---

## Overview

This document defines the phased implementation plan for the Outlook connector. Each phase has clear deliverables with checkboxes. No phase begins until its predecessor is accepted. Phases follow the construction order: domain objects → domain tests → infrastructure modules → connector assembly → integration tests.

### Technology Choices

| Concern | Library / Tool | Rationale |
|---------|---------------|-----------|
| Microsoft Graph client | `msgraph-sdk` (v1.14+) | Official Microsoft Graph SDK for Python. Already declared in `pyproject.toml` extras under `outlook`. Provides typed request builders, pagination helpers, and serialization. |
| Authentication | `azure-identity` + `msal` | Official Microsoft authentication libraries. `msal` handles OAuth 2.0 token acquisition and refresh. `azure-identity` provides credential classes compatible with the Graph SDK. |
| HTML → text fallback | `beautifulsoup4` + `lxml` (already in project) | Extract readable text from HTML email bodies when no plain-text body is available. |
| Retry with backoff | `tenacity` (already in project from Gmail) | Declarative retry policies. Handles Graph API throttling with `Retry-After` header respect. |
| Structured logging | `structlog` (already in project) | Consistent with project-wide logging. |
| Environment config | `python-dotenv` (already in project) | Load `~/.env` credentials. |
| Testing | `pytest`, `pytest-mock`, `hypothesis` | Unit, integration, property tests per project standards. |

### New Dependencies

Add to `pyproject.toml` under `outlook` extras:

```
outlook = [
    "msgraph-sdk>=1.14",
    "azure-identity>=1.19",
    "msal>=1.31",
]
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `APPIF_OUTLOOK_CLIENT_ID` | Yes | — | Azure AD application (client) ID |
| `APPIF_OUTLOOK_CLIENT_SECRET` | Yes | — | Azure AD client secret |
| `APPIF_OUTLOOK_TENANT_ID` | Yes | — | Azure AD tenant ID (or `"common"` for multi-tenant / personal accounts) |
| `APPIF_OUTLOOK_ACCOUNT` | Yes | — | Target mailbox address (e.g. `user@organization.com`) |
| `APPIF_OUTLOOK_CREDENTIALS_DIR` | No | `~/.config/appif/outlook` | Directory containing per-account credential JSON files |
| `APPIF_OUTLOOK_POLL_INTERVAL_SECONDS` | No | `30` | Polling interval in seconds |
| `APPIF_OUTLOOK_FOLDER_FILTER` | No | `Inbox` | Folder name or ID to watch for new messages |
| `APPIF_OUTLOOK_DELIVERY_MODE` | No | `AUTOMATIC` | `AUTOMATIC` or `ASSISTED` (draft mode) |

### Credential Storage

Credentials are stored as per-account JSON files in `~/.config/appif/outlook/<account>.json`, produced by the consent script (`scripts/outlook_consent.py`). This mirrors the Gmail connector's approach:

1. **Refresh token rotation**: Azure AD may rotate refresh tokens on use. File-based storage lets the connector persist updated tokens without requiring manual env var edits.
2. **Multi-account support**: One file per account, named by email address, enables connecting multiple Outlook accounts.
3. **MSAL token cache**: MSAL provides a `SerializableTokenCache` that can be saved to and loaded from a file. The credential file stores the serialized cache alongside client configuration.

The credential JSON file contains: `client_id`, `client_secret`, `tenant_id`, `token_cache` (serialized MSAL cache containing access token, refresh token, and metadata). See `docs/design/outlook/setup.md` (Phase 0 deliverable) for the full format.

### File Layout

```
src/appif/adapters/outlook/
├── __init__.py              # Public exports: OutlookConnector, OutlookAuth
├── connector.py             # OutlookConnector class
├── _auth.py                 # OutlookAuth protocol + MsalAuth default impl
├── _normalizer.py           # Graph message → MessageEvent
├── _message_builder.py      # MessageContent → Graph message payload
├── _poller.py               # Delta query polling loop
└── _rate_limiter.py         # Retry decorator + error mapping
```

---

## Phase 0: Prerequisites and Credential Setup

**Goal**: Register an Azure AD application, obtain working OAuth 2.0 credentials, and validate Microsoft Graph API access before writing any connector code.

### Azure AD App Registration

Register a new application in the Azure portal (or use an existing one):

- **Supported account types**: Choose based on target audience:
  - "Accounts in this organizational directory only" for single-tenant organizational
  - "Accounts in any organizational directory and personal Microsoft accounts" for broad compatibility
- **Redirect URI**: Add `http://localhost` as a Mobile and Desktop application redirect URI (for the consent script's device code or authorization code flow)
- **API permissions**: Add the following Microsoft Graph **delegated** permissions:
  - `Mail.Read` — read messages in all mail folders
  - `Mail.Send` — send mail as the user
  - `Mail.ReadWrite` — create drafts, manage message properties
  - `User.Read` — read the user's profile
- **Client secret**: Generate a client secret under "Certificates & secrets". Record the secret value (it is shown only once).

### Consent Script

Write `scripts/outlook_consent.py` — a one-time setup utility that:

1. Uses MSAL's `PublicClientApplication` or `ConfidentialClientApplication` to initiate an authorization code flow
2. Opens a browser (or uses device code flow as fallback) for user consent
3. Acquires an initial token set (access token + refresh token)
4. Serializes the MSAL token cache to a JSON file at `{credentials_dir}/{account}.json`
5. Validates the token by calling `GET /me` to confirm the mailbox address
6. Prints success with the account email and token expiration details

The consent script is a setup utility — it does NOT become part of the connector runtime.

### Checklist

- [ ] Register Azure AD application (or document requirements for user to do so)
- [ ] Configure redirect URI as `http://localhost` for desktop flow
- [ ] Add required API permissions (`Mail.Read`, `Mail.Send`, `Mail.ReadWrite`, `User.Read`)
- [ ] Generate client secret and record the value
- [ ] Write `scripts/outlook_consent.py` that:
  - Uses MSAL to run the authorization code flow
  - Saves the token cache to `~/.config/appif/outlook/<account>.json`
  - Validates credentials by calling `GET /me`
- [ ] Validate credentials manually: use the saved tokens to call `/me` and `/me/mailFolders/Inbox/messages?$top=1`
- [ ] Update `.env.example` with all `APPIF_OUTLOOK_*` variables (commented out, with descriptions)
- [ ] Write `docs/design/outlook/setup.md` documenting the full Azure app registration and credential acquisition flow

### Acceptance Criteria

A credential file at `~/.config/appif/outlook/<account>.json` that can be used to call the Microsoft Graph API and retrieve the authenticated user's profile and inbox messages.

---

## Phase 1: Auth Module (`_auth.py`)

**Goal**: Implement the pluggable auth boundary for Outlook OAuth 2.0 using MSAL.

### Component: `OutlookAuth` Protocol

Defines the contract that any auth provider must satisfy:

- Method `get_token()` → returns an access token string suitable for calling Microsoft Graph
- Method `validate()` → confirms credentials are present and structurally valid; raises `NotAuthorized` on failure
- Property `account` → returns the mailbox address string

### Component: `MsalAuth` (Default Implementation)

- Reads `APPIF_OUTLOOK_ACCOUNT` from environment (required — identifies which credential file to load)
- Reads `APPIF_OUTLOOK_CREDENTIALS_DIR` from environment (optional — defaults to `~/.config/appif/outlook`)
- Reads `APPIF_OUTLOOK_CLIENT_ID`, `APPIF_OUTLOOK_CLIENT_SECRET`, `APPIF_OUTLOOK_TENANT_ID` from environment
- On construction:
  1. Loads the MSAL `SerializableTokenCache` from `{credentials_dir}/{account}.json`
  2. Creates an MSAL `ConfidentialClientApplication` with the client credentials and deserialized cache
- `get_token()`:
  1. Attempts `acquire_token_silent()` with the required scopes and the account from the cache
  2. If silent acquisition succeeds → return the access token
  3. If silent acquisition fails (no valid cached token, refresh failed) → raise `NotAuthorized`
  4. After successful acquisition, persist the updated cache to the JSON file (refresh tokens may rotate)
- Token cache persistence: write uses atomic rename pattern (write to temp file, then rename) for safety
- Thread safety: `ConfidentialClientApplication` is thread-safe internally. File writes are serialized by a lock.
- `validate()` checks:
  1. `APPIF_OUTLOOK_ACCOUNT` env var is present and non-empty
  2. `APPIF_OUTLOOK_CLIENT_ID`, `APPIF_OUTLOOK_CLIENT_SECRET`, `APPIF_OUTLOOK_TENANT_ID` env vars are present
  3. The credential file exists at the expected path
  4. The credential file contains a valid serialized MSAL token cache
  5. Does NOT make an API call (that happens at `connect()` time)

### Graph SDK Integration

The `msgraph-sdk` expects a credential object implementing `azure.core.credentials.TokenCredential`. To bridge MSAL to the SDK:

- Implement a thin wrapper class (`MsalTokenCredential`) that implements `get_token(*scopes)` by delegating to the MSAL app's `acquire_token_silent()`.
- This wrapper is used to construct the `GraphServiceClient`.

### Checklist

- [ ] Define `OutlookAuth` protocol in `src/appif/adapters/outlook/_auth.py`
- [ ] Implement `MsalAuth` class
- [ ] Implement `MsalTokenCredential` wrapper for Graph SDK compatibility
- [ ] Load environment variables via `dotenv.load_dotenv(Path.home() / ".env")`
- [ ] Load MSAL token cache from `{credentials_dir}/{account}.json`
- [ ] `get_token()` calls `acquire_token_silent()` → returns access token string
- [ ] `get_token()` persists updated token cache after successful acquisition
- [ ] `validate()` raises `NotAuthorized("outlook", "missing APPIF_OUTLOOK_ACCOUNT")` if account not set
- [ ] `validate()` raises `NotAuthorized("outlook", "missing APPIF_OUTLOOK_CLIENT_ID")` if client ID not set (and similarly for secret, tenant)
- [ ] `validate()` raises `NotAuthorized("outlook", "credential file not found: ...")` if file missing
- [ ] `validate()` raises `NotAuthorized("outlook", "invalid credential file: ...")` if cache malformed
- [ ] Write unit tests: valid file → token acquired; missing file → `NotAuthorized`; missing env → `NotAuthorized`; expired cache with no refresh → `NotAuthorized`
- [ ] Test `MsalTokenCredential` wrapper returns token in the format expected by `azure.core`

### Acceptance Criteria

`MsalAuth` produces a valid access token from a cached MSAL token file. Missing file or env var raises `NotAuthorized`. No API calls made during construction or `validate()`. Token cache is persisted after refresh. `MsalTokenCredential` is compatible with `GraphServiceClient`.

---

## Phase 2: Rate Limiter and Error Mapping (`_rate_limiter.py`)

**Goal**: Centralize Microsoft Graph API error handling and retry logic.

### Component: `graph_retry` Decorator

A `tenacity` retry decorator configured for Microsoft Graph error patterns:

- **Retry on**: HTTP 429 (throttled), 500 (internal server error), 502 (bad gateway), 503 (service unavailable), 504 (gateway timeout)
- **Do not retry on**: 401 (auth — raise `NotAuthorized`), 403 (forbidden/scope — raise `NotAuthorized`), 400 (bad request — raise `ConnectorError`), 404 (not found — raise `TargetUnavailable`)
- **Backoff**: Exponential, starting at 1 second, max 60 seconds
- **Retry-After respect**: On 429, extract the `Retry-After` header value and use it as the minimum wait before retrying
- **Max attempts**: 5
- **Max total wait**: 180 seconds (Graph API throttling can have longer `Retry-After` values than Gmail)
- **On retry exhaustion**: Raise `TransientFailure("outlook", ...)`

### Component: `map_graph_error` Function

Converts Microsoft Graph API errors (from the SDK's exception types or raw HTTP responses) to the appropriate typed connector error.

| HTTP Status | Error Code Pattern | Connector Error |
|-------------|-------------------|-----------------|
| 401 | `InvalidAuthenticationToken`, `CompactToken.*` | `NotAuthorized` |
| 403 | `Authorization_RequestDenied`, `AccessDenied` | `NotAuthorized` |
| 404 | `ErrorItemNotFound`, `MailboxNotFound` | `TargetUnavailable` |
| 429 | `*` (throttled) | `TransientFailure` |
| 400 | `ErrorInvalidRecipients` | `TargetUnavailable` |
| 400 | Other | `ConnectorError` |
| 500, 502, 503, 504 | `*` | `TransientFailure` |
| Other | `*` | `ConnectorError` |

Additionally, check the `error.code` field in the Graph error response for specific conditions:
- `ErrorQuotaExceeded` → `ConnectorError` with "sending quota exceeded"
- `ErrorMessageSizeExceeded` → `ConnectorError` with "message size limit exceeded"
- `ErrorAccessDenied` (conditional access) → `NotAuthorized`

### Graph SDK Error Handling

The `msgraph-sdk` raises `ODataError` (or subclasses) for API failures. The error mapper must:

1. Extract the HTTP status code from `ODataError.response_status_code`
2. Extract the error code from `ODataError.error.code`
3. Extract the error message from `ODataError.error.message`
4. Map to the appropriate connector error using the table above

For network-level errors (timeouts, connection failures), catch `httpx` or `aiohttp` exceptions and wrap in `TransientFailure`.

### Checklist

- [ ] Implement `graph_retry` as a configurable `tenacity.retry` decorator in `src/appif/adapters/outlook/_rate_limiter.py`
- [ ] Implement `map_graph_error(error: ODataError) -> ConnectorError` function
- [ ] Extract `Retry-After` header value for 429 responses and pass to tenacity as minimum wait
- [ ] Ensure the decorator uses a custom `retry_error_callback` that wraps the final error in `TransientFailure`
- [ ] Write unit tests with mocked `ODataError` objects for each status code → expected connector error
- [ ] Write unit tests for `Retry-After` header extraction and minimum wait enforcement
- [ ] Write unit tests verifying retry count and backoff behavior
- [ ] Confirm that non-SDK exceptions (network errors, timeouts) are caught and wrapped in `TransientFailure`

### Acceptance Criteria

`@graph_retry` retries transient errors with backoff, respecting `Retry-After` headers. Non-transient errors map to the correct typed error immediately. No Graph-specific exception types escape this module.

---

## Phase 3: Normalizer (`_normalizer.py`)

**Goal**: Convert Microsoft Graph message objects into canonical `MessageEvent` objects.

### Component: `normalize_message` Function

Signature: `normalize_message(message: dict, account_id: str) -> MessageEvent | None`

Input: A Microsoft Graph message resource (JSON dict from the `/messages` endpoint with body content).

Processing:

1. **Filter**: Return `None` if the message was sent by `account_id` itself (echo suppression). Detect by checking the `from.emailAddress.address` field against `account_id`.
2. **Author**: Extract from the `from.emailAddress` object. Construct `Identity(id=email_address, display_name=name, connector="outlook")`. If no display name, use the local part of the email address.
3. **Timestamp**: Parse `receivedDateTime` (ISO 8601 string) to `datetime` in UTC.
4. **Body extraction**:
   - Graph provides `body.contentType` (`"text"` or `"html"`) and `body.content`
   - If `contentType == "text"` → use `body.content` directly
   - If `contentType == "html"` → strip tags using `BeautifulSoup(html, "lxml").get_text()`
   - Request messages with `$select` including `body` and prefer `text/plain` using the `Prefer: outlook.body-content-type="text"` request header where supported
5. **Attachments**: If `hasAttachments` is `True`, extract attachment metadata from the `attachments` collection (fetched via `$expand=attachments` or a separate call):
   - For each file attachment (where `@odata.type` is `#microsoft.graph.fileAttachment`):
     - Extract `name`, `contentType`, `size`
     - Construct `Attachment(filename=name, mime_type=contentType, size_bytes=size, content_ref=<composite_key>, content=None)`
   - Item attachments and reference attachments: note in metadata but do not create `Attachment` objects (v1 scope)
6. **ConversationRef**: Build from message metadata:
   - `connector="outlook"`, `account_id=account_id`, `type="email_thread"`
   - `opaque_id={"conversation_id": msg["conversationId"], "message_id": msg["id"], "subject": msg["subject"], "to": <sender address for reply targeting>}`
7. **Metadata**: Populate with `folder` (parentFolderId), `importance`, `categories`, `isRead`, `internetMessageHeaders` (if requested).

### Component: `extract_body` Helper

Encapsulates body content extraction and HTML-to-text conversion. Separated for independent testability.

### Component: `extract_attachments` Helper

Encapsulates attachment descriptor extraction from Graph message attachment collections.

### Composite `content_ref` Format

For lazy attachment resolution: `{message_id}:{attachment_id}`

Graph attachment IDs are opaque strings that may contain various characters. Use a separator that is guaranteed not to appear in either ID. Graph message IDs and attachment IDs use URL-safe base64 characters. Use `::` as separator for safety: `{message_id}::{attachment_id}`.

### Checklist

- [ ] Implement `normalize_message` in `src/appif/adapters/outlook/_normalizer.py`
- [ ] Implement `extract_body(message: dict) -> str` helper
- [ ] Implement `extract_attachments(message: dict) -> list[Attachment]` helper
- [ ] Parse `from.emailAddress` for author identity
- [ ] Parse `receivedDateTime` ISO 8601 string to UTC datetime
- [ ] Handle `body.contentType` of `"text"` and `"html"`
- [ ] Echo suppression: return `None` when sender matches `account_id`
- [ ] Store `subject` and sender address in `opaque_id` for reply construction
- [ ] Use `::` as composite key separator for `content_ref`
- [ ] Handle messages with no body gracefully (empty string)
- [ ] Write unit tests with representative Graph API message fixtures:
  - Simple text body message
  - HTML-only body message
  - Message with file attachments
  - Message with item attachment (attached email) → noted in metadata, not in attachments list
  - Message with reference attachment (OneDrive link) → noted in metadata, not in attachments list
  - Echo message (sent by self) → returns `None`
  - Message with missing display name → falls back to local part of email
  - Message with importance and categories → appear in metadata
- [ ] Property test: any valid `normalize_message` output has non-empty `message_id`, `connector == "outlook"`, valid `timestamp`

### Acceptance Criteria

`normalize_message` converts any Graph API message dict into a valid `MessageEvent` or `None`. No Graph-specific types in output. Body extraction handles text and HTML content types.

---

## Phase 4: Message Builder (`_message_builder.py`)

**Goal**: Convert outbound `MessageContent` + `ConversationRef` into a Microsoft Graph message payload suitable for the send or draft endpoints.

### Component: `build_message` Function

Signature: `build_message(conversation: ConversationRef, content: MessageContent, from_address: str) -> dict`

Returns: A dictionary representing a Graph API message resource, suitable for `POST /me/sendMail` (wrapped in `{"message": ...}`) or `POST /me/messages` (draft creation).

Processing:

1. **Determine reply vs new thread**:
   - If `conversation.opaque_id` contains `conversation_id` and `message_id` → this is a reply
   - Otherwise → new thread
2. **For new thread**:
   - `subject`: from `content.metadata.get("subject")` — raise `ConnectorError("outlook", "subject required for new thread")` if missing
   - `toRecipients`: from `conversation.opaque_id["to"]` — the recipient address(es). Supports single address as string or list of addresses.
   - `body`: `{"contentType": "text", "content": content.text}`
3. **For reply**: The message payload is used with the Graph reply endpoints (`/messages/{id}/createReply` to create draft reply, or `/messages/{id}/reply` to send immediately). The reply endpoints automatically populate:
   - `toRecipients` (from the original message's sender)
   - `subject` (with `RE:` prefix)
   - The payload only needs to specify:
     - `body` override: the new reply content as `{"contentType": "text", "content": content.text}`
     - `comment` field (alternative to body for the reply endpoints — use whichever the SDK supports cleanly)
4. **Attachments**: If `content.attachments` is non-empty:
   - For attachments ≤ 3 MB (leaving margin under the 4 MB API limit for base64 encoding overhead): include as inline file attachments in the message payload with `@odata.type: "#microsoft.graph.fileAttachment"`, base64-encoded `contentBytes`
   - For attachments > 3 MB: these must use upload sessions. Flag them for separate handling by the connector's send flow (Phase 6).
   - Size validation: sum of all attachment sizes. If > 150 MB (Graph limit), raise `ConnectorError("outlook", "message exceeds 150 MB limit")`

### Component: `build_attachment_payload` Helper

Converts a domain `Attachment` object into a Graph API file attachment dict:

```
{
    "@odata.type": "#microsoft.graph.fileAttachment",
    "name": attachment.filename,
    "contentType": attachment.mime_type,
    "contentBytes": base64.b64encode(attachment.content).decode()
}
```

### Subject Convention

Same as Gmail connector — the metadata key for subject on new outbound messages is `"subject"`:
- `MessageContent(text="...", metadata={"subject": "Meeting tomorrow"})`
- Missing key on a new-thread send → `ConnectorError`
- Ignored on replies (Graph reply endpoints handle subject propagation)

### Checklist

- [ ] Implement `build_message` in `src/appif/adapters/outlook/_message_builder.py`
- [ ] Implement `build_attachment_payload(attachment: Attachment) -> dict` helper
- [ ] New thread: require `subject` in metadata, require `to` in `opaque_id`
- [ ] Reply: produce payload compatible with Graph reply endpoints (body content only)
- [ ] Handle inline attachments (≤ 3 MB) as base64-encoded `fileAttachment` objects
- [ ] Flag large attachments (> 3 MB) for upload session handling
- [ ] Validate total message size ≤ 150 MB before building payload
- [ ] Handle multiple recipients (to addresses as list)
- [ ] Write unit tests:
  - New thread → correct subject, toRecipients, body
  - Reply → body-only payload (no subject/recipients override)
  - Message with small attachments → inline attachment payloads
  - Message with large attachment → flagged for upload session
  - Missing subject on new thread → `ConnectorError`
  - Message exceeding 150 MB → `ConnectorError`
  - Unicode body → proper encoding
  - Multiple recipients → all included in toRecipients
- [ ] Property test: `build_message` for new threads always includes `subject` and `toRecipients` keys

### Acceptance Criteria

`build_message` produces Graph-API-compatible message payloads. Reply payloads are compatible with the reply endpoints. Size limits enforced. No Graph SDK types in the interface.

---

## Phase 5: Poller (`_poller.py`)

**Goal**: Implement the background polling loop that checks Microsoft Graph for new messages using delta queries and dispatches to listeners.

### Component: `OutlookPoller` Class

Manages the polling lifecycle:

- **State**: `_delta_link: str | None`, `_running: bool`, `_stop_event: threading.Event`
- **Dependencies** (injected): a function to get a valid access token (`get_token: Callable[[], str]`), `account_id`, folder filter, poll interval, a callback `on_new_messages(messages: list[dict]) -> None`
- **Start**: Performs initial delta query to establish baseline `deltaLink`. Spawns a daemon thread running the poll loop.
- **Poll cycle**:
  1. Call the `deltaLink` URL (or initial delta URL on first call) to get changes
  2. Page through results using `@odata.nextLink` until a `@odata.deltaLink` is returned
  3. Filter for new messages (delta responses include created, updated, and deleted; filter for created only based on `@removed` absence)
  4. For each new message: fetch the full message with body and attachment metadata if not already included
  5. Invoke callback with the list of full message dicts
  6. Update `_delta_link` to the new value from the response
  7. Handle errors: token expiration → re-acquire token; other errors → log and continue
- **Stop**: Set `_stop_event`, join the poll thread with a timeout.

### Delta Query Details

Initial delta query URL:
```
GET /me/mailFolders('{folder_id}')/messages/delta?$select=id,subject,from,receivedDateTime,body,hasAttachments,conversationId,parentFolderId,importance,categories&$top=50
```

The initial call returns all current messages (which we skip — we only care about changes after baseline). The response includes a `@odata.deltaLink` that we store.

On subsequent polls:
```
GET {deltaLink}
```

The response returns only changes since the last delta. Each changed message includes:
- Full message properties (for new/modified messages)
- `@removed` annotation (for deleted messages — we ignore these)

### Folder Targeting

- `APPIF_OUTLOOK_FOLDER_FILTER` specifies the folder to watch. Default: `Inbox`.
- Use the well-known folder name (`/me/mailFolders('Inbox')`) or a folder ID.
- To find folder IDs for custom folders: `GET /me/mailFolders?$filter=displayName eq 'FolderName'`
- Multiple folders: if needed, run separate delta queries per folder (similar to Gmail's multi-label approach). For v1, single folder is sufficient.

### Echo Suppression

- Handled by the normalizer (Phase 3), not the poller.
- The poller fetches all new messages in the watched folder; the normalizer filters out self-sent messages.

### Threading Considerations

- The poll loop runs on a single daemon thread — no concurrent polls
- `_delta_link` is updated only by the poll thread — no concurrent writes
- `_stop_event` is a `threading.Event` for clean shutdown signaling
- API calls are wrapped with `@graph_retry` from Phase 2
- The callback is invoked on the poll thread; the connector dispatches to listeners asynchronously (Phase 6)

### Checklist

- [ ] Implement `OutlookPoller` in `src/appif/adapters/outlook/_poller.py`
- [ ] Start: perform initial delta query to establish baseline `deltaLink`
- [ ] Poll loop: call `deltaLink` → collect new messages → invoke callback
- [ ] Handle pagination (`@odata.nextLink`) within a single delta response
- [ ] Filter out deleted messages (`@removed` annotation) and modified-only messages (already emitted)
- [ ] Use `threading.Event` for shutdown signaling (`_stop_event.wait(interval)` for interruptible sleep)
- [ ] Apply `@graph_retry` to all API calls
- [ ] Support configurable folder filter
- [ ] Handle delta link invalidation (Graph returns 410 Gone or resync token) → re-initialize delta, log warning
- [ ] Stop: signal event, join thread with timeout
- [ ] Write unit tests with mocked Graph API responses:
  - Normal poll cycle: delta returns new messages → callback invoked
  - Empty poll cycle: no changes → callback not invoked
  - Deleted messages in delta → filtered out
  - Paginated delta response → all pages processed
  - Delta link invalidation (410) → re-initialize
  - Stop signal → thread exits cleanly
- [ ] Integration test (live API): start poller, send a test email, verify callback fires within 2× poll interval

### Acceptance Criteria

`OutlookPoller` runs a background thread that detects new messages via delta query polling and invokes a callback. Clean start/stop. Handles delta invalidation gracefully. All API errors retried or mapped.

---

## Phase 6: Connector (`connector.py`)

**Goal**: Assemble all components into `OutlookConnector` implementing the `Connector` protocol.

### Component: `OutlookConnector` Class

Constructor parameters:
- `auth: OutlookAuth = None` — defaults to `MsalAuth()` if not provided
- `delivery_mode: str = None` — defaults to `APPIF_OUTLOOK_DELIVERY_MODE` env var or `"AUTOMATIC"`
- `poll_interval: int = None` — defaults to `APPIF_OUTLOOK_POLL_INTERVAL_SECONDS` env var or `30`

Internal state:
- `_status: ConnectorStatus` — lifecycle state machine
- `_client: GraphServiceClient` — built from auth credentials
- `_poller: OutlookPoller | None`
- `_listeners: list[MessageListener]` — registered listeners
- `_listeners_lock: threading.Lock` — guards listener list
- `_executor: ThreadPoolExecutor` — for async listener dispatch
- `_delivery_mode: Literal["AUTOMATIC", "ASSISTED"]`

### Lifecycle: `connect()`

1. Call `auth.validate()` — raises `NotAuthorized` if credentials missing
2. Build `GraphServiceClient` using `MsalTokenCredential` wrapper from the auth module
3. Validate credentials by calling `GET /me` — raises `NotAuthorized` on 401/403
4. Confirm profile email (`mail` or `userPrincipalName`) matches `auth.account`
5. Create and start `OutlookPoller` with `on_new_messages` callback
6. Set status to `CONNECTED`

### Lifecycle: `disconnect()`

1. Stop the poller
2. Shutdown the thread pool executor (wait for in-flight listener calls, timeout 10s)
3. Set `_client = None`
4. Set status to `DISCONNECTED`

### Listener Dispatch: `_on_new_messages` Callback

Invoked by the poller on its thread:

1. For each message dict, call `normalize_message(msg, account_id)` from Phase 3
2. Skip `None` results (filtered messages)
3. For each `MessageEvent`, dispatch to all registered listeners:
   - Acquire `_listeners_lock`, copy the listener list
   - For each listener, submit `listener.on_message(event)` to the thread pool executor
   - Wrap each call in try/except — log errors, never propagate to other listeners

### Discovery: `list_accounts()`

Returns `[Account(id=auth.account, connector="outlook", name=auth.account)]`

### Discovery: `list_targets()`

Returns `[]` (email targets are unbounded per design.md §7).

### Outbound: `send(conversation, content)`

1. Build Graph message payload via `build_message(conversation, content, from_address=auth.account)` from Phase 4
2. Determine reply vs new thread from `conversation.opaque_id`
3. **New thread, AUTOMATIC mode**:
   - Call `POST /me/sendMail` with `{"message": payload}` via Graph SDK with `@graph_retry`
4. **New thread, ASSISTED mode**:
   - Call `POST /me/messages` with `payload` to create a draft via Graph SDK with `@graph_retry`
5. **Reply, AUTOMATIC mode**:
   - Call `POST /me/messages/{message_id}/reply` with the reply payload via Graph SDK with `@graph_retry`
6. **Reply, ASSISTED mode**:
   - Call `POST /me/messages/{message_id}/createReply` to create a draft reply, then update the draft body with the reply content via Graph SDK with `@graph_retry`
7. **Large attachment handling**: If `build_message` flagged any attachments > 3 MB:
   - First create the message as a draft (even in AUTOMATIC mode)
   - For each large attachment: create an upload session via `POST /me/messages/{draftId}/attachments/createUploadSession`, upload the content in chunks
   - If AUTOMATIC: send the draft via `POST /me/messages/{draftId}/send`
   - If ASSISTED: leave as draft
8. Construct and return `SendReceipt`:
   - `message_id`: from API response
   - `connector`: `"outlook"`
   - `timestamp`: now (UTC)
   - `metadata`: `{"conversation_id": ..., "delivery_mode": ..., "draft_id": ... (if ASSISTED)}`

### Backfill: `backfill(account_id, scope)`

1. Validate `account_id` matches the connector's account
2. Build OData filter from `BackfillScope`:
   - `$filter=receivedDateTime ge {start_iso} and receivedDateTime le {end_iso}`
   - `$orderby=receivedDateTime asc`
   - `$select=id,subject,from,receivedDateTime,body,hasAttachments,conversationId,parentFolderId,importance,categories`
3. If folder filter specified in scope, query from `/me/mailFolders('{folder}')/messages`; otherwise from `/me/messages`
4. Page through results following `@odata.nextLink` with `@graph_retry`
5. For each message with `hasAttachments == True`, fetch attachments via `$expand=attachments` or separate call
6. Normalize and dispatch to listeners (same path as realtime)
7. Process oldest-first (ensured by `$orderby=receivedDateTime asc`)
8. Respect throttling via `@graph_retry` and internal pacing

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

1. Split `content_ref` on `::` separator to get `(message_id, attachment_id)`
2. Call `GET /me/messages/{message_id}/attachments/{attachment_id}` with `@graph_retry`
3. For file attachments: base64-decode the `contentBytes` field
4. Return `bytes`

### Checklist

- [ ] Implement `OutlookConnector` class in `src/appif/adapters/outlook/connector.py`
- [ ] Constructor: accept optional `auth`, `delivery_mode`, `poll_interval`; default from env
- [ ] `connect()`: validate auth → build GraphServiceClient → verify profile via `/me` → start poller → set CONNECTED
- [ ] `disconnect()`: stop poller → shutdown executor → set DISCONNECTED
- [ ] `get_status()`: return current `ConnectorStatus`
- [ ] `register_listener()` / `unregister_listener()`: thread-safe with `_listeners_lock`
- [ ] `_on_new_messages`: normalize → dispatch to listeners via thread pool
- [ ] Listener dispatch: each listener in its own executor task, errors logged not propagated
- [ ] `list_accounts()`: return single account
- [ ] `list_targets()`: return empty list
- [ ] `send()` new thread AUTOMATIC: `sendMail` endpoint
- [ ] `send()` new thread ASSISTED: create draft via `POST /me/messages`
- [ ] `send()` reply AUTOMATIC: `reply` endpoint
- [ ] `send()` reply ASSISTED: `createReply` endpoint
- [ ] `send()` large attachment path: create draft → upload session → send (if AUTOMATIC)
- [ ] `backfill()`: OData filter query → page → fetch attachments → normalize → dispatch
- [ ] `backfill()` oldest-first ordering via `$orderby`
- [ ] `get_capabilities()`: return declared capabilities
- [ ] `resolve_attachment()`: fetch attachment bytes by composite `content_ref`
- [ ] Export `OutlookConnector` and `OutlookAuth` from `__init__.py`
- [ ] Write unit tests with mocked Graph SDK client:
  - `connect()` success → status is CONNECTED
  - `connect()` with bad credentials → `NotAuthorized`
  - `disconnect()` → status is DISCONNECTED, poller stopped
  - `send()` AUTOMATIC new thread → `sendMail` called
  - `send()` AUTOMATIC reply → `reply` endpoint called
  - `send()` ASSISTED → draft created
  - `send()` with large attachment → upload session used
  - `backfill()` → messages fetched and dispatched to listeners
  - Listener error does not crash connector
  - `resolve_attachment()` → returns decoded bytes
- [ ] Integration test (live API): connect → send a test email → verify it arrives

### Acceptance Criteria

`OutlookConnector` satisfies the `Connector` protocol. All lifecycle transitions work. Listeners receive normalized events. Send/draft works in both modes with both small and large attachments. Backfill retrieves historical messages. All errors are typed connector errors.

---

## Phase 7: Testing

**Goal**: Comprehensive test coverage following project testing philosophy.

### Unit Tests (No I/O)

| Module | Key Test Cases |
|--------|---------------|
| `_auth.py` | Valid file → credentials; missing file → `NotAuthorized`; missing env → `NotAuthorized`; all four env vars required; `MsalTokenCredential` wrapper returns expected format |
| `_normalizer.py` | Text body, HTML body, file attachments, item/reference attachments in metadata, echo suppression, missing display name, importance/categories in metadata |
| `_message_builder.py` | New thread payload, reply payload, small attachments inline, large attachment flagging, size limit, unicode, multiple recipients |
| `_rate_limiter.py` | Each HTTP status → correct error type; `Retry-After` extraction; retry count; backoff timing; ODataError mapping; network errors |
| `_poller.py` | Normal cycle, empty cycle, deleted messages filtered, pagination, delta invalidation (410), shutdown |
| `connector.py` | Connect/disconnect lifecycle, send modes (4 combinations), large attachment upload, backfill dispatch, listener isolation |

### Integration Tests (Live Microsoft Graph API)

- [ ] `test_outlook_auth_live`: MSAL token cache produces valid access token; `GET /me` returns expected account
- [ ] `test_outlook_send_live`: Send a test email to self; verify it arrives via API query
- [ ] `test_outlook_draft_live`: Create draft; verify it appears in Drafts folder
- [ ] `test_outlook_poll_live`: Start connector, send email from another source, verify listener receives event within 2× poll interval
- [ ] `test_outlook_backfill_live`: Backfill last 1 hour; verify known recent messages appear
- [ ] `test_outlook_attachment_live`: Send email with attachment; verify `resolve_attachment` returns correct bytes
- [ ] `test_outlook_reply_live`: Reply to an existing conversation; verify reply is threaded correctly

### Property Tests (Hypothesis)

- [ ] `normalize_message` output (when not None) always has: non-empty `message_id`, `connector == "outlook"`, valid UTC `timestamp`, non-empty `content.text` or empty string
- [ ] `build_message` for new threads always includes `subject` and `toRecipients` keys
- [ ] `build_message` for replies never includes `subject` or `toRecipients` (handled by Graph reply endpoints)
- [ ] `map_graph_error` never raises an untyped exception — output is always a `ConnectorError` subclass
- [ ] Attachment `content_ref` round-trip: composite key `{msg_id}::{att_id}` can be split back into exactly two components

### Test File Layout

```
tests/
├── unit/
│   ├── test_outlook_auth.py
│   ├── test_outlook_normalizer.py
│   ├── test_outlook_message_builder.py
│   ├── test_outlook_rate_limiter.py
│   ├── test_outlook_poller.py
│   └── test_outlook_connector.py
└── integration/
    └── test_outlook_integration.py
```

### Checklist

- [ ] All unit test files created and passing
- [ ] All integration test files created (gated by env var presence — skip if `APPIF_OUTLOOK_CLIENT_ID` not set)
- [ ] Property tests created and passing
- [ ] No test imports Graph SDK types directly (tests use fixtures and mocks)
- [ ] Coverage: all public functions and all error paths covered

### Acceptance Criteria

Full test suite passes locally. Integration tests pass against a live Outlook account. Property tests validate invariants across random inputs.

---

## Phase 8: CLI and Package Integration

**Goal**: Wire the Outlook connector into the project's CLI and verify package configuration.

### Checklist

- [ ] Update `pyproject.toml` outlook extras to include all required dependencies:
  - `msgraph-sdk>=1.14`
  - `azure-identity>=1.19`
  - `msal>=1.31`
- [ ] Verify `tenacity` is in core dependencies (added during Gmail work)
- [ ] Add CLI commands to `src/cli/` (if applicable — e.g. `appif outlook status`, `appif outlook send`)
- [ ] Update `.env.example` with all `APPIF_OUTLOOK_*` variables
- [ ] Update `readme.md` with Outlook connector section (installation, setup reference, basic usage)
- [ ] Verify `uv pip install -e ".[outlook]"` installs all dependencies cleanly
- [ ] Verify `from appif.adapters.outlook import OutlookConnector, OutlookAuth` works after install
- [ ] Run full test suite: `pytest tests/unit/ -v`

### Acceptance Criteria

Outlook connector is installable via `uv pip install -e ".[outlook]"`. CLI commands (if added) work. All documentation updated. Full unit test suite green.

---

## Component Interaction Summary

```
┌──────────────────────────────────────────────────────────────┐
│                    OutlookConnector                           │
│                    (connector.py)                             │
│                                                              │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ OutlookAuth│  │ OutlookPoller│  │  ThreadPoolExecutor  │ │
│  │  (_auth)   │  │  (_poller)   │  │  (listener dispatch) │ │
│  └─────┬──────┘  └──────┬───────┘  └──────────┬───────────┘ │
│        │                │                     │              │
│        │    ┌───────────▼──────────┐          │              │
│        │    │  Microsoft Graph API │          │              │
│        │    │    (@graph_retry)    │          │              │
│        │    └───────────┬──────────┘          │              │
│        │                │                     │              │
│        │    ┌───────────▼──────────┐   ┌──────▼───────────┐ │
│        │    │    _normalizer       │   │ MessageListener   │ │
│        │    │ (msg → MessageEvent) │   │ .on_message()     │ │
│        │    └──────────────────────┘   └──────────────────┘ │
│        │                                                     │
│        │    ┌──────────────────────┐                        │
│        │    │  _message_builder    │                        │
│        │    │ (MessageContent →    │                        │
│        │    │  Graph API payload)  │                        │
│        │    └──────────────────────┘                        │
└────────┴─────────────────────────────────────────────────────┘
```

### Data Flow: Inbound (Delta Query Polling)

1. `OutlookPoller` calls delta query URL → gets new/changed messages
2. `OutlookPoller` filters for new messages (excludes deleted, modified-only)
3. `OutlookPoller` fetches attachment metadata if `hasAttachments == True`
4. `OutlookPoller` invokes `_on_new_messages` callback on connector
5. Connector calls `normalize_message` for each → `MessageEvent | None`
6. Connector submits `listener.on_message(event)` to thread pool for each listener

### Data Flow: Outbound (Send)

1. Caller invokes `connector.send(conversation_ref, message_content)`
2. Connector calls `build_message` → Graph API message payload dict
3. Connector determines send path (new/reply × automatic/assisted × small/large attachments)
4. Connector calls appropriate Graph API endpoint(s) (with `@graph_retry`)
5. Connector returns `SendReceipt`

### Data Flow: Outbound (Large Attachments)

1. Connector creates message as draft via `POST /me/messages`
2. For each large attachment (> 3 MB):
   - Create upload session via `POST /me/messages/{draftId}/attachments/createUploadSession`
   - Upload content in byte range chunks (recommended: 3.75 MB per chunk)
3. If AUTOMATIC mode: send draft via `POST /me/messages/{draftId}/send`
4. If ASSISTED mode: leave as draft, return draft ID in `SendReceipt`

### Data Flow: Backfill

1. Caller invokes `connector.backfill(account_id, scope)`
2. Connector builds OData `$filter` from scope (date range, folder)
3. Connector pages through `/me/messages` or `/me/mailFolders/{id}/messages` → message objects
4. For each page: fetch attachments if needed → normalize → dispatch to listeners
5. Process oldest-first (via `$orderby=receivedDateTime asc`)

---

## Key Differences from Gmail Connector

| Aspect | Gmail | Outlook |
|--------|-------|---------|
| **API client** | `google-api-python-client` (dict-based) | `msgraph-sdk` (typed SDK with OData) |
| **Auth library** | `google-auth` + `google-auth-oauthlib` | `msal` + `azure-identity` |
| **Token management** | `Credentials` object with built-in refresh | MSAL `SerializableTokenCache` with `acquire_token_silent` |
| **Change detection** | History ID polling | Delta query polling |
| **Cursor** | Monotonically increasing integer (`historyId`) | Opaque URL (`deltaLink`) |
| **Cursor invalidation** | HTTP 404 on history endpoint | HTTP 410 Gone or resync response |
| **Message format** | RFC 2822 MIME (base64url-encoded `raw` field) | JSON payload (Graph API resource format) |
| **Reply mechanism** | Threading headers (`In-Reply-To`, `References`) in MIME | Dedicated reply endpoints (`/messages/{id}/reply`) |
| **Attachment inline limit** | 25 MB total message | 4 MB per request (3 MB practical); 150 MB total via upload sessions |
| **Large attachment upload** | Not needed (25 MB fits in single request) | Upload sessions required for > 4 MB |
| **Organization model** | Labels (flat, multiple per message) | Folders (hierarchical, one per message) |
| **Conversation grouping** | Thread ID + In-Reply-To/References headers | `conversationId` (server-assigned) |
| **Content type preference** | MIME tree walking (text/plain > text/html) | `Prefer: outlook.body-content-type="text"` header or HTML-to-text conversion |

---

## Open Decisions for Implementation Phase

These are decisions the implementing engineer makes during coding:

1. **Thread pool size**: Default worker count for `ThreadPoolExecutor`. Suggest `max_workers=4` — sufficient for listener dispatch without exhausting resources.
2. **Backfill page size**: Number of messages per `$top` parameter. Graph default is 10, max is 1000. Suggest `$top=50` for balanced throughput/throttling.
3. **Poller error handling**: If a poll cycle fails entirely (e.g., network outage), should the poller retry immediately, wait one interval, or apply exponential backoff? Suggest: wait one interval, log the error, continue.
4. **Delta invalidation recovery**: When the delta link is invalidated (410), the poller re-initializes from current state. Should it also trigger an automatic backfill for the gap? Suggest: no — log a warning, let the caller decide to backfill.
5. **Upload session chunk size**: For large attachment uploads, the recommended chunk size is a multiple of 320 KiB. Suggest 3.75 MB (3840 KiB = 12 × 320 KiB) — stays under the 4 MB request limit.
6. **Delta query `$select` fields**: Which message properties to request in the delta query. Include all fields needed by the normalizer to avoid additional per-message fetch calls. If body is not included in delta (it may not be for performance), fetch full message separately.
7. **`Prefer: outlook.body-content-type="text"` usage**: This header asks Graph to return the body as text instead of HTML. Not all message types support it. The normalizer should handle both text and HTML regardless, using this header as an optimization, not a requirement.
8. **Personal vs organizational account detection**: MSAL uses different authority URLs (`https://login.microsoftonline.com/{tenant_id}` vs `https://login.microsoftonline.com/common`). The `APPIF_OUTLOOK_TENANT_ID` env var handles this — `"common"` for personal accounts or multi-tenant, a specific GUID for single-tenant organizational.
