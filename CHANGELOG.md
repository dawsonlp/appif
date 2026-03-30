# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

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
