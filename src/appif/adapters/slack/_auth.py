"""Slack authentication — one-connector-one-identity model.

Provides the ``SlackAuth`` protocol and a concrete ``StaticTokenAuth``
implementation. Give it a bot token (``xoxb-``) and the connector acts
as the bot. Give it a user token (``xoxp-``) and it acts as the user.

The optional ``app_token`` (``xapp-``) enables Socket Mode for real-time
event delivery. When absent the connector operates in API-only mode.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from dotenv import load_dotenv

from appif.domain.messaging.errors import NotAuthorized

_CONNECTOR_NAME = "slack"


def _classify_token(token: str) -> str:
    """Return ``'bot'`` or ``'user'`` based on the Slack token prefix.

    Raises
    ------
    ValueError
        If the token does not start with a recognised prefix.
    """
    if token.startswith("xoxb-"):
        return "bot"
    if token.startswith("xoxp-"):
        return "user"
    raise ValueError(f"Unrecognized Slack token prefix: {token[:5]}...")


@runtime_checkable
class SlackAuth(Protocol):
    """Strategy that supplies Slack API tokens to the connector.

    ``identity_token`` is the OAuth token that determines *who* the
    connector is (bot or user).  ``identity_type`` is derived from the
    token prefix.  ``app_token`` is optional and enables Socket Mode.
    """

    @property
    def identity_token(self) -> str:
        """OAuth identity token (``xoxb-`` or ``xoxp-``)."""
        ...

    @property
    def identity_type(self) -> str:
        """``'bot'`` or ``'user'``, derived from the token prefix."""
        ...

    @property
    def app_token(self) -> str | None:
        """App-level token (``xapp-``) for Socket Mode, or ``None``."""
        ...

    def validate(self) -> None:
        """Raise :class:`NotAuthorized` if the identity token is missing."""
        ...


class StaticTokenAuth:
    """Auth implementation backed by plain strings (env-vars / tests).

    Parameters
    ----------
    identity_token:
        Required OAuth token (``xoxb-`` for bot, ``xoxp-`` for user).
    app_token:
        Optional app-level token (``xapp-``). When ``None``, the
        connector operates in API-only mode without real-time events.
    """

    def __init__(self, *, identity_token: str, app_token: str | None = None) -> None:
        self._identity_token = identity_token
        self._app_token = app_token

    # -- SlackAuth protocol --------------------------------------------------

    @property
    def identity_token(self) -> str:
        return self._identity_token

    @property
    def identity_type(self) -> str:
        return _classify_token(self._identity_token)

    @property
    def app_token(self) -> str | None:
        return self._app_token

    def validate(self) -> None:
        if not self._identity_token:
            raise NotAuthorized(
                connector=_CONNECTOR_NAME,
                reason="APPIF_SLACK_IDENTITY_TOKEN is not set",
            )
        # Fail fast on unrecognised prefix.
        _classify_token(self._identity_token)

    # -- Factory --------------------------------------------------------------

    @classmethod
    def from_env(cls) -> StaticTokenAuth:
        """Build from well-known environment variables.

        Reads ``APPIF_SLACK_IDENTITY_TOKEN`` (required) and
        ``APPIF_SLACK_APP_TOKEN`` (optional) from the environment.
        """
        load_dotenv(Path.home() / ".env")
        return cls(
            identity_token=os.getenv("APPIF_SLACK_IDENTITY_TOKEN", ""),
            app_token=os.getenv("APPIF_SLACK_APP_TOKEN") or None,
        )
