"""Unit tests for the Teams message normalizer."""

from __future__ import annotations

from appif.adapters.teams._normalizer import normalize_message
from appif.domain.messaging.models import MessageEvent

_ME = "U_ME"


def _chat_message(**overrides) -> dict:
    msg = {
        "id": "msg-1",
        "messageType": "message",
        "createdDateTime": "2026-06-08T10:30:00Z",
        "from": {"user": {"id": "U_ALICE", "displayName": "Alice"}},
        "body": {"contentType": "text", "content": "hello there"},
    }
    msg.update(overrides)
    return msg


def _channel_message(**overrides) -> dict:
    msg = {
        "id": "msg-2",
        "messageType": "message",
        "createdDateTime": "2026-06-08T11:00:00Z",
        "from": {"user": {"id": "U_CAROL", "displayName": "Carol"}},
        "body": {"contentType": "html", "content": "<p>channel <b>hi</b></p>"},
        "channelIdentity": {"teamId": "T1", "channelId": "CH1"},
    }
    msg.update(overrides)
    return msg


class TestChatMessages:
    def test_basic_chat_message(self):
        ev = normalize_message(_chat_message(), account_id="acct", authenticated_user_id=_ME, chat_id="C1")
        assert isinstance(ev, MessageEvent)
        assert ev.connector == "teams"
        assert ev.account_id == "acct"
        assert ev.author.id == "U_ALICE"
        assert ev.author.display_name == "Alice"
        assert ev.content.text == "hello there"
        assert ev.conversation_ref.type == "chat"
        assert ev.conversation_ref.opaque_id == {"chat_id": "C1", "message_id": "msg-1"}

    def test_html_body_stripped(self):
        ev = normalize_message(
            _chat_message(body={"contentType": "html", "content": "<p>Hi <b>Bob</b></p>"}),
            account_id="a",
            authenticated_user_id=_ME,
            chat_id="C1",
        )
        assert "Hi" in ev.content.text
        assert "<p>" not in ev.content.text
        assert ev.metadata["html_body"] == "<p>Hi <b>Bob</b></p>"

    def test_empty_id_returns_none(self):
        assert normalize_message(_chat_message(id=""), account_id="a", authenticated_user_id=_ME, chat_id="C1") is None

    def test_system_message_skipped(self):
        msg = _chat_message(messageType="systemEventMessage")
        assert normalize_message(msg, account_id="a", authenticated_user_id=_ME, chat_id="C1") is None

    def test_deleted_message_skipped(self):
        msg = _chat_message(deletedDateTime="2026-06-08T10:31:00Z")
        assert normalize_message(msg, account_id="a", authenticated_user_id=_ME, chat_id="C1") is None

    def test_timestamp_parsed(self):
        ev = normalize_message(_chat_message(), account_id="a", authenticated_user_id=_ME, chat_id="C1")
        assert ev.timestamp.year == 2026
        assert ev.timestamp.tzinfo is not None


class TestSelfSuppression:
    def test_own_message_suppressed_by_default(self):
        msg = _chat_message(**{"from": {"user": {"id": _ME, "displayName": "Me"}}})
        assert normalize_message(msg, account_id="a", authenticated_user_id=_ME, chat_id="C1") is None

    def test_own_message_emitted_with_include_sent(self):
        msg = _chat_message(**{"from": {"user": {"id": _ME, "displayName": "Me"}}})
        ev = normalize_message(msg, account_id="a", authenticated_user_id=_ME, chat_id="C1", include_sent=True)
        assert ev is not None
        assert ev.author.id == _ME

    def test_other_user_always_emitted(self):
        ev = normalize_message(_chat_message(), account_id="a", authenticated_user_id=_ME, chat_id="C1")
        assert ev is not None


class TestChannelMessages:
    def test_channel_routing_from_channel_identity(self):
        ev = normalize_message(_channel_message(), account_id="acct", authenticated_user_id=_ME)
        assert ev.conversation_ref.type == "channel"
        assert ev.conversation_ref.opaque_id["team_id"] == "T1"
        assert ev.conversation_ref.opaque_id["channel_id"] == "CH1"
        # No replyToId -> message is its own root
        assert ev.conversation_ref.opaque_id["message_id"] == "msg-2"

    def test_channel_reply_uses_root_message_id(self):
        ev = normalize_message(_channel_message(replyToId="root-99"), account_id="a", authenticated_user_id=_ME)
        assert ev.conversation_ref.opaque_id["message_id"] == "root-99"

    def test_explicit_ids_take_precedence(self):
        msg = _channel_message()
        del msg["channelIdentity"]
        ev = normalize_message(msg, account_id="a", authenticated_user_id=_ME, team_id="T9", channel_id="CH9")
        assert ev.conversation_ref.opaque_id["team_id"] == "T9"
        assert ev.conversation_ref.opaque_id["channel_id"] == "CH9"


class TestMentionsAsRecipients:
    def test_no_mentions_empty(self):
        ev = normalize_message(_chat_message(), account_id="a", authenticated_user_id=_ME, chat_id="C1")
        assert ev.recipients.to == []

    def test_mentions_become_recipients(self):
        msg = _chat_message(
            mentions=[
                {"mentioned": {"user": {"id": "U1", "displayName": "One"}}},
                {"mentioned": {"user": {"id": "U2", "displayName": "Two"}}},
            ]
        )
        ev = normalize_message(msg, account_id="a", authenticated_user_id=_ME, chat_id="C1")
        assert [i.id for i in ev.recipients.to] == ["U1", "U2"]
        assert ev.recipients.to[0].display_name == "One"

    def test_mentions_deduped(self):
        msg = _chat_message(
            mentions=[
                {"mentioned": {"user": {"id": "U1", "displayName": "One"}}},
                {"mentioned": {"user": {"id": "U1", "displayName": "One"}}},
            ]
        )
        ev = normalize_message(msg, account_id="a", authenticated_user_id=_ME, chat_id="C1")
        assert [i.id for i in ev.recipients.to] == ["U1"]

    def test_non_user_mention_skipped(self):
        msg = _chat_message(mentions=[{"mentioned": {"conversation": {"id": "X"}}}])
        ev = normalize_message(msg, account_id="a", authenticated_user_id=_ME, chat_id="C1")
        assert ev.recipients.to == []


class TestAttachments:
    def test_attachment_metadata(self):
        msg = _chat_message(attachments=[{"id": "att1", "name": "doc.pdf", "contentType": "application/pdf"}])
        ev = normalize_message(msg, account_id="a", authenticated_user_id=_ME, chat_id="C1")
        assert len(ev.content.attachments) == 1
        att = ev.content.attachments[0]
        assert att.filename == "doc.pdf"
        assert att.content_ref == "att1"
