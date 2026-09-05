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
