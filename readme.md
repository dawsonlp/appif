# appif -- Application Interfaces

A Python library that gives AI agents authenticated, normalized access to external platforms -- email, chat, and work tracking systems.

## Purpose

Agents need information that lives behind logins: email threads, Slack messages, Jira tickets. This library provides connectors and adapters that authenticate as you and return clean, structured domain objects suitable for agent reasoning -- platform-specific APIs are fully encapsulated behind shared protocols.

**Two domains are supported:**

- **Messaging** -- Gmail, Outlook, Slack, Microsoft Teams. Unified `MessageEvent` objects via the `Connector` protocol.
- **Work Tracking** -- Jira. Unified `WorkItem` objects via the `WorkTracker` protocol. Multi-instance support with programmatic registration or optional YAML config.

**For the complete usage guide -- the unified model, per-connector mapping tables, code examples, and environment variable reference -- see [docs/usage.md](docs/usage.md).**

## Quick Start

### Messaging (Gmail, Outlook, Slack, Teams)

```bash
pip install appif
```

```python
from appif.adapters.gmail import GmailConnector
from appif.domain.messaging.models import MessageEvent, MessageContent

class MyListener:
    def on_message(self, event: MessageEvent) -> None:
        print(f"[{event.connector}] {event.author.display_name}: {event.content.text}")

connector = GmailConnector()
connector.connect()
connector.register_listener(MyListener())
```

All messaging connectors (Gmail, Outlook, Slack, Teams) follow this same pattern. The full model, per-connector setup, and examples are in **[docs/usage.md](docs/usage.md)**.

### Work Tracking (Jira)

```python
from appif.domain.work_tracking.service import WorkTrackingService
from appif.domain.work_tracking.models import CreateItemRequest, ItemCategory, SearchCriteria
from appif.adapters.jira import JiraAdapter

# Supply credentials directly -- no config files needed. The caller wires the
# concrete adapter into the platform-agnostic service.
service = WorkTrackingService()
service.register(
    "myinstance",
    JiraAdapter(
        "https://mycompany.atlassian.net",
        {"username": "user@example.com", "api_token": "your-token"},
    ),
    make_default=True,
)

# Create a ticket (adapter resolves ItemCategory to platform-specific type)
item = service.create_item(CreateItemRequest(
    project="MYPROJECT",
    title="Fix login bug",
    item_type=ItemCategory.BUG,
    description="Users cannot log in after password reset",
))
print(f"Created: {item.key}")

# Attach a file
from pathlib import Path

attachment = service.attach_file(
    item.key,
    "requirements.md",
    Path("requirements.md").read_bytes(),
)
print(f"Attached: {attachment.filename} ({attachment.size_bytes} bytes)")

# Download an attachment
content = service.download_attachment(attachment.id)
Path("downloaded.md").write_bytes(content.data)

# Search
results = service.search(SearchCriteria(project="MYPROJECT", status="To Do"))
for item in results.items:
    print(f"  {item.key}: {item.title}")
```

See [Configuration](#configuration) for all credential supply options.

## Supported Platforms

### Messaging Connectors

| Service | Connector | Inbound Method | Status |
|---------|-----------|----------------|--------|
| **Gmail** | Google API (OAuth 2.0) | `history.list` polling | Active |
| **Outlook / Microsoft 365** | Microsoft Graph API | Delta-query polling | Active |
| **Slack** | Slack API (Bolt + Socket Mode) | Real-time Socket Mode | Active |
| **Microsoft Teams** | Microsoft Graph API | Delta-query polling | Active |

### Work Tracking Adapters

| Service | Library | Auth Method | Status |
|---------|---------|-------------|--------|
| **Jira Cloud** | `atlassian-python-api` | API token (programmatic or YAML config) | Active |

## CLI

Both Slack and Outlook adapters include command-line interfaces:

```bash
pip install appif

# Slack — identity-first commands (bot or user)
appif-slack bot status
appif-slack bot channels
appif-slack bot send general "Deploy complete"
appif-slack bot listen
appif-slack user channels

# Outlook — verify setup and exercise the connector
appif-outlook status
appif-outlook folders
appif-outlook inbox --limit 5
appif-outlook send user@example.com "Hello from appif"
appif-outlook consent
```

## Installation

### For development

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### As a library dependency

```bash
pip install appif
```

### Prerequisites

- Python 3.13.x
- uv (for development)

## Configuration

All credentials are supplied **programmatically** -- constructor parameters for messaging connectors, and `register()` calls for work tracking adapters. Your application sources credentials however it needs to (vault, environment variables, secrets manager) and passes them directly. No config files are required.

### Messaging

Every messaging connector accepts credentials as constructor parameters:

```python
from appif.adapters.outlook import OutlookConnector

connector = OutlookConnector(
    client_id="your-client-id",
    client_secret="your-client-secret",
    tenant_id="your-tenant-id",
    account="work",
)
```

Gmail, Slack, and Teams connectors follow the same pattern. When a constructor parameter is omitted, the connector falls back to environment variables (`APPIF_GMAIL_CLIENT_ID`, `APPIF_OUTLOOK_CLIENT_ID`, `APPIF_SLACK_BOT_OAUTH_TOKEN`, `APPIF_TEAMS_CLIENT_ID`, etc.). See [.env.example](.env.example) for the full list.

### Work Tracking

Construct a `WorkTrackingService` and register `JiraAdapter` instances with credentials supplied programmatically:

```python
from appif.domain.work_tracking.service import WorkTrackingService
from appif.adapters.jira import JiraAdapter

service = WorkTrackingService()
service.register(
    "production",
    JiraAdapter(
        "https://mycompany.atlassian.net",
        {"username": "bot@mycompany.com", "api_token": get_secret("jira-api-token")},
    ),
    make_default=True,
)
```

Multiple instances can be registered and selected per-call via the `instance` parameter. The service depends only on the platform-agnostic `WorkTrackerBackend` port — the caller (or a composition factory) wires in the concrete adapter, so the domain never imports an adapter (see [ADR-002](docs/adr/002-work-tracking-hexagonal-ports.md)).

> **CLI and personal development use only:** `create_work_tracking_service()` (in `appif.adapters.jira`) builds a service pre-loaded from a YAML file at `~/.config/appif/jira/config.yaml` (or the `APPIF_JIRA_CONFIG` env var). This convenience exists solely for the appif CLIs and local development scripts; applications should construct the service and supply credentials programmatically as above.

## Project Structure

```
appif/
├── src/
│   └── appif/                       # Top-level package (PyPI: appif)
│       ├── __init__.py              # Version via importlib.metadata
│       ├── domain/
│       │   ├── messaging/           # Connector protocol, canonical models, errors
│       │   └── work_tracking/       # WorkTracker protocol, models, service
│       ├── adapters/
│       │   ├── _base.py             # BaseMessagingConnector + BasePoller (shared plumbing)
│       │   ├── _util.py             # Small shared helpers (env_bool)
│       │   ├── _graph/              # Shared Graph HTTP + MSAL auth (Outlook, Teams)
│       │   ├── gmail/               # Gmail messaging connector
│       │   ├── outlook/             # Outlook messaging connector
│       │   ├── slack/               # Slack messaging connector
│       │   ├── teams/               # Microsoft Teams messaging connector
│       │   └── jira/                # Jira work tracking adapter
│       └── cli/                     # CLI entry points (appif-slack, appif-outlook) + shared _common
├── tests/
│   ├── unit/                        # Unit tests (run: pytest tests/unit)
│   ├── integration/                 # Live API tests (Slack, Jira)
│   └── e2e/
├── scripts/                         # OAuth consent flows, cleanup utilities
├── docs/design/                     # Design documents per adapter
├── pyproject.toml
├── .env.example
└── readme.md
```

## Development

```bash
# Set up dev environment
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Run all unit tests
pytest tests/unit -v

# Run adapter-specific tests
pytest tests/unit/test_gmail_*.py -v
pytest tests/unit/test_outlook_*.py -v

# Run integration tests (requires live credentials)
pytest tests/integration/test_jira_integration.py -v
pytest tests/integration/test_slack_integration.py -v

# Clean up Jira test tickets
python scripts/jira_cleanup.py

# Lint and format
ruff check src/ tests/
ruff format src/ tests/

# Type check (scoped to the domain layer — see [tool.mypy] in pyproject.toml)
mypy
```

## Architecture

### Messaging: Connector Protocol

All messaging connectors implement a shared `Connector` protocol (`appif.domain.messaging.ports.Connector`) -- a transport adapter that:

- Connects to an external system and manages authentication
- Emits normalized `MessageEvent` objects to registered listeners
- Delivers outbound messages via `send(target, content)`
- Supports historical backfill alongside realtime event ingestion
- Advertises capabilities so upstream logic branches on what the connector supports, not which platform it is

All connectors produce identical canonical types (`MessageEvent`, `ConversationRef`, `SendReceipt`). Platform-specific SDK code is fully encapsulated -- zero Slack/Outlook/Gmail types leak through the public interface.

### Work Tracking: WorkTracker Protocol

The Jira adapter implements the `WorkTracker` protocol (`appif.domain.work_tracking.ports.WorkTracker`):

- CRUD operations: get, create, comment, transition, link, search, attach/download files, project management
- Multi-instance support via `InstanceRegistry` protocol
- `WorkTrackingService` routes operations to the correct adapter instance
- Domain types (`WorkItem`, `CreateItemRequest`, `ItemCategory`, `SearchCriteria`) are platform-agnostic
- `ItemCategory` enum (TASK, SUBTASK, STORY, BUG, EPIC) -- callers express intent, adapters resolve to platform-specific types
- Per-project type discovery and caching via createmeta API

### Internal Module Pattern

Each messaging adapter follows the same decomposition:

```
src/appif/adapters/<platform>/
├── __init__.py          # Public exports
├── connector.py         # Connector protocol implementation
├── _auth.py             # Authentication (protocol + implementation)
├── _normalizer.py       # Platform message -> MessageEvent
├── _message_builder.py  # MessageContent -> platform request (email adapters)
├── _poller.py           # Inbound message detection (polling adapters)
└── _rate_limiter.py     # Retry + platform error -> domain error mapping
```

Plumbing shared by all connectors lives one level up: `adapters/_base.py`
(`BaseMessagingConnector` — listener registry, status, fire-and-forget
dispatch; and `BasePoller` — the daemon-thread poll loop) and `adapters/_graph/`
(one httpx retry layer and one MSAL token-cache auth base shared by the Outlook
and Teams connectors).

The Jira adapter uses a similar pattern with `adapter.py` (operations), `_auth.py` (YAML config + client), and `_normalizer.py` (API dicts to domain types).

### Credential Setup

| Adapter | Auth Method | Setup Guide |
|---------|-------------|-------------|
| Gmail | OAuth 2.0 (`python scripts/gmail_consent.py <account>`) | [docs/design/gmail/setup.md](docs/design/gmail/setup.md) |
| Outlook | OAuth 2.0 (`python scripts/outlook_consent.py <account>`) | [docs/design/outlook/setup.md](docs/design/outlook/setup.md) |
| Slack | Bot + App tokens from Slack app config | [docs/design/slack/setup.md](docs/design/slack/setup.md) |
| Microsoft Teams | OAuth 2.0 (`python scripts/teams_consent.py <account>`) | [docs/usage.md#teams](docs/usage.md) |
| Jira | API token (programmatic `register()` or YAML config) | [docs/design/work_tracking/setup.md](docs/design/work_tracking/setup.md) |

## Documentation

| Document | Description |
|----------|-------------|
| [docs/usage.md](docs/usage.md) | **Start here** — unified messaging model, per-connector setup, code examples |
| [API Reference](docs/api_reference.md) | Complete method signatures, domain models, and error types |
| [CHANGELOG.md](CHANGELOG.md) | Version history, breaking changes, and migration guides |
| [docs/design/gmail/](docs/design/gmail/) | Gmail design, technical design, setup |
| [docs/design/outlook/](docs/design/outlook/) | Outlook design, technical design, setup |
| [docs/design/slack/](docs/design/slack/) | Slack design, technical design, setup |
| [docs/design/work_tracking/](docs/design/work_tracking/) | Jira requirements, design, technical design, setup |
| [docs/adr/](docs/adr/) | Architecture decision records |

## License

GPL-3.0-or-later -- see [LICENSE](LICENSE).
