"""Unit tests for the Gmail poller module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_service(*, profile=None, history=None, messages=None):
    """Build a mock Gmail API service with chainable methods."""
    service = MagicMock()

    # users().getProfile(userId="me").execute()
    profile_data = profile or {"historyId": "12345", "emailAddress": "user@gmail.com"}
    service.users().getProfile.return_value.execute.return_value = profile_data

    # users().history().list(...).execute()
    history_data = history or {"historyId": "12346", "history": []}
    service.users().history().list.return_value.execute.return_value = history_data

    # users().messages().get(...).execute()
    if messages:
        service.users().messages().get.return_value.execute.side_effect = messages
    else:
        service.users().messages().get.return_value.execute.return_value = {
            "id": "msg1",
            "threadId": "t1",
            "payload": {"mimeType": "text/plain", "headers": [], "body": {"data": ""}},
        }

    return service


class TestGmailPollerStart:
    """Tests for poller startup."""

    def test_start_seeds_history_id(self):
        from appif.adapters.gmail._poller import GmailPoller

        service = _make_service(profile={"historyId": "99999"})
        callback = MagicMock()

        with patch("appif.adapters.gmail._poller.call_with_retry", side_effect=lambda fn: fn()):
            poller = GmailPoller(
                service=service,
                account_id="user@gmail.com",
                poll_interval=60,
                on_new_messages=callback,
            )
            # Manually call _seed_history_id
            poller._seed_history_id()

        assert poller._history_id == 99999

    def test_start_launches_thread(self):
        from appif.adapters.gmail._poller import GmailPoller

        service = _make_service()
        callback = MagicMock()

        with patch("appif.adapters.gmail._poller.call_with_retry", side_effect=lambda fn: fn()):
            poller = GmailPoller(
                service=service,
                account_id="user@gmail.com",
                poll_interval=60,
                on_new_messages=callback,
            )
            poller.start()

        assert poller._thread is not None
        assert poller._thread.is_alive()

        poller.stop()
        assert poller._thread is None


class TestGmailPollerPollCycle:
    """Tests for individual poll cycles."""

    def test_normal_cycle_fetches_new_messages(self):
        from appif.adapters.gmail._poller import GmailPoller

        history_response = {
            "historyId": "12347",
            "history": [
                {
                    "messagesAdded": [
                        {"message": {"id": "msg_new_1"}},
                        {"message": {"id": "msg_new_2"}},
                    ]
                }
            ],
        }

        full_msg_1 = {
            "id": "msg_new_1",
            "threadId": "t1",
            "payload": {"mimeType": "text/plain", "headers": [], "body": {}},
        }
        full_msg_2 = {
            "id": "msg_new_2",
            "threadId": "t2",
            "payload": {"mimeType": "text/plain", "headers": [], "body": {}},
        }

        service = _make_service(history=history_response, messages=[full_msg_1, full_msg_2])
        callback = MagicMock()

        with patch("appif.adapters.gmail._poller.call_with_retry", side_effect=lambda fn: fn()):
            poller = GmailPoller(
                service=service,
                account_id="user@gmail.com",
                poll_interval=60,
                on_new_messages=callback,
            )
            poller._history_id = 12345
            poller._poll_cycle()

        callback.assert_called_once()
        messages = callback.call_args[0][0]
        assert len(messages) == 2

    def test_empty_cycle_no_callback(self):
        from appif.adapters.gmail._poller import GmailPoller

        history_response = {"historyId": "12346", "history": []}
        service = _make_service(history=history_response)
        callback = MagicMock()

        with patch("appif.adapters.gmail._poller.call_with_retry", side_effect=lambda fn: fn()):
            poller = GmailPoller(
                service=service,
                account_id="user@gmail.com",
                poll_interval=60,
                on_new_messages=callback,
            )
            poller._history_id = 12345
            poller._poll_cycle()

        callback.assert_not_called()

    def test_expired_history_resets(self):
        from appif.adapters.gmail._poller import GmailPoller
        from appif.domain.messaging.errors import TargetUnavailable

        service = _make_service(profile={"historyId": "99999"})
        callback = MagicMock()

        call_count = 0

        def mock_retry(fn):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call is history.list — simulate 404
                raise TargetUnavailable("gmail", target="history", reason="expired")
            return fn()

        with patch("appif.adapters.gmail._poller.call_with_retry", side_effect=mock_retry):
            poller = GmailPoller(
                service=service,
                account_id="user@gmail.com",
                poll_interval=60,
                on_new_messages=callback,
            )
            poller._history_id = 100
            poller._poll_cycle()

        # History ID should be reset to profile value
        assert poller._history_id == 99999

    def test_deduplicates_message_ids(self):
        from appif.adapters.gmail._poller import GmailPoller

        # Same message appears in two history records
        history_response = {
            "historyId": "12347",
            "history": [
                {"messagesAdded": [{"message": {"id": "msg_dup"}}]},
                {"messagesAdded": [{"message": {"id": "msg_dup"}}]},
            ],
        }

        full_msg = {"id": "msg_dup", "threadId": "t1", "payload": {"mimeType": "text/plain", "headers": [], "body": {}}}
        service = _make_service(history=history_response, messages=[full_msg])
        callback = MagicMock()

        with patch("appif.adapters.gmail._poller.call_with_retry", side_effect=lambda fn: fn()):
            poller = GmailPoller(
                service=service,
                account_id="user@gmail.com",
                poll_interval=60,
                on_new_messages=callback,
            )
            poller._history_id = 12345
            poller._poll_cycle()

        callback.assert_called_once()
        messages = callback.call_args[0][0]
        assert len(messages) == 1


class TestGmailPollerStop:
    """Tests for poller shutdown."""

    def test_stop_sets_event_and_clears_thread(self):
        from appif.adapters.gmail._poller import GmailPoller

        service = _make_service()
        callback = MagicMock()

        with patch("appif.adapters.gmail._poller.call_with_retry", side_effect=lambda fn: fn()):
            poller = GmailPoller(
                service=service,
                account_id="user@gmail.com",
                poll_interval=60,
                on_new_messages=callback,
            )
            poller.start()
            poller.stop()

        assert poller._thread is None
        assert poller._stop_event.is_set()

    def test_stop_without_start_is_safe(self):
        from appif.adapters.gmail._poller import GmailPoller

        service = _make_service()
        callback = MagicMock()

        poller = GmailPoller(
            service=service,
            account_id="user@gmail.com",
            poll_interval=60,
            on_new_messages=callback,
        )
        poller.stop()  # Should not raise


class TestGmailPollerPagination:
    """Tests for paginated history responses."""

    def test_paginated_history(self):
        from appif.adapters.gmail._poller import GmailPoller

        page1 = {
            "historyId": "12347",
            "history": [{"messagesAdded": [{"message": {"id": "msg_p1"}}]}],
            "nextPageToken": "page2_token",
        }
        page2 = {
            "historyId": "12348",
            "history": [{"messagesAdded": [{"message": {"id": "msg_p2"}}]}],
        }

        service = _make_service()
        # Override history().list to return pages in sequence
        service.users().history().list.return_value.execute.side_effect = [page1, page2]

        full_msg1 = {"id": "msg_p1", "threadId": "t1", "payload": {"mimeType": "text/plain", "headers": [], "body": {}}}
        full_msg2 = {"id": "msg_p2", "threadId": "t2", "payload": {"mimeType": "text/plain", "headers": [], "body": {}}}
        service.users().messages().get.return_value.execute.side_effect = [full_msg1, full_msg2]

        callback = MagicMock()

        with patch("appif.adapters.gmail._poller.call_with_retry", side_effect=lambda fn: fn()):
            poller = GmailPoller(
                service=service,
                account_id="user@gmail.com",
                poll_interval=60,
                on_new_messages=callback,
            )
            poller._history_id = 12345
            poller._poll_cycle()

        callback.assert_called_once()
        messages = callback.call_args[0][0]
        assert len(messages) == 2
