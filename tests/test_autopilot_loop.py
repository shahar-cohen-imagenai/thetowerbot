"""The OCR autopilot as the scan loop drives it.

test_autopilot.py hands `step()` a frame directly, so it can prove what the
executor decides but never that anything calls it. That gap is exactly where
this bug lived: the loop reached `step()` on the ATTACK tab and nowhere else,
so the executor's own tests all passed while the bot sat on the UTILITY tab
doing nothing for the rest of the run.

The tab is the whole variable. `screens/in_run.png` is the ATTACK header
crop, so a DEFENSE or UTILITY frame is IN_RUN by its cash counter alone and
carries no panel anchor - and the autopilot's first act under any economy
preset is to open UTILITY, which is a tab it cannot come back from if the
loop stops stepping it there.
"""

from __future__ import annotations

from typing import Callable

import pytest

from strategy import Claims, Shopping, ShoppingRule
from tower_bot import TowerBot


@pytest.mark.parametrize("frame", ["in_run_lit", "in_run_defense", "in_run_utility"])
def test_the_autopilot_steps_on_every_upgrade_tab(
    bot_in_run_on: Callable[[str], TowerBot],
    monkeypatch: pytest.MonkeyPatch,
    frame: str,
) -> None:
    bot = bot_in_run_on(frame)
    bot.controls.apply({"autopilot": {"enabled": True}})
    stepped: list[str] = []
    monkeypatch.setattr(
        bot.autopilot, "step", lambda *args, **kwargs: bool(stepped.append(frame))
    )

    bot.run_once()

    assert stepped == [frame], (
        f"the scan loop never stepped the autopilot on {frame}; a tab whose "
        "header the ATTACK template does not match is still a live run"
    )


def test_the_overlay_shows_what_the_autopilot_read(
    bot_in_run_on: Callable[[str], TowerBot],
) -> None:
    """`boxes` is a local list the legacy matcher fills as it goes, and
    run_once() hands it to the frame buffer at the end of every pass. The
    autopilot path never touched it, so taking over in-run buying meant
    set_boxes([]) blanked the device view on every scan.
    """
    from frames import FrameBuffer

    bot = bot_in_run_on("in_run_lit")
    bot.frames = FrameBuffer()
    bot.controls.apply({"autopilot": {"enabled": True}})

    bot.run_once()

    assert bot.frames.boxes(), "the device view went blank while the autopilot was deciding"


# -- Claim cadence in the loop ---------------------------------------------
def test_a_due_claim_is_armed_from_the_main_menu(bot_on_main_menu: Callable[..., TowerBot]) -> None:
    """The same frame shopping.begin() reserves, and only when it declined.
    A never-claimed account with a read best wave owes a milestones claim."""
    bot = bot_on_main_menu(Shopping(), claims=Claims(enabled=True))  # shopping disabled by default
    bot._best_wave = 137  # a read best wave, as prepare_store() would seed it
    bot.run_once()
    assert bot.milestones_claim.active


def test_an_armed_walk_is_not_re_armed_on_the_next_frame(
    bot_on_main_menu: Callable[..., TowerBot]
) -> None:
    """request() returning False is the normal case, not an error: the walk
    from the previous frame is still holding the menus."""
    bot = bot_on_main_menu(Shopping(), claims=Claims(enabled=True))
    bot._best_wave = 137
    bot.run_once()
    first = bot.milestones_claim.snapshot()["requested_at"]
    bot.run_once()
    assert bot.milestones_claim.snapshot()["requested_at"] == first


def test_a_disabled_cadence_arms_nothing(bot_on_main_menu: Callable[..., TowerBot]) -> None:
    bot = bot_on_main_menu(Shopping())  # claims disabled (the fixture's default)
    bot._best_wave = 137
    bot.run_once()
    assert not bot.claim.active
    assert not bot.milestones_claim.active


def test_a_shopping_visit_keeps_the_frame_from_a_due_claim(
    bot_on_main_menu: Callable[..., TowerBot]
) -> None:
    """One maintenance walk at a time. A visit that took this frame means the
    claim waits - and it must not be armed only to be refused."""
    bot = bot_on_main_menu(
        Shopping(enabled=True, workshop=(ShoppingRule(name="Damage", category="ATTACK"),)),
        claims=Claims(enabled=True),
    )  # BOTH claims and a due shopping visit
    bot._best_wave = 137
    bot.run_once()
    assert bot.shopping.active
    assert not bot.claim.active
    assert not bot.milestones_claim.active
