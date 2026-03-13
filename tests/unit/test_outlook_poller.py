"""Unit tests for the Outlook poller module."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from appif.adapters.outlook._poller import OutlookPoller


def _make_poller(*, callback=None, folders=None, poll_interval=1, sent_ids=None):
    """Create an OutlookPoller with test defaults."""
    return OutlookPoller(
        access_token_fn=lambda: "test-token",
        account_id="test-account",
        folders=folders or ["Inbox"],
        poll_interval=poll_interval,
        callback=callback or MagicMock(),
        sent_ids=sent_ids or set(),
    )


class TestPollerLifecycle:
    """Tests for start/stop lifecycle."""

    def test_start_creates_daemon_thread(self):
        poller = _make_poller(poll_interval=60)

        with patch.object(poller, "_poll_loop"):
            poller.start()
            assert poller._thread is not None
            assert poller._thread.daemon is True
            poller.stop()

    def test_stop_cleans_up_thread(self):
        poller = _make_poller(poll_interval=60)

        with patch.object(poller, "_poll_loop"):
            poller.start()
            poller.stop()
            assert poller._thread is None

    def test_stop_is_idempotent(self):
        """Calling stop without start doesn't raise."""
        poller = _make_poller()
        poller.stop()  # Should not raise

    def test_start_is_idempotent(self):
        """Starting an already-started poller doesn't create a second thread."""
        poller = _make_poller(poll_interval=60)

        # Use an Event to keep the thread alive during the test
        keep_alive = threading.Event()

        def blocking_poll_loop():
            keep_alive.wait()

        with patch.object(poller, "_poll_loop", blocking_poll_loop):
            poller.start()
            first_thread = poller._thread
            poller.start()
            assert poller._thread is first_thread
            keep_alive.set()
            poller.stop()


class TestPollerDeltaQueries:
    """Tests for delta query polling."""

    @patch("appif.adapters.outlook._poller.httpx.get")
    def test_initial_delta_request(self, mock_get):
        """First poll sends initial delta request."""
        callback = MagicMock()
        poller = _make_poller(callback=callback)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_success = True
        mock_response.json.return_value = {
            "value": [
                {
                    "id": "msg1",
                    "from": {"emailAddress": {"name": "Alice", "address": "alice@test.com"}},
                    "subject": "Hello",
                    "body": {"contentType": "text", "content": "Hi there"},
                    "conversationId": "conv1",
                    "receivedDateTime": "2026-02-21T10:00:00Z",
                    "parentFolderId": "folder1",
                    "attachments": [],
                }
            ],
            "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc",
        }
        mock_get.return_value = mock_response

        poller._poll_folder("Inbox")

        # Callback should be invoked with the normalised message
        assert callback.call_count == 1
        event = callback.call_args[0][0]
        assert event.message_id == "msg1"

        # Delta link should be stored
        assert "Inbox" in poller._delta_links

    @patch("appif.adapters.outlook._poller.httpx.get")
    def test_410_gone_resets_delta_link(self, mock_get):
        """410 response resets delta link and re-syncs."""
        poller = _make_poller()
        poller._delta_links["Inbox"] = "https://old-delta-link"

        # First call returns 410, second call returns fresh data
        gone_response = MagicMock()
        gone_response.status_code = 410
        gone_response.is_success = False

        fresh_response = MagicMock()
        fresh_response.status_code = 200
        fresh_response.is_success = True
        fresh_response.json.return_value = {
            "value": [],
            "@odata.deltaLink": "https://new-delta-link",
        }

        mock_get.side_effect = [gone_response, fresh_response]

        poller._poll_folder("Inbox")

        # Delta link should be updated to the new one
        assert poller._delta_links.get("Inbox") == "https://new-delta-link"

    @patch("appif.adapters.outlook._poller.httpx.get")
    def test_echo_suppression_skips_sent_messages(self, mock_get):
        """Messages in sent_ids are not dispatched to callback."""
        callback = MagicMock()
        sent_ids = {"msg_sent_by_us"}
        poller = _make_poller(callback=callback, sent_ids=sent_ids)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_success = True
        mock_response.json.return_value = {
            "value": [
                {
                    "id": "msg_sent_by_us",
                    "from": {"emailAddress": {"name": "Me", "address": "me@test.com"}},
                    "subject": "My message",
                    "body": {"contentType": "text", "content": "Sent by me"},
                    "conversationId": "conv1",
                    "receivedDateTime": "2026-02-21T10:00:00Z",
                    "parentFolderId": "folder1",
                    "attachments": [],
                }
            ],
            "@odata.deltaLink": "https://delta",
        }
        mock_get.return_value = mock_response

        poller._poll_folder("Inbox")

        # Callback should NOT be invoked (echo suppressed)
        callback.assert_not_called()

    @patch("appif.adapters.outlook._poller.httpx.get")
    def test_removed_entries_skipped(self, mock_get):
        """Delta entries with @removed are not dispatched."""
        callback = MagicMock()
        poller = _make_poller(callback=callback)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_success = True
        mock_response.json.return_value = {
            "value": [
                {"id": "deleted_msg", "@removed": {"reason": "deleted"}},
            ],
            "@odata.deltaLink": "https://delta",
        }
        mock_get.return_value = mock_response

        poller._poll_folder("Inbox")
        callback.assert_not_called()
