"""GmailConnector — transport adapter for Gmail.

Implements the Connector protocol using the Gmail API via
``google-api-python-client``. All Gmail-specific types and mechanics
are encapsulated here; only domain types cross the boundary.
"""

from __future__ import annotations

import base64
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from appif.adapters.gmail._auth import FileCredentialAuth, GmailAuth
from appif.adapters.gmail._message_builder import build_message
from appif.adapters.gmail._normalizer import normalize_message
from appif.adapters.gmail._poller import GmailPoller
from appif.adapters.gmail._rate_limiter import call_with_retry
from appif.domain.messaging.errors import ConnectorError, NotAuthorized, NotSupported, TransientFailure
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
from appif.domain.messaging.ports import MessageListener

try:
    from googleapiclient.discovery import build as build_service
except ImportError:
    build_service = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "gmail"


class GmailConnector:
    """Gmail adapter implementing the ``Connector`` protocol.

    Parameters
    ----------
    auth:
        Auth provider. Defaults to ``FileCredentialAuth()`` if not provided.
    delivery_mode:
        ``"AUTOMATIC"`` (send immediately) or ``"ASSISTED"`` (create draft).
        Defaults to ``APPIF_GMAIL_DELIVERY_MODE`` env var or ``"AUTOMATIC"``.
    poll_interval:
        Seconds between poll cycles. Defaults to
        ``APPIF_GMAIL_POLL_INTERVAL_SECONDS`` env var or ``30``.
    label_filter:
        Label IDs to watch. Defaults to ``APPIF_GMAIL_LABEL_FILTER``
        env var (comma-separated) or ``["INBOX"]``.
    """

    def __init__(
        self,
        auth: GmailAuth | None = None,
        *,
        delivery_mode: str | None = None,
        poll_interval: int | None = None,
        label_filter: list[str] | None = None,
    ) -> None:
        self._auth = auth or FileCredentialAuth()
        self._delivery_mode = (delivery_mode or os.environ.get("APPIF_GMAIL_DELIVERY_MODE", "AUTOMATIC")).upper()
        self._poll_interval = poll_interval or int(os.environ.get("APPIF_GMAIL_POLL_INTERVAL_SECONDS", "30"))

        if label_filter is not None:
            self._label_filter = label_filter
        else:
            raw = os.environ.get("APPIF_GMAIL_LABEL_FILTER", "INBOX")
            self._label_filter = [lbl.strip() for lbl in raw.split(",") if lbl.strip()]

        # Internal state
        self._status = ConnectorStatus.DISCONNECTED
        self._service = None
        self._poller: GmailPoller | None = None
        self._listeners: list[MessageListener] = []
        self._listeners_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gmail-dispatch")

    # -- Lifecycle -----------------------------------------------------------

    def connect(self) -> None:
        """Validate auth, build Gmail service, verify profile, start poller."""
        if self._status == ConnectorStatus.CONNECTED:
            return

        self._status = ConnectorStatus.CONNECTING

        try:
            # Validate credentials are present
            self._auth.validate()

            # Build Gmail API service
            self._service = build_service("gmail", "v1", credentials=self._auth.credentials, cache_discovery=False)

            # Verify credentials by calling getProfile
            profile = call_with_retry(self._service.users().getProfile(userId="me").execute)
            profile_email = profile.get("emailAddress", "")

            if profile_email.lower() != self._auth.account.lower():
                logger.warning(
                    "gmail.profile_mismatch",
                    extra={"expected": self._auth.account, "actual": profile_email},
                )

            # Persist any refreshed tokens
            if hasattr(self._auth, "save_credentials"):
                self._auth.save_credentials()

            # Start poller
            self._poller = GmailPoller(
                service=self._service,
                account_id=self._auth.account,
                label_filter=self._label_filter,
                poll_interval=self._poll_interval,
                on_new_messages=self._on_new_messages,
            )
            self._poller.start()

            self._status = ConnectorStatus.CONNECTED
            logger.info(
                "gmail.connected",
                extra={"account": self._auth.account, "email": profile_email},
            )

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
            logger.warning("gmail.disconnect_error", extra={"error": str(exc)})
        finally:
            self._poller = None
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gmail-dispatch")
            self._service = None
            self._status = ConnectorStatus.DISCONNECTED
            logger.info("gmail.disconnected")

    def get_status(self) -> ConnectorStatus:
        return self._status

    # -- Discovery -----------------------------------------------------------

    def list_accounts(self) -> list[Account]:
        return [
            Account(
                account_id=self._auth.account,
                display_name=self._auth.account,
                connector=_CONNECTOR_NAME,
            )
        ]

    def list_targets(self, account_id: str) -> list[Target]:
        """Email targets are unbounded — returns empty list per design."""
        return []

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
        """Send a message or create a draft depending on delivery mode."""
        self._ensure_connected()

        encoded = build_message(conversation, content, from_address=self._auth.account)
        thread_id = conversation.opaque_id.get("thread_id", "")

        body: dict = {"raw": encoded}
        if thread_id:
            body["threadId"] = thread_id

        if self._delivery_mode == "ASSISTED":
            return self._create_draft(body)
        else:
            return self._send_message(body)

    # -- Durability ----------------------------------------------------------

    def backfill(self, account_id: str, scope: BackfillScope) -> None:
        """Retrieve historical messages matching the scope."""
        self._ensure_connected()

        if account_id != self._auth.account:
            raise ConnectorError(_CONNECTOR_NAME, f"account mismatch: {account_id}")

        # Build Gmail search query from scope
        query_parts: list[str] = []
        if scope.oldest:
            query_parts.append(f"after:{scope.oldest.strftime('%Y/%m/%d')}")
        if scope.latest:
            query_parts.append(f"before:{scope.latest.strftime('%Y/%m/%d')}")

        # Label filter
        for label in self._label_filter:
            query_parts.append(f"label:{label}")

        query = " ".join(query_parts) if query_parts else ""

        # Page through messages.list
        page_token: str | None = None
        all_message_ids: list[str] = []

        while True:
            kwargs: dict = {"userId": "me", "maxResults": 100}
            if query:
                kwargs["q"] = query
            if page_token:
                kwargs["pageToken"] = page_token

            response = call_with_retry(self._service.users().messages().list(**kwargs).execute)

            messages = response.get("messages", [])
            for msg in messages:
                all_message_ids.append(msg["id"])

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        # Process oldest-first (Gmail returns newest first)
        all_message_ids.reverse()

        # Fetch full messages and dispatch
        for msg_id in all_message_ids:
            try:
                full_msg = call_with_retry(
                    self._service.users().messages().get(userId="me", id=msg_id, format="full").execute
                )
                event = normalize_message(full_msg, self._auth.account)
                if event is not None:
                    self._dispatch_event(event)
            except Exception:
                logger.warning(
                    "gmail.backfill_error",
                    extra={"message_id": msg_id},
                )

    # -- Capability introspection --------------------------------------------

    def get_capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_realtime=False,
            supports_backfill=True,
            supports_threads=True,
            supports_reply=True,
            supports_auto_send=True,
            delivery_mode=self._delivery_mode,
        )

    # -- Connector-specific: attachment resolution ---------------------------

    def resolve_attachment(self, content_ref: str) -> bytes:
        """Fetch attachment bytes by composite ``content_ref``.

        The ``content_ref`` format is ``{message_id}:{attachment_id}``.

        Returns
        -------
        bytes
            Raw attachment data.
        """
        self._ensure_connected()

        parts = content_ref.split(":", 1)
        if len(parts) != 2:
            raise ConnectorError(_CONNECTOR_NAME, f"invalid content_ref format: {content_ref}")

        message_id, attachment_id = parts

        response = call_with_retry(
            self._service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute
        )

        data = response.get("data", "")
        return base64.urlsafe_b64decode(data)

    # -- Internal ------------------------------------------------------------

    def _on_new_messages(self, messages: list[dict]) -> None:
        """Callback from poller: normalise and dispatch to listeners."""
        for msg in messages:
            event = normalize_message(msg, self._auth.account)
            if event is not None:
                self._dispatch_event(event)

    def _dispatch_event(self, event) -> None:
        """Dispatch a MessageEvent to all registered listeners via thread pool."""
        with self._listeners_lock:
            listeners = list(self._listeners)

        for listener in listeners:
            self._executor.submit(self._safe_listener_call, listener, event)

    @staticmethod
    def _safe_listener_call(listener, event) -> None:
        """Invoke a listener, catching and logging any errors."""
        try:
            listener.on_message(event)
        except Exception:
            logger.exception(
                "gmail.listener_error",
                extra={"listener": type(listener).__name__, "message_id": event.message_id},
            )

    def _send_message(self, body: dict) -> SendReceipt:
        """Send a message via the Gmail API."""
        response = call_with_retry(self._service.users().messages().send(userId="me", body=body).execute)

        return SendReceipt(
            external_id=response.get("id", "accepted"),
            timestamp=datetime.now(UTC),
        )

    def _create_draft(self, body: dict) -> SendReceipt:
        """Create a draft via the Gmail API."""
        response = call_with_retry(self._service.users().drafts().create(userId="me", body={"message": body}).execute)

        draft_id = response.get("id", "")
        msg_id = response.get("message", {}).get("id", "")

        return SendReceipt(
            external_id=msg_id or draft_id or "draft_created",
            timestamp=datetime.now(UTC),
        )

    def _ensure_connected(self) -> None:
        """Raise if connector is not in CONNECTED state."""
        if self._status != ConnectorStatus.CONNECTED:
            raise NotSupported(
                _CONNECTOR_NAME,
                operation=f"not connected (status={self._status.value})",
            )
