"""Teams authentication — MSAL token cache + Graph access tokens.

Reuses the shared :class:`appif.adapters._graph.msal.MsalTokenCacheAuth` but
requests Teams scopes and uses a **separate** token cache directory
(``~/.config/appif/teams``) so Teams and mail consents stay independent even
when they share one Azure app registration. ``acquire()`` returns the raw
access-token string (the Teams connector talks to Graph over httpx directly).
"""

from __future__ import annotations

from typing import Protocol

from appif import config
from appif.adapters._graph.msal import MsalTokenCacheAuth

# Graph scopes grouped by capability. Chat scopes need no admin consent;
# the channel scopes (notably ChannelMessage.Read.All) DO require Azure AD
# admin consent. The connector requests only the groups it is configured to
# use so that silent token acquisition matches what was actually consented.
CHAT_SCOPES = [
    "https://graph.microsoft.com/Chat.Read",
    "https://graph.microsoft.com/ChatMessage.Send",
    "https://graph.microsoft.com/User.Read",
]
CHANNEL_SCOPES = [
    "https://graph.microsoft.com/ChannelMessage.Read.All",
    "https://graph.microsoft.com/Team.ReadBasic.All",
    "https://graph.microsoft.com/Channel.ReadBasic.All",
    "https://graph.microsoft.com/ChannelMessage.Send",
]
_DEFAULT_SCOPES = CHAT_SCOPES + CHANNEL_SCOPES


def scopes_for(*, include_chats: bool = True, include_channels: bool = True) -> list[str]:
    """Return the Graph scope list for the enabled source kinds."""
    scopes: list[str] = []
    if include_chats:
        scopes += CHAT_SCOPES
    if include_channels:
        scopes += CHANNEL_SCOPES
    # User.Read is always useful (identity resolution); ensure present.
    user_read = "https://graph.microsoft.com/User.Read"
    if user_read not in scopes:
        scopes.append(user_read)
    return scopes


class TeamsAuth(Protocol):
    """Provides Graph access tokens for the Teams connector."""

    def acquire(self) -> str: ...

    def account_id(self) -> str: ...

    def user_email(self) -> str: ...


class MsalAuth(MsalTokenCacheAuth):
    """Teams auth backed by a persisted MSAL token cache."""

    connector_name = "teams"
    default_credentials_dir = config.service_dir("teams")
    default_scopes = _DEFAULT_SCOPES
    consent_script = "scripts/teams_consent.py"

    def acquire(self) -> str:
        """Acquire or refresh an access token, returning the token string."""
        return self._acquire_result()["access_token"]
