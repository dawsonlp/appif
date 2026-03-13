"""Gmail connector adapter.

Public exports:
- ``GmailConnector`` — the main connector class
- ``GmailAuth`` — auth protocol
- ``FileCredentialAuth`` — default auth implementation
"""

from appif.adapters.gmail._auth import FileCredentialAuth, GmailAuth
from appif.adapters.gmail.connector import GmailConnector

__all__ = ["GmailConnector", "GmailAuth", "FileCredentialAuth"]
