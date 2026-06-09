"""Normalise Graph ``chatMessage`` dicts into domain MessageEvent objects.

Pure-function layer: no I/O, no SDK calls. Handles both chat messages
(``/me/chats/{id}/messages``) and channel messages
(``/teams/{tid}/channels/{cid}/messages``), which share the ``chatMessage``
resource shape.
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
    Recipients,
)

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "teams"


def normalize_message(
    msg: dict,
    *,
    account_id: str,
    authenticated_user_id: str,
    chat_id: str | None = None,
    team_id: str | None = None,
    channel_id: str | None = None,
    include_sent: bool = False,
) -> MessageEvent | None:
    """Convert a Graph ``chatMessage`` dict into a ``MessageEvent``.

    Parameters
    ----------
    msg:
        Raw ``chatMessage`` JSON from a chat or channel messages endpoint.
    account_id:
        Logical account label for this connector instance.
    authenticated_user_id:
        The connector's own AAD user id, for self-message suppression.
    chat_id:
        Source chat id when the message came from a 1:1/group chat.
    team_id / channel_id:
        Source team + channel ids when the message came from a channel.
        If omitted, they are read from ``msg["channelIdentity"]``.
    include_sent:
        When ``True``, messages authored by the authenticated user are
        emitted instead of suppressed.

    Returns
    -------
    MessageEvent or None
        ``None`` for non-message events (system messages), deleted
        messages, or self-messages when ``include_sent`` is ``False``.
    """
    msg_id = msg.get("id", "")
    if not msg_id:
        return None

    # Only real chat messages — skip system events, typing, etc.
    if msg.get("messageType", "message") != "message":
        return None

    # Skip deleted/tombstoned messages.
    if msg.get("deletedDateTime"):
        return None

    from_user = (msg.get("from") or {}).get("user") or {}
    sender_id = from_user.get("id", "")

    # Self-message suppression (gated by include_sent).
    if not include_sent and sender_id and sender_id == authenticated_user_id:
        logger.debug("teams.self_suppressed", extra={"message_id": msg_id})
        return None

    author = Identity(
        id=sender_id or "unknown",
        display_name=from_user.get("displayName") or sender_id or "unknown",
        connector=_CONNECTOR_NAME,
    )

    # Recipients: Teams has no addressed to/cc on a message, so we treat
    # @-mentioned users as the best-effort involved set (deduped, in order).
    recipients = Recipients(to=_parse_mentions(msg))

    # Body — HTML or text.
    text, body_metadata = _extract_body(msg)

    # Timestamp.
    ts = _parse_ts(msg.get("createdDateTime", ""))

    # Conversation routing — chat vs channel.
    conversation = _build_conversation(
        msg,
        account_id=account_id,
        msg_id=msg_id,
        chat_id=chat_id,
        team_id=team_id,
        channel_id=channel_id,
    )

    metadata: dict = {}
    subject = msg.get("subject")
    if subject:
        metadata["subject"] = subject
    importance = msg.get("importance")
    if importance and importance != "normal":
        metadata["importance"] = importance
    metadata.update(body_metadata)

    return MessageEvent(
        message_id=msg_id,
        connector=_CONNECTOR_NAME,
        account_id=account_id,
        conversation_ref=conversation,
        author=author,
        timestamp=ts,
        content=MessageContent(text=text, attachments=_extract_attachments(msg)),
        recipients=recipients,
        metadata=metadata,
    )


def _build_conversation(
    msg: dict,
    *,
    account_id: str,
    msg_id: str,
    chat_id: str | None,
    team_id: str | None,
    channel_id: str | None,
) -> ConversationRef:
    """Build the reply-routing handle for a chat or channel message."""
    # Prefer explicit ids from the poller; fall back to channelIdentity.
    if not (team_id and channel_id):
        ci = msg.get("channelIdentity") or {}
        team_id = team_id or ci.get("teamId")
        channel_id = channel_id or ci.get("channelId")

    if team_id and channel_id:
        # A reply carries replyToId pointing at the root message.
        root_id = msg.get("replyToId") or msg_id
        return ConversationRef(
            connector=_CONNECTOR_NAME,
            account_id=account_id,
            type="channel",
            opaque_id={
                "team_id": team_id,
                "channel_id": channel_id,
                "message_id": root_id,
            },
        )

    return ConversationRef(
        connector=_CONNECTOR_NAME,
        account_id=account_id,
        type="chat",
        opaque_id={
            "chat_id": chat_id or "",
            "message_id": msg_id,
        },
    )


def _parse_mentions(msg: dict) -> list[Identity]:
    """Extract @-mentioned users as recipient Identities (deduped, ordered)."""
    seen: set[str] = set()
    result: list[Identity] = []
    for mention in msg.get("mentions", []) or []:
        user = (mention.get("mentioned") or {}).get("user") or {}
        uid = user.get("id")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        result.append(
            Identity(
                id=uid,
                display_name=user.get("displayName") or mention.get("mentionText") or uid,
                connector=_CONNECTOR_NAME,
            )
        )
    return result


def _extract_body(msg: dict) -> tuple[str, dict]:
    """Return (plain_text, metadata). HTML bodies are stripped to text."""
    body = msg.get("body", {}) or {}
    content_type = (body.get("contentType") or "text").lower()
    raw = body.get("content", "") or ""

    metadata: dict = {}
    if content_type == "html" and raw:
        metadata["html_body"] = raw
        text = BeautifulSoup(raw, "html.parser").get_text(separator="\n").strip()
    else:
        text = raw.strip()
    return text, metadata


def _extract_attachments(msg: dict) -> list[Attachment]:
    """Best-effort attachment metadata from a chatMessage."""
    result: list[Attachment] = []
    for att in msg.get("attachments", []) or []:
        att_id = att.get("id", "")
        result.append(
            Attachment(
                filename=att.get("name") or att_id or "attachment",
                content_type=att.get("contentType", "application/octet-stream"),
                content_ref=att_id or None,
            )
        )
    return result


def _parse_ts(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now(UTC)
