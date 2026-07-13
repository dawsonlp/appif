# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.0.1] - 2026-07-13

### Fixed

- **README links now resolve on PyPI.** The project description used relative
  links (`docs/...`, `LICENSE`, `.env.example`) which 404 on the PyPI page
  (PyPI does not resolve them against the repo). They are now absolute GitHub
  URLs, which also work on GitHub.

## [2.0.0] - 2026-07-13

Major version bump to signal a **breaking API change** in the work-tracking
domain (see below). Messaging connectors are unaffected.

### Changed

- **Work tracking is wired hexagonally (breaking).** `WorkTrackingService` now
  depends only on the new `WorkTrackerBackend` port and no longer imports the
  Jira adapter, restoring the inward-pointing dependency arrow. Construct the
  service and register a backend explicitly:
  `WorkTrackingService()` then
  `service.register("prod", JiraAdapter(url, creds), make_default=True)`.
  Removed: the `WorkTrackingService(auto_load=...)` parameter and the
  `register(name, platform, server_url, credentials)` signature. YAML
  auto-loading moved to `appif.adapters.jira.create_work_tracking_service()`.
  See [ADR-002](docs/adr/002-work-tracking-hexagonal-ports.md).

### Fixed

- The domain layer no longer imports any adapter, so the scoped `mypy` check is
  naturally hermetic; the temporary `follow_imports = "silent"` workaround was
  removed.

## [1.5.0] - 2026-07-13

### Added

- **Microsoft Teams connector** (`appif.adapters.teams.TeamsConnector`) --
  a new messaging adapter implementing the `Connector` protocol over the
  Microsoft Graph API for Teams chats and channel messages. Mirrors the
  Outlook adapter's Graph + MSAL approach but keeps a **separate token
  cache** (`~/.config/appif/teams`) and can reuse the Outlook Azure app
  registration (`client_id`/`tenant_id` fall back to `APPIF_OUTLOOK_*`).
  - 1:1/group **chats** are watched by default; **channels** are opt-in
    (`include_channels` / `APPIF_TEAMS_INCLUDE_CHANNELS`) because
    `ChannelMessage.Read.All` requires Azure AD admin consent.
  - Inbound via per-conversation `messages/delta` polling; sends route to
    chat / new-channel / channel-reply endpoints from the `ConversationRef`.
  - Honors the shared `include_sent` model (`APPIF_TEAMS_INCLUDE_SENT`):
    own messages suppressed by default, surfaced when enabled.
  - `@`-mentions populate `MessageEvent.recipients.to`; HTML bodies are
    stripped to text. New consent helper: `scripts/teams_consent.py`.

### Fixed

- **Outlook now retries transient failures.** The connector and poller made
  raw HTTP calls with no retry/back-off (the module targeting the msgraph SDK
  was dead code); they now route through a shared Graph HTTP layer that retries
  429/5xx with `Retry-After`. Expired delta tokens (HTTP 410) are handled on the
  same error-driven path (also fixes a latent Teams delta-expiry case).
- **Outlook and Teams deliver events off-thread.** Both dispatched inbound
  messages to listeners synchronously on the poll thread, so a slow listener
  stalled polling — violating the `MessageListener` fire-and-forget contract.
  All connectors now dispatch on a thread pool, like Gmail and Slack.
- **Slack CLI:** `appif-slack bot messages` accessed a nonexistent
  `MessageEvent.conversation` field (crashed when rendering results); now uses
  `conversation_ref`.

### Changed

- Internal refactor to reduce duplication (no public API change): shared
  `BaseMessagingConnector`/`BasePoller`, a single Graph HTTP + MSAL auth layer
  for Outlook/Teams, and a shared CLI helper module.
- Type-checking (`mypy`) is now scoped to the domain layer and enforced in CI.

### Removed

- Dead code: unused `domain/lifecycle.py`, `domain/credentials.py`, the unused
  semantic error subclasses in `domain/errors.py`, the empty `infrastructure/`
  package, and one-off proof scripts superseded by the `appif-slack` CLI.
- Redundant/stale docs: `ADAPTERS.md` (duplicated `docs/usage.md` +
  `docs/api_reference.md`, and had drifted out of date), an orphaned
  `email_monitor` design, and completed development checklists.

## [1.4.0] - 2026-06-08

### Added

- **Messaging: message recipients** (`MessageEvent.recipients`) -- every
  message now carries a `Recipients` value object with `to`/`cc`/`bcc` lists
  of `Identity`, exposing the involved set beyond the sender. Populated per
  connector: Outlook from Graph `toRecipients`/`ccRecipients`/`bccRecipients`
  (the delta and backfill `$select` were widened accordingly), Gmail from the
  `To`/`Cc`/`Bcc` headers, Slack best-effort from `@`-mentions. Defaults empty,
  so the field is backward-compatible and rides onto downstream
  `dataclasses.asdict` envelopes with no contract change.
- **Identity email** (`Identity.email`) -- optional email address on every
  resolved identity (sender and recipients). Equals `id` for email connectors;
  filled from `users.info` for Slack when the token carries `users:read.email`,
  otherwise `None`.

## [1.3.0] - 2026-06-08

### Added

- **Messaging: read your own sent messages** (`include_sent`) -- each
  messaging connector (Gmail, Outlook, Slack) can now deliver messages you
  sent alongside incoming ones. Opt-in via the `include_sent` constructor
  parameter or `APPIF_<CONNECTOR>_INCLUDE_SENT` environment variable;
  default off, preserving prior echo-suppression behavior. When enabled the
  watch set is extended automatically where the platform stores sent mail
  separately (Gmail `SENT` label, Outlook `SentItems` folder); Slack
  delivers own messages over Socket Mode once self-filtering is disabled.
  Applies to both realtime and backfill paths. `[6ac949c]`

### Fixed

- **Test isolation** -- unit tests now strip and restore `APPIF_*`
  environment variables per test, preventing `load_dotenv(~/.env)` (invoked
  during auth construction) from leaking real credentials across tests and
  causing ordering-dependent failures. `[1de3ef8]`

## [1.2.1] - 2026-03-29

### Changed

- **Documentation: credential configuration** -- rewrote the Configuration
  section of the readme to document all three credential supply methods
  (programmatic, environment variables, config files) with priority order
  and production guidance. Previously only file-based config was documented.

## [1.2.0] - 2026-03-29

### Added

- **Work tracking: file attachment upload** (`attach_file()`) -- attach
  caller-provided file content to a work item and receive platform-assigned
  `ItemAttachment` metadata. Completes the attachment lifecycle alongside
  the existing `download_attachment()`. (W13, CLI-03)

## [1.0.0] - 2026-03-13

### Initial Release

- **Messaging domain**: unified `Connector` protocol, canonical `MessageEvent`, `MessageContent`,
  `ConversationRef`, `SendReceipt` types, and typed error hierarchy
- **Gmail connector**: OAuth 2.0, `history.list` polling, send/draft, attachment support
- **Outlook connector**: Microsoft Graph API, delta-query polling, send, MSAL auth
- **Slack connector**: Slack API (Bolt + Socket Mode), real-time events, user cache,
  bot and user token support
- **Work tracking domain**: `WorkTracker` and `InstanceRegistry` protocols
- **Jira adapter**: full CRUD lifecycle (get, create, comment, transition, link, search),
  multi-instance YAML config, per-project type discovery, `ItemCategory` enum with
  adapter-resolved issue types
- **Slack CLI** (`appif-slack`): identity-first commands (bot/user), status, channels,
  messages, listen, send
- **Outlook CLI** (`appif-outlook`): status, folders, inbox, send, consent
- **323 unit tests**, integration tests for Slack and Jira
- GitHub Actions CI (lint + test) and release (build + publish to PyPI) workflows
- GPL-3.0-or-later license

[1.2.1]: https://github.com/dawsonlp/appif/releases/tag/v1.2.1
[1.2.0]: https://github.com/dawsonlp/appif/releases/tag/v1.2.0
[1.0.0]: https://github.com/dawsonlp/appif/releases/tag/v1.0.0
