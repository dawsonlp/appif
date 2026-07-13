"""Unit tests for the Teams outbound message builder."""

from __future__ import annotations

import pytest

from appif.adapters.teams._message_builder import build_message
from appif.domain.messaging.errors import NotSupported
from appif.domain.messaging.models import ConversationRef, MessageContent


def _ref(type_: str, opaque: dict) -> ConversationRef:
    return ConversationRef(connector="teams", account_id="acct", type=type_, opaque_id=opaque)


def test_chat_message_path_and_body():
    path, body = build_message(_ref("chat", {"chat_id": "C1"}), MessageContent(text="hi"))
    assert path == "/chats/C1/messages"
    assert body == {"body": {"contentType": "text", "content": "hi"}}


def test_new_channel_message_path():
    path, _ = build_message(_ref("channel", {"team_id": "T1", "channel_id": "CH1"}), MessageContent(text="x"))
    assert path == "/teams/T1/channels/CH1/messages"


def test_channel_reply_path():
    path, _ = build_message(
        _ref("channel", {"team_id": "T1", "channel_id": "CH1", "message_id": "root9"}),
        MessageContent(text="x"),
    )
    assert path == "/teams/T1/channels/CH1/messages/root9/replies"


def test_chat_without_id_raises():
    with pytest.raises(NotSupported):
        build_message(_ref("chat", {}), MessageContent(text="x"))


def test_channel_without_ids_raises():
    with pytest.raises(NotSupported):
        build_message(_ref("channel", {"team_id": "T1"}), MessageContent(text="x"))


def test_unknown_type_raises():
    with pytest.raises(NotSupported):
        build_message(_ref("email_thread", {}), MessageContent(text="x"))
