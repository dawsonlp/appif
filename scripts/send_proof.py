"""Send a proof-of-life message via the sync SlackConnector."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from appif.adapters.slack._auth import StaticTokenAuth
from appif.adapters.slack.connector import SlackConnector
from appif.domain.messaging.models import ConversationRef, MessageContent


def main() -> None:
    load_dotenv(Path.home() / ".env")

    # Map old env var names to the identity model
    identity_token = os.environ.get("APPIF_SLACK_BOT_OAUTH_TOKEN", "")
    app_token = os.environ.get("APPIF_SLACK_BOT_APP_LEVEL_TOKEN") or None

    auth = StaticTokenAuth(identity_token=identity_token, app_token=app_token)
    print(f"Identity type: {auth.identity_type}")
    print(f"Token prefix:  {auth.identity_token[:10]}...")

    connector = SlackConnector(
        identity_token=auth.identity_token,
        app_token=auth.app_token,
    )

    connector.connect()
    print(f"Status: {connector.get_status()}")
    print(f"Team:   {connector._team_name} ({connector._team_id})")

    # Look up user by email to get their Slack user ID
    resp = connector._client.users_lookupByEmail(email="ldawson@builtglobal.com")
    user_id = resp["user"]["id"]
    print(f"Found user: {user_id}")

    # Open a DM channel with that user
    dm_resp = connector._client.conversations_open(users=[user_id])
    dm_channel = dm_resp["channel"]["id"]
    print(f"DM channel: {dm_channel}")

    # Send the message
    ref = ConversationRef(
        connector="slack",
        account_id=connector._team_id or "",
        type="dm",
        opaque_id={"channel": dm_channel},
    )
    content = MessageContent(
        text="Sync rewrite proof-of-life: this message was sent by the refactored sync SlackConnector using `App` + `WebClient` + `threading.Thread`. No async anywhere. :white_check_mark:"
    )

    receipt = connector.send(ref, content)
    print(f"Sent! ts={receipt.external_id}  at={receipt.timestamp}")

    connector.disconnect()


if __name__ == "__main__":
    main()