"""Move between runs unattended.

Nothing here spends permanent resources: in-run upgrades are bought with
per-run cash that resets, and coins are only ever earned.
"""

from __future__ import annotations

import logging

import config
import events
import vision
from device import Image, tap
from screens import ScreenState

logger = logging.getLogger("tower_bot.navigate")


class Navigator:
    def __init__(
        self,
        templates: vision.TemplateCache,
        bus: events.EventBus,
        cooldown: float = config.NAVIGATION_COOLDOWN_SECONDS,
        threshold: float = 0.8,
    ) -> None:
        self._templates = templates
        self._bus = bus
        self._cooldown = cooldown
        self._threshold = threshold
        self._last = float("-inf")

    def maybe_navigate(
        self, screen: Image, state: ScreenState, device, now: float | None = None
    ) -> str | None:
        entry = config.NAV_BUTTONS.get(state.value)
        if entry is None:
            return None

        moment = 0.0 if now is None else now
        if moment - self._last < self._cooldown:
            return None

        target, template_path = entry
        match = vision.locate_template(
            screen, self._templates.get(template_path), self._threshold
        )
        if match is None:
            return None

        x, y = match.center
        tap(device, x, y)
        self._last = moment
        self._bus.publish(events.Navigated(target=target))
        return target
