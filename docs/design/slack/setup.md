# Slack Connector — Setup Guide

## Prerequisites

- A Slack workspace where you have admin or app-installation permissions.
- Python 3.13+ with the project installed (`uv pip install -e ".[dev]"`).

---

## Identity Model

The Slack connector follows a **one-connector-one-identity** model:

- Give it a **bot token** (`xoxb-`) and it operates as the bot.
- Give it a **user token** (`xoxp-`) and it operates as the user.
- The optional **app-level token** (`xapp-`) enables Socket Mode for real-time event delivery.

Without an app-level token the connector works in **API-only mode** — it can send messages and query the API but does not receive real-time events.

---

## 1. Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App** then **From scratch**.
2. Name it (e.g. `appif-connector`) and select your workspace.

## 2. Configure Token Scopes

Under **OAuth and Permissions**, add the scopes needed for your identity type.

### Bot Token Scopes (for `xoxb-` identity)

| Scope | Reason |
|-------|--------|
| `channels:history` | Read messages in public channels |
| `channels:read` | List and get info about public channels |
| `chat:write` | Send messages |
| `users:read` | Resolve user display names |

### User Token Scopes (for `xoxp-` identity)

| Scope | Reason |
|-------|--------|
| `channels:history` | Read messages in public channels |
| `channels:read` | List and get info about public channels |
| `chat:write` | Send messages |
| `users:read` | Resolve user display names |

Install the app to your workspace. Copy the appropriate token:
- **Bot User OAuth Token** (`xoxb-`) for bot identity
- **User OAuth Token** (`xoxp-`) for user identity

## 3. Enable Socket Mode (Optional — Enables Real-Time)

1. Go to **Settings** then **Socket Mode** and toggle it **on**.
2. Generate an **App-Level Token** with the `connections:write` scope. Copy the token (`xapp-`).

Without this step the connector operates in API-only mode (`supports_realtime=False`, `delivery_mode=MANUAL`).

## 4. Subscribe to Events

Under **Event Subscriptions**, subscribe to bot events (or user events if using a user token):

| Event | Reason |
|-------|--------|
| `message.channels` | Receive messages in public channels |
| `message.groups` | Receive messages in private channels |
| `message.im` | Receive direct messages |
| `message.mpim` | Receive group direct messages |

## 5. Store Credentials

Add the tokens to `~/.env`:

```
# Bot identity token (xoxb-)
APPIF_SLACK_BOT_OAUTH_TOKEN=xoxb-your-bot-token-here

# User identity token (xoxp-) — optional, for connecting as yourself
APPIF_SLACK_USER_OAUTH_TOKEN=xoxp-your-user-token-here

# Optional — enables Socket Mode for real-time events
APPIF_SLACK_BOT_APP_LEVEL_TOKEN=xapp-your-app-level-token
```

## 6. Verify

```bash
# Using the verification script
python scripts/slack_listener.py

# Or using the CLI
python -m appif.cli.slack listen
```

The script prints the identity type, capabilities, and delivery mode. If an app-level token is present it connects via Socket Mode and streams messages. Without an app-level token it reports API-only mode and exits.

### CLI (identity-first)

```bash
# Connect as bot and listen
appif-slack bot listen

# Connect as user and list channels
appif-slack user channels

# Check bot capabilities
appif-slack bot status
```

## 7. Invite the Bot

If using a bot identity, the bot must be a member of any channel it should monitor:

```
/invite @appif-connector
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `NotAuthorized` on startup | Check that `APPIF_SLACK_BOT_OAUTH_TOKEN` or `APPIF_SLACK_USER_OAUTH_TOKEN` is set in `~/.env` |
| No messages received | Ensure the bot/user is in the channel and events are subscribed |
| `invalid_auth` from Slack | Regenerate the identity token and update `~/.env` |
| Socket timeout | Check network / firewall; Socket Mode uses WSS |
| `supports_realtime=False` | Set `APPIF_SLACK_BOT_APP_LEVEL_TOKEN` in `~/.env` to enable Socket Mode |
