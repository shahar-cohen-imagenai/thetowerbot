from __future__ import annotations

import logging
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


def test_close_does_not_hang_when_queue_is_full_and_consumer_wedged() -> None:
    """Verify that close() with timeout completes even if the queue is full
    and the consumer is stuck in handle()."""
    sink = SlowSink(maxsize=2)
    sink.start()
    try:
        # Fill the queue past maxsize so it's blocked
        for _ in range(5):
            sink.offer(events.Navigated(target="RETRY"))

        # Consumer is blocked in handle() waiting for release, queue is full
        # close() must still complete within its timeout
        started = time.monotonic()
        sink.close(timeout=0.5)
        elapsed = time.monotonic() - started

        assert elapsed < 1.0, "close() blocked or hung - shutdown invariant broken"
    finally:
        # Release the handler so the thread can exit if it wasn't already
        sink.release.set()


def test_close_timeout_logs_warning_and_prevents_second_consumer() -> None:
    """Verify that when close() times out, it logs a warning and does not
    null the thread reference, preventing a second consumer from spawning."""
    class NeverStops(QueueSink):
        def __init__(self) -> None:
            super().__init__(maxsize=10)
            self.block = threading.Event()

        def handle(self, event: events.Event) -> None:
            # Block forever so the consumer thread never stops
            self.block.wait()

    sink = NeverStops()
    sink.start()
    original_thread = sink._thread

    try:
        # Offer an event to get it into the queue
        sink.offer(events.Navigated(target="RETRY"))

        # Close with a very short timeout so it times out
        with _capture_logs() as logs:
            sink.close(timeout=0.01)

        # Verify a warning was logged about the timeout
        assert any("did not stop within timeout" in log for log in logs), \
            f"Expected timeout warning in logs, got: {logs}"

        # Verify the thread reference was NOT nulled
        assert sink._thread is not None, "thread reference should not be nulled on timeout"
        assert sink._thread is original_thread, "thread reference should be unchanged"

        # Verify a second start() doesn't create a new consumer
        second_call_thread = sink._thread
        sink.start()  # This should be a no-op
        assert sink._thread is second_call_thread, "start() should not create a second consumer"
    finally:
        # Clean up by releasing the handler
        sink.block.set()
        # Try to join the thread directly since close() didn't work
        sink._thread.join(timeout=2)


class _capture_logs:
    """Context manager to capture log messages."""

    def __init__(self) -> None:
        self.logs: list[str] = []
        self.handler: logging.Handler | None = None

    def __enter__(self) -> list[str]:
        self.handler = logging.Handler()
        self.handler.emit = lambda record: self.logs.append(record.getMessage())
        logger = logging.getLogger("tower_bot.sinks")
        logger.addHandler(self.handler)
        return self.logs

    def __exit__(self, *args: object) -> None:
        if self.handler:
            logger = logging.getLogger("tower_bot.sinks")
            logger.removeHandler(self.handler)
