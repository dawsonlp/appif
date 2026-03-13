"""Work tracking error hierarchy.

Adapters raise only these typed errors. Platform-specific exceptions
(HTTP errors, SDK exceptions, API error strings) are caught internally
and mapped to one of these types. They never leak through the public
interface.
"""

from __future__ import annotations


class WorkTrackingError(Exception):
    """Base error for all work tracking failures."""

    def __init__(self, message: str = "", instance: str | None = None):
        self.instance = instance
        prefix = f"[{instance}] " if instance else ""
        super().__init__(f"{prefix}{message}" if message else f"{prefix}work tracking error")


class ItemNotFound(WorkTrackingError):
    """Requested work item does not exist."""

    def __init__(self, key: str, instance: str | None = None):
        self.key = key
        super().__init__(f"item not found: {key}", instance)


class ProjectNotFound(WorkTrackingError):
    """Requested project does not exist."""

    def __init__(self, key: str, instance: str | None = None):
        self.key = key
        super().__init__(f"project not found: {key}", instance)


class PermissionDenied(WorkTrackingError):
    """Authentication or authorization failure."""

    def __init__(self, reason: str = "", instance: str | None = None):
        self.reason = reason
        msg = "permission denied"
        if reason:
            msg += f": {reason}"
        super().__init__(msg, instance)


class InvalidTransition(WorkTrackingError):
    """Requested transition is not available for the item's current state."""

    def __init__(self, key: str, transition: str, instance: str | None = None):
        self.key = key
        self.transition = transition
        super().__init__(f"invalid transition '{transition}' for item {key}", instance)


class ConnectionFailure(WorkTrackingError):
    """Network error or server unreachable."""

    def __init__(self, reason: str = "", instance: str | None = None):
        self.reason = reason
        msg = "connection failure"
        if reason:
            msg += f": {reason}"
        super().__init__(msg, instance)


class RateLimited(WorkTrackingError):
    """Too many requests. Includes retry-after if available."""

    def __init__(self, retry_after: float | None = None, instance: str | None = None):
        self.retry_after = retry_after
        msg = "rate limited"
        if retry_after is not None:
            msg += f" (retry after {retry_after}s)"
        super().__init__(msg, instance)


class InstanceNotFound(WorkTrackingError):
    """Referenced instance name is not registered."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"instance not found: {name}")


class NoDefaultInstance(WorkTrackingError):
    """Operation omitted instance and no default is set."""

    def __init__(self):
        super().__init__("no default instance configured")


class InstanceAlreadyRegistered(WorkTrackingError):
    """Attempted to register a name that already exists."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"instance already registered: {name}")
