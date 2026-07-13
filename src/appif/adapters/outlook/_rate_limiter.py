"""Outlook Graph HTTP helpers — thin binding over the shared ``_graph.http`` engine.

The retry / back-off logic and error mapping live in
:mod:`appif.adapters._graph.http`; this module only binds the connector name so
errors and log events are attributed to "outlook".
"""

from __future__ import annotations

import httpx

from appif.adapters._graph import http

_CONNECTOR_NAME = "outlook"


def graph_request(method: str, url: str, **kwargs) -> httpx.Response:
    return http.graph_request(_CONNECTOR_NAME, method, url, **kwargs)


def graph_get(url: str, **kwargs) -> httpx.Response:
    return http.graph_get(_CONNECTOR_NAME, url, **kwargs)


def graph_post(url: str, **kwargs) -> httpx.Response:
    return http.graph_post(_CONNECTOR_NAME, url, **kwargs)
