from __future__ import annotations

import random

import pytest

import jitter


def test_a_zero_radius_returns_the_point_untouched() -> None:
    """Zero must mean "off", exactly as a zero click_cooldown does.

    This is the property that keeps the old deterministic behaviour
    reachable from the dashboard without deleting the jitter code.
    """
    assert jitter.point(500, 900, 0.0, random.Random(1)) == (500, 900)


def test_a_jittered_point_stays_within_the_radius() -> None:
    """The radius is a hard bound, not a standard deviation.

    It exists because the tightest tap target on screen - the upgrade buy
    square's price strip - is only 38px tall, so a tail excursion of a few
    hundred pixels would miss the button entirely.
    """
    rng = random.Random(7)
    for _ in range(2000):
        x, y = jitter.point(500, 900, 8.0, rng)
        assert abs(x - 500) <= 8
        assert abs(y - 900) <= 8


def test_jitter_actually_moves_the_point() -> None:
    """A helper that always returned the anchor would pass every bound
    check above while leaving the identical-pixel signature intact."""
    rng = random.Random(7)
    points = {jitter.point(500, 900, 8.0, rng) for _ in range(200)}

    assert len(points) > 20
    assert (500, 900) in points or len(points) > 1


def test_jitter_clusters_near_the_centre() -> None:
    """Gaussian, not uniform: a human's taps bunch at a button's middle
    rather than spreading evenly to its edges."""
    rng = random.Random(11)
    offsets = [abs(jitter.point(0, 0, 9.0, rng)[0]) for _ in range(4000)]

    near = sum(1 for d in offsets if d <= 3)
    far = sum(1 for d in offsets if d >= 6)
    assert near > far * 2


def test_the_same_seed_gives_the_same_point() -> None:
    """Seedable so tests can assert exact coordinates, and so a run can be
    replayed when a tap lands somewhere surprising."""
    a = jitter.point(500, 900, 8.0, random.Random(42))
    b = jitter.point(500, 900, 8.0, random.Random(42))

    assert a == b


def test_a_jittered_point_is_a_pair_of_ints() -> None:
    """adb input tap takes integers; a float would be rejected downstream."""
    x, y = jitter.point(500, 900, 8.0, random.Random(3))

    assert isinstance(x, int)
    assert isinstance(y, int)


def test_a_negative_radius_is_rejected() -> None:
    """Silently treating it as "off" would hide a bad strategy value at
    exactly the moment the operator wanted jitter on."""
    with pytest.raises(ValueError, match="radius"):
        jitter.point(500, 900, -1.0, random.Random(1))


def test_a_zero_fraction_leaves_a_spread_value_alone() -> None:
    """Same "zero means off" contract as a zero radius."""
    assert jitter.spread(2.0, 0.0, random.Random(1)) == 2.0


def test_spread_stays_within_the_fraction_either_side() -> None:
    """Symmetric, and proportional rather than additive, so it still makes
    sense when the operator moves the scan interval from 2s to 30s."""
    rng = random.Random(5)
    for _ in range(2000):
        value = jitter.spread(2.0, 0.15, rng)
        assert 1.7 <= value <= 2.3


def test_spread_goes_both_up_and_down() -> None:
    """The scan interval is the metronome worth breaking in both
    directions - only ever slowing down would still be a pattern."""
    rng = random.Random(5)
    values = [jitter.spread(2.0, 0.15, rng) for _ in range(200)]

    assert any(v < 2.0 for v in values)
    assert any(v > 2.0 for v in values)


def test_a_zero_fraction_leaves_a_stretched_value_alone() -> None:
    assert jitter.stretch(1.0, 0.0, random.Random(1)) == 1.0


def test_stretch_never_returns_less_than_the_value() -> None:
    """The whole reason stretch() is separate from spread().

    click_cooldown and navigation_cooldown are functional *minimums* - one
    gives a slow UI time to respond, the other waits out an animation.
    Jittering them downward would reintroduce the double-tap they exist to
    prevent, so this direction is not merely a preference.
    """
    rng = random.Random(9)
    for _ in range(2000):
        assert jitter.stretch(1.0, 0.15, rng) >= 1.0


def test_stretch_stays_within_the_fraction_above() -> None:
    rng = random.Random(9)
    for _ in range(2000):
        assert jitter.stretch(1.0, 0.15, rng) <= 1.15


def test_stretch_actually_lengthens_sometimes() -> None:
    rng = random.Random(9)
    values = [jitter.stretch(1.0, 0.15, rng) for _ in range(200)]

    assert any(v > 1.0 for v in values)


def test_the_same_seed_gives_the_same_timing() -> None:
    assert jitter.spread(2.0, 0.15, random.Random(4)) == jitter.spread(
        2.0, 0.15, random.Random(4)
    )


@pytest.mark.parametrize("helper", [jitter.spread, jitter.stretch])
def test_a_negative_fraction_is_rejected(helper) -> None:
    """As with a negative radius: a bad strategy value must surface, not be
    quietly read as "off"."""
    with pytest.raises(ValueError, match="fraction"):
        helper(2.0, -0.1, random.Random(1))


def test_pause_sleeps_for_a_jittered_delay() -> None:
    """The sleeper is injected so the caller decides *how* to wait.

    tower_bot passes its stop event's wait(), which aborts the moment a
    shutdown is requested; navigate and shopping pass the same, so a Stop
    is never held up by a pre-tap pause.
    """
    slept: list[float] = []

    delay = jitter.pause(0.12, 0.15, random.Random(2), sleep=slept.append)

    assert slept == [delay]
    assert 0.102 <= delay <= 0.138


def test_a_zero_delay_does_not_sleep_at_all() -> None:
    """Not sleep(0.0) - an operator who turned the delay off should not pay
    a syscall per tap for the privilege."""
    slept: list[float] = []

    delay = jitter.pause(0.0, 0.15, random.Random(2), sleep=slept.append)

    assert slept == []
    assert delay == 0.0


def test_pause_still_jitters_when_the_fraction_is_zero() -> None:
    """A delay with no jitter is still a delay - the two knobs are
    independent, so tap_delay=0.12 with timing_jitter=0 must pause."""
    slept: list[float] = []

    jitter.pause(0.12, 0.0, random.Random(2), sleep=slept.append)

    assert slept == [0.12]
