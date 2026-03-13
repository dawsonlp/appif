#!/usr/bin/env python3
"""Outlook / Microsoft 365 OAuth 2.0 consent helper.

Performs the authorization-code flow for a Microsoft 365 account and
persists the resulting MSAL token cache so the Outlook connector can
pick it up at runtime.

Usage:
    python scripts/outlook_consent.py                        # uses 'default' account
    python scripts/outlook_consent.py --account work         # named account
    python scripts/outlook_consent.py --tenant <tenant-id>   # specific tenant

Prerequisites:
    1. Register an app in Azure AD → App registrations.
    2. Add redirect URI: http://localhost (Mobile & Desktop).
    3. Add API permissions: Mail.ReadWrite, Mail.Send, User.Read.
    4. Set APPIF_OUTLOOK_CLIENT_ID in ~/.env (or pass --client-id).
    5. Run this script — it opens a browser for consent.
    6. After consent the MSAL token cache is stored in:
         ~/.config/appif/outlook/<account>.json
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/User.Read",
]

DEFAULT_DIR = Path.home() / ".config" / "appif" / "outlook"


def main() -> None:
    parser = argparse.ArgumentParser(description="Outlook OAuth consent helper")
    parser.add_argument("--account", default="default", help="Logical account label (default: 'default')")
    parser.add_argument("--credentials-dir", type=Path, default=None, help="Credential directory")
    parser.add_argument("--client-id", default=None, help="Azure AD app (client) ID")
    parser.add_argument("--client-secret", default=None, help="Client secret (omit for public-client)")
    parser.add_argument("--tenant", default=None, help="Azure AD tenant ID (default: 'common')")
    args = parser.parse_args()

    # ── resolve configuration from args / env ─────────────────
    client_id = args.client_id or os.environ.get("APPIF_OUTLOOK_CLIENT_ID")
    if not client_id:
        print("❌  No client ID provided.")
        print("   Set APPIF_OUTLOOK_CLIENT_ID in ~/.env or pass --client-id.")
        sys.exit(1)

    client_secret = args.client_secret or os.environ.get("APPIF_OUTLOOK_CLIENT_SECRET")
    tenant_id = args.tenant or os.environ.get("APPIF_OUTLOOK_TENANT_ID", "common")
    cred_dir = args.credentials_dir or Path(os.environ.get("APPIF_OUTLOOK_CREDENTIALS_DIR", str(DEFAULT_DIR)))
    account = args.account

    # ── lazy-import MSAL (optional extra) ─────────────────────
    try:
        import msal  # type: ignore[import-untyped]
    except ImportError:
        print("❌  msal is not installed.")
        print('   Run: uv pip install -e ".[outlook]"')
        sys.exit(1)

    # ── build the MSAL application ────────────────────────────
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    cache = msal.SerializableTokenCache()

    if client_secret:
        app = msal.ConfidentialClientApplication(
            client_id,
            authority=authority,
            client_credential=client_secret,
            token_cache=cache,
        )
    else:
        app = msal.PublicClientApplication(
            client_id,
            authority=authority,
            token_cache=cache,
        )

    # ── run interactive flow ──────────────────────────────────
    print(f"🔑  Starting consent flow for tenant '{tenant_id}'...")
    print("   A browser window will open for authorization.\n")

    result = app.acquire_token_interactive(
        scopes=SCOPES,
        prompt="consent",
    )

    if "error" in result:
        print(f"❌  Authorization failed: {result.get('error_description', result.get('error'))}")
        sys.exit(1)

    # ── persist the MSAL token cache ──────────────────────────
    token_path = cred_dir / f"{account}.json"
    token_path.parent.mkdir(parents=True, exist_ok=True)

    cache_data = cache.serialize()
    tmp = token_path.with_suffix(".tmp")
    tmp.write_text(cache_data)
    tmp.rename(token_path)

    # ── report success ────────────────────────────────────────
    id_claims = result.get("id_token_claims", {})
    email = id_claims.get("preferred_username", id_claims.get("email", "unknown"))

    print(f"✅  Credentials saved → {token_path}")
    print(f"   Account label : {account}")
    print(f"   User email    : {email}")
    print(f"   Scopes        : {', '.join(SCOPES)}")
    print("\n   The Outlook connector will load this cache on connect().")


if __name__ == "__main__":
    # Load .env if available
    try:
        from dotenv import load_dotenv

        env_path = Path.home() / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass

    main()
