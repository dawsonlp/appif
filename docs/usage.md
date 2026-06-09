# appif Usage Guide

## Overview

appif provides a unified messaging interface across Gmail, Outlook, Slack, and Microsoft Teams. Every connector implements the same `Connector` protocol and produces the same domain types. Your code works with `MessageEvent`, `ConversationRef`, and `MessageContent` -- never with platform-specific objects.

## Installation

```bash
pip install appif
```

All four connectors (Gmail, Outlook, Slack, Teams) and their dependencies are included.

## The Unified Model

### MessageEvent -- every inbound message

Every message from every platform arrives as a `MessageEvent`:

```python
from appif.domain.messaging.models import MessageEvent

# Fields:
#   message_id: str          -- unique ID (platform-assigned)
#   connector: str           -- "gmail", "outlook", "slack", or "teams"
#   account_id: str          -- which account received it
#   conversation_ref: ConversationRef  -- opaque reply handle
#   author: Identity         -- who sent it
#   timestamp: datetime      -- when it was sent
#   content: MessageContent  -- body text + attachments
#   recipients: Recipients   -- who it was addressed to (to/cc/bcc)
#   metadata: dict           -- platform-specific extras
```

### Identity -- a person (sender or recipient)

```python
from appif.domain.messaging.models import Identity

# Fields:
#   id: str            -- platform user ID or email address
#   display_name: str  -- human-readable name
#   connector: str     -- "gmail", "outlook", "slack", or "teams"
#   email: str | None  -- email address when resolvable (None if unknown)
```

For email connectors `email` equals `id` (the address). For Slack it is filled
from `users.info` when the token carries the `users:read.email` scope, and is
`None` otherwise.

### Recipients -- who a message was addressed to

```python
from appif.domain.messaging.models import Recipients

# Fields (each a list[Identity], all default empty):
#   to:  list[Identity]
#   cc:  list[Identity]
#   bcc: list[Identity]
```

Populated per connector: Outlook from Graph `toRecipients`/`ccRecipients`, Gmail
from the `To`/`Cc` headers, Slack best-effort from `@`-mentions in the message
text. `bcc` is normally only present on messages you sent yourself. A connector
that cannot determine recipients leaves them empty, so the field is always safe
to read.

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
#   connector: str      -- "gmail", "outlook", "slack", or "teams"
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

All connectors follow the same lifecycle:

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
| To / Cc / Bcc | `MessageEvent.recipients` (`to`/`cc`/`bcc` lists of `Identity`) |
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
- **Reading sent mail**: By default messages you sent are suppressed. Set `include_sent=True` (or `APPIF_GMAIL_INCLUDE_SENT=true`) to deliver them to listeners alongside incoming mail. The `SENT` label is added to the watch set automatically when enabled. **Caution:** if a listener replies to messages it receives, enabling this can create an echo loop (your reply comes back as a sent message); make such listeners idempotent or filter on `event.author`.
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
| To / Cc / Bcc | `MessageEvent.recipients` (`to`/`cc`/`bcc` lists of `Identity`) |
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
- **Reading sent mail**: By default messages you sent are suppressed. Set `include_sent=True` (or `APPIF_OUTLOOK_INCLUDE_SENT=true`) to deliver them to listeners alongside incoming mail. The `SentItems` folder is added to the watch set automatically when enabled. **Caution:** if a listener replies to messages it receives, enabling this can create an echo loop; make such listeners idempotent or filter on `event.author`.
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
| User | `Identity` (id = Slack user ID, e.g. `U01ABCDEF`; `email` set when `users:read.email` granted) |
| `@`-mentions in text | `MessageEvent.recipients.to` (best-effort) |
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
- **Reading your own messages**: By default messages from the authenticated identity are filtered out. Set `include_sent=True` (or `APPIF_SLACK_INCLUDE_SENT=true`) to deliver them to listeners alongside other messages. **Caution:** if a listener replies to messages it receives, enabling this can create an echo loop; make such listeners idempotent or filter on `event.author`.
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

## Teams

```python
from appif.adapters.teams import TeamsConnector

connector = TeamsConnector()   # chats by default; channels opt-in
connector.connect()
connector.register_listener(handler)
```

Run `python scripts/teams_consent.py` once to authorize (add `--channels` for channel access, which needs admin consent). Teams reuses the same Microsoft Graph + MSAL stack as Outlook but keeps a **separate token cache** at `~/.config/appif/teams`. It can share the Outlook Azure app registration: `client_id`/`tenant_id` fall back to `APPIF_OUTLOOK_*` when the Teams-specific vars are unset.

### Teams Concept Mapping

| Teams Concept | appif Model |
|---|---|
| Microsoft 365 account | `Account.account_id` / `Account.display_name` |
| Chat (1:1, group, meeting) | `Target` (type: `"chat"`); `ConversationRef` (type: `"chat"`) |
| Team channel | `Target` (type: `"channel"`); `ConversationRef` (type: `"channel"`) |
| Chat or channel message | `MessageEvent` |
| Sender | `Identity` (id = AAD user id) |
| `@`-mentions in message | `MessageEvent.recipients.to` (best-effort) |
| Message body (HTML→text) | `MessageContent.text` |
| Subject, importance, raw HTML | `MessageEvent.metadata` |

### Teams-Specific Behavior

- **Sources**: 1:1/group **chats** are watched by default. **Channels** are opt-in (`include_channels=True` / `APPIF_TEAMS_INCLUDE_CHANNELS=true`) because `ChannelMessage.Read.All` requires Azure AD **admin consent**; chats need none.
- **Polling**: Uses Graph `messages/delta` per chat and per channel (v1 has no real-time push; that would require Graph change-notification subscriptions). Poll interval via `APPIF_TEAMS_POLL_INTERVAL_SECONDS` (default 30).
- **Reading your own messages**: By default messages you sent are suppressed (`from.user.id == your AAD id`). Set `include_sent=True` (or `APPIF_TEAMS_INCLUDE_SENT=true`) to deliver them. **Caution:** auto-replying listeners can echo-loop; make them idempotent or filter on `event.author`.
- **Threading**: Channel replies are supported — `ConversationRef.opaque_id` carries `team_id`, `channel_id`, and the root `message_id`; sending with a `message_id` posts a reply.
- **HTML stripping**: Message HTML is converted to plain text automatically (raw HTML kept in `metadata["html_body"]`).
- **Send**: Chats post to `/chats/{id}/messages`; channels post a new message or a reply depending on the `ConversationRef`.

### Capabilities

```
supports_realtime:  False (delta polling)
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
| `APPIF_GMAIL_INCLUDE_SENT` | `false` | Deliver your own sent mail to listeners (adds `SENT` to watched labels) |
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
| `APPIF_OUTLOOK_INCLUDE_SENT` | `false` | Deliver your own sent mail to listeners (adds `SentItems` to watched folders) |
| `APPIF_OUTLOOK_DELIVERY_MODE` | `poll` | Delivery mode |

### Slack

| Variable | Default | Description |
|---|---|---|
| `APPIF_SLACK_BOT_OAUTH_TOKEN` | (required) | Bot user OAuth token (`xoxb-...`) |
| `APPIF_SLACK_USER_OAUTH_TOKEN` | (optional) | User OAuth token (`xoxp-...`) -- for connecting as yourself |
| `APPIF_SLACK_BOT_APP_LEVEL_TOKEN` | (optional) | App-level token for Socket Mode (`xapp-...`) -- enables real-time events |
| `APPIF_SLACK_INCLUDE_SENT` | `false` | Deliver your own messages to listeners instead of filtering them |

### Teams

| Variable | Default | Description |
|---|---|---|
| `APPIF_TEAMS_CLIENT_ID` | (falls back to `APPIF_OUTLOOK_CLIENT_ID`) | Azure AD application (client) ID |
| `APPIF_TEAMS_CLIENT_SECRET` | (falls back to `APPIF_OUTLOOK_CLIENT_SECRET`) | Client secret for confidential-client flow |
| `APPIF_TEAMS_TENANT_ID` | (falls back to `APPIF_OUTLOOK_TENANT_ID`, else `common`) | Azure AD tenant |
| `APPIF_TEAMS_ACCOUNT` | `default` | Logical account label |
| `APPIF_TEAMS_CREDENTIALS_DIR` | `~/.config/appif/teams` | MSAL token cache directory (separate from Outlook) |
| `APPIF_TEAMS_POLL_INTERVAL_SECONDS` | `30` | Seconds between delta-poll cycles |
| `APPIF_TEAMS_INCLUDE_CHATS` | `true` | Watch 1:1/group chat messages |
| `APPIF_TEAMS_INCLUDE_CHANNELS` | `false` | Watch team channel messages (needs admin-consented `ChannelMessage.Read.All`) |
| `APPIF_TEAMS_INCLUDE_SENT` | `false` | Deliver your own messages to listeners instead of suppressing them |
