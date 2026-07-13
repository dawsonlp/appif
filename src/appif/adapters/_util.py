"""Small helpers shared across adapters."""

from __future__ import annotations

import os


def env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable. Truthy: 1/true/yes/on."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
