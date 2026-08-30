from __future__ import annotations

import events
from sinks.log import render


def stamped(event: events.Event) -> events.Event:
    bus = events.EventBus()
    return bus.publish(event)


def test_renders_a_tap_with_action_and_position() -> None:
    line = render(stamped(events.Tapped(action="Damage", x=840, y=1520, score=0.94)))
    assert "TAP" in line
    assert "Damage" in line
    assert "840" in line and "1520" in line


def test_renders_a_skip_with_its_reason() -> None:
    line = render(
        stamped(events.Skipped(action="Damage", reason="unaffordable", detail="$10 > $3"))
    )
    assert "SKIP" in line
    assert "unaffordable" in line


def test_renders_a_screen_transition_with_both_states() -> None:
    line = render(
        stamped(
            events.ScreenChanged(
                prev="IN_RUN", curr="GAME_OVER", confidence=0.998, scores={}
            )
        )
    )
    assert "IN_RUN" in line and "GAME_OVER" in line


def test_renders_unknown_events_without_raising() -> None:
    """A new event type must degrade gracefully, never crash the sink."""

    class Surprise(events.Event):
        pass

    assert isinstance(render(stamped(Surprise())), str)
