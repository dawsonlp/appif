"""Central configuration discovery for appif.

All appif configuration lives under a single, discoverable base directory --
the *config dir* -- with one subdirectory per service::

    ~/.config/appif/
        gmail/config.yaml     + <email>.json      (OAuth token cache)
        outlook/config.yaml   + <account>.json    (MSAL token cache)
        teams/config.yaml     + <account>.json    (MSAL token cache)
        slack/config.yaml
        jira/config.yaml

The base directory is resolved (highest precedence first) from:

1. ``APPIF_CONFIG_DIR``
2. ``XDG_CONFIG_HOME/appif``
3. ``~/.config/appif`` (default)

Each messaging service's ``config.yaml`` describes one or more named accounts::

    accounts:
      default:
        client_id: ...
        tenant_id: common
      work:
        client_id: ...
    default: default

Jira keeps its own ``instances:`` shape (see ``adapters.jira._auth``).

Per-setting resolution precedence, applied by each adapter, is:

1. Explicit constructor argument
2. The selected account in ``<service>/config.yaml``
3. Environment variable (values may be sourced from ``~/.env``)

``~/.env`` is treated as shared *source* data -- it is loaded, never written,
so other programs on the machine can keep using it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

#: Well-known name of the per-service config file within each service directory.
CONFIG_FILENAME = "config.yaml"

_env_loaded = False


def config_dir() -> Path:
    """Return the single base directory that holds all appif configuration.

    Honours ``APPIF_CONFIG_DIR`` then ``XDG_CONFIG_HOME``; defaults to
    ``~/.config/appif``.
    """
    override = os.environ.get("APPIF_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "appif"
    return Path.home() / ".config" / "appif"


def service_dir(service: str) -> Path:
    """Return ``<config_dir>/<service>`` (e.g. the Gmail directory)."""
    return config_dir() / service


def service_config_path(service: str) -> Path:
    """Return the path to ``<config_dir>/<service>/config.yaml``."""
    return service_dir(service) / CONFIG_FILENAME


def env_file() -> Path:
    """Return the shared env file path (``APPIF_ENV_FILE`` or ``~/.env``)."""
    override = os.environ.get("APPIF_ENV_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".env"


def load_env(*, force: bool = False) -> Path | None:
    """Load the shared env file into ``os.environ`` if it exists.

    Idempotent: only loads once per process unless ``force`` is given. Existing
    environment variables are never overridden (``load_dotenv`` default), so a
    variable already exported in the shell wins over the file. Returns the path
    that was loaded, or ``None`` if no env file was found.
    """
    global _env_loaded
    if _env_loaded and not force:
        return None
    path = env_file()
    if not path.exists():
        _env_loaded = True
        return None
    try:
        from dotenv import load_dotenv

        load_dotenv(path)
    except ImportError:
        return None
    _env_loaded = True
    return path


def load_service_config(service: str) -> dict[str, Any]:
    """Load and return the raw YAML dict for a service, or ``{}`` if absent."""
    path = service_config_path(service)
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {}
    return data


def account_names(service: str, *, section: str = "accounts") -> list[str]:
    """Return the configured account names for a service (empty if none)."""
    accounts = load_service_config(service).get(section) or {}
    if not isinstance(accounts, dict):
        return []
    return list(accounts.keys())


def select_account(
    service: str,
    account: str | None = None,
    *,
    env_account_var: str | None = None,
    section: str = "accounts",
    fallback: str = "default",
) -> tuple[str, dict[str, Any]]:
    """Resolve which account to use and return ``(name, settings)``.

    The account name is chosen (highest precedence first) from the explicit
    ``account`` argument, the ``env_account_var`` environment variable, the
    file's ``default:`` key, then ``fallback`` (pass ``fallback=""`` when the
    caller wants an unresolved account to stay empty rather than defaulting).
    ``settings`` is the mapping for that account, or ``{}`` when no config file /
    account matches -- callers then fall back to environment variables per the
    documented precedence.
    """
    config = load_service_config(service)
    accounts = config.get(section) or {}
    if not isinstance(accounts, dict):
        accounts = {}

    name = account
    if not name and env_account_var:
        name = os.environ.get(env_account_var) or None
    if not name:
        default = config.get("default")
        name = default if isinstance(default, str) else None
    if not name:
        name = fallback

    settings = accounts.get(name)
    if not isinstance(settings, dict):
        settings = {}
    return name, settings
