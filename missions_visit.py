"""One explicit, read-only Missions visit: Home -> Missions -> read -> Home.

Nothing here spends, claims or mutates game state. The only two taps it can
ever issue are the main menu's MISSIONS control and the missions page's own
return control, each located on the very frame it is tapped from and never
from a coordinate remembered across scans. Claiming a mission reward or a
weekly milestone is deliberately absent: missions_screen cannot yet read a
claimable state, so there is nothing here to verify a claim against.

One known limit: a visit that cannot locate the return control ends `failed`
on the missions page, and the passive guard then holds every action for as
long as that page is up, publishing `missions_screen_guard` each scan. The
bot stops rather than tapping its way off a page it has no verified target
on, which is the intended trade; recovering from it is a later slice's work.
`nav/missions_return.png` matches the recorded page at 1.0000, so this is a
limit to know about rather than an expected path - the same one
account_collection already has when the Stats panel is left open.

Shaped after account_collection.StatsCollection and reusing its parts - the
same one-action-per-step rule, the same ambiguity-refusing locator, the same
`at_home` test, and the same `ControlTaps` code for the taps themselves -
because the two transactions differ only in where they walk.
What is NOT shared is the arrival evidence: this one is satisfied by the
missions reader identifying its page, not by the account panel reader.

The scan loop drives it (see tower_bot.run_once) and the runner owns the
single instance, so a visit outlives no bot: a restart cancels it, and so
does pausing the bot mid-walk.
"""

from __future__ import annotations

from dataclasses import asdict
from enum import Enum, auto
import threading
import time
from typing import TYPE_CHECKING, Any

import config
from account_collection import (
    CollectionAction, CollectionResult, ControlTaps, MATCH_THRESHOLD,
    STEP_FRAME_BUDGET, at_home,
)
from device import Image
from missions_screen import MissionsReadings

if TYPE_CHECKING:
    from strategy import Strategy

# One shape for both transactions, so the scan loop publishes and draws a tap
# the same way whichever one made it. Named for what they carry here.
VisitAction = CollectionAction
VisitResult = CollectionResult

# The main menu's MISSIONS control and the missions page's return control.
# Both are template matches rather than coordinates: `nav/missions.png` scores
# 1.0000 on menu_main.png and at most .4240 on every other recorded page, and
# `nav/missions_return.png` scores 1.0000 on menu_missions.png and at most
# .3595 elsewhere. The margin is what a .9 threshold is spending.
#
# Not every recorded main menu carries the MISSIONS control: main_menu.png
# does not. A frame without it is a frame this transaction waits on and then
# stops, never one where the button's usual position may be tapped instead.
MISSIONS_TEMPLATE = config.NAV_TARGETS['MISSIONS']
RETURN_TEMPLATE = config.NAV_TARGETS['MISSIONS_RETURN']

MISSIONS_SCREEN = 'missions.daily'


def _at_home(state: str, account: dict[str, Any], missions: dict[str, Any]) -> bool:
    """Home by the anchor, the panel reader AND the missions reader.

    The tracker is debounced, so for a scan or two after the return control
    is tapped it still says MAIN_MENU while the frame is very much still the
    missions page. Confirming home off the anchor alone would let this visit
    report "returned to the main menu" from the page it never left. The
    missions reader looked at the same frame, so it is asked too, and only
    its examined-and-clear answer counts - `scanned` False is a reader that
    reached no conclusion, never a clear menu.
    """
    return (at_home(state, account) and bool(missions['scanned'])
            and missions['screen_id'] is None and missions['error'] is None)


class Step(Enum):
    IDLE = auto()
    OPEN_MISSIONS = auto()
    READ = auto()
    CONFIRM_HOME = auto()


class MissionsVisit(ControlTaps):
    """At most one read-only Missions visit, driven one frame at a time."""

    def __init__(self, *, threshold: float = MATCH_THRESHOLD,
                 frame_budget: int = STEP_FRAME_BUDGET) -> None:
        self._lock = threading.RLock()
        self._threshold = threshold
        self._budget = frame_budget
        self._step = Step.IDLE
        self._waited = 0
        self._requested_at: float | None = None
        self._visited: str | None = None
        self._result: VisitResult | None = None
        self._trail: list[str] = []
        self._tuning: Strategy | None = None

    # -- reporting ---------------------------------------------------------
    @property
    def active(self) -> bool:
        with self._lock:
            return self._step is not Step.IDLE

    def snapshot(self) -> dict[str, Any]:
        """Detached. 'idle' is the absence of a visit, not a failed one.

        Carries no coordinate. A tap is located again on the frame it is made
        from, so a remembered point here could only ever be used wrongly.
        """
        with self._lock:
            status = 'running' if self._step is not Step.IDLE else (
                self._result.status if self._result is not None else 'idle')
            return {'status': status, 'step': self._step.name.lower(),
                    'requested_at': self._requested_at, 'trail': list(self._trail),
                    'result': asdict(self._result) if self._result is not None else None}

    # -- lifecycle ---------------------------------------------------------
    def request(self, now: float | None = None) -> bool:
        """Arm a visit. False when one is already running; never queues."""
        with self._lock:
            if self._step is not Step.IDLE:
                return False
            self._requested_at = time.time() if now is None else now
            self._result = None
            self._visited = None
            self._waited = 0
            self._trail = []
            self._enter(Step.OPEN_MISSIONS)
            return True

    def cancel(self, reason: str, detail: str, now: float | None = None) -> None:
        """End a visit the loop can no longer honour. Idempotent."""
        with self._lock:
            if self._step is Step.IDLE:
                return
            self._finish('failed', reason, detail, time.time() if now is None else now)

    # -- one step ----------------------------------------------------------
    def advance(self, *, screen: Image, device: Any, templates: Any,
                readings: Any, missions: MissionsReadings, state: str,
                now: float | None = None,
                tuning: Strategy | None = None) -> VisitAction | None:
        """One frame of the visit. Returns the tap it issued, if any.

        `readings` is the account panel reader, used only to confirm home the
        way every transaction leaving the main menu confirms it. `missions`
        is this walk's own arrival evidence.
        """
        with self._lock:
            self._tuning = tuning
            if self._step is Step.IDLE:
                return None
            moment = time.time() if now is None else now
            evidence = missions.current_evidence()

            if self._step is Step.OPEN_MISSIONS:
                if not _at_home(state, readings.current_evidence(), evidence):
                    return self._wait('home_not_confirmed', 'The main menu was not confirmed '
                                      'on this frame, so no control was tapped.', moment)
                return self._tap(screen, device, templates, MISSIONS_TEMPLATE,
                                 'missions_control', Step.READ, moment)

            if self._step is Step.READ:
                if evidence['error'] is not None:
                    return self._finish('failed', 'missions_unreadable', 'The missions page was '
                                        'reached but could not be read; nothing was recorded.',
                                        moment)
                if evidence['screen_id'] != MISSIONS_SCREEN:
                    return self._wait('missions_not_reached', 'The missions page was not observed '
                                      'after the missions control was tapped.', moment)
                # Recorded before the return tap, not after: what this visit
                # went for is the reading, and it is taken from the frame the
                # reader actually identified.
                self._visited = evidence['screen_id']
                return self._tap(screen, device, templates, RETURN_TEMPLATE,
                                 'return_control', Step.CONFIRM_HOME, moment)

            if not _at_home(state, readings.current_evidence(), evidence):
                return self._wait('home_not_restored', 'The missions page was read, but the main '
                                  'menu was not confirmed again.', moment)
            return self._finish('completed', 'visited', 'The missions page was read and the game '
                                'returned to the main menu.', moment)

    # -- internals ---------------------------------------------------------
    def _enter(self, step: Step) -> None:
        self._step = step
        self._waited = 0
        self._trail.append(step.name.lower())

    def _wait(self, reason: str, detail: str, moment: float) -> VisitAction | None:
        self._waited += 1
        if self._waited > self._budget:
            return self._finish('failed', reason, detail, moment)
        return None

    def _finish(self, status: str, reason: str, detail: str,
                moment: float) -> VisitAction | None:
        self._result = VisitResult(status, reason, detail, self._visited, moment)
        self._step = Step.IDLE
        self._waited = 0
        return None
