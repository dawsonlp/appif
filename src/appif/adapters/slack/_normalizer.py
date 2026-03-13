"""Normalise raw Slack event dicts into domain :class:`MessageEvent` objects.

Supports both bot and user identity types. The ``authenticated_user_id``
parameter identifies the connector's own identity so that self-message
filtering works regardless of whether the connector is running as a bot
or a user.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from appif.domain.messaging.models import (
    ConversationRef,
    Identity,
    MessageContent,
    MessageEvent,
)

# Type alias for the user-resolve callback injected by the connector.
# Returns an Identity object (matches UserCache.resolve signature).
ResolveUser = Callable[[str], Identity]


def normalize_message(
    event: dict,
    *,
    team_id: str,
    authenticated_user_id: str,
    resolve_user: ResolveUser,
) -> MessageEvent | None:
    """Turn a Slack ``message`` event into a domain :class:`MessageEvent`.

    Returns ``None`` when the event should be skipped (own messages,
    unsupported subtypes).

    Parameters
    ----------
    event:
        Raw event payload from the Slack SDK.
    team_id:
        Workspace identifier obtained at connection time.
    authenticated_user_id:
        The connector's own user-id (bot or human) so we can filter
        self-messages.
    resolve_user:
        Sync callback ``(user_id) -> Identity`` supplied by the
        connector (usually backed by :class:`UserCache`).
    """
    user_id: str = event.get("user", "")

    # Skip messages from the authenticated identity
    if user_id and user_id == authenticated_user_id:
        return None

    identity = (
        resolve_user(user_id)
        if user_id
        else Identity(
            id="unknown",
            display_name="unknown",
            connector="slack",
        )
    )

    ts: str = event.get("ts", "")
    thread_ts: str | None = event.get("thread_ts")
    channel: str = event.get("channel", "")

    timestamp = datetime.fromtimestamp(float(ts), tz=UTC) if ts else datetime.now(tz=UTC)

    conversation_ref = ConversationRef(
        connector="slack",
        account_id=team_id,
        type="thread" if thread_ts else "channel",
        opaque_id={
            "channel": channel,
            **({"thread_ts": thread_ts} if thread_ts else {}),
        },
    )

    return MessageEvent(
        message_id=ts,
        connector="slack",
        account_id=team_id,
        conversation_ref=conversation_ref,
        author=identity,
        timestamp=timestamp,
        content=MessageContent(text=event.get("text", "")),
        metadata=event,
    )
