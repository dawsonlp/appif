# Slack CLI Application - Implementation Checklist

## Overview

Command-line application using `typer` and `rich` that connects to Slack
through the existing `SlackConnector` adapter and displays messages meant
for the authenticated user.

## Dependencies to Add

- `typer` — CLI framework
- `rich` — Terminal formatting (tables, panels, live display)

## Commands

| Command | Purpose |
|---------|---------|
| `status` | Show connector status and capabilities |
| `connect` | Authenticate and connect to the workspace |
| `accounts` | List workspace accounts |
| `channels` | List visible channels, DMs, and groups |
| `messages` | Backfill and display messages from specified channels (or all) |
| `listen` | Start real-time listener, display incoming messages live |

## Implementation Checklist

- [ ] Add `typer` and `rich` to `pyproject.toml` dependencies
- [ ] Create `src/appif/cli/__init__.py` (empty package marker)
- [ ] Create `src/appif/cli/slack.py` — main CLI module
  - [ ] `status` command: construct connector, show status and capabilities in a Rich panel
  - [ ] `connect` command: connect to workspace, display auth result (team name, bot user)
  - [ ] `accounts` command: connect then list accounts in a Rich table
  - [ ] `channels` command: connect then list targets in a Rich table with type/name/id columns
  - [ ] `messages` command: connect, backfill specified channels (with optional `--since` and `--channel` filters), display messages in a Rich table sorted by timestamp
  - [ ] `listen` command: connect, register a listener, display incoming messages live using Rich Live or Console
- [ ] Add `[project.scripts]` entry in `pyproject.toml` for `appif-slack = "appif.cli.slack:app"`
- [ ] Create a `MessageListener` implementation inside the CLI module that prints messages via Rich
- [ ] Handle errors gracefully (missing tokens, connection failures) with Rich error panels
- [ ] Test the CLI runs end-to-end with `python -m appif.cli.slack --help`

## Design Decisions

- Single file `src/appif/cli/slack.py` — the CLI is a thin shell over the connector, no business logic
- Uses the existing `SlackConnector` directly — no new abstractions
- `StaticTokenAuth` from env vars (existing pattern, loads `~/.env`)
- Messages display: sender, channel, timestamp, text (truncated for table display)
- Listen mode uses `signal` handler for clean Ctrl+C shutdown
- No new domain types — uses existing `MessageEvent`, `Account`, `Target`

## Out of Scope

- OAuth browser flow (future work)
- Sending messages from CLI (could be added later)
- Persistent message storage