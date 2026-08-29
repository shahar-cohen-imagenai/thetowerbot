from __future__ import annotations

import threading
import time

import events
from sinks.base import QueueSink


class SlowSink(QueueSink):
    """Blocks in handle() until released, to simulate a stalled consumer."""

    def __init__(self, maxsize: int) -> None:
        super().__init__(maxsize=maxsize)
        self.release = threading.Event()
        self.handled: list[events.Event] = []

    def handle(self, event: events.Event) -> None:
        self.release.wait(timeout=5)
        self.handled.append(event)


def test_offer_never_blocks_when_the_queue_is_full() -> None:
    """The invariant that keeps a stalled sink from freezing the bot."""
    sink = SlowSink(maxsize=2)
    sink.start()
    try:
        started = time.monotonic()
        results = [sink.offer(events.Navigated(target="RETRY")) for _ in range(50)]
        elapsed = time.monotonic() - started

        assert elapsed < 0.5, "offer() blocked - the bus invariant is broken"
        assert results.count(False) > 0, "expected overflow to be refused"
    finally:
        sink.release.set()
        sink.close()


def test_handled_events_reach_the_consumer() -> None:
    class Collector(QueueSink):
        def __init__(self) -> None:
            super().__init__(maxsize=10)
            self.handled: list[events.Event] = []
            self.done = threading.Event()

        def handle(self, event: events.Event) -> None:
            self.handled.append(event)
            self.done.set()

    sink = Collector()
    sink.start()
    try:
        sink.offer(events.Navigated(target="BATTLE"))
        assert sink.done.wait(timeout=2)
        assert sink.handled[0].target == "BATTLE"
    finally:
        sink.close()


def test_a_raising_handler_does_not_kill_the_consumer_thread() -> None:
    class Exploding(QueueSink):
        def __init__(self) -> None:
            super().__init__(maxsize=10)
            self.survived = threading.Event()
            self.calls = 0

        def handle(self, event: events.Event) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            self.survived.set()

    sink = Exploding()
    sink.start()
    try:
        sink.offer(events.Navigated(target="RETRY"))
        sink.offer(events.Navigated(target="BATTLE"))
        assert sink.survived.wait(timeout=2)
    finally:
        sink.close()
