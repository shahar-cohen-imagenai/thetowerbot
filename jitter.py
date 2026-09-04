"""Randomised tap coordinates and timings.

Every tap the bot sends is an `input tap` over ADB: a synthetic event with
no travel path and no dwell, landing on a pixel derived by a fixed offset
from a template match. Without this module that pixel is *identical* on
every tap of a given button, and the scan loop is a metronome. This module
is the one place that variance is introduced, so there is one contract to
reason about rather than a `random` call scattered at each tap site.

Two shapes of jitter, deliberately different:

- `point()` is bounded hard by a radius, because the tightest tap target on
  screen (the upgrade buy square's price strip, 208x38) is small enough
  that a Gaussian tail excursion would miss the button.
- `spread()` and `stretch()` are proportional, so they still make sense
  when an operator moves the scan interval from 2s to 30s.

`stretch()` exists separately from `spread()` because the cooldowns are
*minimums* with a functional job - `config.CLICK_COOLDOWN_SECONDS` gives a
slow UI time to respond, `NAVIGATION_COOLDOWN_SECONDS` waits out an
animation. Jittering those symmetrically would sometimes tap sooner than
the configured floor, reintroducing the double-tap those floors exist to
prevent. So cooldowns only ever stretch longer, never shorter.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

# The module-level default, used when a caller passes no rng. Tests pass
# their own seeded Random so they can assert exact coordinates and delays
# instead of ranges.
_rng = random.Random()

# Divisor turning a hard radius into a standard deviation. 3 puts the
# clamp at 3 sigma, so clamping is rare enough that the distribution keeps
# its centre-weighted shape instead of piling up on the bounds.
_SIGMA_DIVISOR = 3.0


def _offset(radius: float, rng: random.Random) -> int:
    """One axis of `point()`: a centre-weighted integer within ±radius.

    Truncated with int() rather than rounded, because rounding a clamped
    8.7 up to 9 would breach a radius of 8.7. Truncation toward zero can
    only ever land inside the bound.
    """
    raw = rng.gauss(0.0, radius / _SIGMA_DIVISOR)
    return int(max(-radius, min(radius, raw)))


def point(
    x: int, y: int, radius: float, rng: random.Random | None = None
) -> tuple[int, int]:
    """Scatter a tap point inside `radius` pixels of (x, y).

    Gaussian rather than uniform: a human's taps bunch near the middle of a
    button rather than spreading evenly out to its edges.

    A zero radius returns the point untouched, which is how the dashboard
    switches jitter off without the old behaviour needing a separate code
    path.
    """
    if radius < 0:
        raise ValueError(f"radius must not be negative, got {radius}")
    if radius == 0:
        return (int(x), int(y))

    source = _rng if rng is None else rng
    return (x + _offset(radius, source), y + _offset(radius, source))


def _fraction(fraction: float) -> None:
    """Shared guard. A negative fraction is a typo, not a way to say "off"
    - zero already says that, at both call sites and in the strategy."""
    if fraction < 0:
        raise ValueError(f"fraction must not be negative, got {fraction}")


def spread(value: float, fraction: float, rng: random.Random | None = None) -> float:
    """Scale `value` by up to ±`fraction`, either direction.

    Uniform rather than Gaussian, unlike `point()`: the fraction here is a
    range the operator sets and reads back ("±15%"), not a bound on a tail.
    A uniform draw makes the dashboard number mean exactly what it says.

    Used for the between-scan interval, where varying in both directions is
    the point - a bot that only ever waited longer than its nominal
    interval would still be a pattern, just a slower one.
    """
    _fraction(fraction)
    if fraction == 0:
        return value

    source = _rng if rng is None else rng
    return value * (1.0 + source.uniform(-fraction, fraction))


def stretch(value: float, fraction: float, rng: random.Random | None = None) -> float:
    """Scale `value` up by 0 to `fraction`, never down.

    For the cooldowns, which are functional minimums rather than targets -
    see the module docstring. The caller gets a gap that is always at least
    what was configured, and sometimes longer.
    """
    _fraction(fraction)
    if fraction == 0:
        return value

    source = _rng if rng is None else rng
    return value * (1.0 + source.uniform(0.0, fraction))


def pause(
    seconds: float,
    fraction: float,
    rng: random.Random | None = None,
    sleep: Callable[[float], Any] = time.sleep,
) -> float:
    """Wait `seconds` (jittered ±`fraction`) and return the delay used.

    The gap between finding a match and tapping it. Without it, the tap
    leaves in the same breath as the scan that decided it - a reaction time
    of zero.

    `sleep` is injected rather than called directly so the caller chooses
    how to wait. tower_bot passes its stop event's `wait`, which returns
    early on shutdown - free there, since it owns the event. navigate and
    shopping take the default: they are handed a policy per call, not the
    bot's internals, and the lag this can add to a Stop is bounded by
    strategy.MAX_TAP_DELAY rather than by the scan interval, which is the
    3600s case `_stopping` exists for. Tests pass a recorder and stay fast.

    A zero delay returns without calling `sleep` at all: an operator who
    switched the delay off should not pay a syscall per tap for it.
    """
    if seconds <= 0:
        return 0.0

    delay = spread(seconds, fraction, rng)
    sleep(delay)
    return delay
