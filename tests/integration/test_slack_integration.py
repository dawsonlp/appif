"""Integration tests for SlackConnector against a LIVE Slack workspace.

These tests hit the real Slack API -- they are NOT mocked.

- Requires APPIF_SLACK_IDENTITY_TOKEN and APPIF_SLACK_APP_TOKEN
  in ~/.env. Skipped automatically if tokens are missing.
- The send target defaults to APPIF_SLACK_TEST_USER_ID env var.
  Never target a coworker or shared channel from automated tests.

Run with: pytest tests/integration/test_slack_integration.py -v
"""

import os

import pytest
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.env"))

_IDENTITY_TOKEN = os.environ.get("APPIF_SLACK_IDENTITY_TOKEN", "")
_APP_TOKEN = os.environ.get("APPIF_SLACK_APP_TOKEN") or None
_HAS_TOKENS = bool(_IDENTITY_TOKEN and _APP_TOKEN)

pytestmark = pytest.mark.skipif(not _HAS_TOKENS, reason="Slack tokens not configured")


from appif.adapters.slack import SlackConnector
from appif.domain.messaging.models import (
    Account,
    ConnectorCapabilities,
    ConnectorStatus,
    ConversationRef,
    MessageContent,
    SendReceipt,
    Target,
)


def _make_connector() -> SlackConnector:
    """Create a connector using environment tokens."""
    return SlackConnector(identity_token=_IDENTITY_TOKEN, app_token=_APP_TOKEN)


class TestSlackIntegrationLifecycle:
    """Tests the full connect -> query -> disconnect lifecycle."""

    def test_connect_and_disconnect(self):
        connector = _make_connector()
        assert connector.get_status() == ConnectorStatus.DISCONNECTED

        connector.connect()
        assert connector.get_status() == ConnectorStatus.CONNECTED

        connector.disconnect()
        assert connector.get_status() == ConnectorStatus.DISCONNECTED

    def test_capabilities_are_populated(self):
        connector = _make_connector()
        caps = connector.get_capabilities()
        assert isinstance(caps, ConnectorCapabilities)
        assert caps.supports_realtime is True
        assert caps.supports_backfill is True
        assert caps.supports_threads is True
        assert caps.supports_reply is True

    def test_list_accounts_returns_workspace(self):
        connector = _make_connector()
        connector.connect()
        try:
            accounts = connector.list_accounts()
            assert len(accounts) >= 1
            acct = accounts[0]
            assert isinstance(acct, Account)
            assert acct.account_id  # non-empty workspace ID
            assert acct.display_name  # non-empty workspace name
            assert acct.connector == "slack"
        finally:
            connector.disconnect()

    def test_list_targets_returns_channels(self):
        connector = _make_connector()
        connector.connect()
        try:
            accounts = connector.list_accounts()
            assert accounts, "Expected at least one workspace"
            account_id = accounts[0].account_id

            targets = connector.list_targets(account_id)
            assert len(targets) > 0, "Expected at least one conversation"

            target = targets[0]
            assert isinstance(target, Target)
            assert target.target_id  # non-empty channel/DM ID
            assert target.display_name  # non-empty name
            assert target.type in ("channel", "dm", "group")
        finally:
            connector.disconnect()

    def test_listener_registration(self):
        """Register and unregister a listener without errors."""
        connector = _make_connector()

        received = []

        class TestListener:
            def on_message(self, event):
                received.append(event)

        listener = TestListener()
        connector.register_listener(listener)
        assert listener in connector._listeners

        connector.unregister_listener(listener)
        assert listener not in connector._listeners

    def test_send_to_self_dm(self):
        """Send a message to the test user's own DM and verify receipt.

        Uses conversations.open to get/create a DM channel with the
        configured test user.
        """
        test_user_id = os.environ.get("APPIF_SLACK_TEST_USER_ID")
        if not test_user_id:
            pytest.skip("APPIF_SLACK_TEST_USER_ID not set")

        connector = _make_connector()
        connector.connect()
        try:
            accounts = connector.list_accounts()
            account_id = accounts[0].account_id

            dm_resp = connector._client.conversations_open(users=[test_user_id])
            dm_channel = dm_resp["channel"]["id"]

            ref = ConversationRef(
                connector="slack",
                account_id=account_id,
                type="dm",
                opaque_id={"channel": dm_channel},
            )
            content = MessageContent(text="Integration test -- please ignore")

            receipt = connector.send(ref, content)

            assert isinstance(receipt, SendReceipt)
            assert receipt.external_id  # Slack message ts
            assert receipt.timestamp  # datetime populated
        finally:
            connector.disconnect()
