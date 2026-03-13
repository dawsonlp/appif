"""Build RFC 2822 email messages from domain MessageContent.

Translates outbound ``MessageContent`` + ``ConversationRef`` into
base64url-encoded RFC 2822 messages suitable for the Gmail API
``messages.send`` endpoint's ``raw`` field.
"""

from __future__ import annotations

import base64
import logging
from email.message import EmailMessage
from email.utils import formatdate

from appif.domain.messaging.errors import ConnectorError
from appif.domain.messaging.models import ConversationRef, MessageContent

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "gmail"

# Gmail maximum message size
_MAX_MESSAGE_SIZE = 25 * 1024 * 1024  # 25 MB


def build_message(
    conversation: ConversationRef,
    content: MessageContent,
    from_address: str,
) -> str:
    """Build a base64url-encoded RFC 2822 message for the Gmail API.

    Parameters
    ----------
    conversation:
        Routing information. If ``opaque_id`` contains ``thread_id`` and
        ``message_id``, this is treated as a reply; otherwise a new thread.
    content:
        The message body and optional attachments.
    from_address:
        The authenticated sender's email address.

    Returns
    -------
    str
        Base64url-encoded RFC 2822 message (Gmail API ``raw`` format).

    Raises
    ------
    ConnectorError
        If subject is missing for a new thread, or message exceeds 25 MB.
    """
    opaque = conversation.opaque_id
    thread_id = opaque.get("thread_id", "")
    parent_message_id = opaque.get("message_id", "")
    is_reply = bool(thread_id and parent_message_id)

    if is_reply:
        msg = _build_reply(opaque, content, from_address)
    else:
        msg = _build_new_thread(opaque, content, from_address)

    raw_bytes = msg.as_bytes()

    # Size validation
    if len(raw_bytes) > _MAX_MESSAGE_SIZE:
        raise ConnectorError(_CONNECTOR_NAME, "message exceeds 25 MB limit")

    return base64.urlsafe_b64encode(raw_bytes).decode("ascii")


def _build_reply(
    opaque: dict,
    content: MessageContent,
    from_address: str,
) -> EmailMessage:
    """Build an RFC 2822 reply message with threading headers."""
    msg = EmailMessage()

    # Threading headers
    in_reply_to = opaque.get("in_reply_to", "")
    references = opaque.get("references", "")

    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        # Append to references chain
        if references:
            msg["References"] = f"{references} {in_reply_to}"
        else:
            msg["References"] = in_reply_to

    # Subject — derive from original, prefix with Re: if needed
    original_subject = opaque.get("subject", "")
    if original_subject:
        subject = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"
    else:
        subject = "Re:"
    msg["Subject"] = subject

    # To — reply to the original sender (stored in opaque_id["to"] by normalizer,
    # but for a reply we send back to the From of the original message).
    # The normalizer stores the original To header; for reply we typically
    # want the From. The connector passes the right address via opaque_id.
    to_address = opaque.get("to", "")
    if not to_address:
        to_address = from_address  # fallback: reply to self
    msg["To"] = to_address

    msg["From"] = from_address
    msg["Date"] = formatdate(localtime=True)
    msg["MIME-Version"] = "1.0"

    _set_body(msg, content)
    return msg


def _build_new_thread(
    opaque: dict,
    content: MessageContent,
    from_address: str,
) -> EmailMessage:
    """Build an RFC 2822 message for a new email thread."""
    msg = EmailMessage()

    # Subject is required for new threads — check opaque_id first, then fall back
    subject = opaque.get("subject", "")
    if not subject:
        raise ConnectorError(_CONNECTOR_NAME, "subject required for new thread")
    msg["Subject"] = subject

    # To — recipient address
    to_address = opaque.get("to", "")
    if not to_address:
        raise ConnectorError(_CONNECTOR_NAME, "recipient address required (opaque_id['to'])")
    msg["To"] = to_address

    msg["From"] = from_address
    msg["Date"] = formatdate(localtime=True)
    msg["MIME-Version"] = "1.0"

    _set_body(msg, content)
    return msg


def _set_body(msg: EmailMessage, content: MessageContent) -> None:
    """Set the message body, handling plain text and attachments."""
    if not content.attachments:
        # Simple text/plain message
        msg.set_content(content.text)
        return

    # Multipart/mixed: text body + attachments
    msg.set_content(content.text)

    for att in content.attachments:
        if att.data is None:
            continue
        maintype, _, subtype = att.content_type.partition("/")
        if not subtype:
            maintype = "application"
            subtype = "octet-stream"
        msg.add_attachment(
            att.data,
            maintype=maintype,
            subtype=subtype,
            filename=att.filename,
        )
