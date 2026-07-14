"""TeamsConnector — transport adapter for Microsoft Teams messaging.

Implements the ``Connector`` protocol over the Microsoft Graph API for Teams
chats and channel messages. All Graph/MSAL mechanics are encapsulated here;
only domain types cross the boundary.

Reuses the same Azure app registration as the Outlook adapter (client/tenant
default to ``APPIF_OUTLOOK_*`` when Teams-specific vars are unset) but keeps a
separate token cache (``~/.config/appif/teams``).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from appif import config
from appif.adapters._base import BaseMessagingConnector
from appif.adapters._util import env_bool
from appif.adapters.teams._auth import MsalAuth, scopes_for
from appif.adapters.teams._message_builder import build_message
from appif.adapters.teams._normalizer import normalize_message
from appif.adapters.teams._poller import TeamsPoller
from appif.adapters.teams._rate_limiter import graph_get, graph_post
from appif.domain.messaging.errors import NotAuthorized, NotSupported, TransientFailure
from appif.domain.messaging.models import (
    Account,
    BackfillScope,
    ConnectorCapabilities,
    ConnectorStatus,
    ConversationRef,
    MessageContent,
    SendReceipt,
    Target,
)

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "teams"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class TeamsConnector(BaseMessagingConnector):
    """Microsoft Teams adapter implementing the ``Connector`` protocol.

    Parameters
    ----------
    client_id / client_secret / tenant_id:
        Azure AD app credentials. ``client_id``/``tenant_id`` fall back to the
        ``APPIF_OUTLOOK_*`` values (same app registration) when the Teams
        equivalents are unset.
    account:
        Logical account label (token cache filename stem).
    credentials_dir:
        Token cache directory (default ``~/.config/appif/teams``).
    poll_interval:
        Seconds between delta-poll cycles.
    include_sent:
        Deliver messages you sent alongside incoming ones (default off).
    include_chats / include_channels:
        Which source kinds to watch. Channels need admin-consented
        ``ChannelMessage.Read.All``.
    """

    connector_name = _CONNECTOR_NAME

    def __init__(
        self,
        client_id: str | None = None,
        *,
        client_secret: str | None = None,
        tenant_id: str | None = None,
        account: str | None = None,
        credentials_dir: Path | str | None = None,
        poll_interval: int | None = None,
        include_sent: bool | None = None,
        include_chats: bool | None = None,
        include_channels: bool | None = None,
    ) -> None:
        super().__init__()
        # Resolve config: constructor arg > teams/config.yaml account > env var.
        # Credentials fall back to the Outlook Azure app when Teams-specific
        # values are unset, so a shared app registration works out of the box.
        name, settings = config.select_account("teams", account, env_account_var="APPIF_TEAMS_ACCOUNT")
        self._account = name
        self._client_id = (
            client_id
            or settings.get("client_id")
            or os.environ.get("APPIF_TEAMS_CLIENT_ID")
            or os.environ.get("APPIF_OUTLOOK_CLIENT_ID", "")
        )
        self._client_secret = (
            client_secret
            or settings.get("client_secret")
            or os.environ.get("APPIF_TEAMS_CLIENT_SECRET")
            or os.environ.get("APPIF_OUTLOOK_CLIENT_SECRET")
        )
        self._tenant_id = (
            tenant_id
            or settings.get("tenant_id")
            or os.environ.get("APPIF_TEAMS_TENANT_ID")
            or os.environ.get("APPIF_OUTLOOK_TENANT_ID", "common")
        )
        self._credentials_dir = Path(
            credentials_dir or os.environ.get("APPIF_TEAMS_CREDENTIALS_DIR") or config.service_dir("teams")
        )
        self._poll_interval = (
            poll_interval or settings.get("poll_interval_seconds") or int(os.environ.get("APPIF_TEAMS_POLL_INTERVAL_SECONDS", "30"))
        )
        if include_sent is not None:
            self._include_sent = include_sent
        elif "include_sent" in settings:
            self._include_sent = bool(settings["include_sent"])
        else:
            self._include_sent = env_bool("APPIF_TEAMS_INCLUDE_SENT")

        if include_chats is not None:
            self._include_chats = include_chats
        elif "include_chats" in settings:
            self._include_chats = bool(settings["include_chats"])
        else:
            self._include_chats = env_bool("APPIF_TEAMS_INCLUDE_CHATS", True)

        # Channels are opt-in: ChannelMessage.Read.All requires admin consent,
        # so enabling them by default would surface NotAuthorized for anyone
        # who only consented the (no-admin-needed) chat scopes.
        if include_channels is not None:
            self._include_channels = include_channels
        elif "include_channels" in settings:
            self._include_channels = bool(settings["include_channels"])
        else:
            self._include_channels = env_bool("APPIF_TEAMS_INCLUDE_CHANNELS", False)

        # Teams-specific state
        self._auth: MsalAuth | None = None
        self._poller: TeamsPoller | None = None
        self._user_id: str = ""
        self._user_email: str = ""

    # -- Lifecycle -----------------------------------------------------------

    def connect(self) -> None:
        """Authenticate via MSAL, resolve identity, and start the poller."""
        if self._status == ConnectorStatus.CONNECTED:
            return
        if not self._client_id:
            raise NotAuthorized(
                _CONNECTOR_NAME,
                reason="No client_id configured. Set APPIF_TEAMS_CLIENT_ID (or APPIF_OUTLOOK_CLIENT_ID).",
            )

        self._status = ConnectorStatus.CONNECTING
        self._start_dispatch()
        try:
            self._auth = MsalAuth(
                self._client_id,
                credentials_dir=self._credentials_dir,
                account=self._account,
                tenant_id=self._tenant_id,
                client_secret=self._client_secret,
                scopes=scopes_for(include_chats=self._include_chats, include_channels=self._include_channels),
            )

            # Resolve own identity (AAD id for self-suppression + display).
            me = self._graph_get_json("/me")
            self._user_id = me.get("id", "")
            self._user_email = me.get("mail") or me.get("userPrincipalName") or self._account

            logger.info("teams.authenticated", extra={"account": self._account, "user_id": self._user_id})

            self._poller = TeamsPoller(
                access_token_fn=self._get_access_token,
                account_id=self._account,
                authenticated_user_id=self._user_id,
                poll_interval=self._poll_interval,
                callback=self._dispatch,
                include_sent=self._include_sent,
                include_chats=self._include_chats,
                include_channels=self._include_channels,
            )
            self._poller.start()

            self._status = ConnectorStatus.CONNECTED
            logger.info("teams.connected", extra={"account": self._account})

        except NotAuthorized:
            self._status = ConnectorStatus.ERROR
            raise
        except Exception as exc:
            self._status = ConnectorStatus.ERROR
            raise TransientFailure(_CONNECTOR_NAME, reason=str(exc)) from exc

    def disconnect(self) -> None:
        """Stop the poller and tear down resources."""
        if self._status == ConnectorStatus.DISCONNECTED:
            return
        try:
            if self._poller:
                self._poller.stop()
        except Exception as exc:
            logger.warning("teams.disconnect_error", extra={"error": str(exc)})
        finally:
            self._poller = None
            self._auth = None
            self._stop_dispatch()
            self._status = ConnectorStatus.DISCONNECTED
            logger.info("teams.disconnected")

    # -- Discovery -----------------------------------------------------------

    def list_accounts(self) -> list[Account]:
        return [
            Account(
                account_id=self._account,
                display_name=self._user_email or self._account,
                connector=_CONNECTOR_NAME,
            )
        ]

    def list_targets(self, account_id: str) -> list[Target]:
        """List chats and channels as targets."""
        self._ensure_connected()
        targets: list[Target] = []

        if self._include_chats:
            for chat in self._graph_get_json("/me/chats", params={"$top": 50}).get("value", []):
                targets.append(
                    Target(
                        target_id=chat["id"],
                        display_name=chat.get("topic") or chat.get("chatType", "chat"),
                        type="chat",
                        account_id=account_id,
                    )
                )

        if self._include_channels:
            for team in self._graph_get_json("/me/joinedTeams", params={"$top": 50}).get("value", []):
                team_id = team.get("id")
                if not team_id:
                    continue
                for channel in self._graph_get_json(f"/teams/{team_id}/channels").get("value", []):
                    targets.append(
                        Target(
                            target_id=f"{team_id}:{channel['id']}",
                            display_name=f"{team.get('displayName', team_id)} / {channel.get('displayName', '')}",
                            type="channel",
                            account_id=account_id,
                        )
                    )
        return targets

    # -- Outbound ------------------------------------------------------------

    def send(self, conversation: ConversationRef, content: MessageContent) -> SendReceipt:
        """Send a chat message, a new channel message, or a channel reply."""
        self._ensure_connected()

        path, body = build_message(conversation, content)
        headers = {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
        }
        try:
            response = graph_post(f"{_GRAPH_BASE}{path}", headers=headers, json=body)
        except (NotAuthorized, NotSupported):
            raise
        except Exception as exc:
            raise TransientFailure(_CONNECTOR_NAME, reason=f"send failed: {exc}") from exc

        msg_id = ""
        if response.content:
            try:
                msg_id = response.json().get("id", "")
            except Exception:
                pass

        return SendReceipt(external_id=msg_id or "accepted", timestamp=datetime.now(UTC))

    # -- Durability ----------------------------------------------------------

    def backfill(self, account_id: str, scope: BackfillScope) -> None:
        """Retrieve historical messages from chats (and channels) and emit them."""
        self._ensure_connected()

        if scope.conversation_ids:
            # Explicit chat ids requested.
            for chat_id in scope.conversation_ids:
                self._backfill_url(
                    f"{_GRAPH_BASE}/me/chats/{chat_id}/messages",
                    scope=scope,
                    chat_id=chat_id,
                )
            return

        if self._include_chats:
            for chat in self._graph_get_json("/me/chats", params={"$top": 50}).get("value", []):
                self._backfill_url(
                    f"{_GRAPH_BASE}/me/chats/{chat['id']}/messages",
                    scope=scope,
                    chat_id=chat["id"],
                )

        if self._include_channels:
            for team in self._graph_get_json("/me/joinedTeams", params={"$top": 50}).get("value", []):
                team_id = team.get("id")
                if not team_id:
                    continue
                for channel in self._graph_get_json(f"/teams/{team_id}/channels").get("value", []):
                    self._backfill_url(
                        f"{_GRAPH_BASE}/teams/{team_id}/channels/{channel['id']}/messages",
                        scope=scope,
                        team_id=team_id,
                        channel_id=channel["id"],
                    )

    # -- Capability introspection --------------------------------------------

    def get_capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_realtime=False,
            supports_backfill=True,
            supports_threads=True,
            supports_reply=True,
            supports_auto_send=True,
            delivery_mode="AUTOMATIC",
        )

    # -- Internal ------------------------------------------------------------

    def _get_access_token(self) -> str:
        if not self._auth:
            raise NotAuthorized(_CONNECTOR_NAME, reason="Not connected")
        return self._auth.acquire()

    def _graph_get_json(self, path: str, *, params: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._get_access_token()}"}
        url = path if path.startswith("http") else f"{_GRAPH_BASE}{path}"
        return graph_get(url, headers=headers, params=params).json()

    def _backfill_url(
        self,
        url: str,
        *,
        scope: BackfillScope,
        chat_id: str | None = None,
        team_id: str | None = None,
        channel_id: str | None = None,
    ) -> None:
        """Page through one source's messages, dispatching those within scope."""
        headers = {"Authorization": f"Bearer {self._get_access_token()}"}
        params: dict | None = {"$top": 50}
        next_url: str | None = url

        while next_url:
            try:
                response = graph_get(next_url, headers=headers, params=params)
            except Exception as exc:
                logger.warning("teams.backfill_error", extra={"url": next_url, "error": str(exc)})
                return

            data = response.json()
            for msg in data.get("value", []):
                event = normalize_message(
                    msg,
                    account_id=self._account,
                    authenticated_user_id=self._user_id,
                    chat_id=chat_id,
                    team_id=team_id,
                    channel_id=channel_id,
                    include_sent=self._include_sent,
                )
                if event is None:
                    continue
                if scope.oldest and event.timestamp < scope.oldest:
                    continue
                if scope.latest and event.timestamp > scope.latest:
                    continue
                self._dispatch(event)

            next_url = data.get("@odata.nextLink")
            params = None
