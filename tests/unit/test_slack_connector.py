"""Unit tests for the Slack connector and auth (no network, no SDK)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from appif.adapters.slack._auth import StaticTokenAuth, _classify_token
from appif.adapters.slack.connector import SlackConnector
from appif.domain.messaging.errors import NotAuthorized
from appif.domain.messaging.models import ConnectorStatus, MessageEvent

# ---------------------------------------------------------------------------
# Token classification (pure function)
# ---------------------------------------------------------------------------


class TestTokenClassification:
    """_classify_token must return 'bot' or 'user' based on prefix."""

    def test_bot_token_prefix(self):
        assert _classify_token("xoxb-test-token") == "bot"

    def test_user_token_prefix(self):
        assert _classify_token("xoxp-test-token") == "user"

    def test_unrecognized_prefix_raises(self):
        with pytest.raises(ValueError, match="Unrecognized Slack token prefix"):
            _classify_token("xoxr-bad-token")


# ---------------------------------------------------------------------------
# Auth protocol — StaticTokenAuth
# ---------------------------------------------------------------------------


class TestAuthIdentityType:
    """StaticTokenAuth.identity_type must reflect the token prefix."""

    def test_bot_token_identity_type(self):
        auth = StaticTokenAuth(identity_token="xoxb-test")
        assert auth.identity_type == "bot"

    def test_user_token_identity_type(self):
        auth = StaticTokenAuth(identity_token="xoxp-test")
        assert auth.identity_type == "user"

    def test_unrecognized_prefix_raises_on_identity_type(self):
        auth = StaticTokenAuth(identity_token="xoxr-test")
        with pytest.raises(ValueError, match="Unrecognized Slack token prefix"):
            _ = auth.identity_type


class TestAuthValidation:
    """validate() must reject missing or invalid identity tokens."""

    def test_missing_identity_token_raises(self):
        auth = StaticTokenAuth(identity_token="")
        with pytest.raises(NotAuthorized, match="APPIF_SLACK_IDENTITY_TOKEN"):
            auth.validate()

    def test_unrecognized_prefix_raises_on_validate(self):
        auth = StaticTokenAuth(identity_token="xoxr-bad")
        with pytest.raises(ValueError, match="Unrecognized Slack token prefix"):
            auth.validate()

    def test_missing_app_token_not_error(self):
        auth = StaticTokenAuth(identity_token="xoxb-test", app_token=None)
        auth.validate()  # must not raise

    def test_valid_bot_token_accepted(self):
        auth = StaticTokenAuth(identity_token="xoxb-valid")
        auth.validate()  # must not raise

    def test_valid_user_token_accepted(self):
        auth = StaticTokenAuth(identity_token="xoxp-valid")
        auth.validate()  # must not raise


class TestAuthProperties:
    """Property accessors on StaticTokenAuth."""

    def test_identity_token_property(self):
        auth = StaticTokenAuth(identity_token="xoxb-tok", app_token="xapp-app")
        assert auth.identity_token == "xoxb-tok"

    def test_app_token_property(self):
        auth = StaticTokenAuth(identity_token="xoxb-tok", app_token="xapp-app")
        assert auth.app_token == "xapp-app"

    def test_app_token_none_by_default(self):
        auth = StaticTokenAuth(identity_token="xoxb-tok")
        assert auth.app_token is None


class TestAuthFromEnv:
    """from_env() must read the correct environment variables."""

    def test_from_env_reads_correct_vars(self):
        env = {
            "APPIF_SLACK_IDENTITY_TOKEN": "xoxb-from-env",
            "APPIF_SLACK_APP_TOKEN": "xapp-from-env",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "appif.adapters.slack._auth.load_dotenv"
        ):
            auth = StaticTokenAuth.from_env()

        assert auth.identity_token == "xoxb-from-env"
        assert auth.app_token == "xapp-from-env"

    def test_from_env_missing_app_token_gives_none(self):
        env = {
            "APPIF_SLACK_IDENTITY_TOKEN": "xoxb-from-env",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "appif.adapters.slack._auth.load_dotenv"
        ):
            auth = StaticTokenAuth.from_env()

        assert auth.identity_token == "xoxb-from-env"
        assert auth.app_token is None

    def test_from_env_empty_app_token_gives_none(self):
        env = {
            "APPIF_SLACK_IDENTITY_TOKEN": "xoxb-from-env",
            "APPIF_SLACK_APP_TOKEN": "",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "appif.adapters.slack._auth.load_dotenv"
        ):
            auth = StaticTokenAuth.from_env()

        assert auth.app_token is None


# ---------------------------------------------------------------------------
# Connector — credential validation at construction
# ---------------------------------------------------------------------------


class TestCredentialValidation:
    """SlackConnector must reject invalid credentials at construction time."""

    def test_missing_identity_token_raises(self):
        with pytest.raises(NotAuthorized, match="APPIF_SLACK_IDENTITY_TOKEN"):
            SlackConnector(identity_token="", app_token="xapp-valid")

    def test_unrecognized_prefix_raises(self):
        with pytest.raises(ValueError, match="Unrecognized Slack token prefix"):
            SlackConnector(identity_token="xoxr-bad", app_token="xapp-valid")

    def test_valid_bot_token_accepted(self):
        conn = SlackConnector(identity_token="xoxb-valid", app_token="xapp-valid")
        assert conn.get_status() == ConnectorStatus.DISCONNECTED

    def test_valid_user_token_accepted(self):
        conn = SlackConnector(identity_token="xoxp-valid", app_token="xapp-valid")
        assert conn.get_status() == ConnectorStatus.DISCONNECTED

    def test_missing_app_token_accepted(self):
        conn = SlackConnector(identity_token="xoxb-valid")
        assert conn.get_status() == ConnectorStatus.DISCONNECTED


# ---------------------------------------------------------------------------
# Capabilities (offline — no connection required)
# ---------------------------------------------------------------------------


class TestCapabilities:
    """Capability flags must be accurate before any connection."""

    def test_bot_with_app_token_capabilities(self):
        conn = SlackConnector(identity_token="xoxb-valid", app_token="xapp-valid")
        caps = conn.get_capabilities()

        assert caps.supports_realtime is True
        assert caps.supports_backfill is True
        assert caps.supports_threads is True
        assert caps.supports_reply is True
        assert caps.supports_auto_send is True
        assert caps.delivery_mode == "AUTOMATIC"

    def test_bot_without_app_token_capabilities(self):
        conn = SlackConnector(identity_token="xoxb-valid")
        caps = conn.get_capabilities()

        assert caps.supports_realtime is False
        assert caps.supports_backfill is True
        assert caps.supports_threads is True
        assert caps.supports_reply is True
        assert caps.supports_auto_send is True
        assert caps.delivery_mode == "MANUAL"

    def test_user_with_app_token_capabilities(self):
        conn = SlackConnector(identity_token="xoxp-valid", app_token="xapp-valid")
        caps = conn.get_capabilities()

        assert caps.supports_realtime is True
        assert caps.delivery_mode == "AUTOMATIC"

    def test_user_without_app_token_capabilities(self):
        conn = SlackConnector(identity_token="xoxp-valid")
        caps = conn.get_capabilities()

        assert caps.supports_realtime is False
        assert caps.delivery_mode == "MANUAL"

    def test_capabilities_before_connect(self):
        """Capabilities must be queryable without calling connect()."""
        conn = SlackConnector(identity_token="xoxb-valid", app_token="xapp-valid")
        # No connect() call — this must work
        caps = conn.get_capabilities()
        assert caps.supports_realtime is True


# ---------------------------------------------------------------------------
# Lifecycle / status
# ---------------------------------------------------------------------------


class TestLifecycleStatus:
    """Status transitions without actually connecting."""

    def test_initial_status_is_disconnected(self):
        conn = SlackConnector(identity_token="xoxb-valid", app_token="xapp-valid")
        assert conn.get_status() == ConnectorStatus.DISCONNECTED


# ---------------------------------------------------------------------------
# Listener management
# ---------------------------------------------------------------------------


class TestListenerManagement:
    """Registering / unregistering listeners."""

    def test_register_and_unregister_listener(self):
        conn = SlackConnector(identity_token="xoxb-valid", app_token="xapp-valid")

        class _Dummy:
            def on_message(self, event: MessageEvent) -> None:
                pass

        listener = _Dummy()
        conn.register_listener(listener)
        assert listener in conn._listeners

        conn.unregister_listener(listener)
        assert listener not in conn._listeners

    def test_duplicate_register_ignored(self):
        conn = SlackConnector(identity_token="xoxb-valid", app_token="xapp-valid")

        class _Dummy:
            def on_message(self, event: MessageEvent) -> None:
                pass

        listener = _Dummy()
        conn.register_listener(listener)
        conn.register_listener(listener)
        assert conn._listeners.count(listener) == 1

    def test_unregister_nonexistent_no_error(self):
        conn = SlackConnector(identity_token="xoxb-valid", app_token="xapp-valid")

        class _Dummy:
            def on_message(self, event: MessageEvent) -> None:
                pass

        listener = _Dummy()
        conn.unregister_listener(listener)  # must not raise
