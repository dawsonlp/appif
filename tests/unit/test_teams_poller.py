"""Unit tests for the Teams poller (delta polling, dispatch, deltaLink reuse)."""

from __future__ import annotations

import appif.adapters.teams._poller as poller_mod
from appif.adapters.teams._poller import TeamsPoller


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _message(msg_id: str, sender: str = "U_OTHER") -> dict:
    return {
        "id": msg_id,
        "messageType": "message",
        "createdDateTime": "2026-06-08T10:00:00Z",
        "from": {"user": {"id": sender, "displayName": sender}},
        "body": {"contentType": "text", "content": f"body-{msg_id}"},
    }


def _make_poller(**kwargs) -> TeamsPoller:
    defaults = dict(
        access_token_fn=lambda: "tok",
        account_id="acct",
        authenticated_user_id="U_ME",
        callback=lambda e: None,
        include_chats=True,
        include_channels=False,
    )
    defaults.update(kwargs)
    return TeamsPoller(**defaults)


def test_poll_cycle_dispatches_and_stores_delta_link(monkeypatch):
    events = []
    calls = []

    def fake_graph_get(url, **kwargs):
        calls.append(url)
        if url.endswith("/me/chats"):
            return _FakeResponse({"value": [{"id": "C1"}]})
        if "/messages/delta" in url:
            return _FakeResponse({"value": [_message("m1")], "@odata.deltaLink": "https://graph/DELTA1"})
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(poller_mod, "graph_get", fake_graph_get)

    p = _make_poller(callback=events.append)
    p._poll_cycle()

    assert [e.message_id for e in events] == ["m1"]
    assert p._delta_links["chat:C1"] == "https://graph/DELTA1"


def test_second_cycle_reuses_delta_link(monkeypatch):
    seen_delta_urls = []

    def fake_graph_get(url, **kwargs):
        if url.endswith("/me/chats"):
            return _FakeResponse({"value": [{"id": "C1"}]})
        if "delta" in url.lower():
            seen_delta_urls.append(url)
            return _FakeResponse({"value": [], "@odata.deltaLink": "https://graph/DELTA2"})
        raise AssertionError(url)

    monkeypatch.setattr(poller_mod, "graph_get", fake_graph_get)

    p = _make_poller()
    p._poll_cycle()  # seeds delta link from initial /messages/delta
    p._poll_cycle()  # should reuse stored deltaLink

    assert seen_delta_urls[0].endswith("/me/chats/C1/messages/delta")
    assert seen_delta_urls[1] == "https://graph/DELTA2"


def test_pagination_follows_next_link(monkeypatch):
    events = []

    def fake_graph_get(url, **kwargs):
        if url.endswith("/me/chats"):
            return _FakeResponse({"value": [{"id": "C1"}]})
        if url.endswith("/messages/delta"):
            return _FakeResponse({"value": [_message("m1")], "@odata.nextLink": "https://graph/PAGE2"})
        if url == "https://graph/PAGE2":
            return _FakeResponse({"value": [_message("m2")], "@odata.deltaLink": "https://graph/DELTA"})
        raise AssertionError(url)

    monkeypatch.setattr(poller_mod, "graph_get", fake_graph_get)

    p = _make_poller(callback=events.append)
    p._poll_cycle()

    assert [e.message_id for e in events] == ["m1", "m2"]
    assert p._delta_links["chat:C1"] == "https://graph/DELTA"


def test_self_messages_suppressed_in_poll(monkeypatch):
    events = []

    def fake_graph_get(url, **kwargs):
        if url.endswith("/me/chats"):
            return _FakeResponse({"value": [{"id": "C1"}]})
        return _FakeResponse({"value": [_message("m1", sender="U_ME")], "@odata.deltaLink": "d"})

    monkeypatch.setattr(poller_mod, "graph_get", fake_graph_get)

    p = _make_poller(callback=events.append)  # include_sent defaults False
    p._poll_cycle()
    assert events == []
