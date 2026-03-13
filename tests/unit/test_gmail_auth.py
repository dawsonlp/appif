"""Unit tests for the Gmail auth module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from appif.domain.messaging.errors import NotAuthorized


class TestFileCredentialAuth:
    """Tests for FileCredentialAuth credential loading and validation."""

    @staticmethod
    def _write_cred_file(cred_dir: Path, account: str, data: dict) -> Path:
        cred_dir.mkdir(parents=True, exist_ok=True)
        path = cred_dir / f"{account}.json"
        path.write_text(json.dumps(data))
        return path

    @staticmethod
    def _valid_cred_data() -> dict:
        return {
            "token": "ya29.access-token",
            "refresh_token": "1//refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test-client-id.apps.googleusercontent.com",
            "client_secret": "test-client-secret",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        }

    def test_validate_succeeds_with_valid_file(self, tmp_path):
        """Valid credential file → validate passes."""
        cred_dir = tmp_path / "gmail"
        self._write_cred_file(cred_dir, "user@gmail.com", self._valid_cred_data())

        from appif.adapters.gmail._auth import FileCredentialAuth

        auth = FileCredentialAuth("user@gmail.com", credentials_dir=cred_dir)
        auth.validate()  # Should not raise

    def test_validate_raises_when_account_missing(self, tmp_path, monkeypatch):
        """Missing account → NotAuthorized."""
        monkeypatch.delenv("APPIF_GMAIL_ACCOUNT", raising=False)

        from appif.adapters.gmail import _auth as auth_mod
        from appif.adapters.gmail._auth import FileCredentialAuth

        # Prevent _load_env from re-loading ~/.env which would re-set APPIF_GMAIL_ACCOUNT
        monkeypatch.setattr(auth_mod, "_load_env", lambda: None)

        auth = FileCredentialAuth("", credentials_dir=tmp_path)

        with pytest.raises(NotAuthorized, match="missing APPIF_GMAIL_ACCOUNT"):
            auth.validate()

    def test_validate_raises_when_file_missing(self, tmp_path):
        """Missing credential file → NotAuthorized."""
        from appif.adapters.gmail._auth import FileCredentialAuth

        auth = FileCredentialAuth("user@gmail.com", credentials_dir=tmp_path)

        with pytest.raises(NotAuthorized, match="credential file not found"):
            auth.validate()

    def test_validate_raises_on_invalid_json(self, tmp_path):
        """Malformed JSON → NotAuthorized."""
        cred_dir = tmp_path / "gmail"
        cred_dir.mkdir(parents=True, exist_ok=True)
        (cred_dir / "user@gmail.com.json").write_text("not json {{{")

        from appif.adapters.gmail._auth import FileCredentialAuth

        auth = FileCredentialAuth("user@gmail.com", credentials_dir=cred_dir)

        with pytest.raises(NotAuthorized, match="invalid credential file"):
            auth.validate()

    def test_validate_raises_on_missing_keys(self, tmp_path):
        """Missing required keys → NotAuthorized."""
        cred_dir = tmp_path / "gmail"
        self._write_cred_file(cred_dir, "user@gmail.com", {"token": "abc"})

        from appif.adapters.gmail._auth import FileCredentialAuth

        auth = FileCredentialAuth("user@gmail.com", credentials_dir=cred_dir)

        with pytest.raises(NotAuthorized, match="missing keys"):
            auth.validate()

    def test_credentials_property_returns_credentials_object(self, tmp_path):
        """credentials property returns a Credentials object with expected attrs."""
        cred_dir = tmp_path / "gmail"
        data = self._valid_cred_data()
        self._write_cred_file(cred_dir, "user@gmail.com", data)

        from appif.adapters.gmail._auth import FileCredentialAuth

        auth = FileCredentialAuth("user@gmail.com", credentials_dir=cred_dir)
        creds = auth.credentials

        assert creds.token == "ya29.access-token"
        assert creds.refresh_token == "1//refresh-token"
        assert creds.client_id == "test-client-id.apps.googleusercontent.com"
        assert creds.client_secret == "test-client-secret"

    def test_credentials_lazy_loads_once(self, tmp_path):
        """credentials property lazily loads from file on first access."""
        cred_dir = tmp_path / "gmail"
        self._write_cred_file(cred_dir, "user@gmail.com", self._valid_cred_data())

        from appif.adapters.gmail._auth import FileCredentialAuth

        auth = FileCredentialAuth("user@gmail.com", credentials_dir=cred_dir)

        # First access should load
        creds1 = auth.credentials
        # Second access returns same object
        creds2 = auth.credentials
        assert creds1 is creds2

    def test_credentials_uses_env_fallback_for_client_id(self, tmp_path, monkeypatch):
        """Client ID from env when not in file."""
        cred_dir = tmp_path / "gmail"
        data = self._valid_cred_data()
        del data["client_id"]
        self._write_cred_file(cred_dir, "user@gmail.com", data)

        monkeypatch.setenv("APPIF_GMAIL_CLIENT_ID", "env-client-id")

        from appif.adapters.gmail._auth import FileCredentialAuth

        auth = FileCredentialAuth("user@gmail.com", credentials_dir=cred_dir)
        creds = auth.credentials

        assert creds.client_id == "env-client-id"

    def test_save_credentials_writes_atomically(self, tmp_path):
        """save_credentials writes updated token data to file."""
        cred_dir = tmp_path / "gmail"
        self._write_cred_file(cred_dir, "user@gmail.com", self._valid_cred_data())

        from appif.adapters.gmail._auth import FileCredentialAuth

        auth = FileCredentialAuth("user@gmail.com", credentials_dir=cred_dir)
        # Load credentials first
        creds = auth.credentials
        # Simulate token refresh by modifying the token
        creds.token = "ya29.refreshed-token"

        auth.save_credentials()

        # Verify file was updated
        saved = json.loads((cred_dir / "user@gmail.com.json").read_text())
        assert saved["token"] == "ya29.refreshed-token"
        assert saved["refresh_token"] == "1//refresh-token"

    def test_save_credentials_noop_when_no_credentials_loaded(self, tmp_path):
        """save_credentials does nothing if credentials haven't been loaded."""
        from appif.adapters.gmail._auth import FileCredentialAuth

        auth = FileCredentialAuth("user@gmail.com", credentials_dir=tmp_path)
        auth.save_credentials()  # Should not raise

    def test_account_property(self, tmp_path):
        """account property returns the configured account."""
        from appif.adapters.gmail._auth import FileCredentialAuth

        auth = FileCredentialAuth("user@gmail.com", credentials_dir=tmp_path)
        assert auth.account == "user@gmail.com"

    def test_account_from_env(self, tmp_path, monkeypatch):
        """Account read from APPIF_GMAIL_ACCOUNT env var."""
        monkeypatch.setenv("APPIF_GMAIL_ACCOUNT", "env@gmail.com")

        from appif.adapters.gmail._auth import FileCredentialAuth

        auth = FileCredentialAuth(credentials_dir=tmp_path)
        assert auth.account == "env@gmail.com"
