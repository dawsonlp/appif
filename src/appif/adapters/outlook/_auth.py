"""Outlook authentication — MSAL token cache + Graph SDK credential bridge.

Provides the ``OutlookAuth`` protocol and the default ``MsalAuth``
implementation that loads a persisted MSAL ``SerializableTokenCache``
and bridges it to the ``TokenCredential`` interface expected by the
Microsoft Graph SDK.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import msal
from azure.core.credentials import AccessToken, TokenCredential

from appif.domain.messaging.errors import NotAuthorized

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "outlook"

# Default Graph scopes for mail operations
_DEFAULT_SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/User.Read",
]


class OutlookAuth(Protocol):
    """Provides a live TokenCredential for the Graph SDK."""

    def credential(self) -> TokenCredential: ...

    def account_id(self) -> str: ...

    def user_email(self) -> str: ...


class MsalAuth:
    """Default auth backed by a persisted MSAL token cache.

    Reads ``<credentials_dir>/<account>.json``, deserialises the
    ``SerializableTokenCache``, and uses ``acquire_token_silent`` to
    obtain fresh access tokens.

    Parameters
    ----------
    client_id:
        Azure AD application (client) ID.
    credentials_dir:
        Directory containing per-account token cache JSON files.
    account:
        Logical account label (filename stem).
    tenant_id:
        Azure AD tenant. ``"common"`` supports both personal and org.
    client_secret:
        Optional client secret for confidential-client flow.
    scopes:
        Graph API scopes to request.
    """

    def __init__(
        self,
        client_id: str,
        *,
        credentials_dir: Path = Path.home() / ".config" / "appif" / "outlook",
        account: str = "default",
        tenant_id: str = "common",
        client_secret: str | None = None,
        scopes: list[str] | None = None,
    ) -> None:
        self._client_id = client_id
        self._credentials_dir = Path(credentials_dir)
        self._account = account
        self._tenant_id = tenant_id
        self._client_secret = client_secret
        self._scopes = scopes or _DEFAULT_SCOPES

        self._cache = msal.SerializableTokenCache()
        self._app: msal.ClientApplication | None = None
        self._user_email: str = ""

        self._load_cache()
        self._build_app()

    # ── Public interface ──────────────────────────────────────

    def credential(self) -> TokenCredential:
        """Return a ``TokenCredential`` wrapping this auth for the Graph SDK."""
        return MsalTokenCredential(self)

    def account_id(self) -> str:
        return self._account

    def user_email(self) -> str:
        return self._user_email

    # ── Token acquisition (called by MsalTokenCredential) ─────

    def acquire(self) -> AccessToken:
        """Acquire or refresh an access token.

        Returns
        -------
        AccessToken
            With ``.token`` and ``.expires_on`` fields.

        Raises
        ------
        NotAuthorized
            If no cached account or silent acquisition fails.
        """
        assert self._app is not None

        accounts = self._app.get_accounts()
        if not accounts:
            raise NotAuthorized(
                _CONNECTOR_NAME,
                reason=f"No cached credentials for account '{self._account}'. Run: python scripts/outlook_consent.py",
            )

        # Use the first account in the cache
        chosen = accounts[0]
        self._user_email = chosen.get("username", "")

        result = self._app.acquire_token_silent(
            scopes=self._scopes,
            account=chosen,
        )

        if not result:
            raise NotAuthorized(
                _CONNECTOR_NAME,
                reason=f"Token refresh failed for account '{self._account}'. Re-run: python scripts/outlook_consent.py",
            )

        if "error" in result:
            raise NotAuthorized(
                _CONNECTOR_NAME,
                reason=f"Token acquisition error: {result.get('error_description', result.get('error'))}",
            )

        # Persist updated cache atomically
        self._save_cache()

        token = result["access_token"]
        expires_on = result.get("expires_in", 3600)
        # MSAL returns expires_in (seconds from now); convert to epoch
        import time

        expires_epoch = int(time.time()) + int(expires_on)

        logger.debug("outlook.token_acquired", extra={"account": self._account, "email": self._user_email})
        return AccessToken(token, expires_epoch)

    # ── Internal ──────────────────────────────────────────────

    def _cache_path(self) -> Path:
        return self._credentials_dir / f"{self._account}.json"

    def _load_cache(self) -> None:
        """Load the MSAL serialized token cache from disk."""
        path = self._cache_path()
        if path.exists():
            data = path.read_text()
            self._cache.deserialize(data)
            logger.debug("outlook.cache_loaded", extra={"path": str(path)})

    def _save_cache(self) -> None:
        """Atomically persist the MSAL token cache if it changed."""
        if not self._cache.has_state_changed:
            return
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self._cache.serialize())
        tmp.rename(path)
        self._cache.has_state_changed = False
        logger.debug("outlook.cache_saved", extra={"path": str(path)})

    def _build_app(self) -> None:
        """Construct the MSAL application with the loaded cache."""
        authority = f"https://login.microsoftonline.com/{self._tenant_id}"
        if self._client_secret:
            self._app = msal.ConfidentialClientApplication(
                self._client_id,
                authority=authority,
                client_credential=self._client_secret,
                token_cache=self._cache,
            )
        else:
            self._app = msal.PublicClientApplication(
                self._client_id,
                authority=authority,
                token_cache=self._cache,
            )


class MsalTokenCredential:
    """Bridges ``MsalAuth`` to the ``TokenCredential`` protocol.

    The Microsoft Graph SDK expects a ``TokenCredential`` with a
    ``get_token(*scopes, **kwargs) -> AccessToken`` method. This thin
    wrapper delegates to ``MsalAuth.acquire()``.
    """

    def __init__(self, auth: MsalAuth) -> None:
        self._auth = auth

    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        """Return a valid access token, refreshing if necessary."""
        return self._auth.acquire()
