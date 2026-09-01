from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import cv2
import pytest

import config
import events
import screens
import vision
from tower_bot import TowerBot

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"


class Recorder:
    def __init__(self) -> None:
        self.seen: list[events.Event] = []

    def offer(self, event: events.Event) -> bool:
        self.seen.append(event)
        return True

    def of(self, kind: type) -> list[events.Event]:
        return [e for e in self.seen if isinstance(e, kind)]


def frame(name: str):
    return cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)


def settled_bot(fixture: str, monkeypatch: pytest.MonkeyPatch):
    """A bot whose tracker has already confirmed the fixture's screen."""
    dev = MagicMock()
    bus = events.EventBus()
    rec = Recorder()
    bus.subscribe(rec)
    bot = TowerBot(device=dev, templates=vision.TemplateCache(TEMPLATES), bus=bus)
    bot._screen = frame(fixture)
    monkeypatch.setattr(bot, "refresh_screen", lambda: bot._screen)
    bot.run_once()  # first reading
    bot.run_once()  # confirms the transition
    rec.seen.clear()
    return bot, rec, dev


@pytest.fixture
def bot_in_run(monkeypatch: pytest.MonkeyPatch):
    """A bot settled on the IN_RUN screen, paired with its recorded events.

    Reuses settled_bot's fake-device set-up so the Controls-gating tests
    exercise the real scan loop instead of a hand-rolled stand-in.
    """
    bot, rec, _dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()  # ignore cooldown left over from the settling scans
    return bot, rec.seen


def test_game_over_does_not_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for the originating bug.

    The upgrade panel is still rendered behind the death modal and the
    templates score 1.000 against it. Asserting the ABSENCE of a tap is the
    point. The reason is screen_gated, because the screen gate fires before
    the brightness check ever runs - see test_brightness_gate_rejects_dimmed
    for direct coverage of the gate itself.
    """
    bot, rec, dev = settled_bot("game_over", monkeypatch)

    bot.run_once()

    assert rec.of(events.Tapped) == []
    dev.click.assert_not_called()

    # ONE event for the whole scan, not one per action. At the 2s default
    # that is the difference between ~43k and ~172k events a day.
    skips = rec.of(events.Skipped)
    assert [e.reason for e in skips] == ["screen_gated"]
    assert skips[0].detail == "screen is GAME_OVER"


def test_main_menu_does_not_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, rec, dev = settled_bot("main_menu", monkeypatch)

    bot.run_once()

    assert rec.of(events.Tapped) == []
    dev.click.assert_not_called()
    assert [e.reason for e in rec.of(events.Skipped)] == ["screen_gated"]


def test_in_run_taps_every_affordable_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()  # ignore cooldown from the settling scans

    bot.run_once()

    tapped = {e.action for e in rec.of(events.Tapped)}
    assert len(tapped) == 4


def test_brightness_gate_rejects_dimmed_regions() -> None:
    """Direct coverage of BrightnessAffordability, which the full-loop
    game-over test never reaches because screen gating fires first."""
    import config
    from affordability import BrightnessAffordability

    check = BrightnessAffordability()
    action = config.Action(
        name="Damage", template="upgrade_damage.png", threshold=0.9,
        brightness_ratio=0.75,
    )
    cache = vision.TemplateCache(TEMPLATES)
    template = cache.get(action.template)

    dim = frame("game_over")
    match = vision.locate_template(dim, template, threshold=0.9)
    ok, _ = check.affordable(dim, match, template, action)
    assert ok is False

    lit = frame("in_run_lit")
    match = vision.locate_template(lit, template, threshold=0.9)
    ok, _ = check.affordable(lit, match, template, action)
    assert ok is True


def test_a_per_action_brightness_ratio_of_zero_disables_the_check() -> None:
    """README documents `brightness_ratio` as a per-button knob, so it has to
    actually reach the affordability check - it was dead config."""
    import config
    from affordability import BrightnessAffordability

    check = BrightnessAffordability()
    cache = vision.TemplateCache(TEMPLATES)
    template = cache.get("upgrade_damage.png")
    dim = frame("game_over")
    match = vision.locate_template(dim, template, threshold=0.9)

    gated = config.Action(name="Damage", template="upgrade_damage.png",
                          brightness_ratio=0.75)
    assert check.affordable(dim, match, template, gated)[0] is False

    disabled = config.Action(name="Damage", template="upgrade_damage.png",
                             brightness_ratio=0.0)
    assert check.affordable(dim, match, template, disabled)[0] is True


def test_a_per_action_brightness_ratio_reaches_the_scan_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: raising one action's ratio above what the lit frame
    measures must skip that action and only that action."""
    import config

    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()
    monkeypatch.setattr(config, "ACTIONS", (
        config.Action(name="Damage", template="upgrade_damage.png",
                      threshold=0.9, brightness_ratio=0.75),
        config.Action(name="Critical Chance", template="upgrade_critical_chance.png",
                      threshold=0.9, brightness_ratio=99.0),
    ))

    bot.run_once()

    assert [e.action for e in rec.of(events.Tapped)] == ["Damage"]
    skipped = rec.of(events.Skipped)
    assert [(e.action, e.reason) for e in skipped] == [
        ("Critical Chance", "dimmed")
    ]


def test_gating_uses_the_debounced_state_not_the_raw_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate must read the tracker, not the frame in front of it.

    Every other gating test scans one static fixture twice, so the raw
    reading and the confirmed state always agree and an implementation
    gating on the raw reading would pass them all. Debounced gating is the
    spec's defence against a fade frame that still scores as IN_RUN, so it
    needs a test where the two disagree.
    """
    bot, rec, dev = settled_bot("game_over", monkeypatch)
    bot._last_click.clear()

    bot._screen = frame("in_run_lit")  # raw reading flips; tracker has not
    bot.run_once()

    assert bot.screen_state is screens.ScreenState.GAME_OVER
    assert rec.of(events.Tapped) == []
    dev.click.assert_not_called()
    assert [e.reason for e in rec.of(events.Skipped)] == ["screen_gated"]

    # Positive control: the second identical frame confirms IN_RUN, and only
    # then does the bot tap. Otherwise this test would pass against a bot
    # that never taps at all.
    bot.run_once()

    assert bot.screen_state is screens.ScreenState.IN_RUN
    assert len(rec.of(events.Tapped)) == 4


def test_cooldown_suppresses_a_repeat_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()

    bot.run_once()
    rec.seen.clear()
    bot.run_once()  # immediately again - all four are still cooling down

    assert rec.of(events.Tapped) == []
    assert {e.reason for e in rec.of(events.Skipped)} == {"cooldown"}


def test_tapped_events_carry_the_human_name_and_cooldown_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the contract this fix exists for.

    `Tapped.action` must be `action.name` ("Damage"), not `action.template`
    ("upgrade_damage.png") - that is the vocabulary the control page, the
    log, the SSE feed and the stored `events.action` column all need to
    agree on. But cooldown gating must still key on the template
    internally: proven here by scanning twice in a row and seeing the
    second scan suppressed, exactly as it was before the two identities
    were split apart.
    """
    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()

    bot.run_once()

    tapped = rec.of(events.Tapped)
    assert len(tapped) == 4
    names = {a.name for a in config.ACTIONS}
    templates = {a.template for a in config.ACTIONS}
    assert {e.action for e in tapped} == names
    assert not {e.action for e in tapped} & templates

    rec.seen.clear()
    bot.run_once()  # immediately again - cooldown must still suppress this

    assert rec.of(events.Tapped) == []
    assert {e.reason for e in rec.of(events.Skipped)} == {"cooldown"}


def test_taps_land_on_the_buy_button_not_the_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: tapping the matched template opens an info popup.

    Templates crop the LABEL, because the level and price beside it change on
    every purchase and would break the match. But the label is itself a
    button - it opens an info panel that covers the screen. The bot therefore
    bought nothing (prices sat unchanged for minutes while the wallet only
    grew) and every following frame read as `dimmed` behind the overlay.

    The buy button is the square to the label's right, and PRICE_REGION is
    already calibrated inside it for all four upgrades - which is why the tap
    point is derived from that offset rather than from a fifth measurement.
    """
    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()

    bot.run_once()

    tapped = rec.of(events.Tapped)
    assert len(tapped) == 4

    cache = vision.TemplateCache(TEMPLATES)
    region = config.PRICE_REGION
    template_by_name = {a.name: a.template for a in config.ACTIONS}
    for event in tapped:
        template = cache.get(template_by_name[event.action])
        match = vision.locate_template(bot._screen, template, 0.9)
        assert match is not None, event.action
        left, top = match.top_left

        assert left + region.dx <= event.x <= left + region.dx + region.w, event.action
        assert top + region.dy <= event.y <= top + region.dy + region.h, event.action

        on_label = (
            left <= event.x <= left + template.shape[1]
            and top <= event.y <= top + template.shape[0]
        )
        assert not on_label, f"{event.action} tap landed on the info button"


def test_paused_keeps_scanning_but_never_taps(bot_in_run) -> None:
    """Pause must not blind the dashboard.

    A paused bot that stopped reporting would blank the dashboard at the exact
    moment you paused it to look at something, and would lose the screen
    tracking that makes resuming safe. So it still captures, classifies and
    publishes ScanCompleted - it just does not act.
    """
    bot, seen = bot_in_run
    bot.controls.apply({"paused": True})

    bot.run_once()

    kinds = [event.type for event in seen]
    assert "ScanCompleted" in kinds
    assert "Tapped" not in kinds
    # Exactly one, not "at least one": the paused skip is hoisted out of the
    # action loop for the same reason screen_gated is - one per scan, not
    # one per action, or an idle bot emits 4 identical events every 2s
    # (~172k/day) instead of 1.
    paused_skips = [
        event for event in seen if event.type == "Skipped" and event.reason == "paused"
    ]
    assert len(paused_skips) == 1


def test_paused_does_not_auto_navigate(bot_in_run, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pausing must gate auto-navigate too, not just the action loop - a
    paused bot that still tapped RETRY/BATTLE would start a run nobody asked
    for."""
    bot, seen = bot_in_run
    navigated: list[bool] = []
    monkeypatch.setattr(
        bot.navigator, "maybe_navigate", lambda *a, **k: navigated.append(True)
    )
    bot.controls.apply({"auto_navigate": True, "paused": True})

    bot.run_once()

    assert navigated == []


def test_resuming_taps_again(bot_in_run) -> None:
    bot, seen = bot_in_run
    bot.controls.apply({"paused": True})
    bot.run_once()
    seen.clear()
    bot.controls.apply({"paused": False})

    bot.run_once()

    assert any(event.type == "Tapped" for event in seen)


def test_disabled_actions_are_not_tapped(bot_in_run) -> None:
    bot, seen = bot_in_run
    bot.controls.apply({"enabled_actions": []})

    bot.run_once()

    assert not [event for event in seen if event.type == "Tapped"]
