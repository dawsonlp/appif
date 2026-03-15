"""Cross-cutting error hierarchy for the appif ecosystem.

All adapter errors across both appif and appif_ext descend from AppifError.
Domain-specific error modules (messaging, work_tracking) re-parent their
bases under AppifError so that callers can catch at any granularity level:

    except AppifError          -- catches everything
    except ConnectorError      -- catches messaging errors only
    except AuthenticationError -- catches auth failures from any adapter
"""

from __future__ import annotations


class AppifError(Exception):
    """Base error for all application_interfaces errors."""


class AuthenticationError(AppifError):
    """Failed to authenticate with the service."""

    def __init__(self, service: str, reason: str = ""):
        self.service = service
        self.reason = reason
        msg = f"Authentication failed for {service}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class CredentialError(AppifError):
    """Required credentials are missing or invalid."""

    def __init__(self, service: str, missing_keys: tuple[str, ...] = ()):
        self.service = service
        self.missing_keys = missing_keys
        if missing_keys:
            msg = f"Missing credentials for {service}: {', '.join(missing_keys)}"
        else:
            msg = f"Invalid credentials for {service}"
        super().__init__(msg)


class ResourceNotFoundError(AppifError):
    """Requested resource does not exist or is inaccessible."""

    def __init__(self, resource: str, detail: str = ""):
        self.resource = resource
        self.detail = detail
        msg = f"Resource not found: {resource}"
        if detail:
            msg += f" ({detail})"
        super().__init__(msg)


class TransientError(AppifError):
    """Temporary failure -- rate limit, network timeout, etc. Safe to retry."""

    def __init__(self, service: str, reason: str = "", retry_after: float | None = None):
        self.service = service
        self.reason = reason
        self.retry_after = retry_after
        msg = f"Transient failure for {service}"
        if reason:
            msg += f": {reason}"
        if retry_after is not None:
            msg += f" (retry after {retry_after}s)"
        super().__init__(msg)


class NotSupportedError(AppifError):
    """Requested operation is not available for this adapter."""

    def __init__(self, service: str, operation: str = ""):
        self.service = service
        self.operation = operation
        msg = f"Operation not supported by {service}"
        if operation:
            msg += f": {operation}"
        super().__init__(msg)