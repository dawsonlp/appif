"""Outlook / Microsoft 365 messaging connector.

All Graph SDK and MSAL internals are encapsulated here.
No Microsoft-specific types leak through the public ``Connector`` interface.

Auth is pluggable via the ``OutlookAuth`` protocol. The default
``MsalAuth`` loads tokens from a persisted MSAL cache file.
"""

from appif.adapters.outlook._auth import MsalAuth, OutlookAuth
from appif.adapters.outlook.connector import OutlookConnector

__all__ = ["OutlookConnector", "OutlookAuth", "MsalAuth"]
