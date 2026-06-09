"""Microsoft Teams messaging connector.

All Graph and MSAL internals are encapsulated here; no Microsoft-specific
types leak through the public ``Connector`` interface.

Auth is pluggable via the ``TeamsAuth`` protocol. The default ``MsalAuth``
loads tokens from a persisted MSAL cache file in a Teams-specific directory.
"""

from appif.adapters.teams._auth import MsalAuth, TeamsAuth
from appif.adapters.teams.connector import TeamsConnector

__all__ = ["TeamsConnector", "TeamsAuth", "MsalAuth"]
