"""Gmail authentication — OAuth 2.0 file-based credentials.

Provides the ``GmailAuth`` protocol and the default ``FileCredentialAuth``
implementation that loads per-account credential JSON files produced by
the consent script (``scripts/gmail_consent.py``).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Protocol

from appif.domain.messaging.errors import NotAuthorized

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "gmail"

_DEFAULT_CREDENTIALS_DIR = Path.home() / ".config" / "appif" / "gmail"

_REQUIRED_KEYS = {"refresh_token", "client_id", "client_secret"}

# Gmail OAuth scopes
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]


def _load_env() -> None:
    """Best-effort load of ~/.env via python-dotenv."""
    try:
        from dotenv import load_dotenv

        env_path = Path.home() / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass


class GmailAuth(Protocol):
    """Contract for Gmail auth providers."""

    @property
    def credentials(self):
        """Return a ``google.oauth2.credentials.Credentials`` object."""
        ...

    @property
    def account(self) -> str:
        """Return the mailbox address string."""
        ...

    def validate(self) -> None:
        """Check credentials are present. Raises ``NotAuthorized`` on failure."""
        ...


class FileCredentialAuth:
    """Default auth backed by per-account credential JSON files.

    Reads ``{credentials_dir}/{account}.json`` produced by the consent
    script. Token refresh is handled by ``google-auth`` automatically
    when credentials are used with an authorised transport. After refresh,
    updated tokens are persisted back to the file.

    Parameters
    ----------
    account:
        Gmail address (e.g. ``user@gmail.com``). If not provided, read
        from ``APPIF_GMAIL_ACCOUNT`` environment variable.
    credentials_dir:
        Directory containing per-account JSON files. Defaults to
        ``APPIF_GMAIL_CREDENTIALS_DIR`` env var or ``~/.config/appif/gmail``.
    """

    def __init__(
        self,
        account: str | None = None,
        *,
        credentials_dir: Path | str | None = None,
    ) -> None:
        _load_env()

        self._account = account or os.environ.get("APPIF_GMAIL_ACCOUNT", "")
        self._credentials_dir = Path(
            credentials_dir or os.environ.get("APPIF_GMAIL_CREDENTIALS_DIR", str(_DEFAULT_CREDENTIALS_DIR))
        )
        self._creds = None

    @property
    def account(self) -> str:
        return self._account

    @property
    def credentials(self):
        """Return a live ``google.oauth2.credentials.Credentials`` object.

        Lazily loads from the JSON file on first access.
        """
        if self._creds is None:
            self._load_credentials()
        return self._creds

    def validate(self) -> None:
        """Check that credentials are present and structurally valid.

        Does NOT make any API calls — that happens at ``connect()`` time.

        Raises
        ------
        NotAuthorized
            If account env var is missing, credential file is absent,
            or file is malformed / missing required keys.
        """
        if not self._account:
            raise NotAuthorized(_CONNECTOR_NAME, reason="missing APPIF_GMAIL_ACCOUNT")

        path = self._credential_path()
        if not path.exists():
            raise NotAuthorized(_CONNECTOR_NAME, reason=f"credential file not found: {path}")

        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise NotAuthorized(_CONNECTOR_NAME, reason=f"invalid credential file: {path} ({exc})") from exc

        missing = _REQUIRED_KEYS - set(data.keys())
        if missing:
            raise NotAuthorized(
                _CONNECTOR_NAME,
                reason=f"invalid credential file: {path} (missing keys: {', '.join(sorted(missing))})",
            )

    def save_credentials(self) -> None:
        """Persist current credentials back to the JSON file.

        Called after a token refresh so the new access token (and
        potentially rotated refresh token) are preserved.
        """
        if self._creds is None:
            return

        path = self._credential_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "token": self._creds.token,
            "refresh_token": self._creds.refresh_token,
            "token_uri": self._creds.token_uri,
            "client_id": self._creds.client_id,
            "client_secret": self._creds.client_secret,
            "scopes": list(self._creds.scopes) if self._creds.scopes else GMAIL_SCOPES,
        }

        # Atomic write: temp file + rename
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.rename(path)
        logger.debug("gmail.credentials_saved", extra={"path": str(path)})

    # ── Internal ──────────────────────────────────────────────

    def _credential_path(self) -> Path:
        return self._credentials_dir / f"{self._account}.json"

    def _load_credentials(self) -> None:
        """Load credentials from the JSON file using google-auth."""
        from google.oauth2.credentials import Credentials

        path = self._credential_path()

        # Read raw JSON to supplement client_id/secret from env if needed
        data = json.loads(path.read_text())

        client_id = data.get("client_id") or os.environ.get("APPIF_GMAIL_CLIENT_ID", "")
        client_secret = data.get("client_secret") or os.environ.get("APPIF_GMAIL_CLIENT_SECRET", "")

        self._creds = Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=client_id,
            client_secret=client_secret,
            scopes=data.get("scopes", GMAIL_SCOPES),
        )

        logger.debug("gmail.credentials_loaded", extra={"path": str(path), "account": self._account})
