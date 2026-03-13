"""Messaging connector domain — canonical event model and connector interface.

These types are connector-agnostic. They apply to all messaging connectors
(Slack, Teams, Email, etc.) and are separate from the content-extraction
models used by viewer connectors (Economist, Foreign Affairs, etc.).
"""

from appif.domain.messaging.errors import (
    ConnectorError,
    NotAuthorized,
    NotSupported,
    TargetUnavailable,
    TransientFailure,
)
from appif.domain.messaging.models import (
    Account,
    BackfillScope,
    ConnectorCapabilities,
    ConnectorStatus,
    ConversationRef,
    Identity,
    MessageContent,
    MessageEvent,
    SendReceipt,
    Target,
)
from appif.domain.messaging.ports import Connector, MessageListener

__all__ = [
    # Models
    "Account",
    "BackfillScope",
    "ConnectorCapabilities",
    "ConnectorStatus",
    "ConversationRef",
    "Identity",
    "MessageContent",
    "MessageEvent",
    "SendReceipt",
    "Target",
    # Errors
    "ConnectorError",
    "NotAuthorized",
    "NotSupported",
    "TargetUnavailable",
    "TransientFailure",
    # Ports
    "Connector",
    "MessageListener",
]
