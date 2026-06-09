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
import threading
from datetime import UTC, datetime
from pathlib import Path

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
    MessageEvent,
    SendReceipt,
    Target,
)
from appif.domain.messaging.ports import MessageListener

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "teams"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable. Truthy: 1/true/yes/on."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class TeamsConnector:
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
        self._client_id = (
            client_id or os.environ.get("APPIF_TEAMS_CLIENT_ID") or os.environ.get("APPIF_OUTLOOK_CLIENT_ID", "")
        )
        self._client_secret = (
            client_secret
            or os.environ.get("APPIF_TEAMS_CLIENT_SECRET")
            or os.environ.get("APPIF_OUTLOOK_CLIENT_SECRET")
        )
        self._tenant_id = (
            tenant_id or os.environ.get("APPIF_TEAMS_TENANT_ID") or os.environ.get("APPIF_OUTLOOK_TENANT_ID", "common")
        )
        self._account = account or os.environ.get("APPIF_TEAMS_ACCOUNT", "default")
        self._credentials_dir = Path(
            credentials_dir
            or os.environ.get("APPIF_TEAMS_CREDENTIALS_DIR", str(Path.home() / ".config" / "appif" / "teams"))
        )
        self._poll_interval = poll_interval or int(os.environ.get("APPIF_TEAMS_POLL_INTERVAL_SECONDS", "30"))
        self._include_sent = include_sent if include_sent is not None else _env_bool("APPIF_TEAMS_INCLUDE_SENT")
        self._include_chats = (
            include_chats if include_chats is not None else _env_bool("APPIF_TEAMS_INCLUDE_CHATS", True)
        )
        # Channels are opt-in: ChannelMessage.Read.All requires admin consent,
        # so enabling them by default would surface NotAuthorized for anyone
        # who only consented the (no-admin-needed) chat scopes.
        self._include_channels = (
            include_channels if include_channels is not None else _env_bool("APPIF_TEAMS_INCLUDE_CHANNELS", False)
        )

        # Internal state
        self._status = ConnectorStatus.DISCONNECTED
        self._listeners: list[MessageListener] = []
        self._listeners_lock = threading.Lock()
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
                callback=self._on_message,
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
            self._status = ConnectorStatus.DISCONNECTED
            logger.info("teams.disconnected")

    def get_status(self) -> ConnectorStatus:
        return self._status

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

    # -- Inbound -------------------------------------------------------------

    def register_listener(self, listener: MessageListener) -> None:
        with self._listeners_lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unregister_listener(self, listener: MessageListener) -> None:
        with self._listeners_lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

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
                self._on_message(event)

            next_url = data.get("@odata.nextLink")
            params = None

    def _on_message(self, event: MessageEvent) -> None:
        with self._listeners_lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener.on_message(event)
            except Exception:
                logger.exception(
                    "teams.listener_error",
                    extra={"listener": type(listener).__name__, "message_id": event.message_id},
                )

    def _ensure_connected(self) -> None:
        if self._status != ConnectorStatus.CONNECTED:
            raise NotSupported(_CONNECTOR_NAME, operation=f"not connected (status={self._status.value})")
