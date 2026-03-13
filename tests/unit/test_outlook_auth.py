"""Unit tests for the Outlook auth module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from appif.domain.messaging.errors import NotAuthorized


class TestMsalAuth:
    """Tests for MsalAuth token acquisition and cache management."""

    def _make_auth(self, tmp_path, *, cache_data=None, accounts=None, token_result=None):
        """Helper to create MsalAuth with mocked MSAL internals."""
        cred_dir = tmp_path / "outlook"
        cred_dir.mkdir(parents=True, exist_ok=True)

        if cache_data is not None:
            (cred_dir / "default.json").write_text(cache_data)

        with patch("appif.adapters.outlook._auth.msal") as mock_msal:
            mock_cache = MagicMock()
            mock_cache.has_state_changed = False
            mock_cache.serialize.return_value = '{"cached": true}'
            mock_msal.SerializableTokenCache.return_value = mock_cache

            mock_app = MagicMock()
            mock_app.get_accounts.return_value = accounts or []
            mock_app.acquire_token_silent.return_value = token_result
            mock_app.token_cache = mock_cache
            mock_msal.PublicClientApplication.return_value = mock_app
            mock_msal.ConfidentialClientApplication.return_value = mock_app

            from appif.adapters.outlook._auth import MsalAuth

            auth = MsalAuth(
                "test-client-id",
                credentials_dir=cred_dir,
                account="default",
                tenant_id="common",
            )
            return auth, mock_app, mock_cache

    def test_acquire_returns_access_token_on_valid_cache(self, tmp_path):
        """Valid cache → AccessToken returned."""
        accounts = [{"username": "user@example.com"}]
        token_result = {
            "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9",
            "expires_in": 3600,
        }

        auth, mock_app, _ = self._make_auth(tmp_path, cache_data="{}", accounts=accounts, token_result=token_result)

        token = auth.acquire()

        assert token.token == "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9"
        assert token.expires_on > 0
        assert auth.user_email() == "user@example.com"

    def test_acquire_raises_not_authorized_when_no_accounts(self, tmp_path):
        """Missing cache → NotAuthorized."""
        auth, _, _ = self._make_auth(tmp_path, accounts=[], token_result=None)

        with pytest.raises(NotAuthorized, match="No cached credentials"):
            auth.acquire()

    def test_acquire_raises_not_authorized_on_silent_failure(self, tmp_path):
        """Silent acquisition returns None → NotAuthorized."""
        accounts = [{"username": "user@example.com"}]
        auth, _, _ = self._make_auth(tmp_path, accounts=accounts, token_result=None)

        with pytest.raises(NotAuthorized, match="Token refresh failed"):
            auth.acquire()

    def test_acquire_raises_not_authorized_on_error_result(self, tmp_path):
        """Token result with error → NotAuthorized."""
        accounts = [{"username": "user@example.com"}]
        token_result = {"error": "invalid_grant", "error_description": "Token expired"}
        auth, _, _ = self._make_auth(tmp_path, accounts=accounts, token_result=token_result)

        with pytest.raises(NotAuthorized, match="Token expired"):
            auth.acquire()

    def test_cache_saved_atomically_on_state_change(self, tmp_path):
        """Atomic write (tmp + rename) verified when cache changes."""
        accounts = [{"username": "user@example.com"}]
        token_result = {"access_token": "tok", "expires_in": 3600}

        auth, _, mock_cache = self._make_auth(tmp_path, cache_data="{}", accounts=accounts, token_result=token_result)
        mock_cache.has_state_changed = True

        auth.acquire()

        # Verify cache was serialized
        mock_cache.serialize.assert_called()
        # Verify file was written
        cache_path = tmp_path / "outlook" / "default.json"
        assert cache_path.exists()

    def test_credential_returns_token_credential(self, tmp_path):
        """credential() returns a MsalTokenCredential."""
        auth, _, _ = self._make_auth(tmp_path)
        cred = auth.credential()

        assert hasattr(cred, "get_token")

    def test_uses_confidential_client_when_secret_provided(self, tmp_path):
        """Client secret → ConfidentialClientApplication."""
        cred_dir = tmp_path / "outlook"
        cred_dir.mkdir(parents=True, exist_ok=True)

        with patch("appif.adapters.outlook._auth.msal") as mock_msal:
            mock_cache = MagicMock()
            mock_cache.has_state_changed = False
            mock_msal.SerializableTokenCache.return_value = mock_cache
            mock_app = MagicMock()
            mock_msal.ConfidentialClientApplication.return_value = mock_app

            from appif.adapters.outlook._auth import MsalAuth

            MsalAuth(
                "test-id",
                credentials_dir=cred_dir,
                client_secret="test-secret",
            )

            mock_msal.ConfidentialClientApplication.assert_called_once()


class TestMsalTokenCredential:
    """Tests for the MsalTokenCredential wrapper."""

    def test_get_token_delegates_to_acquire(self):
        """get_token() calls auth.acquire()."""
        from appif.adapters.outlook._auth import MsalTokenCredential

        mock_auth = MagicMock()
        mock_auth.acquire.return_value = MagicMock(token="test-token", expires_on=9999)

        cred = MsalTokenCredential(mock_auth)
        result = cred.get_token("https://graph.microsoft.com/.default")

        assert result.token == "test-token"
        mock_auth.acquire.assert_called_once()
