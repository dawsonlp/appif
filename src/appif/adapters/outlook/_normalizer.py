"""Normalise raw Graph API message dicts into domain MessageEvent objects.

Pure-function layer: no I/O, no SDK calls. Transforms the JSON
representation of a Graph message into the canonical domain shape.
"""

from __future__ import annotations

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

_CONNECTOR_NAME = "outlook"


def normalize_message(
    msg: dict,
    *,
    account_id: str,
    sent_ids: set[str] | None = None,
) -> MessageEvent | None:
    """Convert a Graph API message dict into a ``MessageEvent``.

    Parameters
    ----------
    msg:
        Raw JSON dict from Graph API ``/messages`` or delta response.
    account_id:
        Logical account label for this connector instance.
    sent_ids:
        Set of message IDs that were sent by this connector (echo suppression).

    Returns
    -------
    MessageEvent or None
        ``None`` if the message should be suppressed (echo or invalid).
    """
    msg_id = msg.get("id", "")
    if not msg_id:
        return None

    # Echo suppression — skip messages we sent ourselves
    if sent_ids and msg_id in sent_ids:
        logger.debug("outlook.echo_suppressed", extra={"message_id": msg_id})
        return None

    # Build Identity from sender
    from_field = msg.get("from", {})
    email_address = from_field.get("emailAddress", {})
    sender_email = email_address.get("address", "unknown")
    sender_name = email_address.get("name", sender_email)

    author = Identity(
        id=sender_email,
        display_name=sender_name,
        connector=_CONNECTOR_NAME,
    )

    # Build ConversationRef
    conversation_id = msg.get("conversationId", msg_id)
    folder_id = msg.get("parentFolderId", "")

    conversation = ConversationRef(
        connector=_CONNECTOR_NAME,
        account_id=account_id,
        type="email_thread",
        opaque_id={
            "conversation_id": conversation_id,
            "message_id": msg_id,
            "folder_id": folder_id,
        },
    )

    # Extract body (text + optional html metadata)
    text, body_metadata = extract_body(msg)

    # Extract attachments
    attachments = extract_attachments(msg)

    # Parse timestamp
    received = msg.get("receivedDateTime", "")
    try:
        ts = datetime.fromisoformat(received.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        ts = datetime.now(UTC)

    # Build metadata dict (subject, html_body, etc.)
    metadata: dict = {}
    subject = msg.get("subject", "")
    if subject:
        metadata["subject"] = subject
    metadata.update(body_metadata)

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


def extract_body(msg: dict) -> tuple[str, dict]:
    """Extract the message body, converting HTML to plain text if needed.

    Returns
    -------
    tuple[str, dict]
        The plain text body and a metadata dict. If the original body
        was HTML, ``metadata["html_body"]`` contains the raw HTML.
    """
    body = msg.get("body", {})
    content_type = body.get("contentType", "text").lower()
    raw_content = body.get("content", "")

    metadata: dict = {}

    if content_type == "html" and raw_content:
        metadata["html_body"] = raw_content
        soup = BeautifulSoup(raw_content, "html.parser")
        text = soup.get_text(separator="\n").strip()
    else:
        text = raw_content.strip() if raw_content else ""

    return text, metadata


def extract_attachments(msg: dict) -> list[Attachment]:
    """Extract file attachments from a Graph message dict.

    Only ``#microsoft.graph.fileAttachment`` types are included.
    Each attachment gets a composite ``content_ref`` of
    ``message_id::attachment_id`` for lazy download.
    """
    msg_id = msg.get("id", "")
    raw_attachments = msg.get("attachments", [])
    result: list[Attachment] = []

    for att in raw_attachments:
        odata_type = att.get("@odata.type", "")
        if odata_type != "#microsoft.graph.fileAttachment":
            continue

        att_id = att.get("id", "")
        filename = att.get("name", "unknown")
        content_type = att.get("contentType", "application/octet-stream")
        size = att.get("size")

        result.append(
            Attachment(
                filename=filename,
                content_type=content_type,
                size_bytes=size,
                content_ref=f"{msg_id}::{att_id}" if att_id else None,
            )
        )

    return result
