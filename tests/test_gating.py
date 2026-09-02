from __future__ import annotations

import dataclasses
import time
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


def test_recorded_boxes_carry_the_buy_point_not_the_label_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """config.buy_point() documents that the label is itself a button - a tap
    there buys nothing. The overlay must record where the bot actually taps
    (tap_x/tap_y), separate from the label's own matched (x, y)."""
    from frames import FrameBuffer

    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()
    bot.frames = FrameBuffer()
    bot.frames.publish(bot._screen)

    bot.run_once()

    boxes = bot.frames.boxes()
    assert len(boxes) == 4
    for box in boxes:
        expected_x, expected_y = config.buy_point((box["x"], box["y"]))
        assert (box["tap_x"], box["tap_y"]) == (expected_x, expected_y)
        # The whole point: the buy square is not the label's own origin.
        assert (box["tap_x"], box["tap_y"]) != (box["x"], box["y"])


def test_boxes_are_swapped_in_atomically_not_added_incrementally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The atomic-swap regression guard: run_once() must hand the whole
    scan's boxes to FrameBuffer in one set_boxes() call, not build them up
    with per-match add_box() calls - the latter is exactly the window a
    reader could catch mid-scan with only some of the frame's matches
    visible. Caught via the add_box spy: a set_boxes() that regressed to
    clear-then-add_box-in-a-loop would call the spied add_box() once per
    box instead of leaving `calls` as just ["set_boxes"]."""
    from frames import FrameBuffer

    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()
    bot.frames = FrameBuffer()
    bot.frames.publish(bot._screen)

    calls: list[str] = []
    monkeypatch.setattr(
        bot.frames, "add_box",
        lambda box: calls.append("add_box"),
    )
    real_set_boxes = bot.frames.set_boxes

    def spy_set_boxes(boxes: list[dict]) -> None:
        calls.append("set_boxes")
        real_set_boxes(boxes)

    monkeypatch.setattr(bot.frames, "set_boxes", spy_set_boxes)

    bot.run_once()

    assert calls == ["set_boxes"], calls
    assert len(bot.frames.boxes()) == 4


def test_tapped_flag_reaches_frames_boxes_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FrameBuffer.mark_tapped() is the entire visual signal for "this is the
    one it actually tapped" - it is what turns a box emerald instead of amber
    in the overlay. Run a real scan through frames.boxes() rather than
    presetting `tapped` by hand (as test_frame_api.py does), and check both
    directions: a tapped upgrade comes back True, and one that matched but
    was withheld comes back False. The second half is what makes this a real
    assertion instead of "everything is true" - the in_run_lit fixture taps
    every action when nothing is on cooldown (see
    test_in_run_taps_every_affordable_upgrade), so three of the four
    templates are seeded into cooldown here to force a real, matched-but-
    not-tapped box for the others to be compared against.
    """
    from frames import FrameBuffer

    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()
    now = time.monotonic()
    for action in config.ACTIONS:
        if action.name != "Damage":
            bot._last_click[action.template] = now

    bot.frames = FrameBuffer()
    bot.frames.publish(bot._screen)

    bot.run_once()

    tapped_names = {e.action for e in rec.of(events.Tapped)}
    assert tapped_names == {"Damage"}

    tapped_by_name = {box["name"]: box["tapped"] for box in bot.frames.boxes()}
    assert tapped_by_name["Damage"] is True
    assert tapped_by_name["Attack Speed"] is False


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
    """End to end: dimming one action's own region below the ratio its row
    demands must skip that action and only that action.

    The loop now walks the strategy's rows, not config.ACTIONS, so the
    strategy - not the module constant - is what has to carry the ratio.
    Darkening the fixture in place (rather than the old trick of setting an
    impossible ratio like 99.0) is necessary too: ActionRule clamps
    brightness_ratio to [0, 1], unlike config.Action, which enforced
    nothing - and this fixture, being the exact frame every template was
    captured from, measures 1.0 for every region, so no in-range ratio could
    ever force a skip on its own. TM_CCOEFF_NORMED is blind to brightness
    (see vision.brightness_ratio's own docstring), so darkening the region
    leaves the match score untouched and only the brightness gate notices.
    """
    from control import Controls
    from strategy import ActionRule, Strategy

    bot, rec, dev = settled_bot("in_run_lit", monkeypatch)
    bot._last_click.clear()

    cache = vision.TemplateCache(TEMPLATES)
    template = cache.get("upgrade_critical_chance.png")
    match = vision.locate_template(bot._screen, template, 0.9)
    assert match is not None
    left, top = match.top_left
    height, width = template.shape[:2]
    bot._screen = bot._screen.copy()
    region = bot._screen[top : top + height, left : left + width]
    region[:] = (region.astype(float) * 0.3).astype(region.dtype)

    bot.controls = Controls(strategy=Strategy(
        name="t",
        actions=(
            ActionRule(name="Damage", template="upgrade_damage.png",
                       threshold=0.9, brightness_ratio=0.75),
            ActionRule(name="Critical Chance", template="upgrade_critical_chance.png",
                       threshold=0.9, brightness_ratio=0.75),
        ),
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

    # config.ACTIONS currently has a bijective name<->template mapping, so
    # asserting only the behaviour above (taps, then cooldown suppression)
    # would pass just as well against an implementation that keyed
    # _last_click by name instead of template - the two are interchangeable
    # while every action has a unique name AND a unique template. Reach into
    # the cooldown dict directly (the fixture already does, via
    # bot._last_click.clear()) to pin the mechanism, not just the emergent
    # behaviour. The second assertion is the one that actually catches a
    # regression: it fails the moment _last_click is keyed by name.
    assert set(bot._last_click) <= templates
    assert not set(bot._last_click) & names

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
    """Disabling is now a per-row `enabled` flag on the strategy, not a
    separate `enabled_actions` allow-list - apply() must patch the whole
    `actions` list, since Strategy.merged() replaces it wholesale rather
    than merging row by row."""
    bot, seen = bot_in_run
    disabled = [
        {
            "name": rule.name,
            "template": rule.template,
            "enabled": False,
            "threshold": rule.threshold,
            "brightness_ratio": rule.brightness_ratio,
        }
        for rule in bot.controls.snapshot().strategy.actions
    ]
    bot.controls.apply({"actions": disabled})

    bot.run_once()

    assert not [event for event in seen if event.type == "Tapped"]


def test_the_loop_taps_in_strategy_order_not_config_order(
    bot_in_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Order IS priority. Reversing the rows must reverse the evaluation.

    This is the behaviour change the whole plan exists for: before, the loop
    walked config.ACTIONS and used the strategy only as an on/off filter.
    """
    from control import Controls
    from strategy import ActionRule, Strategy

    bot, _ = bot_in_run
    tried: list[str] = []
    bot.controls = Controls(strategy=Strategy(
        name="t",
        actions=(
            ActionRule(name="Critical Chance", template="upgrade_critical_chance.png"),
            ActionRule(name="Damage", template="upgrade_damage.png"),
        ),
    ))

    def record(action, boxes=None, cooldown=None):
        tried.append(action.name)
        return False

    monkeypatch.setattr(bot, "find_and_click_image", record)
    bot.run_once()
    assert tried == ["Critical Chance", "Damage"]


def test_a_disabled_row_is_never_tried(
    bot_in_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    from control import Controls
    from strategy import ActionRule, Strategy

    bot, _ = bot_in_run
    tried: list[str] = []
    bot.controls = Controls(strategy=Strategy(
        name="t",
        actions=(
            ActionRule(name="Damage", template="upgrade_damage.png", enabled=False),
            ActionRule(name="Critical Chance", template="upgrade_critical_chance.png"),
        ),
    ))
    monkeypatch.setattr(
        bot, "find_and_click_image",
        lambda action, boxes=None, cooldown=None: tried.append(action.name),
    )
    bot.run_once()
    assert tried == ["Critical Chance"]


def test_the_per_row_threshold_reaches_the_matcher(
    bot_in_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A threshold edited on the strategy page must change what the vision
    layer is asked for - otherwise the control does nothing and says nothing.
    """
    from control import Controls
    from strategy import ActionRule, Strategy

    bot, _ = bot_in_run
    seen_thresholds: list[float] = []
    bot.controls = Controls(strategy=Strategy(
        name="t",
        actions=(ActionRule(
            name="Damage", template="upgrade_damage.png", threshold=0.42
        ),),
    ))
    monkeypatch.setattr(
        bot, "find_and_click_image",
        lambda action, boxes=None, cooldown=None: seen_thresholds.append(action.threshold),
    )
    bot.run_once()
    assert seen_thresholds == [0.42]


def test_click_cooldown_comes_from_the_strategy(
    bot_in_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-strategy click_cooldown must reach find_and_click_image.

    Two passes back to back: the second must be refused by the cooldown,
    which only happens if the loop reads the strategy's 30s. click_cooldown
    is no longer a constructor argument at all - it would have to come from
    here or nowhere.
    """
    from control import Controls
    from strategy import ActionRule, Strategy

    bot, seen = bot_in_run
    bot.controls = Controls(strategy=Strategy(
        name="t",
        actions=(ActionRule(name="Damage", template="upgrade_damage.png"),),
        click_cooldown=30.0,
    ))
    monkeypatch.setattr("tower_bot.tap", lambda *a, **k: None)

    bot.run_once()
    taps_after_first = len([e for e in seen if isinstance(e, events.Tapped)])
    assert taps_after_first == 1

    bot.run_once()
    taps_after_second = len([e for e in seen if isinstance(e, events.Tapped)])
    assert taps_after_second == taps_after_first


def test_a_pass_reuses_its_own_snapshot_for_every_matched_row(
    bot_in_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one-snapshot-per-pass invariant covers click_cooldown too, not
    just the top-of-loop reads.

    find_and_click_image() is called once per matched rule inside the
    action loop, with no `break` after a tap - so if it fell back to
    re-reading self.controls.snapshot() instead of using the value run_once
    already threads down, a PATCH landing mid-scan (run_forever and
    serve_web run on different threads) could judge a later row's cooldown
    against a click_cooldown from a different instant than the strategy
    that selected and ordered the rows.

    Proven by making every snapshot() call after the pass's own first one
    return a maximally restrictive click_cooldown: both rows can only tap if
    the loop passed down the *first* snapshot's permissive value rather
    than asking again per row.
    """
    from control import Controls
    from strategy import ActionRule, Strategy, MAX_COOLDOWN

    bot, seen = bot_in_run
    bot.controls = Controls(strategy=Strategy(
        name="t",
        actions=(
            ActionRule(name="Attack Speed", template="upgrade_attack_speed.png"),
            ActionRule(name="Critical Chance", template="upgrade_critical_chance.png"),
        ),
        click_cooldown=1.0,
    ))

    # Both "last tapped" 2s ago: affordable under the strategy's 1.0s
    # cooldown, still cooling down under the poisoned MAX_COOLDOWN below
    # (Strategy.__post_init__ caps click_cooldown there, so that - not an
    # arbitrarily large number - is the most restrictive legal value).
    now = time.monotonic()
    bot._last_click["upgrade_attack_speed.png"] = now - 2.0
    bot._last_click["upgrade_critical_chance.png"] = now - 2.0

    real_snapshot = bot.controls.snapshot
    calls = {"n": 0}

    def poisoned_snapshot():
        calls["n"] += 1
        live = real_snapshot()
        if calls["n"] == 1:
            # run_once()'s own top-of-pass read - the value every row must
            # actually be judged against.
            return live
        # Any snapshot taken after that one, inside the loop, must never be
        # consulted - if it is, this restrictive cooldown blocks the row
        # that asked for it.
        return dataclasses.replace(
            live,
            strategy=dataclasses.replace(live.strategy, click_cooldown=MAX_COOLDOWN),
        )

    monkeypatch.setattr(bot.controls, "snapshot", poisoned_snapshot)

    bot.run_once()

    tapped = {e.action for e in seen if isinstance(e, events.Tapped)}
    assert tapped == {"Attack Speed", "Critical Chance"}
    # Exactly one snapshot for the whole pass - find_and_click_image's own
    # None-fallback (its only other caller is a direct test) never fires
    # when run_once is the one calling it.
    assert calls["n"] == 1


def test_max_runs_falls_back_to_the_strategy(bot_in_run) -> None:
    """The parameter wins when set; the strategy supplies it otherwise.

    The two can never both be meaningfully set in the web path, because the
    CLI persists its flag into the strategy rather than carrying it alongside.
    """
    from control import Controls
    from strategy import ActionRule, Strategy

    bot, _ = bot_in_run
    bot.controls = Controls(strategy=Strategy(
        name="t",
        actions=(ActionRule(name="Damage", template="upgrade_damage.png"),),
        max_runs=3,
    ))
    bot.runs.completed = 3
    assert bot.run_cap_reached(None) is True
    assert bot.run_cap_reached(10) is False
