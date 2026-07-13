"""Tests for the cross-cutting error hierarchy.

These tests verify the business-meaningful outcome: callers can catch errors
at the right granularity level after the re-parenting consolidation.
"""

import pytest

from appif.domain.errors import AppifError
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
