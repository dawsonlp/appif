"""Quick proof-of-concept: connect to Slack and print incoming messages."""

from __future__ import annotations

import logging

from appif.adapters.slack._auth import StaticTokenAuth
from appif.adapters.slack.connector import SlackConnector
from appif.domain.messaging.models import MessageEvent

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")


class _Printer:
    def on_message(self, event: MessageEvent) -> None:
        print(f"[{event.timestamp:%H:%M:%S}] {event.author.display_name}: {event.content.text}")


def main() -> None:
    auth = StaticTokenAuth.from_env()
    conn = SlackConnector(
        identity_token=auth.identity_token,
        app_token=auth.app_token,
    )

    conn.register_listener(_Printer())
    conn.connect()
    conn.listen_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye")
