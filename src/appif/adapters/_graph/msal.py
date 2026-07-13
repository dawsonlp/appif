"""Shared MSAL token-cache auth base for the Outlook and Teams adapters.

Both adapters authenticate the same way: load a persisted MSAL
``SerializableTokenCache`` from ``<credentials_dir>/<account>.json`` and use
``acquire_token_silent`` to obtain fresh access tokens. They differ only in the
connector name, default cache directory, default scopes, consent-script name,
and what ``acquire()`` returns (Outlook wraps the token in an azure-core
``AccessToken`` for the Graph SDK; Teams returns the raw token string).

Subclasses set the class attributes and implement ``acquire()`` on top of
``_acquire_result()``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import msal

from appif.domain.messaging.errors import NotAuthorized

logger = logging.getLogger(__name__)


class MsalTokenCacheAuth:
    """Base for MSAL-cache-backed auth. Subclasses set the class attributes."""

    #: Connector name used in errors and log events.
    connector_name: str = "graph"
    #: Cache directory used when the caller does not pass ``credentials_dir``.
    default_credentials_dir: Path = Path.home() / ".config" / "appif"
    #: Graph scopes requested when the caller does not pass ``scopes``.
    default_scopes: list[str] = []
    #: Consent script named in ``NotAuthorized`` messages.
    consent_script: str = "scripts/consent.py"

    def __init__(
        self,
        client_id: str,
        *,
        credentials_dir: Path | None = None,
        account: str = "default",
        tenant_id: str = "common",
        client_secret: str | None = None,
        scopes: list[str] | None = None,
    ) -> None:
        self._client_id = client_id
        self._credentials_dir = Path(credentials_dir) if credentials_dir is not None else self.default_credentials_dir
        self._account = account
        self._tenant_id = tenant_id
        self._client_secret = client_secret
        self._scopes = scopes or self.default_scopes

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

    # ── Token acquisition ─────────────────────────────────────

    def _acquire_result(self) -> dict:
        """Silent-acquire a token and return the raw MSAL result dict.

        Persists the on-disk cache when it changed. Raises ``NotAuthorized``
        when there is no cached account or acquisition fails.
        """
        assert self._app is not None

        accounts = self._app.get_accounts()
        if not accounts:
            raise NotAuthorized(
                self.connector_name,
                reason=f"No cached credentials for account '{self._account}'. Run: python {self.consent_script}",
            )

        chosen = accounts[0]
        self._user_email = chosen.get("username", "")

        result = self._app.acquire_token_silent(scopes=self._scopes, account=chosen)
        if not result:
            raise NotAuthorized(
                self.connector_name,
                reason=f"Token refresh failed for account '{self._account}'. Re-run: python {self.consent_script}",
            )
        if "error" in result:
            raise NotAuthorized(
                self.connector_name,
                reason=f"Token acquisition error: {result.get('error_description', result.get('error'))}",
            )

        self._save_cache()
        logger.debug(f"{self.connector_name}.token_acquired", extra={"account": self._account, "email": self._user_email})
        return result

    # ── Internal ──────────────────────────────────────────────

    def _cache_path(self) -> Path:
        return self._credentials_dir / f"{self._account}.json"

    def _load_cache(self) -> None:
        path = self._cache_path()
        if path.exists():
            self._cache.deserialize(path.read_text())
            logger.debug(f"{self.connector_name}.cache_loaded", extra={"path": str(path)})

    def _save_cache(self) -> None:
        if not self._cache.has_state_changed:
            return
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self._cache.serialize())
        tmp.rename(path)
        self._cache.has_state_changed = False
        logger.debug(f"{self.connector_name}.cache_saved", extra={"path": str(path)})

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
