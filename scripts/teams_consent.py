#!/usr/bin/env python3
"""Microsoft Teams OAuth consent helper.

Performs the interactive authorization flow for Teams messaging and persists
the MSAL token cache to the Teams credential directory
(``~/.config/appif/teams/<account>.json``) so ``TeamsConnector`` can load it
at runtime. After consent it lists your chats as a quick confirmation.

Usage:
    python scripts/teams_consent.py                       # chat scopes only (no admin consent)
    python scripts/teams_consent.py --channels            # also request channel scopes (needs admin consent)
    python scripts/teams_consent.py --account work        # named account
    python scripts/teams_consent.py --tenant <tenant-id>

Scopes:
    Chat read/send + User.Read need no admin consent. Channel scopes
    (ChannelMessage.Read.All, Team/Channel.ReadBasic.All, ChannelMessage.Send)
    require Azure AD admin consent — request them with ``--channels`` once an
    admin has approved them for the app registration.

Prerequisites:
    Set APPIF_TEAMS_CLIENT_ID (or APPIF_OUTLOOK_CLIENT_ID — same app reg) and,
    if needed, APPIF_TEAMS_TENANT_ID in ~/.env. Add a redirect URI of
    http://localhost (Mobile & Desktop) to the app registration.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

GRAPH = "https://graph.microsoft.com/v1.0"
DEFAULT_DIR = Path.home() / ".config" / "appif" / "teams"


def main() -> None:
    parser = argparse.ArgumentParser(description="Teams OAuth consent helper")
    parser.add_argument("--account", default="default", help="Logical account label (default: 'default')")
    parser.add_argument("--credentials-dir", type=Path, default=None, help="Credential directory")
    parser.add_argument("--client-id", default=None, help="Azure AD app (client) ID")
    parser.add_argument("--client-secret", default=None, help="Client secret (omit for public client)")
    parser.add_argument("--tenant", default=None, help="Azure AD tenant ID (default: 'common')")
    parser.add_argument("--channels", action="store_true", help="Also request channel scopes (needs admin consent)")
    args = parser.parse_args()

    client_id = args.client_id or os.environ.get("APPIF_TEAMS_CLIENT_ID") or os.environ.get("APPIF_OUTLOOK_CLIENT_ID")
    if not client_id:
        print(
            "❌  No client ID. Set APPIF_TEAMS_CLIENT_ID (or APPIF_OUTLOOK_CLIENT_ID) in ~/.env, or pass --client-id."
        )
        sys.exit(1)

    client_secret = args.client_secret or os.environ.get("APPIF_TEAMS_CLIENT_SECRET")
    tenant_id = (
        args.tenant or os.environ.get("APPIF_TEAMS_TENANT_ID") or os.environ.get("APPIF_OUTLOOK_TENANT_ID", "common")
    )
    cred_dir = args.credentials_dir or Path(os.environ.get("APPIF_TEAMS_CREDENTIALS_DIR", str(DEFAULT_DIR)))

    try:
        import httpx
        import msal
    except ImportError:
        print("❌  Missing deps. Run: uv pip install msal httpx")
        sys.exit(1)

    # Scope set must match what TeamsConnector requests at runtime.
    from appif.adapters.teams._auth import scopes_for

    scopes = scopes_for(include_chats=True, include_channels=args.channels)

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    cache = msal.SerializableTokenCache()
    token_path = cred_dir / f"{args.account}.json"
    if token_path.exists():
        cache.deserialize(token_path.read_text())

    if client_secret:
        app = msal.ConfidentialClientApplication(
            client_id, authority=authority, client_credential=client_secret, token_cache=cache
        )
    else:
        app = msal.PublicClientApplication(client_id, authority=authority, token_cache=cache)

    print(f"🔑  Requesting consent for scopes: {', '.join(s.split('/')[-1] for s in scopes)}")
    print("    A browser window will open for authorization.\n")
    result = app.acquire_token_interactive(scopes=scopes, prompt="consent")

    if "access_token" not in result:
        print(f"❌  Authorization failed: {result.get('error_description', result.get('error'))}")
        sys.exit(1)

    if cache.has_state_changed:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = token_path.with_suffix(".tmp")
        tmp.write_text(cache.serialize())
        tmp.rename(token_path)

    claims = result.get("id_token_claims", {})
    email = claims.get("preferred_username", claims.get("email", "unknown"))
    print(f"✅  Credentials saved → {token_path}")
    print(f"   Account label : {args.account}")
    print(f"   User email    : {email}")

    # Quick confirmation: list chats.
    headers = {"Authorization": f"Bearer {result['access_token']}"}
    try:
        chats = (
            httpx.get(f"{GRAPH}/me/chats", params={"$top": 5}, headers=headers, timeout=30.0).json().get("value", [])
        )
        print(f"\n💬  Verified Teams access — {len(chats)} chat(s) visible (showing up to 5):")
        for c in chats:
            print(f"    - {c.get('topic') or c.get('chatType', 'chat')}")
    except Exception as exc:
        print(f"\n⚠️   Token saved but chat listing failed: {exc}")

    print("\n   TeamsConnector will load this cache on connect().")


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv(Path.home() / ".env")
    except ImportError:
        pass
    main()
