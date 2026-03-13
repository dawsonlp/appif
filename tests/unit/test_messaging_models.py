"""Unit tests for messaging domain models."""

from datetime import UTC, datetime

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


class TestIdentity:
    def test_construction(self):
        identity = Identity(id="U123", display_name="Alice", connector="slack")
        assert identity.id == "U123"
        assert identity.display_name == "Alice"
        assert identity.connector == "slack"

    def test_frozen(self):
        identity = Identity(id="U123", display_name="Alice", connector="slack")
        try:
            identity.id = "U999"
            assert False, "Should not allow mutation"
        except AttributeError:
            pass

    def test_equality(self):
        a = Identity(id="U123", display_name="Alice", connector="slack")
        b = Identity(id="U123", display_name="Alice", connector="slack")
        assert a == b

    def test_inequality(self):
        a = Identity(id="U123", display_name="Alice", connector="slack")
        b = Identity(id="U456", display_name="Bob", connector="slack")
        assert a != b


class TestMessageContent:
    def test_text_only(self):
        content = MessageContent(text="Hello world")
        assert content.text == "Hello world"
        assert content.attachments == []

    def test_with_attachments(self):
        content = MessageContent(text="See attached", attachments=["file1.pdf"])
        assert content.attachments == ["file1.pdf"]

    def test_frozen(self):
        content = MessageContent(text="Hello")
        try:
            content.text = "Changed"
            assert False, "Should not allow mutation"
        except AttributeError:
            pass

    def test_default_attachments_not_shared(self):
        a = MessageContent(text="A")
        b = MessageContent(text="B")
        assert a.attachments is not b.attachments


class TestConversationRef:
    def test_construction(self):
        ref = ConversationRef(
            connector="slack",
            account_id="T123",
            type="channel",
            opaque_id={"channel": "C456"},
        )
        assert ref.connector == "slack"
        assert ref.account_id == "T123"
        assert ref.type == "channel"
        assert ref.opaque_id == {"channel": "C456"}

    def test_default_opaque_id(self):
        ref = ConversationRef(connector="slack", account_id="T123", type="dm")
        assert ref.opaque_id == {}

    def test_frozen(self):
        ref = ConversationRef(connector="slack", account_id="T123", type="channel")
        try:
            ref.type = "dm"
            assert False, "Should not allow mutation"
        except AttributeError:
            pass

    def test_equality(self):
        a = ConversationRef(connector="slack", account_id="T1", type="channel", opaque_id={"c": "C1"})
        b = ConversationRef(connector="slack", account_id="T1", type="channel", opaque_id={"c": "C1"})
        assert a == b


class TestMessageEvent:
    def _make_event(self, **overrides):
        defaults = dict(
            message_id="msg-001",
            connector="slack",
            account_id="T123",
            conversation_ref=ConversationRef(
                connector="slack", account_id="T123", type="channel", opaque_id={"channel": "C1"}
            ),
            author=Identity(id="U1", display_name="Alice", connector="slack"),
            timestamp=datetime(2026, 2, 18, 12, 0, 0, tzinfo=UTC),
            content=MessageContent(text="Hello"),
        )
        defaults.update(overrides)
        return MessageEvent(**defaults)

    def test_required_fields(self):
        event = self._make_event()
        assert event.message_id == "msg-001"
        assert event.connector == "slack"
        assert event.account_id == "T123"
        assert event.author.display_name == "Alice"
        assert event.content.text == "Hello"
        assert event.metadata == {}

    def test_with_metadata(self):
        event = self._make_event(metadata={"subtype": "bot_message"})
        assert event.metadata == {"subtype": "bot_message"}

    def test_frozen(self):
        event = self._make_event()
        try:
            event.message_id = "changed"
            assert False, "Should not allow mutation"
        except AttributeError:
            pass

    def test_equality(self):
        a = self._make_event()
        b = self._make_event()
        assert a == b


class TestSendReceipt:
    def test_construction(self):
        ts = datetime(2026, 2, 18, 12, 0, 0, tzinfo=UTC)
        receipt = SendReceipt(external_id="1708257600.000100", timestamp=ts)
        assert receipt.external_id == "1708257600.000100"
        assert receipt.timestamp == ts

    def test_frozen(self):
        ts = datetime(2026, 2, 18, 12, 0, 0, tzinfo=UTC)
        receipt = SendReceipt(external_id="ext-1", timestamp=ts)
        try:
            receipt.external_id = "changed"
            assert False, "Should not allow mutation"
        except AttributeError:
            pass


class TestConnectorCapabilities:
    def test_slack_capabilities(self):
        caps = ConnectorCapabilities(
            supports_realtime=True,
            supports_backfill=True,
            supports_threads=True,
            supports_reply=True,
            supports_auto_send=True,
            delivery_mode="AUTOMATIC",
        )
        assert caps.supports_realtime is True
        assert caps.supports_backfill is True
        assert caps.supports_threads is True
        assert caps.supports_reply is True
        assert caps.supports_auto_send is True
        assert caps.delivery_mode == "AUTOMATIC"

    def test_limited_capabilities(self):
        caps = ConnectorCapabilities(
            supports_realtime=False,
            supports_backfill=True,
            supports_threads=False,
            supports_reply=True,
            supports_auto_send=False,
            delivery_mode="MANUAL",
        )
        assert caps.supports_realtime is False
        assert caps.delivery_mode == "MANUAL"

    def test_frozen(self):
        caps = ConnectorCapabilities(
            supports_realtime=True,
            supports_backfill=True,
            supports_threads=True,
            supports_reply=True,
            supports_auto_send=True,
            delivery_mode="AUTOMATIC",
        )
        try:
            caps.supports_realtime = False
            assert False, "Should not allow mutation"
        except AttributeError:
            pass


class TestConnectorStatus:
    def test_values(self):
        assert ConnectorStatus.DISCONNECTED.value == "disconnected"
        assert ConnectorStatus.CONNECTING.value == "connecting"
        assert ConnectorStatus.CONNECTED.value == "connected"
        assert ConnectorStatus.ERROR.value == "error"

    def test_all_states(self):
        assert len(ConnectorStatus) == 4


class TestAccount:
    def test_construction(self):
        account = Account(account_id="T123", display_name="My Workspace", connector="slack")
        assert account.account_id == "T123"
        assert account.display_name == "My Workspace"
        assert account.connector == "slack"

    def test_frozen(self):
        account = Account(account_id="T123", display_name="WS", connector="slack")
        try:
            account.account_id = "T999"
            assert False, "Should not allow mutation"
        except AttributeError:
            pass


class TestTarget:
    def test_construction(self):
        target = Target(target_id="C123", display_name="#general", type="channel", account_id="T1")
        assert target.target_id == "C123"
        assert target.display_name == "#general"
        assert target.type == "channel"
        assert target.account_id == "T1"

    def test_frozen(self):
        target = Target(target_id="C123", display_name="#gen", type="channel", account_id="T1")
        try:
            target.type = "dm"
            assert False, "Should not allow mutation"
        except AttributeError:
            pass


class TestBackfillScope:
    def test_defaults(self):
        scope = BackfillScope()
        assert scope.conversation_ids == ()
        assert scope.oldest is None
        assert scope.latest is None

    def test_with_values(self):
        oldest = datetime(2026, 1, 1, tzinfo=UTC)
        latest = datetime(2026, 2, 1, tzinfo=UTC)
        scope = BackfillScope(conversation_ids=("C1", "C2"), oldest=oldest, latest=latest)
        assert scope.conversation_ids == ("C1", "C2")
        assert scope.oldest == oldest
        assert scope.latest == latest

    def test_frozen(self):
        scope = BackfillScope()
        try:
            scope.oldest = datetime.now(tz=UTC)
            assert False, "Should not allow mutation"
        except AttributeError:
            pass
