"""Jira authentication and configuration.

Reads instance configuration from a YAML file (same format as the
jira-helper MCP server config) and creates authenticated Jira clients
using the ``atlassian-python-api`` library.

Config file location (checked in order):
1. ``APPIF_JIRA_CONFIG`` environment variable
2. ``~/.config/appif/jira/config.yaml``

YAML format::

    instances:
      personal:
        jira:
          url: https://dawsonlp.atlassian.net
          username: larry.dawson@gmail.com
          api_token: <token>

    default: personal
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from atlassian import Jira

from appif.domain.work_tracking.errors import ConnectionFailure, PermissionDenied

_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "appif" / "jira" / "config.yaml"


def _config_path() -> Path:
    env = os.environ.get("APPIF_JIRA_CONFIG")
    if env:
        return Path(env).expanduser()
    return _DEFAULT_CONFIG_PATH


def load_config() -> dict:
    """Load and return the raw YAML config dict.

    Returns an empty dict if the config file does not exist.
    """
    path = _config_path()
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def create_jira_client(server_url: str, credentials: dict[str, str]) -> Jira:
    """Create an authenticated Jira client.

    Parameters
    ----------
    server_url:
        Jira Cloud or Server URL (e.g. ``https://dawsonlp.atlassian.net``).
    credentials:
        Must contain ``username`` and ``api_token`` keys.

    Raises
    ------
    PermissionDenied
        If required credential keys are missing.
    ConnectionFailure
        If the client cannot reach the server.
    """
    username = credentials.get("username")
    api_token = credentials.get("api_token")
    if not username or not api_token:
        raise PermissionDenied("missing username or api_token in credentials")
    try:
        return Jira(
            url=server_url,
            username=username,
            password=api_token,
            cloud=True,
        )
    except Exception as exc:
        raise ConnectionFailure(str(exc)) from exc
