"""Unit tests for the Gmail message builder module."""

from __future__ import annotations

import base64
import email
from email.policy import default as default_policy

import pytest

from appif.adapters.gmail._message_builder import build_message
from appif.domain.messaging.errors import ConnectorError
from appif.domain.messaging.models import Attachment, ConversationRef, MessageContent


def _make_conversation(
    *,
    thread_id: str = "",
    message_id: str = "",
    subject: str = "",
    to: str = "",
    in_reply_to: str = "",
    references: str = "",
) -> ConversationRef:
    return ConversationRef(
        connector="gmail",
        account_id="user@gmail.com",
        type="email_thread",
        opaque_id={
            "thread_id": thread_id,
            "message_id": message_id,
            "subject": subject,
            "to": to,
            "in_reply_to": in_reply_to,
            "references": references,
        },
    )


def _decode_raw(raw: str) -> email.message.EmailMessage:
    """Decode base64url raw message to EmailMessage."""
    raw_bytes = base64.urlsafe_b64decode(raw)
    return email.message_from_bytes(raw_bytes, policy=default_policy)


class TestBuildMessageReply:
    """Tests for reply message construction."""

    def test_reply_has_threading_headers(self):
        conv = _make_conversation(
            thread_id="t1",
            message_id="m1",
            subject="Original Subject",
            to="sender@example.com",
            in_reply_to="<original@example.com>",
            references="<ref1@example.com>",
        )
        content = MessageContent(text="My reply")

        raw = build_message(conv, content, from_address="user@gmail.com")
        msg = _decode_raw(raw)

        assert msg["In-Reply-To"] == "<original@example.com>"
        assert "<ref1@example.com>" in msg["References"]
        assert "<original@example.com>" in msg["References"]
        assert msg["Subject"] == "Re: Original Subject"
        assert msg["To"] == "sender@example.com"
        assert msg["From"] == "user@gmail.com"

    def test_reply_subject_not_double_prefixed(self):
        conv = _make_conversation(
            thread_id="t1",
            message_id="m1",
            subject="Re: Already prefixed",
            to="sender@example.com",
        )
        content = MessageContent(text="Reply text")

        raw = build_message(conv, content, from_address="user@gmail.com")
        msg = _decode_raw(raw)

        assert msg["Subject"] == "Re: Already prefixed"
        assert not msg["Subject"].startswith("Re: Re:")

    def test_reply_body_in_output(self):
        conv = _make_conversation(thread_id="t1", message_id="m1", to="a@b.com")
        content = MessageContent(text="Reply body here")

        raw = build_message(conv, content, from_address="user@gmail.com")
        msg = _decode_raw(raw)

        body = msg.get_body(preferencelist=("plain",))
        assert "Reply body here" in body.get_content()


class TestBuildMessageNewThread:
    """Tests for new thread message construction."""

    def test_new_thread_with_subject(self):
        conv = _make_conversation(to="recipient@example.com", subject="New Thread")
        content = MessageContent(text="Hello!")

        raw = build_message(conv, content, from_address="user@gmail.com")
        msg = _decode_raw(raw)

        assert msg["Subject"] == "New Thread"
        assert msg["To"] == "recipient@example.com"
        assert msg["From"] == "user@gmail.com"
        assert "In-Reply-To" not in msg
        assert "References" not in msg

    def test_new_thread_missing_subject_raises(self):
        conv = _make_conversation(to="recipient@example.com")
        content = MessageContent(text="Hello!")

        with pytest.raises(ConnectorError, match="subject required"):
            build_message(conv, content, from_address="user@gmail.com")

    def test_new_thread_missing_recipient_raises(self):
        conv = _make_conversation(subject="Test")  # no "to"
        content = MessageContent(text="Hello!")

        with pytest.raises(ConnectorError, match="recipient address required"):
            build_message(conv, content, from_address="user@gmail.com")


class TestBuildMessageOutput:
    """Tests for output format."""

    def test_output_is_valid_base64url(self):
        conv = _make_conversation(to="a@b.com", subject="Test")
        content = MessageContent(text="Test")

        raw = build_message(conv, content, from_address="user@gmail.com")

        # Should decode without error
        decoded = base64.urlsafe_b64decode(raw + "==")  # padding tolerance
        assert len(decoded) > 0

    def test_unicode_body_encoded(self):
        conv = _make_conversation(to="a@b.com", subject="Unicode")
        content = MessageContent(text="Héllo wörld 你好")

        raw = build_message(conv, content, from_address="user@gmail.com")
        msg = _decode_raw(raw)

        body = msg.get_body(preferencelist=("plain",))
        assert "Héllo wörld 你好" in body.get_content()

    def test_message_with_attachment(self):
        conv = _make_conversation(to="a@b.com", subject="With Attachment")
        att = Attachment(
            filename="test.txt",
            content_type="text/plain",
            size_bytes=11,
            data=b"hello world",
        )
        content = MessageContent(
            text="See attached",
            attachments=[att],
        )

        raw = build_message(conv, content, from_address="user@gmail.com")
        msg = _decode_raw(raw)

        # Should be multipart
        assert msg.is_multipart()
        parts = list(msg.iter_attachments())
        assert len(parts) == 1
        assert parts[0].get_filename() == "test.txt"

    def test_size_limit_enforced(self):
        conv = _make_conversation(to="a@b.com", subject="Too Big")
        # Create content that exceeds 25MB
        large_data = b"x" * (26 * 1024 * 1024)
        att = Attachment(
            filename="huge.bin",
            content_type="application/octet-stream",
            data=large_data,
        )
        content = MessageContent(
            text="Big file",
            attachments=[att],
        )

        with pytest.raises(ConnectorError, match="25 MB"):
            build_message(conv, content, from_address="user@gmail.com")

    def test_attachment_without_data_skipped(self):
        conv = _make_conversation(to="a@b.com", subject="Lazy Attachment")
        att = Attachment(
            filename="lazy.pdf",
            content_type="application/pdf",
            content_ref="msg1:att1",
            data=None,  # Not downloaded
        )
        content = MessageContent(
            text="See attached",
            attachments=[att],
        )

        raw = build_message(conv, content, from_address="user@gmail.com")
        msg = _decode_raw(raw)

        # Should still produce a valid message (attachment skipped)
        body = msg.get_body(preferencelist=("plain",))
        assert "See attached" in body.get_content()
