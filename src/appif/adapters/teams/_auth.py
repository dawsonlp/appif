"""Teams authentication — MSAL token cache + Graph access tokens.

Mirrors the Outlook ``MsalAuth`` but requests Teams scopes and uses a
**separate** token cache directory (``~/.config/appif/teams``) so Teams and
mail consents stay independent even though they may share one Azure app
registration.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import msal

from appif.domain.messaging.errors import NotAuthorized

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "teams"

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


class MsalAuth:
    """Auth backed by a persisted MSAL token cache.

    Reads ``<credentials_dir>/<account>.json``, deserialises the
    ``SerializableTokenCache``, and uses ``acquire_token_silent`` to obtain
    fresh access tokens.
    """

    def __init__(
        self,
        client_id: str,
        *,
        credentials_dir: Path = Path.home() / ".config" / "appif" / "teams",
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

    def account_id(self) -> str:
        return self._account

    def user_email(self) -> str:
        return self._user_email

    def acquire(self) -> str:
        """Acquire or refresh an access token, returning the token string."""
        assert self._app is not None

        accounts = self._app.get_accounts()
        if not accounts:
            raise NotAuthorized(
                _CONNECTOR_NAME,
                reason=f"No cached credentials for account '{self._account}'. Run: python scripts/teams_consent.py",
            )

        chosen = accounts[0]
        self._user_email = chosen.get("username", "")

        result = self._app.acquire_token_silent(scopes=self._scopes, account=chosen)

        if not result:
            raise NotAuthorized(
                _CONNECTOR_NAME,
                reason=f"Token refresh failed for account '{self._account}'. Re-run: python scripts/teams_consent.py",
            )
        if "error" in result:
            raise NotAuthorized(
                _CONNECTOR_NAME,
                reason=f"Token acquisition error: {result.get('error_description', result.get('error'))}",
            )

        self._save_cache()
        logger.debug("teams.token_acquired", extra={"account": self._account, "email": self._user_email})
        return result["access_token"]

    # ── Internal ──────────────────────────────────────────────

    def _cache_path(self) -> Path:
        return self._credentials_dir / f"{self._account}.json"

    def _load_cache(self) -> None:
        path = self._cache_path()
        if path.exists():
            self._cache.deserialize(path.read_text())
            logger.debug("teams.cache_loaded", extra={"path": str(path)})

    def _save_cache(self) -> None:
        if not self._cache.has_state_changed:
            return
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self._cache.serialize())
        tmp.rename(path)
        self._cache.has_state_changed = False
        logger.debug("teams.cache_saved", extra={"path": str(path)})

    def _build_app(self) -> None:
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
