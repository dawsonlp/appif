# Outlook / Microsoft 365 Connector -- Quickstart Guide

## Overview

The Outlook connector reads and sends email via the Microsoft Graph API using delegated permissions. It operates under your own identity -- no admin-level or application-level access is needed.

## Prerequisites

- Python 3.13+
- Application Interfaces repo cloned and installed: `uv pip install -e ".[dev]"`
- Access to an Azure AD / Entra ID tenant (your organization's IT creates the app registration)

---

## Step 1: Request an Azure AD App Registration

Submit a request to your IT team for an Entra ID (Azure AD) app registration. Provide these details:

### What IT needs to create

| Setting | Value |
|---------|-------|
| Display name | `appif-outlook` |
| Supported account types | Accounts in this organizational directory only |
| Redirect URI | Platform: **Mobile and desktop applications**, URI: `http://localhost` |
| Allow public client flows | **Yes** (under Authentication > Advanced settings) |

### API permissions (all Delegated, not Application)

| Permission | Type | Purpose |
|-----------|------|---------|
| `Mail.ReadWrite` | Delegated | Read and write mail |
| `Mail.Send` | Delegated | Send mail |
| `User.Read` | Delegated | Read user profile (for email address) |

Ask IT to grant admin consent for these permissions.

### What you need back from IT

- The **Application (client) ID** -- a GUID from the app's Overview page (e.g. `9a750564-d180-406d-8222-851d38ea0e75`)

### Security notes for the ticket

- All permissions are delegated (acts as you, not as the application)
- No client secret needed -- this uses the public client flow
- Only accesses your own mailbox, no other users' data
- No admin or application-level permissions requested

### Reference

The redirect URI `http://localhost` is required per Microsoft's documentation for desktop apps using system browsers with MSAL:
[Desktop app that calls web APIs: Code configuration](https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-app-registration)

---

## Step 2: Discover Your Tenant ID

If your app is registered as single-tenant (recommended), you need your organization's tenant ID. You can discover it from your email domain:

```bash
curl -s "https://login.microsoftonline.com/YOUR-DOMAIN.com/.well-known/openid-configuration" \
  | jq -r '.token_endpoint'
```

The tenant ID is the GUID in the URL path. For example:

```
https://login.microsoftonline.com/120aeae9-286f-438a-bbf3-de3ab96fcf5d/oauth2/token
                                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                   This is your tenant ID
```

Alternatively, ask IT for the **Directory (tenant) ID** from the app's Overview page in the Azure Portal.

---

## Step 3: Configure Environment Variables

Add to your `~/.env` file:

```bash
# Required -- the Application (client) ID from Azure
APPIF_OUTLOOK_CLIENT_ID=9a750564-d180-406d-8222-851d38ea0e75

# Required for single-tenant apps -- your org's tenant ID
APPIF_OUTLOOK_TENANT_ID=120aeae9-286f-438a-bbf3-de3ab96fcf5d
```

### Optional settings

```bash
# Logical account label (default: "default")
# APPIF_OUTLOOK_ACCOUNT=default

# Directory for per-account token caches (default: ~/.config/appif/outlook)
# APPIF_OUTLOOK_CREDENTIALS_DIR=~/.config/appif/outlook

# Delta-poll cadence in seconds (default: 30)
# APPIF_OUTLOOK_POLL_INTERVAL_SECONDS=30

# Folder to watch (default: Inbox)
# APPIF_OUTLOOK_FOLDER_FILTER=Inbox
```

---

## Step 4: Run the Consent Flow

The consent script opens a browser for you to sign in and authorize the app:

```bash
python scripts/outlook_consent.py
```

On success:

```
✅  Credentials saved -> ~/.config/appif/outlook/default.json
   Account label : default
   User email    : you@yourorg.com
   Scopes        : Mail.ReadWrite, Mail.Send, User.Read
```

### Named accounts

```bash
python scripts/outlook_consent.py --account work
python scripts/outlook_consent.py --account personal
```

### Using the CLI

```bash
appif-outlook consent
appif-outlook consent --account work
```

---

## Step 5: Verify with the CLI

### Check status and configuration

```bash
appif-outlook status
```

Shows your configuration, whether the credential file exists, and tests connectivity by calling the Graph API.

### List mail folders

```bash
appif-outlook folders
```

### Read recent inbox messages

```bash
appif-outlook inbox
appif-outlook inbox --limit 5
appif-outlook inbox --since 1h
```

### Send a test email

```bash
appif-outlook send you@yourorg.com "Hello from appif" --subject "Test message"
```

---

## Credential Storage

Credentials are stored as MSAL serialized token caches:

```
~/.config/appif/outlook/
├── default.json     # Default account
├── work.json        # Named account "work"
└── personal.json    # Named account "personal"
```

These files contain OAuth refresh tokens. The connector automatically refreshes access tokens using `acquire_token_silent`. If the refresh token expires (typically 90 days of inactivity), re-run the consent script.

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `appif-outlook status` | Show config, test connection, display capabilities |
| `appif-outlook folders` | List mail folders |
| `appif-outlook inbox` | Show recent inbox messages |
| `appif-outlook inbox --limit N` | Limit number of messages shown |
| `appif-outlook inbox --since 1h` | Show messages from the last hour |
| `appif-outlook send TO TEXT` | Send an email |
| `appif-outlook send TO TEXT --subject S` | Send with custom subject |
| `appif-outlook consent` | Run the OAuth consent flow |
| `appif-outlook consent --account NAME` | Consent for a named account |

---

## Programmatic Usage

```python
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path.home() / ".env")

from appif.adapters.outlook.connector import OutlookConnector
from appif.domain.messaging.models import ConversationRef, MessageContent

connector = OutlookConnector()
connector.connect()

# Check status
print(connector.get_status())        # ConnectorStatus.CONNECTED
print(connector.list_accounts())     # [Account(display_name='you@org.com', ...)]

# Send a message
conv = ConversationRef(
    connector="outlook",
    account_id="default",
    type="email_thread",
    opaque_id={"recipient": "someone@example.com"},
)
content = MessageContent(text="Hello from the Outlook connector!")
receipt = connector.send(conv, content)
print(receipt)

connector.disconnect()
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `NotAuthorized: No cached credentials` | Consent not run | Run `appif-outlook consent` |
| `NotAuthorized: Token refresh failed` | Refresh token expired | Re-run `appif-outlook consent` |
| `AADSTS50194: not configured as multi-tenant` | Using `common` with single-tenant app | Set `APPIF_OUTLOOK_TENANT_ID` to your org tenant ID |
| `AADSTS65001: consent required` | Missing API permissions | Ask IT to add permissions in Azure Portal |
| `AADSTS50011: reply URL mismatch` | Wrong redirect URI | Ask IT to add `http://localhost` as Mobile and Desktop redirect |
| `TokenCache has no attribute serialize` | Old consent script | Update to latest `scripts/outlook_consent.py` |
| `appif-outlook: command not found` | Package not installed | Run `uv pip install -e ".[dev]"` |