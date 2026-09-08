"""Take the free gem that orbits the tower during a battle.

Two facts shape everything here.

The gem MOVES. Between the frame that saw it and the tap that follows, it
has travelled along the ring - so a miss is the ordinary case, not the
exceptional one. That is why this retries within a bounded budget instead
of firing once and hoping.

And a tap is not evidence. `floating_gem.find` says where a magenta blob
is, not that tapping it collected anything, so the claim is confirmed by
watching the HUD gem counter rise across the tap. When it does not rise -
because the tap missed, or because the counter row is absent on an account
holding no gems - this publishes ClaimUncertain, the event this codebase
already reserves for a claim that was tapped and never confirmed. It never
invents the two gems the wiki says a pickup pays.

Unlike the missions and milestones claims, this walks no menus and owns no
screen: it is one tap inside a battle the bot is otherwise playing
normally. It therefore has no place in the transaction machinery those use,
and holds only the state a confirmation needs.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Protocol

import config
import digits
import events
import floating_gem
import jitter
from device import Image, tap

logger = logging.getLogger("tower_bot.gem_claim")

Detector = Callable[[Image, tuple[int, int]], floating_gem.Sighting | None]


class Tuning(Protocol):
    """The tap-shaping fields this reads off a Strategy."""

    tap_jitter_px: float
    timing_jitter: float
    tap_delay: float


class FloatingGemClaim:
    """One tap at the orbiting gem, and the counter that proves it landed."""

    def __init__(
        self,
        bus: Any,
        reader: Any | None = None,
        *,
        detect: Detector = floating_gem.find,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        self._bus = bus
        self._reader = reader if reader is not None else digits.NumberReader()
        self._detect = detect
        self._sleep = sleep
        self._before: int | None = None
        self._point: tuple[int, int] | None = None
        self._taps = 0
        self._scans = 0
        self._run_id: int | None = None
        self._quiet_until = 0.0

    @property
    def active(self) -> bool:
        """True while a tap is waiting on the counter to confirm it."""
        return self._point is not None

    def observe(
        self,
        *,
        screen: Image,
        anchor: tuple[int, int],
        device: Any,
        policy: Tuning,
        now: float = 0.0,
        run_id: int | None = None,
    ) -> bool:
        """One scan's worth of looking, tapping and confirming.

        Returns True if this scan tapped. `anchor` is the cash counter's
        top-left, which the loop has already found for the wallet read.
        """
        sighting = self._detect(screen, anchor)

        if self.active:
            return self._confirm(screen, anchor, device, policy, sighting, now)

        if sighting is None or now < self._quiet_until:
            return False

        # Read BEFORE tapping: the after-reading is the change this is
        # measuring, and taking the baseline afterwards would race it.
        self._before = self._read(screen, anchor)
        self._point = sighting.point
        self._run_id = run_id
        self._taps = 0
        self._scans = 0
        self._tap(device, policy, sighting)
        return True

    # -- the two halves -----------------------------------------------------

    def _confirm(
        self,
        screen: Image,
        anchor: tuple[int, int],
        device: Any,
        policy: Tuning,
        sighting: floating_gem.Sighting | None,
        now: float,
    ) -> bool:
        self._scans += 1
        after = self._read(screen, anchor)

        if self._before is not None and after is not None and after > self._before:
            self._bus.publish(
                events.FloatingGemClaimed(
                    point=self._point or (0, 0),
                    gems_before=self._before,
                    gems_after=after,
                    delta=after - self._before,
                    run_id=self._run_id,
                )
            )
            self._reset()
            return False

        # Still there and the counter has not moved: the tap landed where
        # the gem WAS. Worth another, up to the budget.
        if sighting is not None and self._taps < config.FLOATING_GEM_MAX_TAPS:
            self._point = sighting.point
            self._tap(device, policy, sighting)
            return True

        if self._scans >= config.FLOATING_GEM_CONFIRM_SCANS:
            self._give_up(
                "counter_unreadable" if self._before is None else "counter_unchanged",
                now,
            )
        return False

    def _give_up(self, reason: str, now: float) -> None:
        logger.info("floating gem tapped at %s but unconfirmed: %s", self._point, reason)
        self._bus.publish(
            events.ClaimUncertain(
                target="floating_gem",
                reason=reason,
                detail=f"tapped {self._taps}x at {self._point}",
            )
        )
        self._reset()
        self._quiet_until = now + config.FLOATING_GEM_COOLDOWN_SECONDS

    def _reset(self) -> None:
        self._before = None
        self._point = None
        self._taps = 0
        self._scans = 0
        self._run_id = None

    # -- the tap ------------------------------------------------------------

    def _tap(self, device: Any, policy: Tuning, sighting: floating_gem.Sighting) -> None:
        x, y = jitter.point(*sighting.point, policy.tap_jitter_px)
        if self._sleep is None:
            jitter.pause(policy.tap_delay, policy.timing_jitter)
        else:
            jitter.pause(policy.tap_delay, policy.timing_jitter, sleep=self._sleep)
        tap(device, x, y)
        self._taps += 1
        logger.info("tapped a floating gem at (%d,%d), attempt %d", x, y, self._taps)

    def _read(self, screen: Image, anchor: tuple[int, int]) -> int | None:
        return self._reader.read(screen, config.GEMS_FROM_CASH, anchor, "wallet")
