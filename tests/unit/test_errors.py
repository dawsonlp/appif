"""Tests for the cross-cutting error hierarchy.

These tests verify the business-meaningful outcome: callers can catch errors
at the right granularity level after the re-parenting consolidation.
"""

import pytest

from appif.domain.errors import (
    AppifError,
    AuthenticationError,
    CredentialError,
    NotSupportedError,
    ResourceNotFoundError,
    TransientError,
)
from appif.domain.messaging.errors import (
    ConnectorError,
    NotAuthorized,
    NotSupported,
    TargetUnavailable,
    TransientFailure,
)
from appif.domain.work_tracking.errors import (
    ConnectionFailure,
    InstanceAlreadyRegistered,
    InstanceNotFound,
    InvalidTransition,
    ItemNotFound,
    NoDefaultInstance,
    PermissionDenied,
    ProjectNotFound,
    RateLimited,
    WorkTrackingError,
)


class TestAppifErrorCatchesAll:
    """except AppifError catches errors from every domain."""

    @pytest.mark.parametrize(
        "error",
        [
            NotAuthorized("gmail"),
            NotSupported("gmail", "reply"),
            TargetUnavailable("slack", "channel"),
            TransientFailure("outlook", "timeout"),
            PermissionDenied("denied", None),
            ConnectionFailure("timeout", None),
            RateLimited(30.0, None),
            ItemNotFound("PROJ-1", None),
            ProjectNotFound("PROJ", None),
            InvalidTransition("PROJ-1", "close", None),
            InstanceNotFound("prod"),
            NoDefaultInstance(),
            InstanceAlreadyRegistered("prod"),
            AuthenticationError("fathom", "bad key"),
            CredentialError("fathom", ("API_KEY",)),
            ResourceNotFoundError("/recording/1"),
            TransientError("fathom", "rate limited", 30.0),
            NotSupportedError("fathom", "delete"),
        ],
        ids=lambda e: type(e).__name__,
    )
    def test_appif_error_catches(self, error):
        with pytest.raises(AppifError):
            raise error


class TestDomainBasesStillCatch:
    """Existing except ConnectorError / WorkTrackingError still work."""

    @pytest.mark.parametrize(
        "error",
        [
            NotAuthorized("gmail"),
            NotSupported("gmail", "reply"),
            TargetUnavailable("slack", "#general"),
            TransientFailure("outlook", "timeout"),
        ],
        ids=lambda e: type(e).__name__,
    )
    def test_connector_error_catches_messaging(self, error):
        with pytest.raises(ConnectorError):
            raise error

    @pytest.mark.parametrize(
        "error",
        [
            PermissionDenied("denied"),
            ConnectionFailure("timeout"),
            RateLimited(30.0),
            ItemNotFound("PROJ-1"),
            ProjectNotFound("PROJ"),
            InvalidTransition("PROJ-1", "close"),
            InstanceNotFound("prod"),
            NoDefaultInstance(),
            InstanceAlreadyRegistered("prod"),
        ],
        ids=lambda e: type(e).__name__,
    )
    def test_work_tracking_error_catches(self, error):
        with pytest.raises(WorkTrackingError):
            raise error


class TestCrossCuttingErrorAttributes:
    """Cross-cutting error types expose their attributes correctly."""

    def test_authentication_error_attributes(self):
        err = AuthenticationError("fathom", reason="invalid API key")
        assert err.service == "fathom"
        assert err.reason == "invalid API key"
        assert "fathom" in str(err)
        assert "invalid API key" in str(err)

    def test_credential_error_attributes(self):
        err = CredentialError("fathom", missing_keys=("FATHOM_API_KEY",))
        assert err.service == "fathom"
        assert err.missing_keys == ("FATHOM_API_KEY",)
        assert "FATHOM_API_KEY" in str(err)

    def test_resource_not_found_attributes(self):
        err = ResourceNotFoundError("/recording/42", detail="returned 404")
        assert err.resource == "/recording/42"
        assert err.detail == "returned 404"

    def test_transient_error_attributes(self):
        err = TransientError("fathom", reason="rate limited", retry_after=60.0)
        assert err.service == "fathom"
        assert err.reason == "rate limited"
        assert err.retry_after == 60.0

    def test_not_supported_error_attributes(self):
        err = NotSupportedError("fathom", operation="delete_recording")
        assert err.service == "fathom"
        assert err.operation == "delete_recording"


class TestDomainErrorsDoNotCrossCatch:
    """ConnectorError does not catch WorkTrackingError and vice versa."""

    def test_connector_error_does_not_catch_work_tracking(self):
        with pytest.raises(WorkTrackingError):
            try:
                raise ItemNotFound("PROJ-1")
            except ConnectorError:
                pytest.fail("ConnectorError should not catch WorkTrackingError")

    def test_work_tracking_error_does_not_catch_connector(self):
        with pytest.raises(ConnectorError):
            try:
                raise NotAuthorized("gmail")
            except WorkTrackingError:
                pytest.fail("WorkTrackingError should not catch ConnectorError")
