"""Shared base for messaging connectors.

Provides the connector plumbing that is identical across every adapter: the
listener registry, status tracking, a not-connected guard, and fire-and-forget
dispatch of events to listeners on a thread pool.

The thread pool matters for correctness, not just tidiness: the
:class:`~appif.domain.messaging.ports.MessageListener` contract requires that
"the connector must not block on listener execution". Dispatching on a pool
keeps a slow or misbehaving listener from stalling a connector's poll loop or
socket thread.

Concrete subclasses set the ``connector_name`` class attribute and implement the
platform-specific parts of the Connector protocol (connect, disconnect, send,
backfill, list_accounts, list_targets, get_capabilities). They call
``_start_dispatch()`` from ``connect()`` and ``_stop_dispatch()`` from
``disconnect()``, and hand inbound events to ``_dispatch()``.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from appif.domain.messaging.errors import NotSupported
from appif.domain.messaging.models import ConnectorStatus, MessageEvent
from appif.domain.messaging.ports import MessageListener

logger = logging.getLogger(__name__)


class BaseMessagingConnector:
    """Common plumbing shared by all messaging connectors."""

    #: Overridden by each subclass; used in errors and dispatch log events.
    connector_name: str = "connector"
    #: Size of the listener-dispatch thread pool.
    _dispatch_workers: int = 4

    def __init__(self) -> None:
        self._status = ConnectorStatus.DISCONNECTED
        self._listeners: list[MessageListener] = []
        self._listeners_lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None

    # -- Status --------------------------------------------------------------

    def get_status(self) -> ConnectorStatus:
        return self._status

    def _ensure_connected(self) -> None:
        """Raise if the connector is not in CONNECTED state."""
        if self._status != ConnectorStatus.CONNECTED:
            raise NotSupported(self.connector_name, operation=f"not connected (status={self._status.value})")

    # -- Listener registry ---------------------------------------------------

    def register_listener(self, listener: MessageListener) -> None:
        with self._listeners_lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unregister_listener(self, listener: MessageListener) -> None:
        with self._listeners_lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    # -- Dispatch (fire-and-forget) ------------------------------------------

    def _start_dispatch(self) -> None:
        """Create the dispatch thread pool. Called from ``connect()``."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._dispatch_workers,
                thread_name_prefix=f"{self.connector_name}-dispatch",
            )

    def _stop_dispatch(self, *, wait: bool = True) -> None:
        """Tear down the dispatch thread pool. Called from ``disconnect()``."""
        if self._executor is not None:
            self._executor.shutdown(wait=wait, cancel_futures=False)
            self._executor = None

    def _dispatch(self, event: MessageEvent) -> None:
        """Deliver an event to every listener without blocking the caller."""
        with self._listeners_lock:
            listeners = list(self._listeners)

        executor = self._executor
        for listener in listeners:
            if executor is not None:
                executor.submit(self._safe_listener_call, listener, event)
            else:
                # No live dispatch pool (e.g. event produced outside a connected
                # session) — deliver inline rather than dropping the event.
                self._safe_listener_call(listener, event)

    def _safe_listener_call(self, listener: MessageListener, event: MessageEvent) -> None:
        """Invoke a listener, catching and logging any errors."""
        try:
            listener.on_message(event)
        except Exception:
            logger.exception(
                f"{self.connector_name}.listener_error",
                extra={"listener": type(listener).__name__, "message_id": event.message_id},
            )
