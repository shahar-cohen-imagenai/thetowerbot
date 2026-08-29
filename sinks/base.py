"""Base class for every event sink.

Sinks own a bounded queue and a consumer thread. offer() only ever does
put_nowait, so the publishing thread - the scan loop - is never blocked by
a slow consumer. Overflow is dropped and counted by the bus.
"""

from __future__ import annotations

import logging
import queue
import threading

import events

logger = logging.getLogger("tower_bot.sinks")

_SHUTDOWN = object()


class QueueSink:
    def __init__(self, maxsize: int = 1000) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._consume, name=type(self).__name__, daemon=True
        )
        self._thread.start()

    def offer(self, event: events.Event) -> bool:
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            return False

    def close(self, timeout: float = 2.0) -> None:
        if self._thread is None:
            return
        self._queue.put(_SHUTDOWN)
        self._thread.join(timeout=timeout)
        self._thread = None

    def _consume(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SHUTDOWN:
                return
            try:
                self.handle(item)
            except Exception:  # noqa: BLE001 - one bad event must not kill the sink
                logger.exception("sink %s failed handling an event", type(self).__name__)

    def handle(self, event: events.Event) -> None:
        raise NotImplementedError
