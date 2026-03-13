"""Send a message AS the user to the bot, via sync SlackConnector with user token."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from appif.adapters.slack._auth import StaticTokenAuth
from appif.adapters.slack.connector import SlackConnector
from appif.domain.messaging.models import ConversationRef, MessageContent


def main() -> None:
    load_dotenv(Path.home() / ".env")

    # User token — connector acts as the user
    identity_token = os.environ.get("APPIF_SLACK_USER_OAUTH_TOKEN", "")
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
    print(f"Authenticated as user: {connector._authenticated_user_id}")

    # Look up the bot's user ID and find the existing DM channel via bot token
    # (user token lacks im:write scope to call conversations.open)
    bot_token = os.environ.get("APPIF_SLACK_BOT_OAUTH_TOKEN", "")
    from slack_sdk import WebClient as WC

    bot_client = WC(token=bot_token)
    bot_auth = bot_client.auth_test()
    bot_user_id = bot_auth["user_id"]
    bot_name = bot_auth.get("user", "bot")
    print(f"Bot user ID: {bot_user_id} ({bot_name})")

    # Use bot to open/find the DM (bot has im:write scope)
    dm_resp = bot_client.conversations_open(users=[connector._authenticated_user_id])
    dm_channel = dm_resp["channel"]["id"]
    print(f"DM channel: {dm_channel}")

    # Send message as the user
    ref = ConversationRef(
        connector="slack",
        account_id=connector._team_id or "",
        type="dm",
        opaque_id={"channel": dm_channel},
    )
    content = MessageContent(
        text="User-identity proof-of-life: this message was sent AS the user (xoxp- token) to the bot, via the sync SlackConnector. One connector, one identity. :bust_in_silhouette: :arrow_right: :robot_face:"
    )

    receipt = connector.send(ref, content)
    print(f"Sent! ts={receipt.external_id}  at={receipt.timestamp}")

    connector.disconnect()


if __name__ == "__main__":
    main()