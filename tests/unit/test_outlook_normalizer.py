"""Unit tests for the Outlook normalizer module."""

from __future__ import annotations

from appif.adapters.outlook._normalizer import extract_attachments, extract_body, normalize_message


def _make_graph_message(**overrides):
    """Build a realistic Graph API message dict."""
    msg = {
        "id": "AAMkAGI2TG93AAA=",
        "conversationId": "AAQkAGI2TG93conv=",
        "parentFolderId": "AAMkAGI2TG93folder=",
        "receivedDateTime": "2026-02-21T10:30:00Z",
        "subject": "Test Subject",
        "from": {
            "emailAddress": {
                "name": "Alice Smith",
                "address": "alice@example.com",
            }
        },
        "body": {
            "contentType": "text",
            "content": "Hello, world!",
        },
        "attachments": [],
    }
    msg.update(overrides)
    return msg


class TestNormalizeMessage:
    """Tests for normalize_message."""

    def test_basic_text_message(self):
        """Basic text message normalises correctly."""
        msg = _make_graph_message()
        event = normalize_message(msg, account_id="test-account")

        assert event is not None
        assert event.message_id == "AAMkAGI2TG93AAA="
        assert event.connector == "outlook"
        assert event.account_id == "test-account"
        assert event.content.text == "Hello, world!"
        assert event.author.id == "alice@example.com"
        assert event.author.display_name == "Alice Smith"
        assert event.metadata.get("subject") == "Test Subject"

    def test_echo_suppression(self):
        """Message whose id is in sent_ids returns None."""
        msg = _make_graph_message()
        sent_ids = {"AAMkAGI2TG93AAA="}

        event = normalize_message(msg, account_id="test", sent_ids=sent_ids)
        assert event is None

    def test_empty_id_returns_none(self):
        """Message without id returns None."""
        msg = _make_graph_message(id="")
        event = normalize_message(msg, account_id="test")
        assert event is None

    def test_html_body_converted_to_text(self):
        """HTML body is converted to plain text."""
        msg = _make_graph_message(
            body={
                "contentType": "html",
                "content": "<html><body><p>Hello</p><p>World</p></body></html>",
            }
        )
        event = normalize_message(msg, account_id="test")

        assert event is not None
        assert "Hello" in event.content.text
        assert "World" in event.content.text
        # Raw HTML preserved in metadata
        assert "<html>" in event.metadata.get("html_body", "")

    def test_missing_subject_does_not_raise(self):
        """Missing subject field doesn't raise."""
        msg = _make_graph_message()
        del msg["subject"]

        event = normalize_message(msg, account_id="test")
        assert event is not None
        assert "subject" not in event.metadata

    def test_missing_attachments_does_not_raise(self):
        """Missing attachments field doesn't raise."""
        msg = _make_graph_message()
        del msg["attachments"]

        event = normalize_message(msg, account_id="test")
        assert event is not None
        assert event.content.attachments == []

    def test_missing_from_field_uses_defaults(self):
        """Missing from field uses 'unknown' defaults."""
        msg = _make_graph_message()
        del msg["from"]

        event = normalize_message(msg, account_id="test")
        assert event is not None
        assert event.author.id == "unknown"

    def test_conversation_ref_populated(self):
        """ConversationRef opaque_id contains expected keys."""
        msg = _make_graph_message()
        event = normalize_message(msg, account_id="test")

        assert event is not None
        ref = event.conversation_ref
        assert ref.connector == "outlook"
        assert ref.type == "email_thread"
        assert ref.opaque_id["conversation_id"] == "AAQkAGI2TG93conv="
        assert ref.opaque_id["message_id"] == "AAMkAGI2TG93AAA="

    def test_timestamp_parsed_from_iso(self):
        """receivedDateTime is parsed to datetime."""
        msg = _make_graph_message(receivedDateTime="2026-02-21T15:30:00Z")
        event = normalize_message(msg, account_id="test")

        assert event is not None
        assert event.timestamp.year == 2026
        assert event.timestamp.month == 2
        assert event.timestamp.day == 21


class TestExtractBody:
    """Tests for extract_body."""

    def test_plain_text_body(self):
        msg = {"body": {"contentType": "text", "content": "Plain text"}}
        text, metadata = extract_body(msg)
        assert text == "Plain text"
        assert "html_body" not in metadata

    def test_html_body_strips_tags(self):
        msg = {"body": {"contentType": "html", "content": "<b>Bold</b> text"}}
        text, metadata = extract_body(msg)
        assert "Bold" in text
        assert "text" in text
        assert metadata["html_body"] == "<b>Bold</b> text"

    def test_empty_body(self):
        msg = {"body": {"contentType": "text", "content": ""}}
        text, metadata = extract_body(msg)
        assert text == ""

    def test_missing_body(self):
        msg = {}
        text, metadata = extract_body(msg)
        assert text == ""


class TestExtractAttachments:
    """Tests for extract_attachments."""

    def test_file_attachments_extracted(self):
        msg = {
            "id": "msg123",
            "attachments": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "id": "att456",
                    "name": "report.pdf",
                    "contentType": "application/pdf",
                    "size": 1024,
                },
            ],
        }
        atts = extract_attachments(msg)
        assert len(atts) == 1
        assert atts[0].filename == "report.pdf"
        assert atts[0].content_type == "application/pdf"
        assert atts[0].size_bytes == 1024
        assert atts[0].content_ref == "msg123::att456"

    def test_non_file_attachments_skipped(self):
        """Reference attachments and item attachments are ignored."""
        msg = {
            "id": "msg123",
            "attachments": [
                {
                    "@odata.type": "#microsoft.graph.referenceAttachment",
                    "id": "att789",
                    "name": "link.url",
                },
            ],
        }
        atts = extract_attachments(msg)
        assert len(atts) == 0

    def test_no_attachments(self):
        msg = {"id": "msg123", "attachments": []}
        atts = extract_attachments(msg)
        assert len(atts) == 0

    def test_missing_attachments_key(self):
        msg = {"id": "msg123"}
        atts = extract_attachments(msg)
        assert len(atts) == 0

    def test_composite_content_ref(self):
        """content_ref uses message_id::attachment_id format."""
        msg = {
            "id": "MSG_ID",
            "attachments": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "id": "ATT_ID",
                    "name": "file.txt",
                    "contentType": "text/plain",
                },
            ],
        }
        atts = extract_attachments(msg)
        assert atts[0].content_ref == "MSG_ID::ATT_ID"
