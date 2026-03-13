# Outlook Connector — Next Steps

## Current Status

The Outlook connector is fully implemented and unit-tested (66 tests passing). The Graph API has been validated against the Highspring tenant via Graph Explorer, confirming that mail data is accessible in the expected format.

The remaining blocker is an Azure AD app registration, which requires admin action in the Highspring tenant.

## Pending: Azure AD App Registration

A support ticket has been submitted requesting the app registration.

- Ticket: #40098 — "Requesting Azure AD App Registration for Internal Development Tool"
- Submitted: 2026-02-21

### What was requested

1. App registration in Entra ID:
   - Display name: appif-outlook
   - Supported account types: Accounts in this organizational directory only
   - Redirect URI: Public client/native (mobile and desktop), http://localhost

2. Delegated API permissions:
   - Microsoft Graph, Mail.ReadWrite
   - Microsoft Graph, Mail.Send
   - Microsoft Graph, User.Read

3. Admin consent granted for the above permissions

4. The Application (client) ID returned to me

## Once You Receive the Client ID

When the admin provides the Application (client) ID (a GUID), follow these steps:

### 1. Set environment variables

Add to ~/.env:

    APPIF_OUTLOOK_CLIENT_ID=<the-client-id-guid>
    APPIF_OUTLOOK_TENANT_ID=120aeae9-286f-438a-bbf3-de3ab96fcf5d

### 2. Run the consent flow

    cd /Users/ldawson/repos/appif/appif
    source .venv/bin/activate
    python scripts/outlook_consent.py --account highspring --tenant 120aeae9-286f-438a-bbf3-de3ab96fcf5d

This opens a browser for login. After authorization, credentials are saved to ~/.config/appif/outlook/highspring.json.

### 3. Test the connector

    python -c "
    from appif.adapters.outlook import OutlookConnector
    import os
    from dotenv import load_dotenv
    from pathlib import Path

    load_dotenv(Path.home() / '.env')

    connector = OutlookConnector(
        client_id=os.environ['APPIF_OUTLOOK_CLIENT_ID'],
        tenant_id='120aeae9-286f-438a-bbf3-de3ab96fcf5d',
        account='highspring',
    )
    connector.connect()
    print('Status:', connector.get_status())
    print('Accounts:', connector.list_accounts())

    targets = connector.list_targets('highspring')
    print('Folders:', targets)

    connector.disconnect()
    print('Disconnected:', connector.get_status())
    "

## Validated So Far

- Tenant ID: 120aeae9-286f-438a-bbf3-de3ab96fcf5d (Highspring)
- User: larry.dawson@highspring.com (id: e8a61024-c97c-4559-8354-b4e5adad0198)
- Graph API mail access: confirmed working via Graph Explorer with Mail.ReadWrite consent
- 66 unit tests passing across all connector modules
- 148 total unit tests passing (full test suite minus Slack extras)

## Fallback Options (if ticket is denied)

If the admin denies the app registration:

1. Power Automate: Create a flow to export emails to OneDrive/local folder, then build a file-based connector adapter
2. Personal Microsoft account: Register the app under a personal Outlook.com account (no admin restrictions)
3. Escalation: Request the "Application Developer" Azure AD role for your account, which allows self-service app registration