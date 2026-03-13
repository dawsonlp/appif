"""Internal user identity cache for the Slack connector.

Caches resolved Slack user IDs → Identity objects to avoid repeated
``users.info`` API calls. TTL-based expiration.
"""

from __future__ import annotations

import time

from slack_sdk import WebClient

from appif.adapters.slack._rate_limiter import call_with_retry
from appif.domain.messaging.models import Identity


class UserCache:
    """TTL-based cache mapping Slack user IDs to Identity objects."""

    def __init__(self, client: WebClient, ttl_seconds: float = 3600.0):
        self._client = client
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[Identity, float]] = {}

    def resolve(self, user_id: str) -> Identity:
        """Resolve a Slack user ID to an Identity, using cache when valid."""
        cached = self._cache.get(user_id)
        if cached is not None:
            identity, expiry = cached
            if time.monotonic() < expiry:
                return identity

        # Cache miss or expired — fetch from Slack
        identity = self._fetch(user_id)
        self._cache[user_id] = (identity, time.monotonic() + self._ttl)
        return identity

    def invalidate(self, user_id: str) -> None:
        """Remove a user from the cache."""
        self._cache.pop(user_id, None)

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()

    def _fetch(self, user_id: str) -> Identity:
        """Fetch user info from Slack API."""
        response = call_with_retry(self._client.users_info, user=user_id)
        user_data = response.data.get("user", {})

        display_name = (
            user_data.get("profile", {}).get("display_name")
            or user_data.get("profile", {}).get("real_name")
            or user_data.get("real_name")
            or user_data.get("name")
            or user_id
        )

        return Identity(
            id=user_id,
            display_name=display_name,
            connector="slack",
        )
