# Technical Design: Slack Connector v2.1

**Author**: Senior Engineer
**Date**: 2026-03-07
**Status**: Draft
**Implements**: Requirements v2.0, Design v2.1

---

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 2.1 | 2026-03-07 | Sync interface alignment per design.md v2.1 constraint 11. Replaces async SDK with sync SDK + threading. Fixes `ConnectorCapabilities` field names and normalizer return type to match domain models. Preserves all identity refactor decisions from v2.0. |
| 2.0 | 2026-03-07 | Initial identity refactor: one-identity model, token classification, capability computation, graceful degradation, normalizer identity-aware filtering. |

---

## Overview

This technical design bridges the architect's design document (`design.md` v2.1) to implementation. It addresses two concerns:

1. **Identity refactor** (implemented in v2.0, preserved here): one-connector-one-identity model, token classification, capability computation, graceful degradation, identity-aware filtering.

2. **Synchronous interface alignment** (new in v2.1): the `Connector` protocol and `MessageListener` protocol in `ports.py` define synchronous interfaces. The current Slack connector implementation uses async SDK classes (`AsyncApp`, `AsyncWebClient`, `AsyncSocketModeHandler`) and `async def` public methods, violating protocol conformance. This document specifies the changes required to make the implementation synchronous, using threading for internal concurrency (Socket Mode WebSocket).

### Reference Documents

| Document | Role |
|----------|------|
| `requirements.md` v2.0 | What the system must do |
| `design.md` v2.1 | Interfaces and constraints (includes sync interface constraint) |
| This document | How to realize the design in code |

### Design Principle

Per RULES.md and `design.md` v2.1 Section 10:

- Interfaces serve their callers, not their implementations.
- All known callers (CLI, scripts, agents) are synchronous.
- Sync is the safer default: async callers wrap sync code trivially via `asyncio.to_thread()`; the reverse is error-prone.
- Internal concurrency (Socket Mode WebSocket) is an implementation detail managed by `threading.Thread`, not by making the public interface async.

### Reference Implementation

The Gmail connector (`src/appif/adapters/gmail/connector.py`) is the canonical example of this pattern:
- All public methods are `def` (sync)
- Background polling runs on a `threading.Thread`
- Listener dispatch uses `ThreadPoolExecutor`
- `threading.Lock` protects the listener list

The Slack connector must follow the same pattern.

---

## Current State Analysis

### What is correct (identity refactor — preserve)

- `_auth.py`: `SlackAuth` protocol with `identity_token`, `identity_type`, `app_token`. `StaticTokenAuth` with `from_env()`. Token classification via `_classify_token()`. All sync. No changes needed.
- `_user_cache.py`: Sync `UserCache` with sync `WebClient`, sync `resolve()` returning `Identity`. No changes needed.
- `_rate_limiter.py`: Sync rate limiter. No changes needed.
- `__init__.py`: Exports `SlackAuth`, `StaticTokenAuth`, `SlackConnector`. No changes needed.
- `.env.example`: Correct env var names (`APPIF_SLACK_IDENTITY_TOKEN`, `APPIF_SLACK_APP_TOKEN`).

### What must change (sync/async alignment)

| File | Current (async) | Required (sync) |
|------|----------------|-----------------|
| `connector.py` | `AsyncApp`, `AsyncWebClient`, `AsyncSocketModeHandler` | `App`, `WebClient`, `SocketModeHandler` |
| `connector.py` | `async def connect()`, `async def disconnect()`, `async def send_message()` | `def connect()`, `def disconnect()`, `def send()` |
| `connector.py` | `await` calls throughout | Plain sync calls |
| `connector.py` | No `ThreadPoolExecutor` for listener dispatch | `ThreadPoolExecutor` + `threading.Lock` for listeners |
| `connector.py` | `ConnectorCapabilities(can_send=..., can_receive=...)` | `ConnectorCapabilities(supports_reply=..., supports_backfill=...)` — match domain model fields |
| `connector.py` | Returns `Message` | Returns `SendReceipt` from `send()`, dispatches `MessageEvent` to listeners |
| `connector.py` | `add_listener` / `remove_listener` | `register_listener` / `unregister_listener` (match protocol) |
| `connector.py` | String `status` field | `ConnectorStatus` enum |
| `_normalizer.py` | `async def normalize_message()` | `def normalize_message()` |
| `_normalizer.py` | `ResolveUser = Callable[[str], Awaitable[str]]` | `ResolveUser = Callable[[str], str]` |
| `_normalizer.py` | Returns `Message` | Returns `MessageEvent` (domain canonical type) |
| `test_slack_normalizer.py` | `@pytest.mark.asyncio`, `async def`, `await` | Plain sync `def` tests |
| `test_slack_connector.py` | Dummy listener with `async def on_message` | Sync `def on_message` |
| `cli/slack.py` | `async def on_message`, `asyncio.run()` | Sync `def on_message`, direct sync calls |
| `scripts/slack_listener.py` | `async def main()`, `asyncio.run()` | Sync `def main()`, direct sync calls |
| `scripts/slack_proof.py` | `async def main()`, `asyncio.run()` | Sync `def main()`, direct sync calls |

---

## Change 1: Normalizer — Sync with Sync Callback

The normalizer must be a plain synchronous function. The `resolve_user` callback is already sync in `UserCache.resolve()` — the async wrapper was unnecessary.

### Type alias

```python
# Before (wrong):
ResolveUser = Callable[[str], Awaitable[str]]

# After (correct):
ResolveUser = Callable[[str], str]
```

### Function signature

```python
def normalize_message(
    event: dict,
    *,
    team_id: str,
    authenticated_user_id: str,
    resolve_user: ResolveUser,
) -> MessageEvent | None:
```

Changes from current:
- `async def` becomes `def`
- `await resolve_user(user_id)` becomes `resolve_user(user_id)` — it returns `str` directly
- Returns `MessageEvent | None` instead of `Message`
- Returns `None` when the event should be skipped (message from self, unsupported subtype)
- Moves self-message filtering INTO the normalizer (currently in the connector's event handler)

### Return type

The normalizer returns `MessageEvent` (the domain canonical type), not `Message` (which does not exist in the domain models). Fields are mapped as:

| Normalizer output | MessageEvent field |
|-------------------|--------------------|
| Slack `ts` | `message_id` |
| `"slack"` | `connector` |
| `team_id` | `account_id` |
| Constructed `ConversationRef` | `conversation_ref` |
| Resolved `Identity` | `author` |
| Parsed timestamp | `timestamp` |
| `MessageContent(text=...)` | `content` |
| Raw event dict | `metadata` |

The `resolve_user` callback now returns `str` (display name). The normalizer constructs the full `Identity` object using the user ID, display name, and `"slack"` connector tag — consistent with how `UserCache.resolve()` already works. Alternatively, the callback signature can be changed to return `Identity` directly, since `UserCache.resolve()` already returns `Identity`. This is the preferred approach as it avoids duplicating identity construction logic.

### Revised callback type

```python
ResolveUser = Callable[[str], Identity]
```

This aligns with `UserCache.resolve()` which already returns `Identity`.

---

## Change 2: Connector — Sync Public Interface with Threading

### Imports

```python
# Before (async SDK):
from slack_bolt.app.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient

# After (sync SDK):
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
```

Plus threading infrastructure:

```python
import threading
from concurrent.futures import ThreadPoolExecutor
```

### Constructor

```python
def __init__(
    self,
    *,
    identity_token: str,
    app_token: str | None = None,
) -> None:
    self._auth = StaticTokenAuth(
        identity_token=identity_token, app_token=app_token
    )
    self._auth.validate()

    self._status = ConnectorStatus.DISCONNECTED
    self._listeners: list[MessageListener] = []
    self._listeners_lock = threading.Lock()
    self._handler: SocketModeHandler | None = None
    self._socket_thread: threading.Thread | None = None
    self._client: WebClient | None = None
    self._user_cache: UserCache | None = None
    self._authenticated_user_id: str | None = None
    self._team_id: str | None = None
    self._executor: ThreadPoolExecutor | None = None
    self._rate_limiter = SlackRateLimiter()
```

Key changes from current:
- `ConnectorStatus.DISCONNECTED` enum instead of string `"disconnected"`
- `threading.Lock` for listener list (matches Gmail pattern)
- `SocketModeHandler` instead of `AsyncSocketModeHandler`
- `threading.Thread` for Socket Mode background work
- `ThreadPoolExecutor` for listener dispatch
- `WebClient` instead of `AsyncWebClient`

### connect() — Sync with Threading

```python
def connect(self) -> None:
    if self._status == ConnectorStatus.CONNECTED:
        return

    self._status = ConnectorStatus.CONNECTING
    try:
        self._client = WebClient(token=self._auth.identity_token)
        self._user_cache = UserCache(self._client)
        self._executor = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="slack-listener",
        )

        auth_response = self._client.auth_test()
        self._authenticated_user_id = auth_response.get("user_id")
        self._team_id = auth_response.get("team_id")

        logger.info(
            "slack_authenticated",
            extra={
                "team_id": self._team_id,
                "identity_type": self._auth.identity_type,
                "user_id": self._authenticated_user_id,
            },
        )

        if self._auth.app_token:
            app = App(token=self._auth.identity_token)

            @app.event("message")
            def _handle_message(event, say):
                self._on_slack_message(event)

            self._handler = SocketModeHandler(app, self._auth.app_token)
            self._socket_thread = threading.Thread(
                target=self._handler.start,
                name="slack-socket-mode",
                daemon=True,
            )
            self._socket_thread.start()
        else:
            logger.info(
                "slack_no_socket_mode",
                extra={"reason": "no app-level token provided"},
            )

        self._status = ConnectorStatus.CONNECTED

    except (NotAuthorized, TransientFailure):
        self._status = ConnectorStatus.ERROR
        raise
    except Exception as exc:
        self._status = ConnectorStatus.ERROR
        raise TransientFailure(_CONNECTOR_NAME, reason=str(exc)) from exc
```

Key points:
- `def connect()` — sync, not `async def`
- `WebClient` (sync) instead of `AsyncWebClient`
- `App` (sync) instead of `AsyncApp`
- `SocketModeHandler` (sync) instead of `AsyncSocketModeHandler`
- `SocketModeHandler.start()` blocks — runs on a daemon `threading.Thread`
- `@app.event("message")` callback is a plain `def`, not `async def`
- `ConnectorStatus` enum values throughout

### disconnect() — Sync

```python
def disconnect(self) -> None:
    if self._status == ConnectorStatus.DISCONNECTED:
        return

    try:
        if self._handler:
            self._handler.close()
            self._handler = None
        if self._socket_thread and self._socket_thread.is_alive():
            self._socket_thread.join(timeout=5.0)
            self._socket_thread = None
    except Exception as exc:
        logger.warning("slack.disconnect_error", extra={"error": str(exc)})
    finally:
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor = None
        self._client = None
        self._status = ConnectorStatus.DISCONNECTED
        logger.info("slack.disconnected")
```

### send() — Sync, Protocol-Conforming Signature

```python
def send(self, conversation: ConversationRef, content: MessageContent) -> SendReceipt:
    self._ensure_connected()
    self._rate_limiter.acquire()

    channel = conversation.opaque_id.get("channel", "")
    thread_ts = conversation.opaque_id.get("thread_ts")

    kwargs: dict[str, Any] = {
        "channel": channel,
        "text": content.text,
    }
    if thread_ts:
        kwargs["thread_ts"] = thread_ts

    response = self._client.chat_postMessage(**kwargs)
    data = response.data

    return SendReceipt(
        external_id=data.get("ts", ""),
        timestamp=datetime.now(UTC),
    )
```

Key changes:
- `def send()` not `async def send_message()`
- Signature matches `Connector` protocol: `send(self, conversation: ConversationRef, content: MessageContent) -> SendReceipt`
- Uses sync `WebClient` — no `await`
- Returns `SendReceipt` (domain type), not `Message`

### get_capabilities() — Correct Field Names

```python
def get_capabilities(self) -> ConnectorCapabilities:
    has_app_token = self._auth.app_token is not None
    return ConnectorCapabilities(
        supports_realtime=has_app_token,
        supports_backfill=True,
        supports_threads=True,
        supports_reply=True,
        supports_auto_send=True,
        delivery_mode="AUTOMATIC" if has_app_token else "MANUAL",
    )
```

Uses the actual `ConnectorCapabilities` fields from the domain model, not the incorrect `can_send` / `can_receive` / `supports_reactions` / `supports_attachments` fields.

### get_status() — Returns Enum

```python
def get_status(self) -> ConnectorStatus:
    return self._status
```

### register_listener / unregister_listener — Protocol Names

```python
def register_listener(self, listener: MessageListener) -> None:
    with self._listeners_lock:
        if listener not in self._listeners:
            self._listeners.append(listener)

def unregister_listener(self, listener: MessageListener) -> None:
    with self._listeners_lock:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass
```

Thread-safe with `threading.Lock`, matching Gmail pattern.

### Internal Event Dispatch

```python
def _on_slack_message(self, event: dict) -> None:
    if event.get("subtype") in (
        "message_changed", "message_deleted",
        "channel_join", "channel_leave",
    ):
        return

    if event.get("user") == self._authenticated_user_id:
        return

    message_event = normalize_message(
        event,
        team_id=self._team_id or "",
        authenticated_user_id=self._authenticated_user_id or "",
        resolve_user=self._user_cache.resolve,
    )
    if message_event is not None:
        self._dispatch_event(message_event)

def _dispatch_event(self, event: MessageEvent) -> None:
    with self._listeners_lock:
        listeners = list(self._listeners)

    for listener in listeners:
        self._executor.submit(self._safe_listener_call, listener, event)

@staticmethod
def _safe_listener_call(listener, event) -> None:
    try:
        listener.on_message(event)
    except Exception:
        logger.exception(
            "slack.listener_error",
            extra={"listener": type(listener).__name__},
        )
```

All sync. `normalize_message()` called synchronously. `_user_cache.resolve` is already sync. Listener dispatch via `ThreadPoolExecutor.submit()` — fire-and-forget, matching Gmail pattern.

### listen_forever() — Sync Blocking

```python
def listen_forever(self) -> None:
    """Block until disconnect or interrupt — for scripts / CLI."""
    try:
        while self._status == ConnectorStatus.CONNECTED:
            time.sleep(1)
    except KeyboardInterrupt:
        self.disconnect()
```

Uses `time.sleep()` instead of `asyncio.sleep()`.

---

## Change 3: Test Updates

### Normalizer Tests — Remove Async

Every test in `test_slack_normalizer.py`:
- Remove `@pytest.mark.asyncio` decorator
- Change `async def test_...` to `def test_...`
- Remove `await` from `normalize_message()` calls
- Change `_fake_resolve` from `async def` to plain `def`

The test assertions remain identical — they test the same domain outcomes.

### Connector Tests — Sync Listener, Correct Capability Fields

In `test_slack_connector.py`:
- Dummy listener class: change `async def on_message` to `def on_message`
- Capability assertions: replace `caps.can_send`, `caps.can_receive`, `caps.supports_reactions`, `caps.supports_attachments` with `caps.supports_reply`, `caps.supports_backfill`, `caps.supports_auto_send`, `caps.delivery_mode`
- Status assertions: use `ConnectorStatus.DISCONNECTED` instead of string `"disconnected"`
- Method names: `register_listener` / `unregister_listener` instead of `add_listener` / `remove_listener`

---

## Change 4: CLI — Sync Listener, Direct Calls

In `cli/slack.py`:
- `_RichPrinter.on_message`: change `async def` to `def`
- Remove `asyncio.run()` wrapper
- Call `connector.connect()` and `connector.listen_forever()` directly
- Use `register_listener()` instead of `add_listener()`
- Update capability field names in the display table

---

## Change 5: Scripts — Plain Sync

### `scripts/slack_listener.py`

- `_PrintListener.on_message`: change `async def` to `def`
- `main()`: change `async def` to plain `def`
- Remove `asyncio.run()`, call connector methods directly
- Use `register_listener()` instead of `add_listener()`
- Update capability field names in the report

### `scripts/slack_proof.py`

- `_Printer.on_message`: change `async def` to `def`
- `main()`: change `async def` to plain `def`
- Remove `asyncio.run()`, call connector methods directly
- Use `register_listener()` instead of `add_listener()`

---

## What Is NOT Changing

| Component | Reason |
|-----------|--------|
| `_auth.py` | Already sync. Identity refactor is correct. |
| `_user_cache.py` | Already sync. Uses sync `WebClient` and sync `resolve()`. |
| `_rate_limiter.py` | Already sync. |
| `__init__.py` | Exports unchanged. |
| `.env.example` | Already has correct env var names. |
| `ports.py` | Domain protocol already defines sync interfaces. |
| `models.py` | Domain models are correct. |
| Error hierarchy | No changes needed. |
| `requirements.md` | Approved, no changes. |
| `design.md` | v2.1 already includes sync constraint. |

---

## Construction Order

Per RULES.md: domain-adjacent first, tests second, infrastructure last.

| Step | What | Files |
|------|------|-------|
| 1 | Normalizer sync rewrite (function + type alias + return type) | `_normalizer.py` |
| 2 | Normalizer tests sync rewrite | `test_slack_normalizer.py` |
| 3 | Connector sync rewrite (imports, constructor, lifecycle, send, capabilities, dispatch, listener management) | `connector.py` |
| 4 | Connector tests sync rewrite (capability fields, status enum, method names, listener sync) | `test_slack_connector.py` |
| 5 | CLI sync rewrite | `cli/slack.py` |
| 6 | Scripts sync rewrite | `scripts/slack_listener.py`, `scripts/slack_proof.py` |
| 7 | Verify full test suite passes | All |

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| `SocketModeHandler.start()` blocks indefinitely on calling thread | Low | Runs on daemon `threading.Thread`; `disconnect()` calls `handler.close()` + `thread.join(timeout=5)` |
| Bolt sync `App` event callbacks run on Bolt's internal thread | Low | Callbacks delegate to `_on_slack_message` which dispatches via `ThreadPoolExecutor` — no shared state mutation without lock |
| `Message` type referenced in existing code does not exist in domain models | Medium | Replace with `MessageEvent` in normalizer return type and all call sites |
| Capability field mismatch causes test failures | Low | Update all test assertions to use correct domain field names in same step as connector changes |
| Tests become order-dependent during refactor | Medium | Run full test suite after each step; no step leaves tests broken |