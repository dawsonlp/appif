"""Unit tests for the Gmail connector module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from appif.domain.messaging.errors import ConnectorError, NotAuthorized, NotSupported
from appif.domain.messaging.models import (
    ConnectorStatus,
    ConversationRef,
    MessageContent,
)


def _make_mock_auth(account="user@gmail.com"):
    """Create a mock auth that passes validation."""
    auth = MagicMock()
    auth.account = account
    auth.validate.return_value = None
    auth.credentials = MagicMock()
    auth.save_credentials.return_value = None
    return auth


def _make_mock_service(profile_email="user@gmail.com"):
    """Create a mock Gmail API service."""
    service = MagicMock()
    service.users().getProfile.return_value.execute.return_value = {
        "emailAddress": profile_email,
        "historyId": "12345",
    }
    service.users().messages().send.return_value.execute.return_value = {
        "id": "sent_msg_1",
        "threadId": "t1",
    }
    service.users().drafts().create.return_value.execute.return_value = {
        "id": "draft_1",
        "message": {"id": "draft_msg_1"},
    }
    service.users().messages().list.return_value.execute.return_value = {
        "messages": [],
    }
    return service


class TestGmailConnectorLifecycle:
    """Tests for connect/disconnect lifecycle."""

    @patch("appif.adapters.gmail.connector.call_with_retry", side_effect=lambda fn: fn())
    @patch("appif.adapters.gmail.connector.build_service")
    @patch("appif.adapters.gmail.connector.GmailPoller")
    def test_connect_success(self, mock_poller_cls, mock_build, mock_retry):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        service = _make_mock_service()
        mock_build.return_value = service

        connector = GmailConnector(auth=auth, poll_interval=60)
        connector.connect()

        assert connector.get_status() == ConnectorStatus.CONNECTED
        auth.validate.assert_called_once()
        mock_poller_cls.return_value.start.assert_called_once()

    @patch("appif.adapters.gmail.connector.call_with_retry", side_effect=lambda fn: fn())
    @patch("appif.adapters.gmail.connector.build_service")
    @patch("appif.adapters.gmail.connector.GmailPoller")
    def test_connect_bad_credentials(self, mock_poller_cls, mock_build, mock_retry):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        auth.validate.side_effect = NotAuthorized("gmail", reason="bad creds")

        connector = GmailConnector(auth=auth)

        with pytest.raises(NotAuthorized, match="bad creds"):
            connector.connect()

        assert connector.get_status() == ConnectorStatus.ERROR

    @patch("appif.adapters.gmail.connector.call_with_retry", side_effect=lambda fn: fn())
    @patch("appif.adapters.gmail.connector.build_service")
    @patch("appif.adapters.gmail.connector.GmailPoller")
    def test_disconnect(self, mock_poller_cls, mock_build, mock_retry):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        mock_build.return_value = _make_mock_service()

        connector = GmailConnector(auth=auth, poll_interval=60)
        connector.connect()
        connector.disconnect()

        assert connector.get_status() == ConnectorStatus.DISCONNECTED
        mock_poller_cls.return_value.stop.assert_called_once()

    def test_disconnect_when_already_disconnected(self):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        connector = GmailConnector(auth=auth)
        connector.disconnect()  # Should not raise

        assert connector.get_status() == ConnectorStatus.DISCONNECTED

    @patch("appif.adapters.gmail.connector.call_with_retry", side_effect=lambda fn: fn())
    @patch("appif.adapters.gmail.connector.build_service")
    @patch("appif.adapters.gmail.connector.GmailPoller")
    def test_connect_already_connected_is_noop(self, mock_poller_cls, mock_build, mock_retry):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        mock_build.return_value = _make_mock_service()

        connector = GmailConnector(auth=auth, poll_interval=60)
        connector.connect()
        connector.connect()  # Second call should be noop

        # Validate only called once
        assert auth.validate.call_count == 1


class TestGmailConnectorDiscovery:
    """Tests for list_accounts and list_targets."""

    def test_list_accounts(self):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth(account="user@gmail.com")
        connector = GmailConnector(auth=auth)

        accounts = connector.list_accounts()
        assert len(accounts) == 1
        assert accounts[0].account_id == "user@gmail.com"
        assert accounts[0].connector == "gmail"

    @patch("appif.adapters.gmail.connector.call_with_retry", side_effect=lambda fn: fn())
    @patch("appif.adapters.gmail.connector.build_service")
    @patch("appif.adapters.gmail.connector.GmailPoller")
    def test_list_targets_returns_empty(self, mock_poller_cls, mock_build, mock_retry):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        mock_build.return_value = _make_mock_service()

        connector = GmailConnector(auth=auth, poll_interval=60)
        connector.connect()

        targets = connector.list_targets("user@gmail.com")
        assert targets == []


class TestGmailConnectorSend:
    """Tests for send in AUTOMATIC and ASSISTED modes."""

    @patch("appif.adapters.gmail.connector.call_with_retry", side_effect=lambda fn: fn())
    @patch("appif.adapters.gmail.connector.build_service")
    @patch("appif.adapters.gmail.connector.GmailPoller")
    @patch("appif.adapters.gmail.connector.build_message", return_value="base64encoded")
    def test_send_automatic_calls_messages_send(self, mock_builder, mock_poller_cls, mock_build, mock_retry):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        service = _make_mock_service()
        mock_build.return_value = service

        connector = GmailConnector(auth=auth, delivery_mode="AUTOMATIC", poll_interval=60)
        connector.connect()

        conv = ConversationRef(
            connector="gmail",
            account_id="user@gmail.com",
            type="email_thread",
            opaque_id={"thread_id": "t1", "to": "a@b.com"},
        )
        content = MessageContent(text="Hello")

        receipt = connector.send(conv, content)

        assert receipt.external_id == "sent_msg_1"
        service.users().messages().send.assert_called()

    @patch("appif.adapters.gmail.connector.call_with_retry", side_effect=lambda fn: fn())
    @patch("appif.adapters.gmail.connector.build_service")
    @patch("appif.adapters.gmail.connector.GmailPoller")
    @patch("appif.adapters.gmail.connector.build_message", return_value="base64encoded")
    def test_send_assisted_creates_draft(self, mock_builder, mock_poller_cls, mock_build, mock_retry):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        service = _make_mock_service()
        mock_build.return_value = service

        connector = GmailConnector(auth=auth, delivery_mode="ASSISTED", poll_interval=60)
        connector.connect()

        conv = ConversationRef(
            connector="gmail",
            account_id="user@gmail.com",
            type="email_thread",
            opaque_id={"to": "a@b.com"},
        )
        content = MessageContent(text="Draft message")

        receipt = connector.send(conv, content)

        assert receipt.external_id in ("draft_msg_1", "draft_1")
        service.users().drafts().create.assert_called()

    def test_send_when_not_connected_raises(self):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        connector = GmailConnector(auth=auth)

        conv = ConversationRef(connector="gmail", account_id="u", type="email_thread")
        content = MessageContent(text="x")

        with pytest.raises(NotSupported, match="not connected"):
            connector.send(conv, content)

    @patch("appif.adapters.gmail.connector.call_with_retry", side_effect=lambda fn: fn())
    @patch("appif.adapters.gmail.connector.build_service")
    @patch("appif.adapters.gmail.connector.GmailPoller")
    @patch("appif.adapters.gmail.connector.build_message", return_value="base64encoded")
    def test_send_reply_includes_thread_id(self, mock_builder, mock_poller_cls, mock_build, mock_retry):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        service = _make_mock_service()
        mock_build.return_value = service

        connector = GmailConnector(auth=auth, poll_interval=60)
        connector.connect()

        conv = ConversationRef(
            connector="gmail",
            account_id="user@gmail.com",
            type="email_thread",
            opaque_id={"thread_id": "thread_abc", "message_id": "msg_def", "to": "a@b.com"},
        )
        content = MessageContent(text="Reply")
        connector.send(conv, content)

        # Verify threadId was included in the API call body
        send_call = service.users().messages().send
        call_kwargs = send_call.call_args
        body = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body", {})
        assert body.get("threadId") == "thread_abc"


class TestGmailConnectorListeners:
    """Tests for listener registration and dispatch."""

    @patch("appif.adapters.gmail.connector.call_with_retry", side_effect=lambda fn: fn())
    @patch("appif.adapters.gmail.connector.build_service")
    @patch("appif.adapters.gmail.connector.GmailPoller")
    def test_register_and_unregister_listener(self, mock_poller_cls, mock_build, mock_retry):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        mock_build.return_value = _make_mock_service()

        connector = GmailConnector(auth=auth, poll_interval=60)
        connector.connect()

        listener = MagicMock()
        connector.register_listener(listener)
        assert listener in connector._listeners

        connector.unregister_listener(listener)
        assert listener not in connector._listeners

    @patch("appif.adapters.gmail.connector.call_with_retry", side_effect=lambda fn: fn())
    @patch("appif.adapters.gmail.connector.build_service")
    @patch("appif.adapters.gmail.connector.GmailPoller")
    def test_listener_error_does_not_crash(self, mock_poller_cls, mock_build, mock_retry):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        mock_build.return_value = _make_mock_service()

        connector = GmailConnector(auth=auth, poll_interval=60)
        connector.connect()

        bad_listener = MagicMock()
        bad_listener.on_message.side_effect = RuntimeError("listener exploded")
        good_listener = MagicMock()

        connector.register_listener(bad_listener)
        connector.register_listener(good_listener)

        # Simulate _safe_listener_call
        event = MagicMock()
        event.message_id = "test"
        GmailConnector._safe_listener_call(bad_listener, event)  # Should not raise
        GmailConnector._safe_listener_call(good_listener, event)

        good_listener.on_message.assert_called_once_with(event)


class TestGmailConnectorCapabilities:
    """Tests for get_capabilities."""

    def test_capabilities_automatic(self):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        connector = GmailConnector(auth=auth, delivery_mode="AUTOMATIC")

        caps = connector.get_capabilities()
        assert caps.supports_realtime is False
        assert caps.supports_backfill is True
        assert caps.supports_threads is True
        assert caps.supports_reply is True
        assert caps.supports_auto_send is True
        assert caps.delivery_mode == "AUTOMATIC"

    def test_capabilities_assisted(self):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        connector = GmailConnector(auth=auth, delivery_mode="ASSISTED")

        caps = connector.get_capabilities()
        assert caps.delivery_mode == "ASSISTED"


class TestGmailConnectorResolveAttachment:
    """Tests for resolve_attachment."""

    @patch("appif.adapters.gmail.connector.call_with_retry", side_effect=lambda fn: fn())
    @patch("appif.adapters.gmail.connector.build_service")
    @patch("appif.adapters.gmail.connector.GmailPoller")
    def test_resolve_attachment_returns_bytes(self, mock_poller_cls, mock_build, mock_retry):
        import base64

        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        service = _make_mock_service()
        encoded = base64.urlsafe_b64encode(b"file content").decode()
        service.users().messages().attachments().get.return_value.execute.return_value = {
            "data": encoded,
        }
        mock_build.return_value = service

        connector = GmailConnector(auth=auth, poll_interval=60)
        connector.connect()

        data = connector.resolve_attachment("msg1:att1")
        assert data == b"file content"

    @patch("appif.adapters.gmail.connector.call_with_retry", side_effect=lambda fn: fn())
    @patch("appif.adapters.gmail.connector.build_service")
    @patch("appif.adapters.gmail.connector.GmailPoller")
    def test_resolve_attachment_invalid_ref(self, mock_poller_cls, mock_build, mock_retry):
        from appif.adapters.gmail.connector import GmailConnector

        auth = _make_mock_auth()
        mock_build.return_value = _make_mock_service()

        connector = GmailConnector(auth=auth, poll_interval=60)
        connector.connect()

        with pytest.raises(ConnectorError, match="invalid content_ref"):
            connector.resolve_attachment("no_separator")
