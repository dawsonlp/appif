"""Unit tests for the Slack message normalizer."""

from __future__ import annotations

from appif.adapters.slack._normalizer import normalize_message
from appif.domain.messaging.models import Identity

# Shared fixtures ----------------------------------------------------------

_TEAM_ID = "T_TEAM"
_AUTH_UID = "U_AUTH"
_OTHER_UID = "U_OTHER"


def _fake_resolve(user_id: str) -> Identity:
    return Identity(id=user_id, display_name=f"display-{user_id}", connector="slack")


def _base_event(**overrides) -> dict:
    base = {
        "type": "message",
        "user": _OTHER_UID,
        "text": "hello",
        "ts": "1700000000.000001",
        "channel": "C_CHAN",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------


class TestBasicMapping:
    """Happy-path field mapping."""

    def test_connector_is_slack(self):
        msg = normalize_message(
            _base_event(),
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is not None
        assert msg.connector == "slack"

    def test_account_id(self):
        msg = normalize_message(
            _base_event(),
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is not None
        assert msg.account_id == _TEAM_ID

    def test_text(self):
        msg = normalize_message(
            _base_event(text="hey!"),
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is not None
        assert msg.content.text == "hey!"

    def test_author_display_name(self):
        msg = normalize_message(
            _base_event(),
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is not None
        assert msg.author.display_name == f"display-{_OTHER_UID}"

    def test_author_id(self):
        msg = normalize_message(
            _base_event(),
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is not None
        assert msg.author.id == _OTHER_UID

    def test_message_id_is_ts(self):
        msg = normalize_message(
            _base_event(),
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is not None
        assert msg.message_id == "1700000000.000001"

    def test_conversation_ref_channel(self):
        msg = normalize_message(
            _base_event(),
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is not None
        assert msg.conversation_ref.opaque_id["channel"] == "C_CHAN"

    def test_metadata_contains_raw_event(self):
        event = _base_event()
        msg = normalize_message(
            event,
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is not None
        assert msg.metadata == event


class TestSelfMessageFiltering:
    """Messages from the authenticated identity must be skipped."""

    def test_message_from_other_user_returned(self):
        msg = normalize_message(
            _base_event(),
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is not None

    def test_message_from_authenticated_bot_skipped(self):
        msg = normalize_message(
            _base_event(user=_AUTH_UID),
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is None

    def test_message_from_authenticated_user_skipped(self):
        """User-token identity: message from the authenticated user is skipped."""
        user_uid = "U_HUMAN_SELF"
        msg = normalize_message(
            _base_event(user=user_uid),
            team_id=_TEAM_ID,
            authenticated_user_id=user_uid,
            resolve_user=_fake_resolve,
        )
        assert msg is None


class TestThreading:
    """Conversation ref type and thread_ts handling."""

    def test_top_level_message_type_is_channel(self):
        msg = normalize_message(
            _base_event(),
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is not None
        assert msg.conversation_ref.type == "channel"
        assert "thread_ts" not in msg.conversation_ref.opaque_id

    def test_threaded_message_type_is_thread(self):
        msg = normalize_message(
            _base_event(thread_ts="1700000000.000000"),
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is not None
        assert msg.conversation_ref.type == "thread"
        assert msg.conversation_ref.opaque_id["thread_ts"] == "1700000000.000000"


class TestTimestamp:
    """Timestamp parsing edge cases."""

    def test_missing_ts_falls_back_to_now(self):
        msg = normalize_message(
            _base_event(ts=""),
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is not None
        assert msg.timestamp is not None

    def test_valid_ts_parsed(self):
        msg = normalize_message(
            _base_event(),
            team_id=_TEAM_ID,
            authenticated_user_id=_AUTH_UID,
            resolve_user=_fake_resolve,
        )
        assert msg is not None
        assert msg.timestamp.year == 2023
