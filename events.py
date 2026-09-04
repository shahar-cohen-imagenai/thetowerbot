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
class PageChanged(Event):
    """Which MENU page is showing. Separate from ScreenChanged, which tracks
    the run lifecycle - see pages.py for why the two never merge.

    Fields named `prev_page` and `curr_page` rather than `prev` and `curr`.
    This is not cosmetic: sinks/store.py has a special case that maps
    `ScreenChanged.curr` to the `screen` column (the run-lifecycle screen).
    A `PageChanged` with a field called `curr` would hit that same branch,
    writing menu pages (WORKSHOP, CARDS) into the `screen` column. The
    database schema groups events by `screen` for the Stats dashboard's
    "events by screen" chart, and menu pages mixed in would silently change
    what a chart the user already relies on means. With the fields renamed,
    they fall through to the JSON `detail` blob and the `screen` column keeps
    meaning exactly one thing: the run-lifecycle screen. Renaming is the
    smaller and more durable fix than special-casing store.to_row.
    """

    prev_page: str
    curr_page: str
    confidence: float


@dataclass(frozen=True, kw_only=True)
class ShoppingStarted(Event):
    """A visit begins. No `coins`/`gems` fields here on purpose: begin()
    has not read a frame yet when this publishes, so any balance here would
    always be None - a field that can only ever hold one value is not
    reporting anything. The first real balance shows up on the first
    Purchased or PurchaseSkipped of the visit instead."""

    visit: int
    dry_run: bool = True


@dataclass(frozen=True, kw_only=True)
class ShoppingUnavailable(Event):
    """Published once, the first time ShoppingSession.begin() declines
    because this machine's header atlas cannot support a balance read - not
    on every scan that follows, which would flood the feed with the same
    fact forever. See build_shopping() and ShoppingSession.begin()."""

    reason: str


@dataclass(frozen=True, kw_only=True)
class Purchased(Event):
    """One thing bought, or - when dry_run - one thing that would have been.

    `price`, `coins_before` and `gems_before` are None rather than 0 when
    they could not be read. Zero is a free upgrade; None is "we did not
    know", and collapsing the two would make an unreadable price look like a
    bargain in the log.

    `coins_before` and `gems_before` are separate fields, not one balance
    reused for whichever currency this purchase spent: a card purchase
    spends gems, and a `Purchased(category="CARDS")` row whose `coins_before`
    silently held the gem balance would be exactly the kind of dishonesty
    this event exists to prevent. A workshop row purchase spends coins and
    reports `coins_before`; a card purchase spends gems and reports
    `gems_before`; the other field stays None for that purchase rather than
    being reused for the wrong currency.
    """

    item: str
    category: str
    price: int | None = None
    coins_before: int | None = None
    gems_before: int | None = None
    dry_run: bool = True


@dataclass(frozen=True, kw_only=True)
class PurchaseSkipped(Event):
    item: str
    # unaffordable | unreadable | no_match | capped - "disabled" is not in
    # this list on purpose: a disabled row is filtered out of rows_for()
    # before BUY_ROWS ever sees it, so that value is never emitted.
    reason: str
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class ShoppingEnded(Event):
    visit: int
    bought: int
    spent: int
    aborted: bool = False
    reason: str = ""


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
