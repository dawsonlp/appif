"""Delta-query polling loop for inbound Outlook messages.

Runs in a daemon thread, periodically requesting delta changes from the
Graph API for each configured mail folder. New messages are normalised
and dispatched to the registered callback.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import httpx

from appif.adapters.outlook._normalizer import normalize_message
from appif.domain.messaging.models import MessageEvent

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "outlook"

# Graph API base URL
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class OutlookPoller:
    """Polls Microsoft Graph delta queries for new mail messages.

    Parameters
    ----------
    access_token_fn:
        Callable returning a current access token string.
    account_id:
        Logical account label for normalised events.
    folders:
        List of well-known folder names to poll (e.g. ``["Inbox"]``).
    poll_interval:
        Seconds between poll cycles.
    callback:
        Function to invoke for each normalised ``MessageEvent``.
    sent_ids:
        Shared set of message IDs sent by the connector (echo suppression).
    """

    def __init__(
        self,
        *,
        access_token_fn: Callable[[], str],
        account_id: str,
        folders: list[str],
        poll_interval: int = 30,
        callback: Callable[[MessageEvent], None],
        sent_ids: set[str],
    ) -> None:
        self._access_token_fn = access_token_fn
        self._account_id = account_id
        self._folders = folders or ["Inbox"]
        self._poll_interval = poll_interval
        self._callback = callback
        self._sent_ids = sent_ids

        # State
        self._delta_links: dict[str, str] = {}  # folder → deltaLink (volatile)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Launch the polling loop in a daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="outlook-poller",
            daemon=True,
        )
        self._thread.start()
        logger.info("outlook.poller.started", extra={"folders": self._folders, "interval": self._poll_interval})

    def stop(self) -> None:
        """Signal the polling thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval * 2)
            self._thread = None
        logger.info("outlook.poller.stopped")

    def _poll_loop(self) -> None:
        """Main polling loop — runs until stop_event is set."""
        while not self._stop_event.is_set():
            try:
                self._poll_all_folders()
            except Exception:
                logger.exception("outlook.poller.cycle_error")

            # Interruptible sleep
            self._stop_event.wait(timeout=self._poll_interval)

    def _poll_all_folders(self) -> None:
        """Poll each configured folder for delta changes."""
        for folder in self._folders:
            try:
                self._poll_folder(folder)
            except Exception:
                logger.exception("outlook.poller.folder_error", extra={"folder": folder})

    def _poll_folder(self, folder: str) -> None:
        """Poll a single folder using delta queries."""
        token = self._access_token_fn()
        headers = {"Authorization": f"Bearer {token}"}

        delta_link = self._delta_links.get(folder)

        if delta_link:
            url = delta_link
        else:
            # Initial delta request
            url = (
                f"{_GRAPH_BASE}/me/mailFolders/{folder}/messages/delta"
                "?$select=id,from,subject,body,conversationId,receivedDateTime,"
                "parentFolderId,hasAttachments,attachments"
            )

        messages = []
        next_delta_link = None

        while url:
            try:
                response = httpx.get(url, headers=headers, timeout=30.0)
            except httpx.HTTPError as exc:
                logger.warning("outlook.poller.http_error", extra={"folder": folder, "error": str(exc)})
                return

            if response.status_code == 410:
                # Delta link expired — reset and do full sync
                logger.info("outlook.poller.delta_expired", extra={"folder": folder})
                self._delta_links.pop(folder, None)
                self._poll_folder(folder)
                return

            if response.status_code == 401:
                logger.warning("outlook.poller.auth_expired", extra={"folder": folder})
                return

            if not response.is_success:
                logger.warning(
                    "outlook.poller.unexpected_status",
                    extra={"folder": folder, "status": response.status_code},
                )
                return

            data = response.json()

            # Collect messages from this page
            for msg in data.get("value", []):
                messages.append(msg)

            # Follow pagination
            url = data.get("@odata.nextLink")
            if not url:
                next_delta_link = data.get("@odata.deltaLink")

        # Store the new delta link
        if next_delta_link:
            self._delta_links[folder] = next_delta_link

        # Normalise and dispatch
        for msg in messages:
            # Skip removed entries (delta returns @removed for deleted messages)
            if "@removed" in msg:
                continue

            event = normalize_message(
                msg,
                account_id=self._account_id,
                sent_ids=self._sent_ids,
            )
            if event is not None:
                try:
                    self._callback(event)
                except Exception:
                    logger.exception(
                        "outlook.poller.callback_error",
                        extra={"message_id": event.message_id},
                    )
