#!/usr/bin/env python3
"""One-time OAuth consent flow for Gmail API access.

Runs the Google OAuth 2.0 installed-app flow, opens a browser for user
consent, and saves credentials to a JSON file for the connector to use.

Usage:
    python scripts/gmail_consent.py                          # interactive
    python scripts/gmail_consent.py --account user@gmail.com # specify account

Prerequisites:
    uv pip install -e ".[gmail]"

Environment variables (from ~/.env):
    APPIF_GMAIL_CLIENT_ID       OAuth 2.0 client ID
    APPIF_GMAIL_CLIENT_SECRET   OAuth 2.0 client secret

This script is a setup utility — it does NOT become part of the connector
runtime. It runs once (or whenever tokens expire) to obtain and persist
OAuth credentials.

Token Lifetime Notes:
    - Consumer Gmail (@gmail.com) with app in "Testing" mode: 7 days
    - Consumer Gmail with verified/published app: indefinite (with caveats)
    - Google Workspace with "Internal" consent screen: indefinite
    - Re-run this script to re-authorize when tokens expire
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load ~/.env for client credentials
load_dotenv(Path.home() / ".env")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]

DEFAULT_CREDENTIALS_DIR = Path.home() / ".config" / "appif" / "gmail"


def get_client_config() -> dict:
    """Build OAuth client config from environment variables."""
    import os

    client_id = os.environ.get("APPIF_GMAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("APPIF_GMAIL_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        print("ERROR: APPIF_GMAIL_CLIENT_ID and APPIF_GMAIL_CLIENT_SECRET must be set in ~/.env")
        print()
        print("To obtain these:")
        print("  1. Go to https://console.cloud.google.com/apis/credentials")
        print("  2. Create an OAuth 2.0 Client ID (type: Desktop application)")
        print("  3. Copy the client ID and secret to ~/.env")
        print()
        print("If the client secret is not visible in the newer Google Auth")
        print("Platform UI, see docs/design/gmail/bug_report.md for workarounds.")
        sys.exit(1)

    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def run_consent_flow(client_config: dict):
    """Run the OAuth installed-app flow and return credentials."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("ERROR: google-auth-oauthlib not installed.")
        print("  Install with: uv pip install -e '.[gmail]'")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)

    # run_local_server opens a browser and starts a temporary local HTTP
    # server to receive the OAuth callback. Works for both consumer and
    # Workspace accounts. No external IP needed — redirect goes to localhost.
    credentials = flow.run_local_server(
        port=8090,
        prompt="consent",
        access_type="offline",
    )

    return credentials


def save_credentials(credentials, account: str, credentials_dir: Path) -> Path:
    """Persist credentials to a JSON file with restrictive permissions.

    The file format is compatible with google.oauth2.credentials.Credentials
    .from_authorized_user_info(), so the connector can reload it directly.
    """
    credentials_dir.mkdir(parents=True, exist_ok=True)

    cred_path = credentials_dir / f"{account}.json"

    cred_data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes or SCOPES),
    }

    cred_path.write_text(json.dumps(cred_data, indent=2))
    cred_path.chmod(0o600)  # Owner read/write only

    return cred_path


def verify_credentials(credentials) -> str:
    """Call Gmail API to verify credentials and return the account email."""
    try:
        from googleapiclient.discovery import build
    except ImportError:
        print("ERROR: google-api-python-client not installed.")
        print("  Install with: uv pip install -e '.[gmail]'")
        sys.exit(1)

    service = build("gmail", "v1", credentials=credentials)
    profile = service.users().getProfile(userId="me").execute()
    return profile["emailAddress"]


def main():
    parser = argparse.ArgumentParser(
        description="Gmail OAuth consent flow — obtain and save credentials for the Gmail connector"
    )
    parser.add_argument(
        "--account",
        help="Expected email address (auto-detected from API if not provided)",
    )
    parser.add_argument(
        "--credentials-dir",
        type=Path,
        default=DEFAULT_CREDENTIALS_DIR,
        help=f"Directory to store credentials (default: {DEFAULT_CREDENTIALS_DIR})",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Gmail OAuth Consent Flow")
    print("=" * 60)
    print()
    print("This will open your browser to authorize Gmail API access.")
    print("Required scopes:")
    for scope in SCOPES:
        print(f"  - {scope.split('/')[-1]}")
    print()

    client_config = get_client_config()
    credentials = run_consent_flow(client_config)

    # Verify credentials and detect account email
    print("Verifying credentials with Gmail API...")
    detected_account = verify_credentials(credentials)
    account = args.account or detected_account

    if args.account and args.account.lower() != detected_account.lower():
        print(f"WARNING: Specified account '{args.account}' does not match")
        print(f"         authenticated account '{detected_account}'")
        print(f"         Saving under detected account: {detected_account}")
        account = detected_account

    # Save credentials to file
    cred_path = save_credentials(credentials, account, args.credentials_dir)

    print()
    print("=" * 60)
    print("SUCCESS")
    print("=" * 60)
    print(f"  Account:     {account}")
    print(f"  Credentials: {cred_path}")
    print(f"  Permissions: 600 (owner read/write only)")
    print()
    print("Ensure ~/.env contains:")
    print(f"  APPIF_GMAIL_ACCOUNT={account}")
    print()

    # Detect account type and warn about token lifetime
    if account.lower().endswith("@gmail.com") or account.lower().endswith("@googlemail.com"):
        print("CONSUMER ACCOUNT DETECTED")
        print("  Refresh tokens expire after 7 days while your Google Cloud")
        print("  app is in 'Testing' mode. Re-run this script to re-authorize.")
        print("  To avoid this, submit your app for Google verification.")
    else:
        print("WORKSPACE ACCOUNT DETECTED")
        print("  If your OAuth consent screen is set to 'Internal',")
        print("  refresh tokens do not expire. No action needed.")
    print()


if __name__ == "__main__":
    main()