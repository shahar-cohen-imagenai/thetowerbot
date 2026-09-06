"""The Missions claim walk, driven one fake frame at a time.

No OCR: `MissionsReadings.claim_evidence()` is stood in for directly, so every
case here is about what the transaction DOES with that evidence rather than
about reading the page. The two device-touching taps it can still make -
opening Missions from the main menu and returning from it - go through the
real `account_collection.locate_control` against real captures and a real
`vision.TemplateCache`, not a synthetic image: a constant screen matched
against a constant template is a degenerate normalized cross-correlation (see
tests/test_account_screens.py's own `locate_control(zeros, zeros)` case) that
reports every position tied at the top score, which `locate_control` then
correctly calls ambiguous rather than located - so a synthetic frame here
would make `_tap` refuse before the walk ever reached the CLAIM step, and
every assertion below would hold for that wrong reason instead of the one it
names. Real frames are what test_missions_visit.py uses for the same reason.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import pytest

import config
import events
import missions_claim
import missions_screen
import vision

FIXTURES = Path(__file__).parent / 'fixtures'


class FakeBus:
    def __init__(self) -> None:
        self.published: list[events.Event] = []

    def publish(self, event: events.Event) -> events.Event:
        self.published.append(event)
        return event

    def of(self, kind: type) -> list[events.Event]:
        return [e for e in self.published if isinstance(e, kind)]


class FakeReadings:
    """Stands in for MissionsReadings, replaying scripted frames."""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = frames
        self._at = 0

    def step(self) -> None:
        self._at = min(self._at + 1, len(self._frames) - 1)

    def claim_evidence(self) -> dict[str, Any]:
        return self._frames[self._at]

    def current_evidence(self) -> dict[str, Any]:
        frame = self._frames[self._at]
        return {k: frame[k] for k in ('screen_id', 'error', 'scanned')}


class FakePanel:
    def current_evidence(self) -> dict[str, Any]:
        return {'screen_id': None, 'error': None, 'scanned': True}


def home() -> dict[str, Any]:
    return {'screen_id': None, 'error': None, 'scanned': True,
            'completed': None, 'claims': ()}


def page(completed: int | None, claims: int) -> dict[str, Any]:
    targets = tuple(
        missions_screen.ClaimTarget(f'mission_{i}', f'Mission {i}', 25, 3,
                                    (454, 751 + 265 * i, 153, 46))
        for i in range(claims))
    return {'screen_id': 'missions.daily', 'error': None, 'scanned': True,
            'completed': completed, 'claims': targets}


def image(name: str) -> Any:
    frame = cv2.imread(str(FIXTURES / f'{name}.png'))
    assert frame is not None, f'missing fixture: {name}.png'
    return frame


def screen(step: str) -> Any:
    """A real 2400x1080 capture, chosen for whichever tap this step might
    make.

    OPEN_MISSIONS taps the MISSIONS control, which is only on the main menu -
    menu_main.png, matching nav/missions.png at 1.0000, same as
    test_missions_visit.py's MISSIONS_CONTROL. Every other step's own tap -
    the CLAIM step's return-control tap, made once claims run out or the
    bound is hit - is located on the missions page:
    menu_missions_claimable_no_status_bar.png matches nav/missions_return.png
    at 0.9999986, same coordinate test_missions_visit.py's RETURN_CONTROL
    names. No other step calls locate_control at all (a CLAIM button is
    tapped from the reader's own rect, never by template match), so which of
    the two stands in for those frames is not load-bearing.
    """
    return image('menu_main' if step == 'open_missions' else
                 'menu_missions_claimable_no_status_bar')


class FakeTemplates:
    """The real template cache. `_tap` must run genuine `cv2.matchTemplate`
    here, not a stand-in that could report `located` by construction."""

    def __init__(self) -> None:
        self._cache = vision.TemplateCache(config.TEMPLATE_DIR)

    def get(self, name: str) -> Any:
        return self._cache.get(name)


class FakeDevice:
    """Records taps. Nothing here asserts on them, but a real `located`
    match now reaches `device.click`, which `None` cannot answer."""

    def __init__(self) -> None:
        self.taps: list[tuple[int, int]] = []

    def click(self, x: int, y: int) -> None:
        self.taps.append((x, y))


def drive(frames: list[dict[str, Any]], *, taps: list[tuple[int, int]] | None = None,
          steps: int = 20) -> tuple[missions_claim.MissionsClaim, FakeBus]:
    """Run a walk to completion against scripted frames."""
    claim = missions_claim.MissionsClaim()
    claim.request(now=0.)
    bus, readings = FakeBus(), FakeReadings(frames)
    templates, device = FakeTemplates(), FakeDevice()
    for _ in range(steps):
        if not claim.active:
            break
        claim.advance(screen=screen(claim.snapshot()['step']), device=device,
                      templates=templates, readings=FakePanel(), missions=readings,
                      bus=bus, state='MAIN_MENU', now=0.)
        readings.step()
    return claim, bus


def test_a_walk_refuses_to_tap_when_the_counter_cannot_be_read() -> None:
    """The success test IS the counter. Without it a claim is unverifiable,
    and an unverifiable claim is worse than no claim: nothing afterwards could
    say whether the reward was taken."""
    claim, bus = drive([home(), page(None, 2)])
    assert claim.snapshot()['result']['reason'] == 'counter_unreadable'
    assert not bus.of(events.MissionClaimed)
    assert bus.of(events.ClaimSkipped)


def test_a_claim_is_only_recorded_once_the_counter_moves() -> None:
    claim, bus = drive([home(), page(0, 2), page(1, 1), page(2, 0)])
    claimed = bus.of(events.MissionClaimed)
    assert len(claimed) == 2
    assert (claimed[0].completed_before, claimed[0].completed_after) == (0, 1)


def test_a_counter_that_never_moves_ends_the_walk_rather_than_retapping() -> None:
    """A tap that changed nothing is reported, not repeated."""
    frames = [home()] + [page(0, 2)] * 12
    claim, bus = drive(frames)
    assert claim.snapshot()['result']['status'] == 'failed'
    assert not bus.of(events.MissionClaimed)


def test_a_walk_with_nothing_claimable_returns_home_without_tapping() -> None:
    claim, bus = drive([home(), page(4, 0), home()])
    assert not bus.of(events.MissionClaimed)
    ended = bus.of(events.ClaimEnded)
    assert ended and ended[0].claimed == 0


def test_a_walk_stops_at_the_bound_even_if_the_page_keeps_offering() -> None:
    """The page offers at most 8. A page that keeps offering is a misread of
    reflow, and a walk that keeps tapping it is a loop."""
    frames = [home()] + [page(n, 3) for n in range(1, 30)]
    claim, bus = drive(frames, steps=80)
    assert len(bus.of(events.MissionClaimed)) <= missions_claim.MAX_CLAIMS_PER_WALK


def test_pausing_mid_walk_cancels_it() -> None:
    claim = missions_claim.MissionsClaim()
    claim.request(now=0.)
    claim.cancel('paused', 'The bot was paused mid-claim.', now=1.)
    assert claim.active is False
    assert claim.snapshot()['result']['reason'] == 'paused'


def test_the_snapshot_carries_no_coordinate() -> None:
    """A tap is located again on the frame it is made from, so a remembered
    point here could only ever be used wrongly."""
    claim, _ = drive([home(), page(0, 1), page(1, 0)])
    text = repr(claim.snapshot())
    assert '454' not in text and 'rect' not in text
