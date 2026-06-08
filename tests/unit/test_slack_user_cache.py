"""Unit tests for the Slack UserCache identity resolution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from appif.adapters.slack._user_cache import UserCache


def _client_returning(user: dict) -> MagicMock:
    client = MagicMock()
    client.users_info.return_value = SimpleNamespace(data={"user": user})
    return client


class TestUserCacheResolve:
    def test_resolves_display_name_and_email(self):
        client = _client_returning({"id": "U1", "profile": {"display_name": "Alice", "email": "alice@x.com"}})
        identity = UserCache(client).resolve("U1")
        assert identity.id == "U1"
        assert identity.display_name == "Alice"
        assert identity.email == "alice@x.com"

    def test_email_none_when_scope_absent(self):
        # Without users:read.email the profile has no email key.
        client = _client_returning({"id": "U1", "profile": {"display_name": "Alice"}})
        identity = UserCache(client).resolve("U1")
        assert identity.email is None

    def test_empty_email_normalized_to_none(self):
        client = _client_returning({"id": "U1", "profile": {"display_name": "Alice", "email": ""}})
        identity = UserCache(client).resolve("U1")
        assert identity.email is None

    def test_display_name_falls_back_through_profile(self):
        client = _client_returning({"id": "U1", "profile": {"real_name": "Real Alice"}})
        identity = UserCache(client).resolve("U1")
        assert identity.display_name == "Real Alice"

    def test_cache_hit_avoids_second_api_call(self):
        client = _client_returning({"id": "U1", "profile": {"display_name": "Alice"}})
        cache = UserCache(client)
        cache.resolve("U1")
        cache.resolve("U1")
        assert client.users_info.call_count == 1
