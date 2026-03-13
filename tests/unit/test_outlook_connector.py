"""Unit tests for the OutlookConnector."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from appif.domain.messaging.errors import NotAuthorized, NotSupported
from appif.domain.messaging.models import (
    BackfillScope,
    ConnectorStatus,
    ConversationRef,
    MessageContent,
)


class TestConnectorLifecycle:
    """Tests for connect / disconnect."""

    def _make_connector(self, **kwargs):
        """Create an OutlookConnector with defaults."""
        from appif.adapters.outlook.connector import OutlookConnector

        defaults = {
            "client_id": "test-client-id",
            "credentials_dir": "/tmp/outlook-test",
            "account": "test",
            "poll_interval": 60,
        }
        defaults.update(kwargs)
        return OutlookConnector(**defaults)

    @patch("appif.adapters.outlook.connector.OutlookPoller")
    @patch("appif.adapters.outlook.connector.MsalAuth")
    def test_connect_disconnect_cycle(self, MockAuth, MockPoller):
        """connect() + disconnect() completes without error."""
        mock_auth = MagicMock()
        mock_auth.acquire.return_value = MagicMock(token="test-token", expires_on=9999)
        mock_auth.user_email.return_value = "user@test.com"
        MockAuth.return_value = mock_auth

        mock_poller = MagicMock()
        MockPoller.return_value = mock_poller

        connector = self._make_connector()

        connector.connect()
        assert connector.get_status() == ConnectorStatus.CONNECTED

        connector.disconnect()
        assert connector.get_status() == ConnectorStatus.DISCONNECTED
        mock_poller.stop.assert_called_once()

    def test_connect_without_client_id_raises(self):
        """Missing client_id → NotAuthorized."""
        from appif.adapters.outlook.connector import OutlookConnector

        connector = OutlookConnector(
            client_id="",
            credentials_dir="/tmp/test",
        )

        with pytest.raises(NotAuthorized, match="No client_id"):
            connector.connect()

    @patch("appif.adapters.outlook.connector.OutlookPoller")
    @patch("appif.adapters.outlook.connector.MsalAuth")
    def test_disconnect_is_idempotent(self, MockAuth, MockPoller):
        """Calling disconnect when already disconnected doesn't raise."""
        from appif.adapters.outlook.connector import OutlookConnector

        connector = OutlookConnector(client_id="test", credentials_dir="/tmp/test")
        connector.disconnect()  # Should not raise


class TestConnectorCapabilities:
    """Tests for get_capabilities."""

    def test_capabilities_values(self):
        """get_capabilities returns correct Outlook-specific values."""
        from appif.adapters.outlook.connector import OutlookConnector

        connector = OutlookConnector(client_id="test", credentials_dir="/tmp/test")
        caps = connector.get_capabilities()

        assert caps.supports_threads is True
        assert caps.supports_backfill is True
        assert caps.supports_reply is True
        assert caps.supports_auto_send is True
        assert caps.delivery_mode == "AUTOMATIC"


class TestConnectorDiscovery:
    """Tests for list_accounts and list_targets."""

    def test_list_accounts_returns_configured_account(self):
        """list_accounts returns the configured account."""
        from appif.adapters.outlook.connector import OutlookConnector

        connector = OutlookConnector(
            client_id="test",
            credentials_dir="/tmp/test",
            account="myaccount",
        )
        accounts = connector.list_accounts()
        assert len(accounts) == 1
        assert accounts[0].account_id == "myaccount"

    def test_list_targets_requires_connected(self):
        """list_targets raises when not connected."""
        from appif.adapters.outlook.connector import OutlookConnector

        connector = OutlookConnector(client_id="test", credentials_dir="/tmp/test")
        with pytest.raises(NotSupported, match="not connected"):
            connector.list_targets("test")


class TestConnectorListeners:
    """Tests for register/unregister listeners."""

    def test_register_and_unregister(self):
        from appif.adapters.outlook.connector import OutlookConnector

        connector = OutlookConnector(client_id="test", credentials_dir="/tmp/test")

        listener = MagicMock()
        connector.register_listener(listener)
        assert listener in connector._listeners

        connector.unregister_listener(listener)
        assert listener not in connector._listeners

    def test_unregister_nonexistent_is_silent(self):
        from appif.adapters.outlook.connector import OutlookConnector

        connector = OutlookConnector(client_id="test", credentials_dir="/tmp/test")
        connector.unregister_listener(MagicMock())  # Should not raise

    def test_duplicate_register_is_idempotent(self):
        from appif.adapters.outlook.connector import OutlookConnector

        connector = OutlookConnector(client_id="test", credentials_dir="/tmp/test")
        listener = MagicMock()
        connector.register_listener(listener)
        connector.register_listener(listener)
        assert connector._listeners.count(listener) == 1


class TestConnectorSend:
    """Tests for send method."""

    def test_send_requires_connected(self):
        """send() raises when not connected."""
        from appif.adapters.outlook.connector import OutlookConnector

        connector = OutlookConnector(client_id="test", credentials_dir="/tmp/test")
        conversation = ConversationRef(
            connector="outlook",
            account_id="test",
            type="email_thread",
            opaque_id={"recipient": "bob@test.com"},
        )
        content = MessageContent(text="hello")

        with pytest.raises(NotSupported, match="not connected"):
            connector.send(conversation, content)


class TestConnectorBackfill:
    """Tests for backfill method."""

    def test_backfill_requires_connected(self):
        """backfill() raises when not connected."""
        from appif.adapters.outlook.connector import OutlookConnector

        connector = OutlookConnector(client_id="test", credentials_dir="/tmp/test")
        scope = BackfillScope()

        with pytest.raises(NotSupported, match="not connected"):
            connector.backfill("test", scope)
