# Identity Refactor Implementation Checklist

**Status**: Complete
**Author**: Senior Engineer
**Date**: 2026-03-07

## Construction Order

Following RULES.md: Domain objects first → Domain tests → Transaction Scripts → Infrastructure last.
The domain layer (`ports.py`, `models.py`, `errors.py`) requires **no changes** — only the adapter layer is refactored.

---

## Checklist

### Domain Layer (no changes required)

- [x] Confirmed: `ConnectorCapabilities`, `Connector` protocol, error hierarchy — no modifications needed.

### Adapter Layer — Auth (Step 1)

- [x] **Refactor `SlackAuth` protocol and `StaticTokenAuth`** in `_auth.py`
  - [x] Rename `bot_token` property → `identity_token`
  - [x] Add `identity_type` property returning `"bot"` or `"user"` based on token prefix
  - [x] Make `app_token` optional (returns `str | None`)
  - [x] `StaticTokenAuth.__init__` accepts `identity_token: str` (required) and `app_token: str | None = None`
  - [x] `from_env()` reads `APPIF_SLACK_IDENTITY_TOKEN` (required) and `APPIF_SLACK_APP_TOKEN` (optional)
  - [x] `validate()` raises `NotAuthorized` only if `identity_token` is empty; `app_token` absence is not an error
  - [x] Token prefix classification: `xoxb-` → `"bot"`, `xoxp-` → `"user"`, unrecognized → `ValueError`
  - **Acceptance**: `SlackAuth` protocol has `identity_token`, `identity_type`, `app_token` properties. No `bot_token` property exists.

### Adapter Layer — Auth Tests (Step 2)

- [x] **Write unit tests for auth refactor** in `tests/unit/test_slack_connector.py`
  - [x] Test: `xoxb-` token → `identity_type == "bot"`
  - [x] Test: `xoxp-` token → `identity_type == "user"`
  - [x] Test: unrecognized prefix → `ValueError`
  - [x] Test: missing identity token → `NotAuthorized`
  - [x] Test: missing app token is NOT an error (graceful)
  - [x] Test: `from_env()` reads correct env vars (`APPIF_SLACK_IDENTITY_TOKEN`, `APPIF_SLACK_APP_TOKEN`)
  - **Acceptance**: All auth tests pass. No test references `bot_token` or legacy env vars.

### Adapter Layer — Connector Constructor + Capabilities (Step 3)

- [x] **Refactor `SlackConnector` constructor and `get_capabilities()`** in `connector.py`
  - [x] Constructor accepts `identity_token: str` (required) and `app_token: str | None = None`
  - [x] `_bot_user_id` renamed to `_authenticated_user_id`
  - [x] `get_capabilities()` computes `supports_realtime` based on `app_token` presence
  - [x] `delivery_mode` returns `"AUTOMATIC"` if `app_token` present, `"MANUAL"` otherwise
  - [x] Capabilities queryable before `connect()` — no connection required
  - **Acceptance**: Constructor uses new parameter names. Capabilities reflect token presence.

### Adapter Layer — Connector Tests (Step 4)

- [x] **Write unit tests for connector construction and capabilities** in `tests/unit/test_slack_connector.py`
  - [x] Test: bot token + app token → `supports_realtime=True`, `delivery_mode="AUTOMATIC"`
  - [x] Test: bot token, no app token → `supports_realtime=False`, `delivery_mode="MANUAL"`
  - [x] Test: user token + app token → `supports_realtime=True`, `delivery_mode="AUTOMATIC"`
  - [x] Test: user token, no app token → `supports_realtime=False`, `delivery_mode="MANUAL"`
  - [x] Test: capabilities queryable before `connect()`
  - **Acceptance**: All capability tests pass.

### Adapter Layer — connect() Graceful Degradation (Step 5)

- [x] **Refactor `connect()` for graceful degradation** in `connector.py`
  - [x] If `app_token` present: start Socket Mode, transition to `connected`
  - [x] If `app_token` absent: skip Socket Mode, transition to `connected` (API-only mode)
  - [x] `connect()` does not raise on missing `app_token`
  - **Acceptance**: `connect()` succeeds with and without app token.

### Adapter Layer — Normalizer Rename (Step 6)

- [x] **Rename `bot_user_id` parameter** in `_normalizer.py`
  - [x] `normalize_message(event, team_id, bot_user_id, resolve_user)` → `normalize_message(event, team_id, authenticated_user_id, resolve_user)`
  - [x] Internal references updated (`is_from_bot` logic uses `authenticated_user_id`)
  - [x] No behavioral changes — only the parameter name changes
  - **Acceptance**: `bot_user_id` does not appear in `_normalizer.py`.

### Adapter Layer — Normalizer Tests (Step 7)

- [x] **Update normalizer tests** in `tests/unit/test_slack_normalizer.py`
  - [x] All calls use `authenticated_user_id=` keyword argument
  - [x] Add test: user-token identity — message from authenticated user is marked `is_from_bot=True`
  - **Acceptance**: No test references `bot_user_id`. New user-identity test passes.

### Verification Script (Step 8)

- [x] **Create `scripts/slack_listener.py`** verification script
  - [x] Reads `APPIF_SLACK_IDENTITY_TOKEN` and `APPIF_SLACK_APP_TOKEN` from environment
  - [x] Prints identity type, capabilities, and delivery mode
  - [x] Connects and listens for messages (if realtime supported)
  - [x] Demonstrates graceful degradation (API-only if no app token)
  - **Acceptance**: Script runs without errors using new env vars.

### Setup Documentation (Step 9)

- [x] **Update `docs/design/slack/setup.md`**
  - [x] Document new env var names (`APPIF_SLACK_IDENTITY_TOKEN`, `APPIF_SLACK_APP_TOKEN`)
  - [x] Explain identity model (bot vs user token)
  - [x] Remove all legacy env var references
  - **Acceptance**: Setup doc references only new env vars. No legacy names.

### Docstrings (Step 10)

- [x] **Update module and class docstrings** in `_auth.py`, `connector.py`, `_normalizer.py`
  - [x] Reflect one-connector-one-identity model
  - [x] Document token classification behavior
  - [x] Document graceful degradation
  - **Acceptance**: All public classes and functions have accurate docstrings.

### Environment Example (Step 11)

- [x] **Update `.env.example`**
  - [x] Replace `APPIF_SLACK_BOT_OAUTH_TOKEN` → `APPIF_SLACK_IDENTITY_TOKEN`
  - [x] Replace `APPIF_SLACK_BOT_APP_LEVEL_TOKEN` → `APPIF_SLACK_APP_TOKEN`
  - [x] Add comments explaining identity model
  - **Acceptance**: `.env.example` contains only new env var names.

### Adapter Exports (Step 12)

- [x] **Review `src/appif/adapters/slack/__init__.py`**
  - [x] Ensure `SlackConnector`, `SlackAuth`, `StaticTokenAuth` are exported
  - [x] Verify no legacy names exported
  - **Acceptance**: Public API is clean.

### CLI Update (Step 13)

- [x] **Update `src/appif/cli/slack.py`**
  - [x] Constructor call uses `identity_token=` and `app_token=`
  - [x] CLI help text references new env vars
  - [x] No references to `bot_token` or legacy env vars
  - **Acceptance**: CLI constructs connector with new parameter names.

### Final Verification (Step 14)

- [x] **Run full verification**
  - [x] `pytest tests/unit -v` — all 128 tests pass
  - [x] `ruff check src/ tests/` — no lint errors
  - [x] `grep -r "bot_token" src/appif/adapters/slack/` — no matches
  - [x] `grep -r "BOT_OAUTH_TOKEN\|BOT_APP_LEVEL_TOKEN" .` — no matches (excluding docs/design/)
  - [x] `grep -r "bot_user_id" src/appif/adapters/slack/` — no matches
  - **Acceptance**: Zero legacy references in production code and tests.