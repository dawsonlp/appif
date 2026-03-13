"""Connector and listener protocols.

These define the stable public surface area for all messaging connectors.
Nothing here mentions Slack, Bolt, Teams, or any specific platform SDK.
"""

from __future__ import annotations

from typing import Protocol

from appif.domain.messaging.models import (
    Account,
    BackfillScope,
    ConnectorCapabilities,
    ConnectorStatus,
    ConversationRef,
    MessageContent,
    MessageEvent,
    SendReceipt,
    Target,
)


class MessageListener(Protocol):
    """Sink for inbound message events.

    Design rules:
    - Fire-and-forget: connector must not block on listener execution.
    - At-least-once delivery: listener code must be idempotent.
    - No return values: interpretation happens elsewhere.
    - No backpressure coupling: connector queues internally if needed.
    """

    def on_message(self, event: MessageEvent) -> None: ...


class Connector(Protocol):
    """Public interface for a messaging connector.

    This is the entire surface area any upstream system sees.
    Implementations are platform-specific (Slack, Teams, Email)
    but this protocol is platform-agnostic.
    """

    # -- Lifecycle -----------------------------------------------------------

    def connect(self) -> None:
        """Authenticate and begin receiving events."""
        ...

    def disconnect(self) -> None:
        """Tear down connections and stop event ingestion."""
        ...

    def get_status(self) -> ConnectorStatus:
        """Return current lifecycle state."""
        ...

    # -- Discovery -----------------------------------------------------------

    def list_accounts(self) -> list[Account]:
        """List configured workspaces / accounts."""
        ...

    def list_targets(self, account_id: str) -> list[Target]:
        """List available channels, DMs, groups within an account."""
        ...

    # -- Inbound -------------------------------------------------------------

    def register_listener(self, listener: MessageListener) -> None:
        """Subscribe a listener to receive inbound message events."""
        ...

    def unregister_listener(self, listener: MessageListener) -> None:
        """Remove a previously registered listener."""
        ...

    # -- Outbound ------------------------------------------------------------

    def send(self, conversation: ConversationRef, content: MessageContent) -> SendReceipt:
        """Send a message to the conversation identified by the ref.

        The caller does not specify platform — it replies using the
        ConversationRef it received. The connector resolves routing.
        """
        ...

    # -- Durability ----------------------------------------------------------

    def backfill(self, account_id: str, scope: BackfillScope) -> None:
        """Retrieve historical messages and emit to registered listeners.

        Realtime and backfill are explicitly separate. This is an explicit
        call — on startup, on schedule, or during failure recovery.
        """
        ...

    # -- Capability introspection --------------------------------------------

    def get_capabilities(self) -> ConnectorCapabilities:
        """Return what this connector supports."""
        ...
