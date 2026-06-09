"""Delta-query polling loop for inbound Teams messages.

Runs in a daemon thread. Each cycle it enumerates the user's chats (so new
conversations are picked up) and the cached channel set, then issues a
per-source ``messages/delta`` query, normalises new messages, and dispatches
them via the callback.

Real-time delivery (Graph change-notification subscriptions) is out of scope
for v1; this mirrors the Outlook delta-polling model.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from appif.adapters.teams._normalizer import normalize_message
from appif.adapters.teams._rate_limiter import graph_get
from appif.domain.messaging.errors import NotAuthorized, TargetUnavailable
from appif.domain.messaging.models import MessageEvent

logger = logging.getLogger(__name__)

_CONNECTOR_NAME = "teams"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class TeamsPoller:
    """Polls Graph delta queries for new chat and channel messages.

    Parameters
    ----------
    access_token_fn:
        Callable returning a current access token string.
    account_id / authenticated_user_id:
        Logical account label and the connector's own AAD user id.
    poll_interval:
        Seconds between poll cycles.
    callback:
        Invoked with each normalised ``MessageEvent``.
    include_sent:
        Forwarded to the normalizer (surface own messages when ``True``).
    include_chats / include_channels:
        Toggle the two source kinds. Channels require admin-consented
        ``ChannelMessage.Read.All``; when that consent is missing the channel
        queries fail and are logged, leaving chats unaffected.
    """

    def __init__(
        self,
        *,
        access_token_fn: Callable[[], str],
        account_id: str,
        authenticated_user_id: str,
        poll_interval: int = 30,
        callback: Callable[[MessageEvent], None],
        include_sent: bool = False,
        include_chats: bool = True,
        include_channels: bool = True,
    ) -> None:
        self._access_token_fn = access_token_fn
        self._account_id = account_id
        self._authenticated_user_id = authenticated_user_id
        self._poll_interval = poll_interval
        self._callback = callback
        self._include_sent = include_sent
        self._include_chats = include_chats
        self._include_channels = include_channels

        # source key -> deltaLink
        self._delta_links: dict[str, str] = {}
        # cached (team_id, channel_id) pairs, discovered once at start
        self._channels: list[tuple[str, str]] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if self._include_channels:
            try:
                self._channels = self._discover_channels()
            except Exception as exc:
                logger.warning("teams.poller.channel_discovery_failed", extra={"error": str(exc)})
                self._channels = []
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="teams-poller", daemon=True)
        self._thread.start()
        logger.info(
            "teams.poller.started",
            extra={"interval": self._poll_interval, "channels": len(self._channels), "chats": self._include_chats},
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval * 2)
            self._thread = None
        logger.info("teams.poller.stopped")

    # ── Polling ───────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_cycle()
            except Exception:
                logger.exception("teams.poller.cycle_error")
            self._stop_event.wait(timeout=self._poll_interval)

    def _poll_cycle(self) -> None:
        if self._include_chats:
            try:
                for chat_id in self._discover_chats():
                    self._poll_source(
                        key=f"chat:{chat_id}",
                        initial_url=f"{_GRAPH_BASE}/me/chats/{chat_id}/messages/delta",
                        chat_id=chat_id,
                    )
            except Exception:
                logger.exception("teams.poller.chat_discovery_error")

        for team_id, channel_id in self._channels:
            self._poll_source(
                key=f"channel:{team_id}:{channel_id}",
                initial_url=f"{_GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages/delta",
                team_id=team_id,
                channel_id=channel_id,
            )

    def _poll_source(
        self,
        *,
        key: str,
        initial_url: str,
        chat_id: str | None = None,
        team_id: str | None = None,
        channel_id: str | None = None,
    ) -> None:
        """Page through one source's delta, dispatch new messages, store the deltaLink."""
        url: str | None = self._delta_links.get(key, initial_url)
        headers = {"Authorization": f"Bearer {self._access_token_fn()}"}

        while url and not self._stop_event.is_set():
            try:
                response = graph_get(url, headers=headers)
            except TargetUnavailable:
                # 404 — source gone or delta expired; drop the link to re-seed next cycle.
                logger.info("teams.poller.source_unavailable", extra={"source": key})
                self._delta_links.pop(key, None)
                return
            except NotAuthorized:
                logger.warning("teams.poller.unauthorized", extra={"source": key})
                return
            except Exception:
                logger.exception("teams.poller.request_error", extra={"source": key})
                return

            data = response.json()
            for msg in data.get("value", []):
                event = normalize_message(
                    msg,
                    account_id=self._account_id,
                    authenticated_user_id=self._authenticated_user_id,
                    chat_id=chat_id,
                    team_id=team_id,
                    channel_id=channel_id,
                    include_sent=self._include_sent,
                )
                if event is not None:
                    self._dispatch(event)

            next_link = data.get("@odata.nextLink")
            delta_link = data.get("@odata.deltaLink")
            if next_link:
                url = next_link
            else:
                if delta_link:
                    self._delta_links[key] = delta_link
                url = None

    def _dispatch(self, event: MessageEvent) -> None:
        try:
            self._callback(event)
        except Exception:
            logger.exception("teams.poller.callback_error", extra={"message_id": event.message_id})

    # ── Discovery ─────────────────────────────────────────────

    def _discover_chats(self) -> list[str]:
        """Return the user's chat ids (1:1, group, meeting)."""
        headers = {"Authorization": f"Bearer {self._access_token_fn()}"}
        ids: list[str] = []
        url: str | None = f"{_GRAPH_BASE}/me/chats"
        params: dict | None = {"$top": 50}
        while url:
            response = graph_get(url, headers=headers, params=params)
            data = response.json()
            ids.extend(c["id"] for c in data.get("value", []) if c.get("id"))
            url = data.get("@odata.nextLink")
            params = None  # nextLink carries its own params
        return ids

    def _discover_channels(self) -> list[tuple[str, str]]:
        """Return (team_id, channel_id) pairs across the user's joined teams."""
        headers = {"Authorization": f"Bearer {self._access_token_fn()}"}
        pairs: list[tuple[str, str]] = []

        teams_resp = graph_get(f"{_GRAPH_BASE}/me/joinedTeams", headers=headers, params={"$top": 50})
        for team in teams_resp.json().get("value", []):
            team_id = team.get("id")
            if not team_id:
                continue
            ch_resp = graph_get(f"{_GRAPH_BASE}/teams/{team_id}/channels", headers=headers)
            for channel in ch_resp.json().get("value", []):
                channel_id = channel.get("id")
                if channel_id:
                    pairs.append((team_id, channel_id))
        return pairs
