# Gmail Connector Setup

**Last Updated**: 2026-02-21

---

## Overview

The Gmail connector uses OAuth 2.0 to access the Gmail API. Authentication requires a one-time consent flow that produces credentials stored as a JSON file. The connector loads these credentials at runtime — no refresh tokens in environment variables.

This guide covers two scenarios:

| Scenario | Token Lifetime | Consent Screen Type |
|----------|----------------|---------------------|
| **Google Workspace** (org email) | Indefinite | Internal |
| **Consumer Gmail** (@gmail.com) | 7 days in Testing mode | External |

---

## Prerequisites

- A Google Cloud project with the Gmail API enabled
- `uv pip install -e ".[gmail]"` (installs `google-api-python-client` and `google-auth-oauthlib`)
- Access to `~/.env` for client credentials

---

## Step 1: Google Cloud Project Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Navigate to **APIs & Services → Library**
4. Search for "Gmail API" and click **Enable**

---

## Step 2: OAuth Consent Screen

### For Google Workspace Accounts (Recommended)

1. Navigate to **APIs & Services → OAuth consent screen**
2. Select **Internal** as the user type
3. Fill in the required fields:
   - App name: `Application Interfaces`
   - User support email: your email
   - Developer contact email: your email
4. Add scopes:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.send`
   - `https://www.googleapis.com/auth/gmail.compose`
   - `https://www.googleapis.com/auth/gmail.modify`
5. Save

**Result**: Internal apps do not require Google verification. Refresh tokens do not expire.

### For Consumer Gmail (@gmail.com)

1. Navigate to **APIs & Services → OAuth consent screen**
2. Select **External** as the user type
3. Fill in the required fields (same as above)
4. Add the same scopes listed above
5. Under **Test users**, add your Gmail address
6. Save — the app remains in "Testing" status

**Important**: While in Testing mode, refresh tokens expire after **7 days**. You must re-run the consent script to re-authorize. To get permanent tokens, submit the app for Google verification (takes days to weeks).

---

## Step 3: Create OAuth 2.0 Credentials

1. Navigate to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. Application type: **Desktop application**
4. Name: `appif-gmail` (or any descriptive name)
5. Click **Create**
6. Copy the **Client ID** and **Client Secret**

Add these to `~/.env`:

```bash
# Gmail OAuth client credentials
APPIF_GMAIL_CLIENT_ID=your-client-id.apps.googleusercontent.com
APPIF_GMAIL_CLIENT_SECRET=your-client-secret
```

> **Troubleshooting**: If the client secret is not visible in the newer Google Auth Platform UI, or the "Download JSON" button does not work, see `docs/design/gmail/bug_report.md` for known issues and workarounds.

---

## Step 4: Run the Consent Flow

```bash
python scripts/gmail_consent.py
```

This will:

1. Read `APPIF_GMAIL_CLIENT_ID` and `APPIF_GMAIL_CLIENT_SECRET` from `~/.env`
2. Open your browser for Google OAuth consent
3. After authorization, verify the credentials with the Gmail API
4. Save credentials to `~/.config/appif/gmail/<account>.json`
5. Print the detected account email

### Specifying an Account

```bash
python scripts/gmail_consent.py --account user@gmail.com
```

If the specified account doesn't match the authenticated account, the script warns and uses the detected account.

### Custom Credentials Directory

```bash
python scripts/gmail_consent.py --credentials-dir /path/to/dir
```

---

## Step 5: Configure Environment

After the consent flow succeeds, add to `~/.env`:

```bash
# Which account the connector should use
APPIF_GMAIL_ACCOUNT=user@gmail.com

# Optional configuration
# APPIF_GMAIL_POLL_INTERVAL_SECONDS=30
# APPIF_GMAIL_LABEL_FILTER=INBOX
# APPIF_GMAIL_DELIVERY_MODE=AUTOMATIC
# APPIF_GMAIL_CREDENTIALS_DIR=~/.config/appif/gmail
```

---

## Credential Storage

### File Location

Credentials are stored as JSON files, one per account:

```
~/.config/appif/gmail/
├── user@gmail.com.json
├── user@workspace.example.com.json
└── ...
```

### File Format

```json
{
  "token": "ya29.access-token...",
  "refresh_token": "1//refresh-token...",
  "token_uri": "https://oauth2.googleapis.com/token",
  "client_id": "...apps.googleusercontent.com",
  "client_secret": "...",
  "scopes": [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify"
  ]
}
```

### Security

- File permissions: `600` (owner read/write only)
- Files are stored outside the project directory — never committed to git
- The `google-auth` library handles token refresh automatically using the stored refresh token and client credentials
- Access tokens expire after ~1 hour and are refreshed transparently

### Multi-Account Support

The connector identifies credentials by account email. To connect multiple accounts:

1. Run the consent script for each account:
   ```bash
   python scripts/gmail_consent.py  # authorize first account
   python scripts/gmail_consent.py  # authorize second account (different browser session)
   ```
2. Each produces a separate JSON file named after the detected account

---

## Token Expiry Reference

| Account Type | Consent Screen | Token Lifetime | Action on Expiry |
|-------------|----------------|----------------|------------------|
| Workspace | Internal | Indefinite | None needed |
| Consumer | External (Testing) | **7 days** | Re-run `gmail_consent.py` |
| Consumer | External (Published) | Indefinite* | None needed |

\* Published apps must pass Google's verification review. Tokens may still be revoked if the user removes access or Google detects policy violations.

---

## Troubleshooting

### "Access blocked: This app's request is invalid"
- Verify the OAuth consent screen has the correct scopes
- For external apps: ensure your email is listed as a test user

### "Error 403: access_denied"
- For Workspace: ensure the Gmail API is enabled in the admin console
- For consumer: ensure your email is listed as a test user on the consent screen

### Client secret not visible / "Download JSON" does nothing
- See `docs/design/gmail/bug_report.md` for a detailed write-up of this known Google Console UI issue and workarounds

### Token expired (consumer account)
```bash
python scripts/gmail_consent.py
```
Re-running the consent flow produces a fresh token.

### Credentials file not found
- Check `~/.config/appif/gmail/` for the expected JSON file
- Verify `APPIF_GMAIL_ACCOUNT` in `~/.env` matches the filename (minus `.json`)

### "RefreshError: Token has been expired or revoked"
- The refresh token is no longer valid (7-day expiry for Testing mode, or user revoked access)
- Re-run `gmail_consent.py` to re-authorize