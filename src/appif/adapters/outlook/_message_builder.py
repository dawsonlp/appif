"""Build Graph API request payloads from domain MessageContent.

Translates outbound ``MessageContent`` + ``ConversationRef`` into the
JSON shapes expected by the Graph API ``sendMail`` and ``reply`` endpoints.
"""

from __future__ import annotations

import base64
import logging

from appif.domain.messaging.models import Attachment, ConversationRef, MessageContent

logger = logging.getLogger(__name__)

# Graph API inline attachment limit
_INLINE_ATTACHMENT_LIMIT = 4 * 1024 * 1024  # 4 MB


def build_message(
    conversation: ConversationRef,
    content: MessageContent,
    *,
    subject: str | None = None,
) -> dict:
    """Build a Graph API message payload for send or reply.

    Parameters
    ----------
    conversation:
        Routing information. If ``opaque_id`` contains a ``message_id``,
        this is treated as a reply; otherwise as a new thread.
    content:
        The message body to send.
    subject:
        Subject line for new threads. Ignored for replies.

    Returns
    -------
    dict
        For a **new thread**: a ``sendMail``-shaped payload.
        For a **reply**: a ``reply``-shaped payload with ``comment``.

    The returned dict also includes a ``_route`` key indicating
    which endpoint to use: ``"send_mail"`` or ``"reply"``.
    """
    opaque = conversation.opaque_id
    parent_message_id = opaque.get("message_id")
    is_reply = bool(parent_message_id)

    if is_reply:
        return _build_reply_payload(content, parent_message_id)
    else:
        return _build_new_thread_payload(conversation, content, subject=subject)


def build_attachment_payload(att: Attachment, data: bytes) -> dict:
    """Build a Graph API attachment payload.

    Parameters
    ----------
    att:
        The domain attachment metadata.
    data:
        Raw file bytes.

    Returns
    -------
    dict
        For attachments ≤ 4 MB: an inline ``fileAttachment`` payload
        with base64-encoded content.
        For attachments > 4 MB: a sentinel dict with ``_upload_session: True``
        indicating the connector should use an upload session.
    """
    if len(data) > _INLINE_ATTACHMENT_LIMIT:
        return {
            "_upload_session": True,
            "name": att.filename,
            "content_type": att.content_type,
            "size": len(data),
            "data": data,
        }

    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": att.filename,
        "contentType": att.content_type,
        "contentBytes": base64.b64encode(data).decode("ascii"),
    }


def _build_new_thread_payload(
    conversation: ConversationRef,
    content: MessageContent,
    *,
    subject: str | None = None,
) -> dict:
    """Build a sendMail payload for a new message thread."""
    recipient = conversation.opaque_id.get("recipient", conversation.account_id)

    payload = {
        "_route": "send_mail",
        "message": {
            "subject": subject or "No Subject",
            "body": {
                "contentType": "text",
                "content": content.text,
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": recipient,
                    }
                }
            ],
        },
    }

    # Add inline attachments
    inline_attachments = _build_inline_attachments(content)
    if inline_attachments:
        payload["message"]["attachments"] = inline_attachments

    return payload


def _build_reply_payload(content: MessageContent, parent_message_id: str) -> dict:
    """Build a reply payload for an existing conversation thread."""
    return {
        "_route": "reply",
        "_parent_message_id": parent_message_id,
        "comment": content.text,
    }


def _build_inline_attachments(content: MessageContent) -> list[dict]:
    """Build inline attachment payloads for attachments that have data."""
    result = []
    for att in content.attachments:
        if att.data is not None and len(att.data) <= _INLINE_ATTACHMENT_LIMIT:
            result.append(
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": att.filename,
                    "contentType": att.content_type,
                    "contentBytes": base64.b64encode(att.data).decode("ascii"),
                }
            )
    return result
