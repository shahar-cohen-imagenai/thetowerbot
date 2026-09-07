from __future__ import annotations

from pathlib import Path

import cv2
import pytest
import numpy as np

from snapshots import SnapshotWriter

FIXTURES = Path(__file__).parent / "fixtures"


def blank() -> np.ndarray:
    return np.zeros((40, 30, 3), dtype=np.uint8)


def frame(name: str) -> np.ndarray:
    """A real captured frame. The dedupe compares screens by structure, so
    synthetic fills prove nothing about it: every uniform image has the same
    structure as every other one.
    """
    return cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)


# Six screens no two of which are each other: three menu tabs, a run, a
# death screen, a card table.
SIX_DISTINCT_SCREENS = (
    "menu_main",
    "menu_cards",
    "menu_missions",
    "menu_workshop",
    "game_over",
    "in_run_lit",
)


def test_writes_a_snapshot(tmp_path: Path) -> None:
    writer = SnapshotWriter(tmp_path)
    path = writer.maybe_write(blank(), now=1000.0)

    assert path is not None
    assert Path(path).exists()


def test_rate_limits_within_the_interval(tmp_path: Path) -> None:
    # Three DIFFERENT screens, so the only thing that can hold the middle one
    # back is the interval. Offering the same screen three times would pass
    # this test even if the rate limit were deleted.
    writer = SnapshotWriter(tmp_path, min_interval=30.0)

    assert writer.maybe_write(frame("menu_main"), now=1000.0) is not None
    assert writer.maybe_write(frame("menu_cards"), now=1010.0) is None
    assert writer.maybe_write(frame("game_over"), now=1031.0) is not None


def test_prunes_to_the_keep_limit(tmp_path: Path) -> None:
    writer = SnapshotWriter(tmp_path, min_interval=0.0, keep=3)

    for i, name in enumerate(SIX_DISTINCT_SCREENS):
        writer.maybe_write(frame(name), now=1000.0 + i)

    assert len(list(tmp_path.glob("*.png"))) == 3


def test_filenames_use_the_wall_clock_not_the_monotonic_clock(tmp_path: Path) -> None:
    """The spec asks for `unknown/<ts>.png`. `time.monotonic()` has an
    arbitrary boot-relative epoch, so it names no real moment and resets on
    every host reboot. The rate limit keeps using monotonic - two clocks, on
    purpose - but the filename must be a wall-clock timestamp.
    """
    import time

    writer = SnapshotWriter(tmp_path)

    before = time.time_ns()
    path = writer.maybe_write(blank(), now=1000.0)  # monotonic-style "now"
    after = time.time_ns()

    assert path is not None
    assert before <= int(path.stem) <= after


def test_the_rate_limit_still_uses_the_injected_monotonic_clock(
    tmp_path: Path,
) -> None:
    """Guard against the two clocks being conflated the other way."""
    writer = SnapshotWriter(tmp_path, min_interval=30.0)

    assert writer.maybe_write(frame("menu_main"), now=1000.0) is not None
    assert writer.maybe_write(frame("menu_cards"), now=1029.0) is None


def test_the_same_screen_is_not_written_twice(tmp_path: Path) -> None:
    """One unmodelled screen the bot sits on must not fill the directory.
    Past the rate limit it is still the same screen, and a second copy of it
    buys no diagnostic information - it only evicts a genuine find.
    """
    writer = SnapshotWriter(tmp_path, min_interval=30.0)

    assert writer.maybe_write(frame("menu_cards"), now=1000.0) is not None
    assert writer.maybe_write(frame("menu_cards"), now=1100.0) is None


@pytest.mark.parametrize(
    "captured,again",
    [
        # The same death screen part-way through its fade-in.
        ("game_over", "game_over_fade"),
        # The death modal sits 46px lower when the "New Highest Wave!" line
        # is there - the same screen, bragging.
        ("game_over", "game_over_newhigh"),
        # A milestones page before and after its rewards were claimed.
        ("menu_milestones_claimable", "menu_milestones_claimed"),
        # The same missions list, scrolled.
        ("menu_missions_claimable", "menu_missions_scrolled"),
        # The same workshop page after the shop restocked its prices.
        ("menu_workshop_utility_early", "menu_workshop_utility_restocked"),
    ],
)
def test_live_values_do_not_make_it_a_new_screen(
    tmp_path: Path, captured: str, again: str
) -> None:
    """What moves between these pairs is content, not layout - a counter, a
    fade, a scroll position, a claimed button. The bot sitting on one
    unmodelled screen sees a frame like this every scan, and each one saved
    is a genuine find evicted.
    """
    writer = SnapshotWriter(tmp_path, min_interval=0.0)

    assert writer.maybe_write(frame(captured), now=1000.0) is not None
    assert writer.maybe_write(frame(again), now=2000.0) is None


@pytest.mark.parametrize(
    "captured,other",
    [
        # The closest two genuinely different screens in the fixture set.
        ("menu_main", "menu_milestones_entry"),
        ("game_over_wave1", "in_run_early"),
        ("menu_cards", "menu_missions"),
        ("main_menu", "menu_workshop"),
    ],
)
def test_a_different_screen_is_still_captured(
    tmp_path: Path, captured: str, other: str
) -> None:
    """The other half of the tradeoff. A directory that refuses new screens
    is worse than one holding duplicates: unmodelled screens are the only
    thing it exists to collect.
    """
    writer = SnapshotWriter(tmp_path, min_interval=0.0)

    assert writer.maybe_write(frame(captured), now=1000.0) is not None
    assert writer.maybe_write(frame(other), now=2000.0) is not None


def test_a_duplicate_does_not_consume_the_rate_limit_slot(tmp_path: Path) -> None:
    """The interval caps writes, not calls. A bot parked on a known screen
    offers one every scan; if each of those reset the clock, the genuinely
    new screen that finally appears would be made to wait out a fresh
    interval behind them.
    """
    writer = SnapshotWriter(tmp_path, min_interval=30.0)

    assert writer.maybe_write(frame("menu_cards"), now=1000.0) is not None
    assert writer.maybe_write(frame("menu_cards"), now=1040.0) is None  # duplicate
    assert writer.maybe_write(frame("game_over"), now=1041.0) is not None


def test_screens_already_on_disk_survive_a_restart(tmp_path: Path) -> None:
    """The directory outlives the process. A writer that only remembered its
    own session would re-capture yesterday's screens on every launch - fifty
    launches, fifty copies, which is the shape of the original problem.
    """
    first = SnapshotWriter(tmp_path, min_interval=0.0)
    assert first.maybe_write(frame("menu_cards"), now=1000.0) is not None

    relaunched = SnapshotWriter(tmp_path, min_interval=0.0)

    assert relaunched.maybe_write(frame("menu_cards"), now=2000.0) is None


def test_pruning_a_screen_lets_it_be_captured_again(tmp_path: Path) -> None:
    """A screen stops being a duplicate when its last copy is evicted.
    Otherwise the directory drains towards empty while the writer goes on
    refusing to refill it.
    """
    writer = SnapshotWriter(tmp_path, min_interval=0.0, keep=1)

    first = writer.maybe_write(frame("menu_cards"), now=1000.0)
    assert first is not None
    # keep=1, so this evicts menu_cards.
    assert writer.maybe_write(frame("game_over"), now=1001.0) is not None
    assert not first.exists()

    assert writer.maybe_write(frame("menu_cards"), now=1002.0) is not None
