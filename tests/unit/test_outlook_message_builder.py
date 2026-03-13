"""Unit tests for the Outlook message builder module."""

from __future__ import annotations

import base64

from appif.adapters.outlook._message_builder import build_attachment_payload, build_message
from appif.domain.messaging.models import Attachment, ConversationRef, MessageContent


class TestBuildMessage:
    """Tests for build_message."""

    def test_new_thread_payload_shape(self):
        """New thread → sendMail payload with subject and toRecipients."""
        conversation = ConversationRef(
            connector="outlook",
            account_id="test",
            type="email_thread",
            opaque_id={"recipient": "bob@example.com"},
        )
        content = MessageContent(text="Hello Bob")

        payload = build_message(conversation, content, subject="Greetings")

        assert payload["_route"] == "send_mail"
        msg = payload["message"]
        assert msg["subject"] == "Greetings"
        assert msg["body"]["contentType"] == "text"
        assert msg["body"]["content"] == "Hello Bob"
        assert msg["toRecipients"][0]["emailAddress"]["address"] == "bob@example.com"

    def test_new_thread_default_subject(self):
        """New thread without subject → 'No Subject'."""
        conversation = ConversationRef(
            connector="outlook",
            account_id="test",
            type="email_thread",
            opaque_id={"recipient": "bob@example.com"},
        )
        content = MessageContent(text="Hello")

        payload = build_message(conversation, content)
        assert payload["message"]["subject"] == "No Subject"

    def test_reply_payload_shape(self):
        """Reply → payload with comment and _parent_message_id."""
        conversation = ConversationRef(
            connector="outlook",
            account_id="test",
            type="email_thread",
            opaque_id={"message_id": "AAMkAGI2=", "conversation_id": "conv123"},
        )
        content = MessageContent(text="Thanks for your message")

        payload = build_message(conversation, content)

        assert payload["_route"] == "reply"
        assert payload["_parent_message_id"] == "AAMkAGI2="
        assert payload["comment"] == "Thanks for your message"

    def test_new_thread_with_inline_attachments(self):
        """New thread with small attachment → inline in payload."""
        small_data = b"hello" * 100  # Well under 4 MB
        conversation = ConversationRef(
            connector="outlook",
            account_id="test",
            type="email_thread",
            opaque_id={"recipient": "bob@example.com"},
        )
        content = MessageContent(
            text="See attached",
            attachments=[
                Attachment(
                    filename="note.txt",
                    content_type="text/plain",
                    size_bytes=len(small_data),
                    data=small_data,
                )
            ],
        )

        payload = build_message(conversation, content, subject="Files")
        msg = payload["message"]
        assert "attachments" in msg
        assert len(msg["attachments"]) == 1
        att = msg["attachments"][0]
        assert att["name"] == "note.txt"
        assert att["@odata.type"] == "#microsoft.graph.fileAttachment"

    def test_reply_does_not_include_subject(self):
        """Reply payloads don't include subject (Graph handles threading)."""
        conversation = ConversationRef(
            connector="outlook",
            account_id="test",
            type="email_thread",
            opaque_id={"message_id": "AAMk="},
        )
        content = MessageContent(text="Reply text")

        payload = build_message(conversation, content, subject="Ignored")
        assert "subject" not in payload
        assert "message" not in payload


class TestBuildAttachmentPayload:
    """Tests for build_attachment_payload."""

    def test_inline_attachment_base64(self):
        """Attachments ≤ 4 MB are base64-encoded inline."""
        att = Attachment(
            filename="doc.pdf",
            content_type="application/pdf",
            size_bytes=1024,
        )
        data = b"PDF content here"

        payload = build_attachment_payload(att, data)

        assert payload["@odata.type"] == "#microsoft.graph.fileAttachment"
        assert payload["name"] == "doc.pdf"
        assert payload["contentType"] == "application/pdf"
        decoded = base64.b64decode(payload["contentBytes"])
        assert decoded == data

    def test_large_attachment_sentinel(self):
        """Attachments > 4 MB return upload session sentinel."""
        att = Attachment(
            filename="bigfile.zip",
            content_type="application/zip",
            size_bytes=5 * 1024 * 1024,
        )
        data = b"x" * (5 * 1024 * 1024)

        payload = build_attachment_payload(att, data)

        assert payload["_upload_session"] is True
        assert payload["name"] == "bigfile.zip"
        assert payload["size"] == len(data)

    def test_exactly_4mb_is_inline(self):
        """Attachment of exactly 4 MB is inline (not upload session)."""
        att = Attachment(
            filename="exact.bin",
            content_type="application/octet-stream",
        )
        data = b"x" * (4 * 1024 * 1024)

        payload = build_attachment_payload(att, data)
        assert "@odata.type" in payload
        assert "_upload_session" not in payload
