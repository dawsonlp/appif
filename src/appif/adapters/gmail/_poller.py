"""History-ID polling loop for inbound Gmail messages.

Runs in a daemon thread, periodically requesting history changes from
the Gmail API. New messages are fetched in full and dispatched to the
registered callback for normalisation and listener delivery.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from appif.adapters._base import BasePoller
from appif.adapters.gmail._rate_limiter import call_with_retry

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "gmail"


class GmailPoller(BasePoller):
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
        super().__init__(poll_interval)
        self._service = service
        self._account_id = account_id
        self._label_filter = label_filter or ["INBOX"]
        self._on_new_messages = on_new_messages
        self._history_id: int | None = None

    connector_name = _CONNECTOR_NAME

    def _on_start(self) -> None:
        # Seed the history ID from the profile before the loop starts.
        self._seed_history_id()

    def _start_log_extra(self) -> dict:
        return {"labels": self._label_filter, "interval": self._poll_interval, "history_id": self._history_id}

    # ── Internal ──────────────────────────────────────────────

    def _seed_history_id(self) -> None:
        """Get the current history ID from the user's profile."""
        profile = call_with_retry(self._service.users().getProfile(userId="me").execute)
        self._history_id = int(profile["historyId"])

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
