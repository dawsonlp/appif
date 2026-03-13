# appif Usage Guide

## Overview

appif provides a unified messaging interface across Gmail, Outlook, and Slack. Every connector implements the same `Connector` protocol and produces the same domain types. Your code works with `MessageEvent`, `ConversationRef`, and `MessageContent` -- never with platform-specific objects.

## Installation

```bash
pip install appif
```

All three connectors (Gmail, Outlook, Slack) and their dependencies are included.

## The Unified Model

### MessageEvent -- every inbound message

Every message from every platform arrives as a `MessageEvent`:

```python
from appif.domain.messaging.models import MessageEvent

# Fields:
#   message_id: str          -- unique ID (platform-assigned)
#   connector: str           -- "gmail", "outlook", or "slack"
#   account_id: str          -- which account received it
#   conversation_ref: ConversationRef  -- opaque reply handle
#   author: Identity         -- who sent it
#   timestamp: datetime      -- when it was sent
#   content: MessageContent  -- body text + attachments
#   metadata: dict           -- platform-specific extras
```

### Identity -- who sent a message

```python
from appif.domain.messaging.models import Identity

# Fields:
#   id: str            -- platform user ID or email address
#   display_name: str  -- human-readable name
#   connector: str     -- "gmail", "outlook", or "slack"
```

### MessageContent -- what was said

```python
from appif.domain.messaging.models import MessageContent, Attachment

# MessageContent:
#   text: str                      -- plain text body
#   attachments: list[Attachment]  -- file attachments (may be empty)

# Attachment:
#   filename: str
#   content_type: str              -- MIME type
#   size_bytes: int | None
#   content_ref: str | None        -- opaque ref for lazy download
#   data: bytes | None             -- inline content (small files)
```

### ConversationRef -- how to reply

`ConversationRef` is an opaque routing handle. You receive it on every inbound message and pass it back to `send()` to reply. Never inspect or construct its `opaque_id` -- only the owning connector reads it.

```python
from appif.domain.messaging.models import ConversationRef

# Fields:
#   connector: str      -- "gmail", "outlook", or "slack"
#   account_id: str     -- which account
#   type: str           -- "email_thread", "dm", "channel", "thread", etc.
#   opaque_id: dict     -- internal routing data (do not touch)
```

### SendReceipt -- confirmation of delivery

```python
from appif.domain.messaging.models import SendReceipt

# Fields:
#   external_id: str     -- platform message ID
#   timestamp: datetime  -- when the platform accepted it
```

### ConnectorCapabilities -- what a connector can do

```python
from appif.domain.messaging.models import ConnectorCapabilities

# Fields:
#   supports_realtime: bool    -- live event streaming
#   supports_backfill: bool    -- historical message retrieval
#   supports_threads: bool     -- threaded conversations
#   supports_reply: bool       -- reply to specific messages
#   supports_auto_send: bool   -- can send without human confirmation
#   delivery_mode: str         -- "AUTOMATIC", "ASSISTED", or "MANUAL"
```

## Using a Connector

All three connectors follow the same lifecycle:

```python
# 1. Create
connector = SomeConnector()

# 2. Connect (authenticates and starts inbound detection)
connector.connect()

# 3. Register a listener for inbound messages
connector.register_listener(my_listener)

# 4. Send messages
receipt = connector.send(conversation_ref, content)

# 5. Disconnect when done
connector.disconnect()
```

### Writing a Listener

Any object with an `on_message(event: MessageEvent)` method works:

```python
from appif.domain.messaging.models import MessageEvent

class MyListener:
    def on_message(self, event: MessageEvent) -> None:
        print(f"[{event.connector}] {event.author.display_name}: {event.content.text}")
        # event.conversation_ref is ready for reply
        # event.content.attachments has any files
        # event.metadata has platform-specific extras
```

### Sending a Message

To reply to a received message, use its `conversation_ref`:

```python
from appif.domain.messaging.models import MessageContent

content = MessageContent(text="Thanks for the message!")
receipt = connector.send(event.conversation_ref, content)
print(f"Sent: {receipt.external_id} at {receipt.timestamp}")
```

To send with attachments:

```python
from appif.domain.messaging.models import MessageContent, Attachment

content = MessageContent(
    text="Here is the report.",
    attachments=[
        Attachment(
            filename="report.pdf",
            content_type="application/pdf",
            data=pdf_bytes,
        )
    ],
)
receipt = connector.send(conversation_ref, content)
```

### Backfilling Historical Messages

Retrieve messages from a time range:

```python
from datetime import datetime, timezone
from appif.domain.messaging.models import BackfillScope

scope = BackfillScope(
    oldest=datetime(2025, 1, 1, tzinfo=timezone.utc),
    latest=datetime(2025, 1, 31, tzinfo=timezone.utc),
)
connector.backfill(account_id, scope)
# Messages arrive through registered listeners
```

## Gmail Connector

Gmail presents your mailbox as a messaging channel. Each email thread becomes a conversation, and individual emails become messages.

### What Gmail Looks Like Through appif

| Gmail Concept | appif Model |
|---|---|
| Google account email | `Account.account_id` / `Account.display_name` |
| Gmail label (Inbox, etc.) | `Target` (type: `"label"`) |
| Email thread | `ConversationRef` (type: `"email_thread"`) |
| Individual email | `MessageEvent` |
| Sender | `Identity` (id = email address) |
| Email body (plain text) | `MessageContent.text` |
| Email attachments | `MessageContent.attachments` |
| Subject, labels, snippet | `MessageEvent.metadata` |

### Setup

```bash
pip install appif
```

1. Create a Google Cloud project with Gmail API enabled
2. Create OAuth 2.0 credentials (Desktop application type)
3. Configure environment variables in `~/.env`:

```bash
APPIF_GMAIL_CLIENT_ID=your-client-id.apps.googleusercontent.com
APPIF_GMAIL_CLIENT_SECRET=your-client-secret
APPIF_GMAIL_ACCOUNT=you@gmail.com
```

4. Run the consent flow:

```bash
python scripts/gmail_consent.py my-account
```

### Usage

```python
from appif.adapters.gmail import GmailConnector
from appif.domain.messaging.models import MessageEvent, MessageContent

class EmailHandler:
    def on_message(self, event: MessageEvent) -> None:
        print(f"From: {event.author.display_name} <{event.author.id}>")
        print(f"Body: {event.content.text[:200]}")
        print(f"Subject: {event.metadata.get('subject', '(no subject)')}")

        for att in event.content.attachments:
            print(f"  Attachment: {att.filename} ({att.content_type})")

connector = GmailConnector()
connector.connect()

handler = EmailHandler()
connector.register_listener(handler)

# List available labels/folders
accounts = connector.list_accounts()
targets = connector.list_targets(accounts[0].account_id)
for t in targets:
    print(f"  {t.display_name} ({t.type})")

# Reply to a received email
reply_content = MessageContent(text="Thanks, received your email.")
receipt = connector.send(event.conversation_ref, reply_content)

# Clean up
connector.disconnect()
```

### Gmail-Specific Behavior

- **Delivery mode**: `AUTOMATIC` (sends immediately) or `ASSISTED` (creates draft). Set via `APPIF_GMAIL_DELIVERY_MODE` env var.
- **Polling**: Uses Gmail `history.list` API. Poll interval configurable via `APPIF_GMAIL_POLL_INTERVAL_SECONDS` (default: 30).
- **Label filtering**: Only watches specified labels. Set via `APPIF_GMAIL_LABEL_FILTER` (default: `INBOX`).
- **HTML stripping**: Email HTML is converted to plain text automatically.
- **Thread handling**: Replies preserve the email thread using Gmail's `threadId`.
- **Attachment download**: Large attachments arrive with `content_ref` only. Use `connector.resolve_attachment(content_ref)` to download bytes.

### Capabilities

```
supports_realtime:  True (polling)
supports_backfill:  True
supports_threads:   True
supports_reply:     True
supports_auto_send: True
delivery_mode:      "AUTOMATIC" or "ASSISTED"
```

## Outlook Connector

Outlook presents your Microsoft 365 mailbox through the Graph API. Mail folders become targets, email threads become conversations, and individual emails become messages.

### What Outlook Looks Like Through appif

| Outlook Concept | appif Model |
|---|---|
| Microsoft 365 account | `Account.account_id` / `Account.display_name` |
| Mail folder (Inbox, etc.) | `Target` (type: `"mail_folder"`) |
| Conversation thread | `ConversationRef` (type: `"email_thread"`) |
| Individual email | `MessageEvent` |
| Sender | `Identity` (id = email address) |
| Email body (plain text) | `MessageContent.text` |
| Email attachments | `MessageContent.attachments` |
| Subject, folder, flags | `MessageEvent.metadata` |

### Setup

```bash
pip install appif
```

1. Register an application in Azure AD
2. Add `Mail.ReadWrite` and `Mail.Send` permissions
3. Configure environment variables in `~/.env`:

```bash
APPIF_OUTLOOK_CLIENT_ID=your-azure-app-client-id
APPIF_OUTLOOK_TENANT_ID=common
# Optional for confidential-client flow:
# APPIF_OUTLOOK_CLIENT_SECRET=your-client-secret
```

4. Run the consent flow:

```bash
python scripts/outlook_consent.py my-account
```

### Usage

```python
from appif.adapters.outlook import OutlookConnector
from appif.domain.messaging.models import MessageEvent, MessageContent

class EmailHandler:
    def on_message(self, event: MessageEvent) -> None:
        print(f"From: {event.author.display_name} <{event.author.id}>")
        print(f"Body: {event.content.text[:200]}")
        print(f"Subject: {event.metadata.get('subject', '(no subject)')}")

connector = OutlookConnector()
connector.connect()

handler = EmailHandler()
connector.register_listener(handler)

# List mail folders
accounts = connector.list_accounts()
targets = connector.list_targets(accounts[0].account_id)
for t in targets:
    print(f"  {t.display_name} ({t.type})")

# Reply to a received email
reply_content = MessageContent(text="Thanks, received your email.")
receipt = connector.send(event.conversation_ref, reply_content)

connector.disconnect()
```

### Outlook-Specific Behavior

- **Delivery mode**: `AUTOMATIC` (sends immediately). Draft mode not yet supported.
- **Polling**: Uses Graph API delta queries. Poll interval configurable via `APPIF_OUTLOOK_POLL_INTERVAL_SECONDS` (default: 30).
- **Folder filtering**: Only watches specified folders. Set via `APPIF_OUTLOOK_FOLDER_FILTER` (default: `Inbox`).
- **HTML stripping**: Email HTML is converted to plain text automatically.
- **Thread handling**: Replies use Graph API's message reply endpoint, preserving the conversation thread.
- **Authentication**: MSAL-based with persisted token cache. Supports both public (device code) and confidential (client secret) flows.

### Capabilities

```
supports_realtime:  True (polling)
supports_backfill:  True
supports_threads:   True
supports_reply:     True
supports_auto_send: True
delivery_mode:      "AUTOMATIC"
```

## Slack Connector

Slack presents your workspace as a real-time messaging channel. Channels, DMs, and group DMs become targets. Messages and threads map naturally to the appif model.

### What Slack Looks Like Through appif

| Slack Concept | appif Model |
|---|---|
| Workspace | `Account.account_id` / `Account.display_name` |
| Channel, DM, group DM | `Target` (type: `"channel"`, `"dm"`, `"group_dm"`, `"private_channel"`) |
| Message thread | `ConversationRef` (type varies by conversation) |
| Message | `MessageEvent` |
| User | `Identity` (id = Slack user ID, e.g. `U01ABCDEF`) |
| Message text | `MessageContent.text` |
| Files | `MessageContent.attachments` |
| Reactions, edited flag, app mentions | `MessageEvent.metadata` |

### Setup

```bash
pip install appif
```

1. Create a Slack App at https://api.slack.com/apps
2. Enable Socket Mode
3. Add bot scopes: `chat:write`, `channels:read`, `channels:history`, `users:read`, `im:read`, `im:history`, `groups:read`, `groups:history`
4. Install the app to your workspace
5. Configure environment variables in `~/.env`:

```bash
APPIF_SLACK_BOT_OAUTH_TOKEN=xoxb-your-bot-token
APPIF_SLACK_BOT_APP_LEVEL_TOKEN=xapp-your-app-level-token
```

### Usage

```python
from appif.adapters.slack import SlackConnector
from appif.domain.messaging.models import MessageEvent, MessageContent

class ChatHandler:
    def on_message(self, event: MessageEvent) -> None:
        print(f"[#{event.metadata.get('channel_name', '?')}] "
              f"{event.author.display_name}: {event.content.text}")

connector = SlackConnector()
connector.connect()

handler = ChatHandler()
connector.register_listener(handler)

# List channels and DMs
accounts = connector.list_accounts()
targets = connector.list_targets(accounts[0].account_id)
for t in targets:
    print(f"  {t.display_name} ({t.type})")

# Send a message
content = MessageContent(text="Hello from appif!")
receipt = connector.send(event.conversation_ref, content)

connector.disconnect()
```

### Slack-Specific Behavior

- **Real-time delivery**: Uses Slack Socket Mode for instant message delivery (no polling delay).
- **User resolution**: The connector automatically resolves Slack user IDs to display names via a built-in user cache.
- **Thread support**: Thread replies are fully supported. `ConversationRef.opaque_id` carries the thread timestamp.
- **Channel types**: The connector distinguishes channels, DMs, group DMs, and private channels via `Target.type`.
- **No consent flow needed**: Authentication uses bot and app-level tokens directly from Slack app configuration.

### Capabilities

```
supports_realtime:  True (Socket Mode)
supports_backfill:  True
supports_threads:   True
supports_reply:     True
supports_auto_send: True
delivery_mode:      "AUTOMATIC"
```

## Error Handling

All connectors raise the same typed errors from `appif.domain.messaging.errors`:

```python
from appif.domain.messaging.errors import (
    ConnectorError,     # Base error
    NotAuthorized,      # Auth failure, expired token, revoked access
    NotSupported,       # Operation not available for this connector
    TargetUnavailable,  # Target not found or inaccessible
    TransientFailure,   # Temporary failure (rate limit, server error)
)
```

Platform-specific exceptions never leak through the interface. All HTTP errors, SDK exceptions, and API failures are caught and mapped to one of these types.

```python
from appif.domain.messaging.errors import NotAuthorized, TransientFailure

try:
    connector.connect()
except NotAuthorized as e:
    print(f"Auth failed: {e.reason}")
    # Re-run consent flow
except TransientFailure as e:
    print(f"Temporary failure: {e.reason}")
    # Retry later
```

Transient failures (rate limits, server errors) are retried automatically by each connector's built-in rate limiter before raising to the caller.

## Multi-Connector Example

Use multiple connectors simultaneously with a shared listener:

```python
from appif.adapters.gmail import GmailConnector
from appif.adapters.slack import SlackConnector
from appif.domain.messaging.models import MessageEvent

class UnifiedHandler:
    def on_message(self, event: MessageEvent) -> None:
        source = event.connector  # "gmail" or "slack"
        who = event.author.display_name
        what = event.content.text[:100]
        print(f"[{source}] {who}: {what}")

handler = UnifiedHandler()

gmail = GmailConnector()
gmail.connect()
gmail.register_listener(handler)

slack = SlackConnector()
slack.connect()
slack.register_listener(handler)

# Both connectors deliver to the same handler
# Messages arrive as identical MessageEvent objects
# Your code never sees Gmail or Slack internals

# ...

gmail.disconnect()
slack.disconnect()
```

## Environment Variable Reference

### Gmail

| Variable | Default | Description |
|---|---|---|
| `APPIF_GMAIL_CLIENT_ID` | (required) | Google Cloud OAuth client ID |
| `APPIF_GMAIL_CLIENT_SECRET` | (required) | Google Cloud OAuth client secret |
| `APPIF_GMAIL_ACCOUNT` | (required) | Account email address |
| `APPIF_GMAIL_CREDENTIALS_DIR` | `~/.config/appif/gmail` | Token storage directory |
| `APPIF_GMAIL_POLL_INTERVAL_SECONDS` | `30` | Seconds between poll cycles |
| `APPIF_GMAIL_LABEL_FILTER` | `INBOX` | Comma-separated label IDs to watch |
| `APPIF_GMAIL_DELIVERY_MODE` | `AUTOMATIC` | `AUTOMATIC` (send) or `ASSISTED` (draft) |

### Outlook

| Variable | Default | Description |
|---|---|---|
| `APPIF_OUTLOOK_CLIENT_ID` | (required) | Azure AD application (client) ID |
| `APPIF_OUTLOOK_CLIENT_SECRET` | (none) | Client secret for confidential-client flow |
| `APPIF_OUTLOOK_TENANT_ID` | `common` | Azure AD tenant |
| `APPIF_OUTLOOK_ACCOUNT` | `default` | Logical account label |
| `APPIF_OUTLOOK_CREDENTIALS_DIR` | `~/.config/appif/outlook` | MSAL token cache directory |
| `APPIF_OUTLOOK_POLL_INTERVAL_SECONDS` | `30` | Seconds between delta-query cycles |
| `APPIF_OUTLOOK_FOLDER_FILTER` | `Inbox` | Comma-separated folder names to watch |
| `APPIF_OUTLOOK_DELIVERY_MODE` | `poll` | Delivery mode |

### Slack

| Variable | Default | Description |
|---|---|---|
| `APPIF_SLACK_BOT_OAUTH_TOKEN` | (required) | Bot user OAuth token (`xoxb-...`) |
| `APPIF_SLACK_USER_OAUTH_TOKEN` | (optional) | User OAuth token (`xoxp-...`) -- for connecting as yourself |
| `APPIF_SLACK_BOT_APP_LEVEL_TOKEN` | (optional) | App-level token for Socket Mode (`xapp-...`) -- enables real-time events |
