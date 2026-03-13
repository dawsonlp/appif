"""Canonical messaging domain models.

These types are connector-agnostic. Every inbound message — Slack, Email,
Teams, or any future platform — arrives in this shape. Every outbound
message uses these types. No platform SDK types appear here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """A message author resolved by a connector."""

    id: str
    display_name: str
    connector: str


# ---------------------------------------------------------------------------
# Attachment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Attachment:
    """A file or media item attached to a message.

    ``content_ref`` is a connector-specific opaque reference for lazy download
    (e.g. ``"message_id::attachment_id"`` for Graph API).  ``data`` holds the
    raw bytes when available (inline small attachments); it is ``None`` when
    the attachment must be fetched via ``content_ref``.
    """

    filename: str
    content_type: str
    size_bytes: int | None = None
    content_ref: str | None = None
    data: bytes | None = None


# ---------------------------------------------------------------------------
# Message content
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MessageContent:
    """The body of a message — text and optional attachments."""

    text: str
    attachments: list[Attachment] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Conversation routing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConversationRef:
    """Opaque routing key for replies.

    Upstream systems use this to reply — they never inspect or construct
    the ``opaque_id``.  Only the owning connector reads it.

    Rule: if something is not needed to reply, it does not belong here.
    """

    connector: str
    account_id: str
    type: str  # "channel", "thread", "dm", "email_thread"
    opaque_id: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Inbound event
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MessageEvent:
    """Canonical inbound message event received by listeners."""

    message_id: str
    connector: str
    account_id: str
    conversation_ref: ConversationRef
    author: Identity
    timestamp: datetime
    content: MessageContent
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Outbound receipt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SendReceipt:
    """Acknowledgement returned after a successful outbound send."""

    external_id: str
    timestamp: datetime


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectorCapabilities:
    """What a connector can do.  Agent logic branches on capabilities,
    not connector type."""

    supports_realtime: bool
    supports_backfill: bool
    supports_threads: bool
    supports_reply: bool
    supports_auto_send: bool
    delivery_mode: Literal["AUTOMATIC", "ASSISTED", "MANUAL"]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class ConnectorStatus(Enum):
    """Lifecycle state of a connector."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Account:
    """A configured workspace, mailbox, or tenant."""

    account_id: str
    display_name: str
    connector: str


@dataclass(frozen=True)
class Target:
    """A reachable destination within an account (channel, DM, group)."""

    target_id: str
    display_name: str
    type: str  # "channel", "dm", "group", etc.
    account_id: str


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackfillScope:
    """Scope for a historical backfill request."""

    conversation_ids: tuple[str, ...] = ()
    oldest: datetime | None = None
    latest: datetime | None = None
