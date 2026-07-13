"""Outlook authentication — MSAL token cache + Graph SDK credential bridge.

The MSAL token-cache machinery lives in
:class:`appif.adapters._graph.msal.MsalTokenCacheAuth`; this module adds the
Outlook-specific scopes, the azure-core ``AccessToken`` return type, and the
``TokenCredential`` bridge expected by the Microsoft Graph SDK.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol

from azure.core.credentials import AccessToken, TokenCredential

from appif.adapters._graph.msal import MsalTokenCacheAuth

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


class MsalAuth(MsalTokenCacheAuth):
    """Outlook auth backed by a persisted MSAL token cache."""

    connector_name = "outlook"
    default_credentials_dir = Path.home() / ".config" / "appif" / "outlook"
    default_scopes = _DEFAULT_SCOPES
    consent_script = "scripts/outlook_consent.py"

    def acquire(self) -> AccessToken:
        """Acquire or refresh an access token as an azure-core ``AccessToken``."""
        result = self._acquire_result()
        # MSAL returns expires_in (seconds from now); convert to epoch.
        expires_epoch = int(time.time()) + int(result.get("expires_in", 3600))
        return AccessToken(result["access_token"], expires_epoch)

    def credential(self) -> TokenCredential:
        """Return a ``TokenCredential`` wrapping this auth for the Graph SDK."""
        return MsalTokenCredential(self)


class MsalTokenCredential:
    """Bridges ``MsalAuth`` to the ``TokenCredential`` protocol.

    The Microsoft Graph SDK expects a ``TokenCredential`` with a
    ``get_token(*scopes, **kwargs) -> AccessToken`` method. This thin wrapper
    delegates to ``MsalAuth.acquire()``.
    """

    def __init__(self, auth: MsalAuth) -> None:
        self._auth = auth

    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        """Return a valid access token, refreshing if necessary."""
        return self._auth.acquire()
