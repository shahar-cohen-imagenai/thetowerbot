"""How a shopping visit sits inside the scan loop.

Three collaborators read the same frame - the screen tracker, the run tracker
and the shopping session - and the interactions between them are where this
feature can break something that already works. Each one gets a test.
"""

from __future__ import annotations

from pathlib import Path

import cv2

import digits
import tower_bot
from strategy import Shopping, ShoppingRule

_FIXTURES = Path(__file__).parent / "fixtures"


def frame(name: str):
    image = cv2.imread(str(_FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert image is not None, f"missing fixture: {name}.png"
    return image


def a_policy(**over) -> Shopping:
    base = dict(
        enabled=True, armed=False,
        workshop=(ShoppingRule(name="Damage", template="workshop/row_damage.png",
                               category="ATTACK"),),
    )
    return Shopping(**{**base, **over})


def navigated(bus) -> list[str]:
    """The targets Navigator actually tapped. Navigator has no
    `last_target` attribute and must not grow one just so a test can read
    it - events.Navigated is the public record of the thing being tested."""
    return [e.target for e in bus.published if e.type == "Navigated"]


def test_navigation_is_suppressed_while_a_visit_is_live(bot_on_main_menu) -> None:
    """Navigator taps BATTLE on MAIN_MENU. Left alone it starts a run in the
    middle of an errand."""
    bot = bot_on_main_menu(a_policy())
    bot.shopping.begin(a_policy(), run_count=1)
    assert bot.shopping.active
    bot.run_once()
    assert navigated(bot.bus) == [], "BATTLE was tapped mid-visit"


def test_navigation_resumes_once_the_visit_ends(bot_on_main_menu) -> None:
    bot = bot_on_main_menu(a_policy(enabled=False))
    assert not bot.shopping.active
    bot.run_once()
    assert "BATTLE" in navigated(bot.bus)


def test_unknown_snapshots_are_suppressed_while_a_visit_is_live(bot_on_workshop) -> None:
    """A workshop page reads UNKNOWN to the screen tracker by design. Without
    this, every visit fills unknown/ with pictures of the workshop and evicts
    the genuine mysteries the directory exists to hold."""
    bot = bot_on_workshop(a_policy())
    bot.shopping.begin(a_policy(), run_count=1)
    for _ in range(4):
        bot.run_once()
    assert bot.snapshots.written == []


def test_unknown_snapshots_still_happen_outside_a_visit(bot_on_workshop) -> None:
    """The suppression must be scoped to a live visit, not switched off."""
    bot = bot_on_workshop(a_policy(enabled=False))
    for _ in range(4):
        bot.run_once()
    assert bot.snapshots.written, "an unmodelled screen must still leave a trail"


def test_a_visit_does_not_block_a_run_from_opening_or_closing(bot_on_main_menu) -> None:
    """Run boundaries come from the screen tracker's own confirmations alone
    - shopping must never suppress or substitute for them.

    bot_on_main_menu's frame never changes on its own (that is what makes it
    useful for the other tests in this file), so an assertion that merely
    watched `runs.current_id` sit still while feeding it the same frame over
    and over would pass against a stub that did nothing: RunTracker.observe()
    short-circuits on every repeat of an already-confirmed reading, so
    RunTracker.transition() is never even called. Proving the property needs
    a REAL transition: feed a genuine in-run frame until the tracker confirms
    IN_RUN, then a menu frame until it confirms leaving it, and check that a
    run actually opens and closes on schedule.

    The visit itself does not survive the in-run frame - a fight page is
    UNKNOWN to pages.classify_page (see pages.py), so it self-aborts within
    two scans, the same off-page-streak safety valve any other unrecognised
    page would trip. That is expected, and is not what this test is about:
    the point is that the run opens and closes correctly regardless of
    whatever shopping happens to be doing at the time.
    """
    bot = bot_on_main_menu(a_policy())
    bot.shopping.begin(a_policy(), run_count=1)
    assert bot.shopping.active
    before_completed = bot.runs.completed

    bot._screen = frame("in_run_lit")
    for _ in range(2):  # SCREEN_CONFIRMATIONS consecutive readings to confirm
        bot.run_once()
    assert bot.runs.current_id is not None, "a run must still open normally"

    bot._screen = frame("main_menu")
    for _ in range(2):
        bot.run_once()
    assert bot.runs.current_id is None, "a run must still close normally"
    assert bot.runs.completed == before_completed + 1


def test_no_visit_starts_while_a_run_is_live(bot_in_run) -> None:
    """A visit begins from MAIN_MENU only. Starting one mid-fight would tap
    the workshop tab over a live run."""
    bot = bot_in_run(a_policy())
    for _ in range(3):
        bot.run_once()
    assert not bot.shopping.active


def test_a_paused_bot_does_not_shop(bot_on_main_menu) -> None:
    """Pause means 'still scanning, not tapping', and that must cover the one
    tap path that costs money - including a visit already under way, not
    just one that has not started yet. Pausing BEFORE begin() is not enough
    to prove this: that only exercises begin()'s own paused guard, which was
    never the gap. The visit is started first, live, and paused only once
    active - the shape that let a real bug through: advance() had no paused
    check at all and kept tapping mid-errand regardless."""
    bot = bot_on_main_menu(a_policy(armed=True))
    bot.shopping.begin(a_policy(armed=True), run_count=1)
    assert bot.shopping.active
    bot.controls.apply({"paused": True})
    for _ in range(4):
        bot.run_once()
    assert bot.device.taps == []
    assert bot.shopping.active, "pause must freeze the visit, not end it"


def test_reaching_the_run_cap_does_not_start_a_visit(bot_on_main_menu) -> None:
    bot = bot_on_main_menu(a_policy())
    for _ in range(3):
        bot.run_once(max_runs=1)
    assert not bot.shopping.active


def test_shopping_is_disabled_when_the_header_atlas_is_incomplete(monkeypatch) -> None:
    """No coin read means no purchase can be approved, so say so once at
    startup rather than failing silently on every visit.

    build_shopping() always returns a session, never None - a disabled one
    carries a non-empty `disabled_reason` and declines every begin()."""
    monkeypatch.setattr(digits.AtlasCache, "get", lambda self, name: None)
    session = tower_bot.build_shopping(bus=None, templates=None)
    assert session.disabled_reason, "an unbuilt header atlas must disable shopping"
    assert session.begin(a_policy(enabled=True), run_count=1) is False
