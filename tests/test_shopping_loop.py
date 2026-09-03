"""How a shopping visit sits inside the scan loop.

Three collaborators read the same frame - the screen tracker, the run tracker
and the shopping session - and the interactions between them are where this
feature can break something that already works. Each one gets a test.
"""

from __future__ import annotations

import digits
import tower_bot
from strategy import Shopping, ShoppingRule


def a_policy(**over) -> Shopping:
    base = dict(
        enabled=True, armed=False,
        workshop=(ShoppingRule(name="Damage", template="workshop/row_damage.png",
                               category="ATTACK"),),
    )
    return Shopping(**{**base, **over})


def navigated(bus) -> list[str]:
    """The targets Navigator actually tapped, per task-9-overrides.md,
    override 1: Navigator has no `last_target` attribute and must not grow
    one just so a test can read it - events.Navigated is the public record
    of the thing being tested."""
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


def test_a_visit_does_not_open_or_close_a_run(bot_on_main_menu) -> None:
    bot = bot_on_main_menu(a_policy())
    bot.shopping.begin(a_policy(), run_count=1)
    before = bot.runs.current_id
    for _ in range(4):
        bot.run_once()
    assert bot.runs.current_id == before


def test_no_visit_starts_while_a_run_is_live(bot_in_run) -> None:
    """A visit begins from MAIN_MENU only. Starting one mid-fight would tap
    the workshop tab over a live run."""
    bot = bot_in_run(a_policy())
    for _ in range(3):
        bot.run_once()
    assert not bot.shopping.active


def test_a_paused_bot_does_not_shop(bot_on_main_menu) -> None:
    """Pause means 'still scanning, not tapping', and that must cover the one
    tap path that costs money."""
    bot = bot_on_main_menu(a_policy(armed=True))
    bot.controls.apply({"paused": True})
    for _ in range(4):
        bot.run_once()
    assert bot.device.taps == []


def test_reaching_the_run_cap_does_not_start_a_visit(bot_on_main_menu) -> None:
    bot = bot_on_main_menu(a_policy())
    for _ in range(3):
        bot.run_once(max_runs=1)
    assert not bot.shopping.active


def test_shopping_is_disabled_when_the_header_atlas_is_incomplete(monkeypatch) -> None:
    """No coin read means no purchase can be approved, so say so once at
    startup rather than failing silently on every visit.

    Per task-9-overrides.md, override 2: build_shopping() always returns a
    session, never None - a disabled one carries a non-empty
    `disabled_reason` and declines every begin()."""
    monkeypatch.setattr(digits.AtlasCache, "get", lambda self, name: None)
    session = tower_bot.build_shopping(bus=None, templates=None)
    assert session.disabled_reason, "an unbuilt header atlas must disable shopping"
    assert session.begin(a_policy(enabled=True), run_count=1) is False
