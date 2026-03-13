"""Normalise raw Gmail API message dicts into domain MessageEvent objects.

Pure-function layer: no I/O, no SDK calls. Transforms the JSON
representation of a Gmail message into the canonical domain shape.
"""

from __future__ import annotations

import base64
import email.utils
import logging
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from appif.domain.messaging.models import (
    Attachment,
    ConversationRef,
    Identity,
    MessageContent,
    MessageEvent,
)

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "gmail"


def normalize_message(message: dict, account_id: str) -> MessageEvent | None:
    """Convert a Gmail API message dict into a ``MessageEvent``.

    Parameters
    ----------
    message:
        Raw JSON dict from ``users.messages.get`` with ``format=full``.
    account_id:
        The authenticated mailbox address (for echo suppression).

    Returns
    -------
    MessageEvent or None
        ``None`` if the message should be suppressed (sent by self).
    """
    msg_id = message.get("id", "")
    if not msg_id:
        return None

    headers = _build_header_map(message)

    # ── Echo suppression ──────────────────────────────────────
    from_header = headers.get("from", "")
    _name, from_email = email.utils.parseaddr(from_header)
    if from_email.lower() == account_id.lower():
        logger.debug("gmail.echo_suppressed", extra={"message_id": msg_id})
        return None

    # ── Author ────────────────────────────────────────────────
    display_name = _name or from_email.split("@")[0]
    author = Identity(
        id=from_email,
        display_name=display_name,
        connector=_CONNECTOR_NAME,
    )

    # ── Timestamp ─────────────────────────────────────────────
    internal_date = message.get("internalDate")
    if internal_date:
        ts = datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC)
    else:
        ts = datetime.now(UTC)

    # ── Body extraction ───────────────────────────────────────
    payload = message.get("payload", {})
    text = extract_body(payload)

    # ── Attachments ───────────────────────────────────────────
    attachments = extract_attachments(payload, msg_id)

    # ── ConversationRef ───────────────────────────────────────
    thread_id = message.get("threadId", "")
    subject = headers.get("subject", "")
    to_header = headers.get("to", "")
    in_reply_to = headers.get("in-reply-to", "")
    references = headers.get("references", "")

    conversation = ConversationRef(
        connector=_CONNECTOR_NAME,
        account_id=account_id,
        type="email_thread",
        opaque_id={
            "thread_id": thread_id,
            "message_id": msg_id,
            "in_reply_to": in_reply_to,
            "references": references,
            "subject": subject,
            "to": to_header,
        },
    )

    # ── Metadata ──────────────────────────────────────────────
    metadata: dict = {}
    if subject:
        metadata["subject"] = subject
    label_ids = message.get("labelIds", [])
    if label_ids:
        metadata["labels"] = label_ids
    snippet = message.get("snippet", "")
    if snippet:
        metadata["snippet"] = snippet

    return MessageEvent(
        message_id=msg_id,
        connector=_CONNECTOR_NAME,
        account_id=account_id,
        conversation_ref=conversation,
        author=author,
        timestamp=ts,
        content=MessageContent(
            text=text,
            attachments=attachments,
        ),
        metadata=metadata,
    )


def extract_body(payload: dict) -> str:
    """Walk the MIME payload tree and extract the best text body.

    Preference order:
    1. ``text/plain`` part
    2. ``text/html`` part → strip tags via BeautifulSoup

    Handles nested ``multipart/alternative`` and ``multipart/mixed`` parts.
    Gmail returns body data as URL-safe base64.
    """
    plain_parts: list[str] = []
    html_parts: list[str] = []
    _walk_parts(payload, plain_parts, html_parts)

    if plain_parts:
        return "\n".join(plain_parts).strip()
    if html_parts:
        combined = "\n".join(html_parts)
        soup = BeautifulSoup(combined, "html.parser")
        return soup.get_text(separator="\n").strip()

    return ""


def extract_attachments(payload: dict, message_id: str) -> list[Attachment]:
    """Extract attachment descriptors from MIME parts.

    Inline images (with ``Content-Disposition: inline`` or lacking a
    filename) are excluded. Only parts with an explicit filename are
    treated as attachments.

    ``content_ref`` uses the composite key format ``{message_id}:{attachment_id}``.
    """
    result: list[Attachment] = []
    _collect_attachments(payload, message_id, result)
    return result


# ── Internal helpers ──────────────────────────────────────────


def _build_header_map(message: dict) -> dict[str, str]:
    """Build a case-insensitive header lookup from the payload headers."""
    headers: dict[str, str] = {}
    payload = message.get("payload", {})
    for h in payload.get("headers", []):
        name = h.get("name", "").lower()
        value = h.get("value", "")
        headers[name] = value
    return headers


def _decode_body_data(data: str) -> str:
    """Decode a Gmail base64url-encoded body part to UTF-8 text."""
    if not data:
        return ""
    # Gmail uses URL-safe base64 without padding
    padded = data + "=" * (4 - len(data) % 4) if len(data) % 4 else data
    raw = base64.urlsafe_b64decode(padded)
    return raw.decode("utf-8", errors="replace")


def _walk_parts(part: dict, plain_parts: list[str], html_parts: list[str]) -> None:
    """Recursively walk MIME parts collecting text/plain and text/html bodies."""
    mime_type = part.get("mimeType", "")

    # Multipart container — recurse into sub-parts
    if mime_type.startswith("multipart/"):
        for sub in part.get("parts", []):
            _walk_parts(sub, plain_parts, html_parts)
        return

    # Leaf part — only extract if no filename (filename = attachment)
    filename = part.get("filename", "")
    if filename:
        return

    body = part.get("body", {})
    data = body.get("data", "")

    if mime_type == "text/plain" and data:
        plain_parts.append(_decode_body_data(data))
    elif mime_type == "text/html" and data:
        html_parts.append(_decode_body_data(data))


def _collect_attachments(part: dict, message_id: str, result: list[Attachment]) -> None:
    """Recursively collect attachment descriptors from MIME parts."""
    mime_type = part.get("mimeType", "")

    if mime_type.startswith("multipart/"):
        for sub in part.get("parts", []):
            _collect_attachments(sub, message_id, result)
        return

    filename = part.get("filename", "")
    if not filename:
        return

    body = part.get("body", {})
    attachment_id = body.get("attachmentId", "")
    size = body.get("size")

    content_ref = f"{message_id}:{attachment_id}" if attachment_id else None

    result.append(
        Attachment(
            filename=filename,
            content_type=mime_type or "application/octet-stream",
            size_bytes=size,
            content_ref=content_ref,
        )
    )
