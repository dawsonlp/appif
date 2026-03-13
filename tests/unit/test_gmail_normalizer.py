"""Unit tests for the Gmail normalizer module."""

from __future__ import annotations

import base64

from appif.adapters.gmail._normalizer import extract_attachments, extract_body, normalize_message
from appif.domain.messaging.models import MessageEvent


def _b64(text: str) -> str:
    """Encode text as URL-safe base64 (Gmail format, no padding)."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _make_message(
    *,
    msg_id: str = "msg123",
    thread_id: str = "thread456",
    from_name: str = "Alice",
    from_email: str = "alice@example.com",
    to_email: str = "user@gmail.com",
    subject: str = "Test Subject",
    body_text: str = "Hello world",
    body_html: str | None = None,
    mime_type: str = "text/plain",
    internal_date: str = "1700000000000",
    label_ids: list[str] | None = None,
    attachments: list[dict] | None = None,
) -> dict:
    """Build a representative Gmail API message dict."""
    headers = [
        {"name": "From", "value": f"{from_name} <{from_email}>"},
        {"name": "To", "value": to_email},
        {"name": "Subject", "value": subject},
    ]

    if body_html and not attachments:
        # multipart/alternative
        payload = {
            "mimeType": "multipart/alternative",
            "headers": headers,
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(body_text)}, "filename": ""},
                {"mimeType": "text/html", "body": {"data": _b64(body_html)}, "filename": ""},
            ],
        }
    elif attachments:
        parts = [{"mimeType": mime_type, "body": {"data": _b64(body_text)}, "filename": ""}]
        for att in attachments:
            parts.append(att)
        payload = {
            "mimeType": "multipart/mixed",
            "headers": headers,
            "parts": parts,
        }
    else:
        payload = {
            "mimeType": mime_type,
            "headers": headers,
            "body": {"data": _b64(body_text)},
        }

    msg = {
        "id": msg_id,
        "threadId": thread_id,
        "internalDate": internal_date,
        "labelIds": label_ids or ["INBOX"],
        "snippet": body_text[:50],
        "payload": payload,
    }
    return msg


class TestNormalizeMessage:
    """Tests for normalize_message."""

    def test_plain_text_message(self):
        msg = _make_message()
        event = normalize_message(msg, "user@gmail.com")

        assert isinstance(event, MessageEvent)
        assert event.message_id == "msg123"
        assert event.connector == "gmail"
        assert event.account_id == "user@gmail.com"
        assert event.content.text == "Hello world"
        assert event.author.id == "alice@example.com"
        assert event.author.display_name == "Alice"
        assert event.metadata["subject"] == "Test Subject"
        assert "INBOX" in event.metadata["labels"]

    def test_html_only_message(self):
        msg = _make_message(
            body_text="<p>Hello <b>world</b></p>",
            mime_type="text/html",
        )
        event = normalize_message(msg, "user@gmail.com")

        assert event is not None
        assert "Hello" in event.content.text
        assert "<p>" not in event.content.text

    def test_multipart_alternative_prefers_plain(self):
        msg = _make_message(
            body_text="Plain text version",
            body_html="<p>HTML version</p>",
        )
        event = normalize_message(msg, "user@gmail.com")

        assert event is not None
        assert event.content.text == "Plain text version"

    def test_echo_suppression_returns_none(self):
        msg = _make_message(from_email="user@gmail.com", from_name="Me")
        event = normalize_message(msg, "user@gmail.com")

        assert event is None

    def test_echo_suppression_case_insensitive(self):
        msg = _make_message(from_email="User@Gmail.com")
        event = normalize_message(msg, "user@gmail.com")

        assert event is None

    def test_empty_message_id_returns_none(self):
        msg = _make_message()
        msg["id"] = ""
        event = normalize_message(msg, "user@gmail.com")

        assert event is None

    def test_conversation_ref_contains_threading_data(self):
        msg = _make_message()
        event = normalize_message(msg, "user@gmail.com")

        ref = event.conversation_ref
        assert ref.connector == "gmail"
        assert ref.type == "email_thread"
        assert ref.opaque_id["thread_id"] == "thread456"
        assert ref.opaque_id["message_id"] == "msg123"
        assert ref.opaque_id["subject"] == "Test Subject"

    def test_timestamp_parsed_from_internal_date(self):
        msg = _make_message(internal_date="1700000000000")
        event = normalize_message(msg, "user@gmail.com")

        assert event.timestamp.year == 2023
        assert event.timestamp.tzinfo is not None

    def test_missing_display_name_uses_local_part(self):
        msg = _make_message()
        # Override From header to have no display name
        for h in msg["payload"]["headers"]:
            if h["name"] == "From":
                h["value"] = "alice@example.com"
        event = normalize_message(msg, "user@gmail.com")

        assert event.author.display_name == "alice"

    def test_message_with_attachments(self):
        att_parts = [
            {
                "mimeType": "application/pdf",
                "filename": "report.pdf",
                "body": {"attachmentId": "att001", "size": 12345},
            },
        ]
        msg = _make_message(attachments=att_parts)
        event = normalize_message(msg, "user@gmail.com")

        assert len(event.content.attachments) == 1
        att = event.content.attachments[0]
        assert att.filename == "report.pdf"
        assert att.content_type == "application/pdf"
        assert att.size_bytes == 12345
        assert att.content_ref == "msg123:att001"

    def test_inline_images_excluded(self):
        """Parts without a filename are not treated as attachments."""
        parts = [
            {
                "mimeType": "image/png",
                "filename": "",
                "body": {"data": _b64("fake image data")},
            },
        ]
        msg = _make_message(attachments=parts)
        event = normalize_message(msg, "user@gmail.com")

        assert len(event.content.attachments) == 0


class TestExtractBody:
    """Tests for extract_body."""

    def test_plain_text_part(self):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": _b64("Simple text")},
        }
        assert extract_body(payload) == "Simple text"

    def test_html_part_stripped(self):
        payload = {
            "mimeType": "text/html",
            "body": {"data": _b64("<p>Hello <b>world</b></p>")},
        }
        result = extract_body(payload)
        assert "Hello" in result
        assert "<p>" not in result

    def test_multipart_alternative(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Plain")}, "filename": ""},
                {"mimeType": "text/html", "body": {"data": _b64("<p>HTML</p>")}, "filename": ""},
            ],
        }
        assert extract_body(payload) == "Plain"

    def test_empty_body(self):
        payload = {"mimeType": "text/plain", "body": {}}
        assert extract_body(payload) == ""

    def test_nested_multipart(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64("Nested plain")}, "filename": ""},
                        {"mimeType": "text/html", "body": {"data": _b64("<p>Nested HTML</p>")}, "filename": ""},
                    ],
                },
            ],
        }
        assert extract_body(payload) == "Nested plain"


class TestExtractAttachments:
    """Tests for extract_attachments."""

    def test_no_attachments(self):
        payload = {"mimeType": "text/plain", "body": {"data": _b64("text")}}
        assert extract_attachments(payload, "msg1") == []

    def test_file_attachment(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("text")}, "filename": ""},
                {
                    "mimeType": "application/pdf",
                    "filename": "doc.pdf",
                    "body": {"attachmentId": "att1", "size": 1024},
                },
            ],
        }
        result = extract_attachments(payload, "msg1")
        assert len(result) == 1
        assert result[0].filename == "doc.pdf"
        assert result[0].content_ref == "msg1:att1"

    def test_multiple_attachments(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("text")}, "filename": ""},
                {
                    "mimeType": "application/pdf",
                    "filename": "a.pdf",
                    "body": {"attachmentId": "att1", "size": 100},
                },
                {
                    "mimeType": "image/jpeg",
                    "filename": "photo.jpg",
                    "body": {"attachmentId": "att2", "size": 200},
                },
            ],
        }
        result = extract_attachments(payload, "msg1")
        assert len(result) == 2
        assert result[0].filename == "a.pdf"
        assert result[1].filename == "photo.jpg"
