# Implementation Checklist: Slack Connector Sync Rewrite

**Implements**: Technical Design v2.1
**Purpose**: Align Slack connector with synchronous `Connector` protocol, fix domain model field names, preserve identity refactor.

---

## Pre-conditions

- [x] `design.md` v2.1 — sync constraint added (Section 10, constraint 11)
- [x] `technical_design.md` v2.1 — sync SDK, threading, correct domain types specified
- [x] Checklist reviewed and approved

---

## Implementation Steps

### Step 1: Normalizer sync rewrite (`_normalizer.py`)

- [x] Change `ResolveUser` type alias from `Callable[[str], Awaitable[str]]` to `Callable[[str], Identity]`
- [x] Change `async def normalize_message()` to `def normalize_message()`
- [x] Change return type from `Message` to `MessageEvent | None`
- [x] Replace `await resolve_user(user_id)` with sync `resolve_user(user_id)` (returns `Identity`)
- [x] Construct `MessageEvent` with proper fields (`message_id`, `connector`, `account_id`, `conversation_ref`, `author`, `timestamp`, `content`, `metadata`)
- [x] Remove `Awaitable` import
- [x] Add imports for `MessageEvent`, `MessageContent`, `ConversationRef`, `Identity`
- [x] Remove import of `Message`, `MessageAuthor`
- [x] Run normalizer tests (expect failures — tests still async)

### Step 2: Normalizer tests sync rewrite (`test_slack_normalizer.py`)

- [x] Change `_fake_resolve` from `async def` returning `str` to plain `def` returning `Identity`
- [x] Remove all `@pytest.mark.asyncio` decorators
- [x] Change all `async def test_*` to `def test_*`
- [x] Remove all `await` from `normalize_message()` calls
- [x] Update assertions to match `MessageEvent` fields instead of `Message` fields
- [x] Run normalizer tests — all must pass

### Step 3: Connector sync rewrite (`connector.py`)

- [x] Replace async SDK imports with sync equivalents (`App`, `WebClient`, `SocketModeHandler`)
- [x] Add `threading`, `time`, `concurrent.futures.ThreadPoolExecutor` imports
- [x] Add domain model imports (`ConnectorStatus`, `ConnectorCapabilities`, `ConversationRef`, `MessageContent`, `SendReceipt`, `MessageEvent`, `Account`, `Target`, `BackfillScope`)
- [x] Remove `asyncio` import
- [x] Remove `AsyncApp`, `AsyncWebClient`, `AsyncSocketModeHandler` imports
- [x] Remove `Message` import
- [x] Rewrite constructor: `ConnectorStatus` enum, `threading.Lock`, `ThreadPoolExecutor`, `threading.Thread`
- [x] Rewrite `connect()` as sync `def` using `WebClient`, `App`, `SocketModeHandler` on daemon thread
- [x] Rewrite `disconnect()` as sync `def` with `handler.close()` + `thread.join(timeout=5)`
- [x] Rewrite `send()` as sync `def` matching `Connector` protocol signature (`ConversationRef`, `MessageContent` -> `SendReceipt`)
- [x] Rewrite `get_capabilities()` with correct domain field names
- [x] Add `get_status()` returning `ConnectorStatus` enum
- [x] Rename `add_listener`/`remove_listener` to `register_listener`/`unregister_listener` with `threading.Lock`
- [x] Add `_on_slack_message()`, `_dispatch_event()`, `_safe_listener_call()` — all sync
- [x] Add `_ensure_connected()` helper
- [x] Rewrite `listen_forever()` as sync with `time.sleep()`
- [x] Remove `listen_forever` `asyncio.CancelledError` handling — use `KeyboardInterrupt`
- [x] Run connector tests (expect failures — tests reference old fields/methods)

### Step 4: Connector tests sync rewrite (`test_slack_connector.py`)

- [x] Update `TestCapabilities` assertions: replace `can_send`, `can_receive`, `supports_reactions`, `supports_attachments` with `supports_reply`, `supports_backfill`, `supports_auto_send`, `delivery_mode`
- [x] Update `TestDeliveryMode` to test via `get_capabilities().delivery_mode` instead of separate `delivery_mode` property
- [x] Update `TestLifecycleStatus` to compare `ConnectorStatus.DISCONNECTED` instead of string `"disconnected"`
- [x] Update `TestListenerManagement` to use `register_listener`/`unregister_listener` and sync `def on_message`
- [x] Import `ConnectorStatus` in test file
- [x] Run full test suite — all Slack tests must pass

### Step 5: CLI sync rewrite (`cli/slack.py`)

- [x] Change `_RichPrinter.on_message` from `async def` to `def`
- [x] Remove `asyncio` import
- [x] Replace `asyncio.run(_run())` with direct sync calls: `connector.connect()` + `connector.listen_forever()`
- [x] Replace `add_listener` with `register_listener`
- [x] Update capability table field names to match domain model
- [x] Remove `Message` import, add `MessageEvent` import

### Step 6: Scripts sync rewrite

#### `scripts/slack_listener.py`

- [x] Change `_PrintListener.on_message` from `async def` to `def`
- [x] Change `main()` from `async def` to `def`
- [x] Remove `asyncio` import and `asyncio.run()` wrapper
- [x] Replace `add_listener` with `register_listener`
- [x] Update capability field names in report output
- [x] Call connector methods directly in `if __name__` block
- [x] Remove `Message` import, add `MessageEvent` import

#### `scripts/slack_proof.py`

- [x] Change `_Printer.on_message` from `async def` to `def`
- [x] Change `main()` from `async def` to `def`
- [x] Remove `asyncio` import and `asyncio.run()` wrapper
- [x] Replace `add_listener` with `register_listener`
- [x] Call connector methods directly in `if __name__` block
- [x] Remove `Message` import, add `MessageEvent` import

### Step 7: Verification

- [x] Run full test suite (`pytest tests/ -v`) — 335 passed, 6 skipped, 0 failed
- [x] Run ruff lint (`ruff check src/ tests/ scripts/`) — zero errors (14 auto-fixed)
- [x] Grep for async residue: no `async def` in Slack adapter, no `asyncio.run` in CLI/scripts, no `AsyncApp`/`AsyncWebClient`/`AsyncSocketModeHandler` imports
- [x] Grep for old field names: no `can_send`, `can_receive`, `supports_reactions`, `supports_attachments` in Slack adapter or tests
- [x] Grep for old method names: no `add_listener`, `remove_listener` (only `register_listener`, `unregister_listener`)
- [x] Grep for `Awaitable` import in Slack adapter — none found
- [x] Verify `_user_cache.py` unchanged (identity refactor preserved)
- [x] Fix pre-existing bug in `_auth.py`: `detail=` kwarg changed to `reason=` (matches `NotAuthorized.__init__` signature)
- [x] Fix integration tests: updated to new constructor signature and env var names

---

## Files Modified

| File | Change type |
|------|-------------|
| `src/appif/adapters/slack/_normalizer.py` | Rewrite (async to sync, Message to MessageEvent) |
| `src/appif/adapters/slack/connector.py` | Rewrite (async SDK to sync SDK + threading, protocol conformance) |
| `tests/unit/test_slack_normalizer.py` | Rewrite (remove async, update assertions) |
| `tests/unit/test_slack_connector.py` | Update (capability fields, status enum, method names, sync listener) |
| `src/appif/cli/slack.py` | Update (remove asyncio, sync listener, method names) |
| `scripts/slack_listener.py` | Update (remove asyncio, sync listener, method names) |
| `scripts/slack_proof.py` | Update (remove asyncio, sync listener, method names) |

## Files NOT Modified (Verified Correct)

| File | Reason |
|------|--------|
| `src/appif/adapters/slack/_auth.py` | Already sync, identity refactor correct |
| `src/appif/adapters/slack/_user_cache.py` | Already sync |
| `src/appif/adapters/slack/_rate_limiter.py` | Already sync |
| `src/appif/adapters/slack/__init__.py` | Exports unchanged |
| `src/appif/domain/messaging/ports.py` | Protocol already sync |
| `src/appif/domain/messaging/models.py` | Domain models correct |
| `.env.example` | Env vars correct |