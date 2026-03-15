"""Cross-cutting lifecycle protocol for connectable adapters.

Any adapter that manages a connection (OAuth session, API client, WebSocket,
browser session) can satisfy the Connectable protocol by implementing
connect(), disconnect(), and get_status().
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol


class SourceStatus(Enum):
    """Lifecycle state of a connectable source."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class Connectable(Protocol):
    """Protocol for adapters that manage a connection lifecycle."""

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def get_status(self) -> SourceStatus: ...
