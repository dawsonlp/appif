"""Connector error hierarchy.

Connectors raise only these typed errors. Platform-specific exceptions
(HTTP errors, SDK exceptions, API error strings) are caught internally
and mapped to one of these types. They never leak through the public
interface.
"""

from appif.domain.errors import AppifError


class ConnectorError(AppifError):
    """Base error for all connector failures."""

    def __init__(self, connector: str, message: str = ""):
        self.connector = connector
        msg = f"[{connector}] {message}" if message else f"[{connector}] connector error"
        super().__init__(msg)


class NotAuthorized(ConnectorError):
    """Authentication failure, expired token, or revoked access."""

    def __init__(self, connector: str, reason: str = ""):
        self.reason = reason
        msg = "not authorized"
        if reason:
            msg += f": {reason}"
        super().__init__(connector, msg)


class NotSupported(ConnectorError):
    """Requested operation is not available for this connector."""

    def __init__(self, connector: str, operation: str = ""):
        self.operation = operation
        msg = "operation not supported"
        if operation:
            msg += f": {operation}"
        super().__init__(connector, msg)


class TargetUnavailable(ConnectorError):
    """Channel, DM, workspace, or other target is not reachable."""

    def __init__(self, connector: str, target: str = "", reason: str = ""):
        self.target = target
        self.reason = reason
        msg = "target unavailable"
        if target:
            msg += f": {target}"
        if reason:
            msg += f" ({reason})"
        super().__init__(connector, msg)


class TransientFailure(ConnectorError):
    """Temporary failure — rate limit, network timeout, etc. Safe to retry."""

    def __init__(self, connector: str, reason: str = "", retry_after: float | None = None):
        self.reason = reason
        self.retry_after = retry_after
        msg = "transient failure"
        if reason:
            msg += f": {reason}"
        if retry_after is not None:
            msg += f" (retry after {retry_after}s)"
        super().__init__(connector, msg)
