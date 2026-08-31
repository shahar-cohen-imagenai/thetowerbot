"""The live event feed the browser subscribes to.

A ring of the last N events plus a `since(seq)` read. That is the whole
mechanism: the SSE endpoint polls the ring from the asyncio side, and a
reconnecting browser replays from its Last-Event-ID against the same buffer.

Why a ring rather than per-client asyncio queues fed by call_soon_threadsafe:
events are published from the scan loop's thread and consumed by the event
loop, and a shared lock-protected deque is the smallest correct thing that
crosses that boundary. It also makes reconnect-replay fall out for free
instead of needing a second buffer beside the queues.

This is the one sink with no consumer thread, and it does not need one:
offer() appends under a lock held for a few instructions, so it cannot block
and the bus invariant holds.
"""

from __future__ import annotations

import dataclasses
import threading
from collections import deque
from typing import Any

import config
import events


def to_payload(event: events.Event) -> dict[str, Any]:
    """The JSON body of one SSE message."""
    data = dataclasses.asdict(event)
    # `type` is a property, so asdict() does not include it - and it is the
    # first thing the browser switches on.
    data["type"] = event.type
    return data


class SseSink:
    def __init__(self, capacity: int = config.SSE_RING_SIZE) -> None:
        self._lock = threading.Lock()
        self._ring: deque[events.Event] = deque(maxlen=capacity)

    def offer(self, event: events.Event) -> bool:
        with self._lock:
            self._ring.append(event)
        return True

    def since(self, seq: int) -> list[events.Event]:
        """Every buffered event after `seq`, oldest first."""
        with self._lock:
            return [event for event in self._ring if event.seq > seq]
