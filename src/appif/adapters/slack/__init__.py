"""Slack connector adapter.

All Slack SDK and Bolt internals are encapsulated here.
No Slack types leak through the public ``Connector`` interface.

Auth is pluggable via the ``SlackAuth`` protocol. The default
``StaticTokenAuth`` loads tokens from environment variables.
Inject a custom ``SlackAuth`` implementation for OAuth, secrets
managers, or any other credential strategy.
"""

from appif.adapters.slack._auth import SlackAuth, StaticTokenAuth
from appif.adapters.slack.connector import SlackConnector

__all__ = ["SlackConnector", "SlackAuth", "StaticTokenAuth"]
