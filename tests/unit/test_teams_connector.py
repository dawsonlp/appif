"""Unit tests for TeamsConnector configuration, scopes, and capabilities."""

from __future__ import annotations

import pytest

from appif.adapters.teams._auth import CHANNEL_SCOPES, CHAT_SCOPES, scopes_for
from appif.adapters.teams.connector import TeamsConnector
from appif.domain.messaging.errors import NotAuthorized, NotSupported
from appif.domain.messaging.models import BackfillScope, ConnectorStatus, ConversationRef, MessageContent


class TestConfig:
    def test_defaults(self):
        c = TeamsConnector(client_id="cid", tenant_id="tid")
        assert c._include_chats is True
        # Channels are opt-in: ChannelMessage.Read.All requires admin consent.
        assert c._include_channels is False
        assert c._include_sent is False
        assert c.get_status() == ConnectorStatus.DISCONNECTED

    def test_include_channels_opt_in_from_env(self, monkeypatch):
        monkeypatch.setenv("APPIF_TEAMS_INCLUDE_CHANNELS", "true")
        assert TeamsConnector(client_id="x")._include_channels is True

    def test_client_id_falls_back_to_outlook_env(self, monkeypatch):
        monkeypatch.delenv("APPIF_TEAMS_CLIENT_ID", raising=False)
        monkeypatch.setenv("APPIF_OUTLOOK_CLIENT_ID", "outlook-cid")
        c = TeamsConnector()
        assert c._client_id == "outlook-cid"

    def test_teams_client_id_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("APPIF_OUTLOOK_CLIENT_ID", "outlook-cid")
        monkeypatch.setenv("APPIF_TEAMS_CLIENT_ID", "teams-cid")
        c = TeamsConnector()
        assert c._client_id == "teams-cid"

    def test_include_sent_from_env(self, monkeypatch):
        monkeypatch.setenv("APPIF_TEAMS_INCLUDE_SENT", "true")
        assert TeamsConnector(client_id="x")._include_sent is True

    def test_include_channels_disabled_from_env(self, monkeypatch):
        monkeypatch.setenv("APPIF_TEAMS_INCLUDE_CHANNELS", "false")
        assert TeamsConnector(client_id="x")._include_channels is False

    def test_default_credentials_dir_is_teams_specific(self):
        from appif import config

        c = TeamsConnector(client_id="x")
        # Teams keeps its own subdir under the appif config dir, distinct from
        # the Outlook cache even though they share an Azure app.
        assert c._credentials_dir.name == "teams"
        assert c._credentials_dir == config.service_dir("teams")


class TestScopes:
    def test_chat_only(self):
        s = scopes_for(include_chats=True, include_channels=False)
        assert "https://graph.microsoft.com/Chat.Read" in s
        assert "https://graph.microsoft.com/ChannelMessage.Read.All" not in s

    def test_channels_add_admin_scope(self):
        s = scopes_for(include_chats=True, include_channels=True)
        for scope in CHAT_SCOPES + CHANNEL_SCOPES:
            assert scope in s

    def test_user_read_always_present(self):
        s = scopes_for(include_chats=False, include_channels=False)
        assert s == ["https://graph.microsoft.com/User.Read"]


class TestCapabilities:
    def test_capabilities(self):
        caps = TeamsConnector(client_id="x").get_capabilities()
        assert caps.supports_realtime is False
        assert caps.supports_backfill is True
        assert caps.supports_threads is True
        assert caps.supports_reply is True
        assert caps.delivery_mode == "AUTOMATIC"


class TestGuards:
    def test_connect_without_client_id_raises(self, monkeypatch):
        monkeypatch.delenv("APPIF_TEAMS_CLIENT_ID", raising=False)
        monkeypatch.delenv("APPIF_OUTLOOK_CLIENT_ID", raising=False)
        with pytest.raises(NotAuthorized, match="client_id"):
            TeamsConnector(client_id="").connect()

    def test_send_requires_connected(self):
        c = TeamsConnector(client_id="x")
        with pytest.raises(NotSupported):
            c.send(
                ConversationRef(connector="teams", account_id="a", type="chat", opaque_id={"chat_id": "C1"}),
                MessageContent(text="hi"),
            )

    def test_backfill_requires_connected(self):
        c = TeamsConnector(client_id="x")
        with pytest.raises(NotSupported):
            c.backfill("a", BackfillScope())

    def test_list_targets_requires_connected(self):
        c = TeamsConnector(client_id="x")
        with pytest.raises(NotSupported):
            c.list_targets("a")


class TestListeners:
    def test_register_and_unregister(self):
        c = TeamsConnector(client_id="x")
        listener = object()
        c.register_listener(listener)
        assert listener in c._listeners
        c.register_listener(listener)  # idempotent
        assert c._listeners.count(listener) == 1
        c.unregister_listener(listener)
        assert listener not in c._listeners

    def test_unregister_unknown_is_silent(self):
        c = TeamsConnector(client_id="x")
        c.unregister_listener(object())  # no error
