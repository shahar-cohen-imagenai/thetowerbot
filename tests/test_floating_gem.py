"""The floating-gem detector, against real battle frames.

The negative half of this file is the load-bearing half. A false positive
costs one inert tap, but a detector that fires on the HUD gem icon or the
ad-gem button would fire on EVERY frame - so every committed in-run capture
is asserted to yield nothing, and those two decoys are named in the test
that covers them.

The positive half is synthetic: no committed capture shows a floating gem
(they spawn at most once per 15 minutes), so the sprite is painted onto a
real frame using the glow colour measured off the HUD gem icon - hue 151,
the same artwork at a different size.
"""

from __future__ import annotations

import glob
import os

import cv2
import numpy as np
import pytest

import config
import floating_gem
import run_hud
import vision

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
IN_RUN = sorted(glob.glob(os.path.join(FIXTURES, "in_run*.png")))


def frame(name: str):
    return cv2.imread(os.path.join(FIXTURES, name))


def anchor_of(screen):
    match = run_hud.find_cash(screen, vision.TemplateCache(config.TEMPLATE_DIR))
    assert match is not None, "fixture has no cash anchor"
    return match.top_left


def paint_gem(screen, centre: tuple[int, int], radius: int = 26):
    """A magenta diamond where the game would draw one.

    Hollow, like the sprite: a white core inside a magenta glow. Drawn in
    HSV and converted, so the hue is exactly the one measured off the HUD
    icon rather than a BGR triple guessed to look right.
    """
    x, y = centre
    swatch = np.zeros((1, 1, 3), dtype=np.uint8)
    swatch[0, 0] = (151, 198, 200)
    glow = tuple(int(c) for c in cv2.cvtColor(swatch, cv2.COLOR_HSV2BGR)[0, 0])
    points = np.array(
        [[x, y - radius], [x + radius, y], [x, y + radius], [x - radius, y]]
    )
    cv2.polylines(screen, [points], True, glow, 7)
    return screen


# -- the positive case ------------------------------------------------------


def test_finds_a_gem_painted_on_the_ring():
    screen = frame("in_run_early.png")
    anchor = anchor_of(screen)
    paint_gem(screen, (560, 700))

    sighting = floating_gem.find(screen, anchor)

    assert sighting is not None


def test_reports_the_centre_of_the_sprite_so_the_tap_lands_on_it():
    # The tap point is the whole output. An off-centre point taps the
    # background beside a gem that was correctly detected.
    screen = frame("in_run_early.png")
    anchor = anchor_of(screen)
    paint_gem(screen, (560, 700))

    sighting = floating_gem.find(screen, anchor)

    assert sighting is not None
    x, y = sighting.point
    assert abs(x - 560) <= 6
    assert abs(y - 700) <= 6


def test_reports_an_absolute_box_not_one_relative_to_the_search_window():
    # The search is a crop; forgetting to add the offset back gives a box
    # ~200px left and ~400px up from the gem - a plausible-looking tap
    # point that is simply wrong. run_hud.find_cash re-adds its offset for
    # the same reason.
    screen = frame("in_run_early.png")
    anchor = anchor_of(screen)
    paint_gem(screen, (560, 700))

    sighting = floating_gem.find(screen, anchor)

    assert sighting is not None
    x, y, w, h = sighting.box
    assert x <= 560 <= x + w
    assert y <= 700 <= y + h


def test_finds_a_gem_anywhere_on_the_ring():
    # It orbits, so any point on the circle has to work - not just the one
    # spot the first test happened to pick.
    anchor = anchor_of(frame("in_run_early.png"))
    for centre in ((330, 700), (790, 700), (560, 480), (560, 940)):
        screen = paint_gem(frame("in_run_early.png"), centre)
        assert floating_gem.find(screen, anchor) is not None, centre


# -- the negative case ------------------------------------------------------


@pytest.mark.parametrize("path", IN_RUN, ids=[os.path.basename(p) for p in IN_RUN])
def test_finds_nothing_in_a_battle_frame_with_no_gem(path):
    # Every committed in-run capture. Two of them carry the decoys that
    # make this worth asserting: the HUD gem counter (hue 151, the same
    # artwork) and the ad-gem button at the bottom left. Both are magenta,
    # both are outside the search region, and this is what says so.
    screen = cv2.imread(path)
    assert floating_gem.find(screen, anchor_of(screen)) is None


def test_ignores_a_sparkle_too_small_to_be_the_gem():
    # The sprite trails small magenta particles. They are the same colour
    # and would be tapped as eagerly as the gem itself.
    screen = frame("in_run_early.png")
    anchor = anchor_of(screen)
    paint_gem(screen, (560, 700), radius=4)

    assert floating_gem.find(screen, anchor) is None


def test_ignores_the_red_enemies():
    # Measured: the enemy diamonds sit at hue 177, the gem at 151. Same
    # shape, same size, nearly the same brightness - colour is the only
    # thing separating them, so this is the assertion that the hue window
    # is actually doing the work.
    screen = frame("in_run_early.png")
    anchor = anchor_of(screen)
    swatch = np.zeros((1, 1, 3), dtype=np.uint8)
    swatch[0, 0] = (177, 198, 200)
    red = tuple(int(c) for c in cv2.cvtColor(swatch, cv2.COLOR_HSV2BGR)[0, 0])
    points = np.array([[560, 674], [586, 700], [560, 726], [534, 700]])
    cv2.polylines(screen, [points], True, red, 7)

    assert floating_gem.find(screen, anchor) is None


def test_survives_a_frame_with_no_room_for_the_search_region():
    # A resolution change must read as "no gem", not an IndexError out of
    # the scan loop - the same contract digits.crop holds.
    tiny = np.zeros((80, 60, 3), dtype=np.uint8)
    assert floating_gem.find(tiny, (10, 10)) is None
