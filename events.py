"""Typed events and the bus that fans them out to sinks.

The bus is the seam between the scan loop and everything that observes it.
Its one hard invariant: publish() never blocks. Sinks offer into bounded
queues and drop on overflow, so a slow browser or a locked database can
never stall the bot.
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Protocol


@dataclass(frozen=True, kw_only=True)
class Event:
    """Base event. The bus stamps seq and ts on publish."""

    seq: int = 0
    ts: float = 0.0

    @property
    def type(self) -> str:
        return type(self).__name__


@dataclass(frozen=True, kw_only=True)
class ScreenChanged(Event):
    prev: str
    curr: str
    confidence: float
    scores: dict[str, float]


@dataclass(frozen=True, kw_only=True)
class ScanCompleted(Event):
    screen: str
    duration_ms: float
    wallet: int | None = None


@dataclass(frozen=True, kw_only=True)
class Tapped(Event):
    action: str
    x: int
    y: int
    score: float
    price: int | None = None
    wallet: int | None = None


@dataclass(frozen=True, kw_only=True)
class Skipped(Event):
    action: str
    reason: str  # paused | screen_gated | dimmed | unaffordable | cooldown
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class RunStarted(Event):
    run_id: int


@dataclass(frozen=True, kw_only=True)
class RunEnded(Event):
    run_id: int
    duration: float
    wave: int | None = None
    coins: int | None = None
    tier: int | None = None
    abandoned: bool = False


@dataclass(frozen=True, kw_only=True)
class Navigated(Event):
    target: str  # RETRY | BATTLE


@dataclass(frozen=True, kw_only=True)
class UnknownScreen(Event):
    snapshot_path: str
    best_anchor: str
    best_score: float


@dataclass(frozen=True, kw_only=True)
class BotError(Event):
    message: str
    traceback: str = ""


@dataclass(frozen=True, kw_only=True)
class ControlChanged(Event):
    """A live setting changed. `changed` holds only the fields that moved.

    Persisted through the events table's JSON `detail` blob, so it needs no
    schema migration - there is no `changed` column and there does not need
    to be one.
    """

    changed: dict[str, Any]
    source: str = "web"


class Sink(Protocol):
    """A consumer of events. offer() MUST NOT block."""

    def offer(self, event: Event) -> bool:
        """Accept the event, or return False if it was dropped."""
        ...


class EventBus:
    """Thread-safe fan-out. publish() never blocks."""

    def __init__(self, start_seq: int = 0) -> None:
        self._counter = itertools.count(start_seq + 1)
        self._lock = threading.Lock()
        self._sinks: list[Sink] = []
        self.dropped = 0

    def subscribe(self, sink: Sink) -> None:
        with self._lock:
            self._sinks.append(sink)

    def publish(self, event: Event) -> Event:
        with self._lock:
            stamped = replace(event, seq=next(self._counter), ts=time.time())
            sinks = list(self._sinks)

        for sink in sinks:
            if not sink.offer(stamped):
                with self._lock:
                    self.dropped += 1
        return stamped
