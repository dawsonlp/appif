"""History-ID polling loop for inbound Gmail messages.

Runs in a daemon thread, periodically requesting history changes from
the Gmail API. New messages are fetched in full and dispatched to the
registered callback for normalisation and listener delivery.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from appif.adapters.gmail._rate_limiter import call_with_retry

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "gmail"


class GmailPoller:
    """Polls Gmail history API for new messages.

    Parameters
    ----------
    service:
        An authorised ``googleapiclient`` Gmail service resource.
    account_id:
        The authenticated mailbox address.
    label_filter:
        Label IDs to watch (default: ``["INBOX"]``).
    poll_interval:
        Seconds between poll cycles.
    on_new_messages:
        Callback invoked with a list of full message dicts.
    """

    def __init__(
        self,
        *,
        service,
        account_id: str,
        label_filter: list[str] | None = None,
        poll_interval: int = 30,
        on_new_messages: Callable[[list[dict]], None],
    ) -> None:
        self._service = service
        self._account_id = account_id
        self._label_filter = label_filter or ["INBOX"]
        self._poll_interval = poll_interval
        self._on_new_messages = on_new_messages

        # State
        self._history_id: int | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Seed the history ID and launch the polling daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return

        # Seed history ID from profile
        self._seed_history_id()

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="gmail-poller",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "gmail.poller.started",
            extra={
                "labels": self._label_filter,
                "interval": self._poll_interval,
                "history_id": self._history_id,
            },
        )

    def stop(self) -> None:
        """Signal the polling thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval * 2)
            self._thread = None
        logger.info("gmail.poller.stopped")

    # ── Internal ──────────────────────────────────────────────

    def _seed_history_id(self) -> None:
        """Get the current history ID from the user's profile."""
        profile = call_with_retry(self._service.users().getProfile(userId="me").execute)
        self._history_id = int(profile["historyId"])

    def _poll_loop(self) -> None:
        """Main polling loop — runs until stop_event is set."""
        while not self._stop_event.is_set():
            try:
                self._poll_cycle()
            except Exception:
                logger.exception("gmail.poller.cycle_error")

            # Interruptible sleep
            self._stop_event.wait(timeout=self._poll_interval)

    def _poll_cycle(self) -> None:
        """Execute a single poll cycle using history.list."""
        if self._history_id is None:
            self._seed_history_id()
            return

        from appif.domain.messaging.errors import TargetUnavailable

        new_message_ids: list[str] = []

        for label_id in self._label_filter:
            try:
                ids = self._fetch_history_for_label(label_id)
                new_message_ids.extend(ids)
            except TargetUnavailable:
                # 404 — history ID expired, reset
                logger.warning(
                    "gmail.poller.history_expired",
                    extra={"label": label_id, "history_id": self._history_id},
                )
                self._seed_history_id()
                return
            except Exception:
                logger.exception(
                    "gmail.poller.label_error",
                    extra={"label": label_id},
                )

        if not new_message_ids:
            return

        # Deduplicate (same message may appear from multiple labels)
        unique_ids = list(dict.fromkeys(new_message_ids))

        # Fetch full messages
        messages: list[dict] = []
        for msg_id in unique_ids:
            try:
                full_msg = call_with_retry(
                    self._service.users().messages().get(userId="me", id=msg_id, format="full").execute
                )
                messages.append(full_msg)
            except Exception:
                logger.warning(
                    "gmail.poller.message_fetch_error",
                    extra={"message_id": msg_id},
                )

        if messages:
            try:
                self._on_new_messages(messages)
            except Exception:
                logger.exception("gmail.poller.callback_error")

    def _fetch_history_for_label(self, label_id: str) -> list[str]:
        """Fetch new message IDs from history.list for a single label.

        Returns
        -------
        list[str]
            Gmail message IDs of newly added messages.
        """
        message_ids: list[str] = []
        page_token: str | None = None
        latest_history_id: int | None = None

        while True:
            kwargs: dict = {
                "userId": "me",
                "startHistoryId": self._history_id,
                "historyTypes": ["messageAdded"],
                "labelId": label_id,
            }
            if page_token:
                kwargs["pageToken"] = page_token

            response = call_with_retry(self._service.users().history().list(**kwargs).execute)

            # Collect message IDs from messagesAdded
            for record in response.get("history", []):
                for added in record.get("messagesAdded", []):
                    msg = added.get("message", {})
                    msg_id = msg.get("id")
                    if msg_id:
                        message_ids.append(msg_id)

            # Track latest history ID
            resp_history_id = response.get("historyId")
            if resp_history_id:
                latest_history_id = int(resp_history_id)

            # Pagination
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        # Update history ID to latest from response
        if latest_history_id is not None:
            self._history_id = latest_history_id

        return message_ids
