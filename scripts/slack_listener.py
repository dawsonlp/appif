#!/usr/bin/env python
"""Slack listener — verification script for the one-identity model.

Reads ``APPIF_SLACK_IDENTITY_TOKEN`` and ``APPIF_SLACK_APP_TOKEN`` from
the environment (via ``~/.env``), prints identity type, capabilities,
and delivery mode, then connects and listens for messages.

If no app-level token is provided the script demonstrates graceful
degradation: it reports API-only mode and skips real-time listening.

Usage::

    python scripts/slack_listener.py
"""

from __future__ import annotations

import logging
import sys

from appif.adapters.slack._auth import StaticTokenAuth
from appif.adapters.slack.connector import SlackConnector
from appif.domain.messaging.models import MessageEvent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)


class _PrintListener:
    """Simple listener that prints every message to stdout."""

    def on_message(self, event: MessageEvent) -> None:
        print(
            f"[{event.timestamp:%H:%M:%S}] "
            f"{event.author.display_name}: {event.content.text}"
        )


def main() -> None:
    auth = StaticTokenAuth.from_env()

    # --- report identity and capabilities ---
    connector = SlackConnector(
        identity_token=auth.identity_token,
        app_token=auth.app_token,
    )

    caps = connector.get_capabilities()

    print("--- Slack Connector Identity Report ---")
    print(f"  identity_type     : {auth.identity_type}")
    print(f"  supports_realtime : {caps.supports_realtime}")
    print(f"  delivery_mode     : {caps.delivery_mode}")
    print(f"  supports_backfill : {caps.supports_backfill}")
    print(f"  supports_reply    : {caps.supports_reply}")
    print(f"  supports_threads  : {caps.supports_threads}")
    print()

    if not caps.supports_realtime:
        print("No app-level token provided — API-only mode.")
        print("Provide APPIF_SLACK_APP_TOKEN to enable real-time listening.")
        sys.exit(0)

    # --- connect and listen ---
    connector.register_listener(_PrintListener())
    print("Connecting …")
    connector.connect()
    print("Listening for messages (Ctrl+C to stop)\n")
    connector.listen_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
