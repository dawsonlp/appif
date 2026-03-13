"""Unit tests for messaging connector error hierarchy."""

from appif.domain.messaging.errors import (
    ConnectorError,
    NotAuthorized,
    NotSupported,
    TargetUnavailable,
    TransientFailure,
)


class TestConnectorError:
    def test_base_with_message(self):
        err = ConnectorError("slack", "something broke")
        assert str(err) == "[slack] something broke"
        assert err.connector == "slack"

    def test_base_without_message(self):
        err = ConnectorError("slack")
        assert str(err) == "[slack] connector error"

    def test_is_exception(self):
        assert issubclass(ConnectorError, Exception)


class TestNotAuthorized:
    def test_with_reason(self):
        err = NotAuthorized("slack", reason="token expired")
        assert "not authorized" in str(err)
        assert "token expired" in str(err)
        assert err.connector == "slack"
        assert err.reason == "token expired"

    def test_without_reason(self):
        err = NotAuthorized("slack")
        assert "not authorized" in str(err)
        assert err.reason == ""

    def test_inheritance(self):
        err = NotAuthorized("slack")
        assert isinstance(err, ConnectorError)
        assert isinstance(err, Exception)


class TestNotSupported:
    def test_with_operation(self):
        err = NotSupported("email", operation="backfill")
        assert "not supported" in str(err)
        assert "backfill" in str(err)
        assert err.operation == "backfill"

    def test_without_operation(self):
        err = NotSupported("email")
        assert "not supported" in str(err)
        assert err.operation == ""

    def test_inheritance(self):
        assert issubclass(NotSupported, ConnectorError)


class TestTargetUnavailable:
    def test_with_target_and_reason(self):
        err = TargetUnavailable("slack", target="#archived", reason="channel archived")
        assert "target unavailable" in str(err)
        assert "#archived" in str(err)
        assert "channel archived" in str(err)
        assert err.target == "#archived"
        assert err.reason == "channel archived"

    def test_with_target_only(self):
        err = TargetUnavailable("slack", target="C123")
        assert "C123" in str(err)

    def test_without_details(self):
        err = TargetUnavailable("slack")
        assert "target unavailable" in str(err)
        assert err.target == ""
        assert err.reason == ""

    def test_inheritance(self):
        assert issubclass(TargetUnavailable, ConnectorError)


class TestTransientFailure:
    def test_with_reason_and_retry(self):
        err = TransientFailure("slack", reason="rate limited", retry_after=30.0)
        assert "transient failure" in str(err)
        assert "rate limited" in str(err)
        assert "30.0s" in str(err)
        assert err.reason == "rate limited"
        assert err.retry_after == 30.0

    def test_with_reason_only(self):
        err = TransientFailure("slack", reason="timeout")
        assert "timeout" in str(err)
        assert err.retry_after is None

    def test_without_details(self):
        err = TransientFailure("slack")
        assert "transient failure" in str(err)

    def test_inheritance(self):
        assert issubclass(TransientFailure, ConnectorError)

    def test_catchable_as_base(self):
        try:
            raise TransientFailure("slack", reason="rate limited")
        except ConnectorError as e:
            assert isinstance(e, TransientFailure)


class TestErrorHierarchy:
    """All connector errors are catchable via ConnectorError."""

    def test_all_subclasses(self):
        for cls in (NotAuthorized, NotSupported, TargetUnavailable, TransientFailure):
            assert issubclass(cls, ConnectorError), f"{cls.__name__} should be a ConnectorError"
            assert issubclass(cls, Exception), f"{cls.__name__} should be an Exception"
