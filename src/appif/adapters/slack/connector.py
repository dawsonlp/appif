"""Slack connector — one-connector-one-identity model.

Implements the :class:`~appif.domain.messaging.ports.Connector` protocol
using the Slack Bolt / Socket-Mode SDK with synchronous public methods
and internal threading for Socket Mode.

Give the connector a bot token (``xoxb-``) and it operates as the bot.
Give it a user token (``xoxp-``) and it operates as the user. The
optional app-level token (``xapp-``) enables Socket Mode for real-time
event delivery; without it the connector works in API-only mode.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

from appif.adapters.slack._auth import StaticTokenAuth
from appif.adapters.slack._normalizer import normalize_message
from appif.adapters.slack._rate_limiter import call_with_retry
from appif.adapters.slack._user_cache import UserCache
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

_CONNECTOR_NAME = "slack"


class SlackConnector:
    """Adapter that bridges Slack to the domain messaging port.

    All public methods are synchronous, conforming to the
    :class:`~appif.domain.messaging.ports.Connector` protocol.
    Internal concurrency (Socket Mode WebSocket) is managed via
    ``threading.Thread``.

    Parameters
    ----------
    identity_token:
        Required OAuth token (``xoxb-`` for bot, ``xoxp-`` for user).
    app_token:
        Optional app-level token (``xapp-``). Enables Socket Mode for
        real-time event delivery. When ``None`` the connector operates
        in API-only mode (``supports_realtime=False``).
    """

    # -- construction ---------------------------------------------------------

    def __init__(self, *, identity_token: str, app_token: str | None = None) -> None:
        self._auth = StaticTokenAuth(identity_token=identity_token, app_token=app_token)
        self._auth.validate()

        self._status = ConnectorStatus.DISCONNECTED
        self._listeners: list[MessageListener] = []
        self._listeners_lock = threading.Lock()
        self._handler: SocketModeHandler | None = None
        self._socket_thread: threading.Thread | None = None
        self._client: WebClient | None = None
        self._user_cache: UserCache | None = None
        self._authenticated_user_id: str | None = None
        self._team_id: str | None = None
        self._team_name: str = ""
        self._executor: ThreadPoolExecutor | None = None

    # -- Connector protocol: lifecycle ----------------------------------------

    def connect(self) -> None:
        """Authenticate and optionally start Socket Mode.

        If an app-level token is present, Socket Mode is started on a
        daemon thread for real-time event delivery. Otherwise the
        connector transitions to CONNECTED in API-only mode.
        """
        if self._status == ConnectorStatus.CONNECTED:
            return

        self._status = ConnectorStatus.CONNECTING
        try:
            self._client = WebClient(token=self._auth.identity_token)
            self._user_cache = UserCache(self._client)
            self._executor = ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix="slack-listener",
            )

            # Verify authentication and get identity
            auth_response = self._client.auth_test()
            self._authenticated_user_id = auth_response.get("user_id")
            self._team_id = auth_response.get("team_id")
            self._team_name = auth_response.get("team", "")

            logger.info(
                "slack_authenticated",
                extra={
                    "team_id": self._team_id,
                    "identity_type": self._auth.identity_type,
                    "user_id": self._authenticated_user_id,
                },
            )

            # Socket Mode only if app token available
            if self._auth.app_token:
                app = App(token=self._auth.identity_token)

                @app.event("message")
                def _handle_message(event: dict, say: Any) -> None:
                    self._on_slack_message(event)

                self._handler = SocketModeHandler(app, self._auth.app_token)
                self._socket_thread = threading.Thread(
                    target=self._handler.start,
                    name="slack-socket-mode",
                    daemon=True,
                )
                self._socket_thread.start()
            else:
                logger.info(
                    "slack_no_socket_mode",
                    extra={"reason": "no app-level token provided"},
                )

            self._status = ConnectorStatus.CONNECTED

        except (NotAuthorized, TransientFailure):
            self._status = ConnectorStatus.ERROR
            raise
        except Exception as exc:
            self._status = ConnectorStatus.ERROR
            raise TransientFailure(_CONNECTOR_NAME, reason=str(exc)) from exc

    def disconnect(self) -> None:
        """Tear down connections and stop event ingestion."""
        if self._status == ConnectorStatus.DISCONNECTED:
            return

        try:
            if self._handler:
                self._handler.close()
                self._handler = None
            if self._socket_thread and self._socket_thread.is_alive():
                self._socket_thread.join(timeout=5.0)
                self._socket_thread = None
        except Exception as exc:
            logger.warning("slack.disconnect_error", extra={"error": str(exc)})
        finally:
            if self._executor:
                self._executor.shutdown(wait=True, cancel_futures=False)
                self._executor = None
            self._client = None
            self._status = ConnectorStatus.DISCONNECTED
            logger.info("slack.disconnected")

    def get_status(self) -> ConnectorStatus:
        """Return current lifecycle state."""
        return self._status

    # -- Connector protocol: discovery ----------------------------------------

    def list_accounts(self) -> list[Account]:
        """List configured workspaces."""
        return [
            Account(
                account_id=self._team_id or "",
                display_name=self._team_name or self._team_id or "",
                connector=_CONNECTOR_NAME,
            )
        ]

    def list_targets(self, account_id: str) -> list[Target]:
        """List available channels in the workspace."""
        self._ensure_connected()

        targets: list[Target] = []
        cursor: str | None = None

        while True:
            kwargs: dict[str, Any] = {"types": "public_channel,private_channel,im,mpim", "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor

            response = self._client.conversations_list(**kwargs)
            channels = response.get("channels", [])

            for ch in channels:
                ch_type = "dm" if ch.get("is_im") else "group" if ch.get("is_mpim") else "channel"
                targets.append(
                    Target(
                        target_id=ch["id"],
                        display_name=ch.get("name", ch["id"]),
                        type=ch_type,
                        account_id=account_id,
                    )
                )

            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        return targets

    # -- Connector protocol: inbound ------------------------------------------

    def register_listener(self, listener: MessageListener) -> None:
        """Subscribe a listener to receive inbound message events."""
        with self._listeners_lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unregister_listener(self, listener: MessageListener) -> None:
        """Remove a previously registered listener."""
        with self._listeners_lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    # -- Connector protocol: outbound -----------------------------------------

    def send(self, conversation: ConversationRef, content: MessageContent) -> SendReceipt:
        """Send a message to the conversation identified by the ref.

        Returns a :class:`SendReceipt` with the platform-assigned
        message timestamp.
        """
        self._ensure_connected()

        channel = conversation.opaque_id.get("channel", "")
        thread_ts = conversation.opaque_id.get("thread_ts")

        kwargs: dict[str, Any] = {
            "channel": channel,
            "text": content.text,
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts

        response = call_with_retry(
            self._client.chat_postMessage,
            connector_name=_CONNECTOR_NAME,
            **kwargs,
        )
        data: dict = response.data  # type: ignore[assignment]

        return SendReceipt(
            external_id=data.get("ts", ""),
            timestamp=datetime.now(UTC),
        )

    # -- Connector protocol: durability ---------------------------------------

    def backfill(self, account_id: str, scope: BackfillScope) -> None:
        """Retrieve historical messages and emit to registered listeners."""
        self._ensure_connected()

        channel_ids = scope.conversation_ids if scope.conversation_ids else ()
        if not channel_ids:
            return

        for channel_id in channel_ids:
            self._backfill_channel(channel_id, scope)

    # -- Connector protocol: capabilities -------------------------------------

    def get_capabilities(self) -> ConnectorCapabilities:
        """Return capabilities computed from construction inputs.

        ``supports_realtime`` is ``True`` only when an app-level token
        is present. All other capabilities are ``True`` for both bot
        and user identity types.
        """
        has_app_token = self._auth.app_token is not None
        return ConnectorCapabilities(
            supports_realtime=has_app_token,
            supports_backfill=True,
            supports_threads=True,
            supports_reply=True,
            supports_auto_send=True,
            delivery_mode="AUTOMATIC" if has_app_token else "MANUAL",
        )

    # -- Convenience ----------------------------------------------------------

    def listen_forever(self) -> None:
        """Block until disconnect or interrupt — useful for scripts / CLI."""
        try:
            while self._status == ConnectorStatus.CONNECTED:
                time.sleep(1)
        except KeyboardInterrupt:
            self.disconnect()

    # -- Internal: event dispatch ---------------------------------------------

    def _on_slack_message(self, event: dict) -> None:
        """Handle an inbound Slack message event."""
        if event.get("subtype") in (
            "message_changed",
            "message_deleted",
            "channel_join",
            "channel_leave",
        ):
            return

        message_event = normalize_message(
            event,
            team_id=self._team_id or "",
            authenticated_user_id=self._authenticated_user_id or "",
            resolve_user=self._user_cache.resolve,
        )
        if message_event is not None:
            self._dispatch_event(message_event)

    def _dispatch_event(self, event: MessageEvent) -> None:
        """Dispatch a MessageEvent to all registered listeners via thread pool."""
        with self._listeners_lock:
            listeners = list(self._listeners)

        for listener in listeners:
            self._executor.submit(self._safe_listener_call, listener, event)

    @staticmethod
    def _safe_listener_call(listener: MessageListener, event: MessageEvent) -> None:
        """Invoke a listener, catching and logging any errors."""
        try:
            listener.on_message(event)
        except Exception:
            logger.exception(
                "slack.listener_error",
                extra={"listener": type(listener).__name__, "message_id": event.message_id},
            )

    # -- Internal: backfill ---------------------------------------------------

    def _backfill_channel(self, channel_id: str, scope: BackfillScope) -> None:
        """Fetch history for a single channel and dispatch events."""
        kwargs: dict[str, Any] = {"channel": channel_id, "limit": 200}
        if scope.oldest:
            kwargs["oldest"] = str(scope.oldest.timestamp())
        if scope.latest:
            kwargs["latest"] = str(scope.latest.timestamp())

        cursor: str | None = None
        while True:
            if cursor:
                kwargs["cursor"] = cursor

            response = self._client.conversations_history(**kwargs)
            messages = response.get("messages", [])

            for msg in reversed(messages):  # oldest first
                event = normalize_message(
                    msg,
                    team_id=self._team_id or "",
                    authenticated_user_id=self._authenticated_user_id or "",
                    resolve_user=self._user_cache.resolve,
                )
                if event is not None:
                    self._dispatch_event(event)

            if not response.get("has_more", False):
                break
            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

    # -- Internal: helpers ----------------------------------------------------

    def _ensure_connected(self) -> None:
        """Raise if connector is not in CONNECTED state."""
        if self._status != ConnectorStatus.CONNECTED:
            raise NotSupported(
                _CONNECTOR_NAME,
                operation=f"not connected (status={self._status.value})",
            )
