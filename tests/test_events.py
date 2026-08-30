from __future__ import annotations

import events


class RecordingSink:
    def __init__(self) -> None:
        self.seen: list[events.Event] = []

    def offer(self, event: events.Event) -> bool:
        self.seen.append(event)
        return True


def test_publish_stamps_monotonic_seq_and_wall_clock() -> None:
    bus = events.EventBus()
    sink = RecordingSink()
    bus.subscribe(sink)

    bus.publish(events.Navigated(target="RETRY"))
    bus.publish(events.Navigated(target="BATTLE"))

    assert [e.seq for e in sink.seen] == [1, 2]
    assert all(e.ts > 0 for e in sink.seen)


def test_publish_seeds_seq_from_start() -> None:
    """seq is seeded from storage so a restart cannot collide on the PK."""
    bus = events.EventBus(start_seq=41)
    sink = RecordingSink()
    bus.subscribe(sink)

    stamped = bus.publish(events.Navigated(target="RETRY"))

    assert stamped.seq == 42


def test_event_type_is_the_class_name() -> None:
    assert events.Navigated(target="RETRY").type == "Navigated"


def test_publish_fans_out_to_every_sink() -> None:
    bus = events.EventBus()
    a, b = RecordingSink(), RecordingSink()
    bus.subscribe(a)
    bus.subscribe(b)

    bus.publish(events.Navigated(target="RETRY"))

    assert len(a.seen) == 1
    assert len(b.seen) == 1


def test_refused_offers_increment_the_dropped_counter() -> None:
    class RefusingSink:
        def offer(self, event: events.Event) -> bool:
            return False

    bus = events.EventBus()
    bus.subscribe(RefusingSink())

    bus.publish(events.Navigated(target="RETRY"))
    bus.publish(events.Navigated(target="BATTLE"))

    assert bus.dropped == 2
