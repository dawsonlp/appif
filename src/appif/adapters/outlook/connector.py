"""OutlookConnector — transport adapter for Microsoft 365 mail.

Implements the Connector protocol using the Microsoft Graph API.
All Graph-specific types and mechanics are encapsulated here;
only domain types cross the boundary.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx

from appif.adapters.outlook._auth import MsalAuth
from appif.adapters.outlook._message_builder import build_message
from appif.adapters.outlook._normalizer import normalize_message
from appif.adapters.outlook._poller import OutlookPoller
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

_CONNECTOR_NAME = "outlook"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class OutlookConnector:
    """Microsoft 365 mail adapter implementing the ``Connector`` protocol.

    Parameters
    ----------
    client_id:
        Azure AD application (client) ID.
    client_secret:
        Optional client secret for confidential-client flow.
    tenant_id:
        Azure AD tenant. ``"common"`` for personal + org accounts.
    account:
        Logical account label (maps to credential file).
    credentials_dir:
        Directory for per-account MSAL token caches.
    poll_interval:
        Seconds between delta-poll cycles.
    folder_filter:
        Well-known folder names to poll (default: ``["Inbox"]``).
    delivery_mode:
        ``"poll"`` for v1. Future: ``"subscription"``.
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
        folder_filter: list[str] | None = None,
        delivery_mode: str | None = None,
    ) -> None:
        # Resolve from env with parameter overrides
        self._client_id = client_id or os.environ.get("APPIF_OUTLOOK_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("APPIF_OUTLOOK_CLIENT_SECRET")
        self._tenant_id = tenant_id or os.environ.get("APPIF_OUTLOOK_TENANT_ID", "common")
        self._account = account or os.environ.get("APPIF_OUTLOOK_ACCOUNT", "default")
        self._credentials_dir = Path(
            credentials_dir
            or os.environ.get("APPIF_OUTLOOK_CREDENTIALS_DIR", str(Path.home() / ".config" / "appif" / "outlook"))
        )
        self._poll_interval = poll_interval or int(os.environ.get("APPIF_OUTLOOK_POLL_INTERVAL_SECONDS", "30"))
        self._delivery_mode = delivery_mode or os.environ.get("APPIF_OUTLOOK_DELIVERY_MODE", "poll")

        # Parse folder filter
        if folder_filter is not None:
            self._folders = folder_filter
        else:
            raw = os.environ.get("APPIF_OUTLOOK_FOLDER_FILTER", "Inbox")
            self._folders = [f.strip() for f in raw.split(",") if f.strip()]

        # Internal state
        self._status = ConnectorStatus.DISCONNECTED
        self._listeners: list[MessageListener] = []
        self._listeners_lock = threading.Lock()
        self._sent_ids: set[str] = set()

        # Components — initialised on connect()
        self._auth: MsalAuth | None = None
        self._poller: OutlookPoller | None = None
        self._user_email: str = ""

    # -- Lifecycle -----------------------------------------------------------

    def connect(self) -> None:
        """Authenticate via MSAL and start the delta-query poller."""
        if self._status == ConnectorStatus.CONNECTED:
            return

        if not self._client_id:
            raise NotAuthorized(
                _CONNECTOR_NAME,
                reason="No client_id configured. Set APPIF_OUTLOOK_CLIENT_ID.",
            )

        self._status = ConnectorStatus.CONNECTING

        try:
            # Build auth
            self._auth = MsalAuth(
                self._client_id,
                credentials_dir=self._credentials_dir,
                account=self._account,
                tenant_id=self._tenant_id,
                client_secret=self._client_secret,
            )

            # Verify we can acquire a token
            self._auth.acquire()
            self._user_email = self._auth.user_email()

            logger.info(
                "outlook.authenticated",
                extra={"account": self._account, "email": self._user_email},
            )

            # Start poller
            self._poller = OutlookPoller(
                access_token_fn=self._get_access_token,
                account_id=self._account,
                folders=self._folders,
                poll_interval=self._poll_interval,
                callback=self._on_message,
                sent_ids=self._sent_ids,
            )
            self._poller.start()

            self._status = ConnectorStatus.CONNECTED
            logger.info("outlook.connected", extra={"account": self._account})

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
            logger.warning("outlook.disconnect_error", extra={"error": str(exc)})
        finally:
            self._poller = None
            self._auth = None
            self._status = ConnectorStatus.DISCONNECTED
            logger.info("outlook.disconnected")

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
        """List mail folders as targets."""
        self._ensure_connected()

        token = self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        try:
            response = httpx.get(
                f"{_GRAPH_BASE}/me/mailFolders",
                headers=headers,
                params={"$top": "100"},
                timeout=30.0,
            )
            response.raise_for_status()
        except Exception as exc:
            raise TransientFailure(_CONNECTOR_NAME, reason=f"list_targets failed: {exc}") from exc

        folders = response.json().get("value", [])
        return [
            Target(
                target_id=f["id"],
                display_name=f.get("displayName", f["id"]),
                type="mail_folder",
                account_id=account_id,
            )
            for f in folders
        ]

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
        """Send a message — new thread or reply, with or without attachments."""
        self._ensure_connected()

        payload = build_message(conversation, content)
        route = payload.pop("_route")
        token = self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            if route == "reply":
                parent_id = payload.pop("_parent_message_id")
                response = httpx.post(
                    f"{_GRAPH_BASE}/me/messages/{parent_id}/reply",
                    json=payload,
                    headers=headers,
                    timeout=30.0,
                )
            else:
                response = httpx.post(
                    f"{_GRAPH_BASE}/me/sendMail",
                    json=payload,
                    headers=headers,
                    timeout=30.0,
                )

            response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            raise TransientFailure(
                _CONNECTOR_NAME,
                reason=f"send failed ({exc.response.status_code}): {exc.response.text[:200]}",
            ) from exc
        except Exception as exc:
            raise TransientFailure(_CONNECTOR_NAME, reason=f"send failed: {exc}") from exc

        # Graph sendMail returns 202 with no body; reply returns 202
        # Track sent message ID for echo suppression
        msg_id = ""
        if response.content:
            try:
                data = response.json()
                msg_id = data.get("id", "")
            except Exception:
                pass

        if msg_id:
            self._sent_ids.add(msg_id)

        return SendReceipt(
            external_id=msg_id or "accepted",
            timestamp=datetime.now(UTC),
        )

    # -- Durability ----------------------------------------------------------

    def backfill(self, account_id: str, scope: BackfillScope) -> None:
        """Retrieve historical messages matching the scope."""
        self._ensure_connected()

        token = self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        # Build OData filter for time range
        filters = []
        if scope.oldest:
            filters.append(f"receivedDateTime ge {scope.oldest.isoformat()}")
        if scope.latest:
            filters.append(f"receivedDateTime le {scope.latest.isoformat()}")

        params: dict = {
            "$select": "id,from,subject,body,conversationId,receivedDateTime,parentFolderId,hasAttachments,attachments",
            "$orderby": "receivedDateTime desc",
            "$top": "50",
        }
        if filters:
            params["$filter"] = " and ".join(filters)

        # Determine which folders to backfill
        conversation_ids = scope.conversation_ids
        if conversation_ids:
            folder_ids = list(conversation_ids)
        else:
            folder_ids = self._folders

        for folder in folder_ids:
            self._backfill_folder(folder, params, headers)

    # -- Capability introspection --------------------------------------------

    def get_capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_realtime=True,
            supports_backfill=True,
            supports_threads=True,
            supports_reply=True,
            supports_auto_send=True,
            delivery_mode="AUTOMATIC",
        )

    # -- Internal ------------------------------------------------------------

    def _get_access_token(self) -> str:
        """Get a current access token from the auth module."""
        if not self._auth:
            raise NotAuthorized(_CONNECTOR_NAME, reason="Not connected")
        return self._auth.acquire().token

    def _on_message(self, event: MessageEvent) -> None:
        """Dispatch an inbound message to all registered listeners."""
        with self._listeners_lock:
            listeners = list(self._listeners)

        for listener in listeners:
            try:
                listener.on_message(event)
            except Exception:
                logger.exception(
                    "outlook.listener_error",
                    extra={"listener": type(listener).__name__, "message_id": event.message_id},
                )

    def _backfill_folder(self, folder: str, params: dict, headers: dict) -> None:
        """Backfill messages from a single folder."""
        url = f"{_GRAPH_BASE}/me/mailFolders/{folder}/messages"

        while url:
            try:
                response = httpx.get(url, headers=headers, params=params, timeout=30.0)
                response.raise_for_status()
            except Exception as exc:
                logger.warning(
                    "outlook.backfill_error",
                    extra={"folder": folder, "error": str(exc)},
                )
                return

            data = response.json()
            for msg in data.get("value", []):
                event = normalize_message(
                    msg,
                    account_id=self._account,
                    sent_ids=self._sent_ids,
                )
                if event is not None:
                    self._on_message(event)

            # Follow pagination
            url = data.get("@odata.nextLink")
            params = {}  # nextLink includes params

    def _ensure_connected(self) -> None:
        """Raise if connector is not in CONNECTED state."""
        if self._status != ConnectorStatus.CONNECTED:
            raise NotSupported(
                _CONNECTOR_NAME,
                operation=f"not connected (status={self._status.value})",
            )
