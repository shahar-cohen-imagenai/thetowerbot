from __future__ import annotations

import json
import threading
import time

import events
from sinks.sse import SseSink, to_payload


def bus_with(sink: SseSink) -> events.EventBus:
    bus = events.EventBus()
    bus.subscribe(sink)
    return bus


def test_events_come_back_in_order_after_a_given_seq() -> None:
    sink = SseSink(capacity=10)
    bus = bus_with(sink)
    for _ in range(4):
        bus.publish(events.Navigated(target="RETRY"))

    assert [e.seq for e in sink.since(2)] == [3, 4]


def test_a_fresh_client_asking_from_zero_gets_everything_buffered() -> None:
    sink = SseSink(capacity=10)
    bus = bus_with(sink)
    bus.publish(events.Navigated(target="RETRY"))

    assert len(sink.since(0)) == 1


def test_the_ring_evicts_the_oldest_beyond_its_capacity() -> None:
    """500 events of history, not unbounded memory in a process that runs for days."""
    sink = SseSink(capacity=3)
    bus = bus_with(sink)
    for _ in range(5):
        bus.publish(events.Navigated(target="RETRY"))

    assert [e.seq for e in sink.since(0)] == [3, 4, 5]


def test_offer_never_blocks_and_never_refuses() -> None:
    """The bus invariant: a subscribed browser must not be able to stall a scan."""
    sink = SseSink(capacity=4)
    started = time.monotonic()
    results = [sink.offer(events.Navigated(target="RETRY")) for _ in range(5000)]

    assert time.monotonic() - started < 0.5
    assert all(results)


def test_concurrent_writers_and_readers_do_not_corrupt_the_ring() -> None:
    """The scan loop publishes while request threads read; both take the lock."""
    sink = SseSink(capacity=64)
    stop = threading.Event()

    def writer() -> None:
        while not stop.is_set():
            sink.offer(events.Navigated(target="RETRY"))

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        for _ in range(500):
            assert isinstance(sink.since(0), list)
    finally:
        stop.set()
        thread.join(timeout=2)


def test_the_payload_names_its_type_and_is_json_serialisable() -> None:
    event = events.EventBus().publish(
        events.Tapped(action="Damage", x=840, y=1520, score=0.94, price=120)
    )

    body = json.loads(json.dumps(to_payload(event)))

    assert body["type"] == "Tapped"
    assert body["action"] == "Damage"
    assert body["seq"] == 1
