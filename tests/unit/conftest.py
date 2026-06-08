"""Unit-test fixtures — hermetic environment isolation.

Unit tests must not depend on the developer's shell or ``~/.env`` file, and
must not leak environment state into one another.

This matters because some auth providers (e.g. ``FileCredentialAuth``) call
``load_dotenv(~/.env)`` during construction. That permanently injects real
``APPIF_*`` values into ``os.environ`` — and ``monkeypatch`` only restores the
keys *it* set, so a dotenv-injected variable survives into later tests. A test
that asserts on a variable being *absent* (e.g. "missing client_id raises")
then fails depending on test ordering and on whose machine it runs.

The autouse fixture below snapshots the environment, strips ``APPIF_*`` up
front so every unit test starts from a known-clean slate, and fully restores
the original environment afterward — wiping any dotenv leak.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def isolate_appif_env():
    """Give each unit test a clean, fully-restored ``APPIF_*`` environment."""
    saved = dict(os.environ)
    for key in [k for k in os.environ if k.startswith("APPIF_")]:
        del os.environ[key]
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)
