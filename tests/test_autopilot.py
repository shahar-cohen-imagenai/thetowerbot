from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import cv2

from tests.test_perception import recorded


class Device:
    def __init__(self) -> None:
        self.actions: list[tuple] = []

    def click(self, x: int, y: int) -> None:
        self.actions.append(("tap", x, y))

    def swipe(self, x: int, y: int, x2: int, y2: int, duration: float) -> None:
        self.actions.append(("swipe", x, y, x2, y2))


def parts() -> tuple:
    from autopilot import BattleAutopilot
    from perception import parse_frame
    from policy import AutopilotPolicy, UpgradeRule
    frame = cv2.imread(str(Path(__file__).parent / "fixtures/in_run_lit.png"))
    observation = parse_frame(frame, recorded("in_run_lit"), "battle", now=100)
    policy = AutopilotPolicy(enabled=True, rules=(UpgradeRule("damage"),))
    return BattleAutopilot(), Device(), frame, observation, policy


def test_purchase_needs_new_frame_acknowledgement() -> None:
    bot, device, frame, observation, policy = parts()
    bot.step(frame, device, policy, cash=100, observation=observation)
    assert len(device.actions) == 1
    assert bot.state.snapshot()["verified_purchases"] == 0
    bot.step(frame, device, policy, cash=100, observation=replace(observation, observed_at=101))
    assert len(device.actions) == 1
    changed = replace(observation, observed_at=102, rows=tuple(
        replace(r, price=12, value=4, observed_at=102) if r.upgrade_id == "damage" else r
        for r in observation.rows))
    bot.step(frame, device, policy, cash=90, observation=changed)
    assert bot.state.snapshot()["verified_purchases"] == 1
    assert len(device.actions) == 1


def test_missing_currency_never_authorizes_a_tap() -> None:
    bot, device, frame, observation, policy = parts()
    bot.step(frame, device, policy, observation=observation)
    assert device.actions == []
    assert "cash" in bot.state.snapshot()["reason"].lower()


def test_reserve_and_target_prevent_spending() -> None:
    bot, device, frame, observation, policy = parts()
    bot.step(frame, device, replace(policy, cash_reserve=95), cash=100, observation=observation)
    assert device.actions == []
    bot.step(frame, device, replace(policy, rules=(replace(policy.rules[0], target=3),)),
             cash=100, observation=observation)
    assert device.actions == []


def test_tab_switch_is_verified_before_buying() -> None:
    from policy import UpgradeRule
    bot, device, frame, observation, policy = parts()
    policy = replace(policy, rules=(UpgradeRule("health"),))
    bot.step(frame, device, policy, cash=100, observation=observation)
    assert len(device.actions) == 1
    assert device.actions[0][1] == 540
    # The game ignored navigation; it must not buy Damage in Health's place.
    for n in range(1,5):
        bot.step(frame, device, policy, cash=100, observation=replace(observation, observed_at=100+n))
    assert bot.state.snapshot()["verified_purchases"] == 0
    assert len(device.actions) <= 2


def test_unknown_search_has_a_finite_scroll_budget() -> None:
    from policy import UpgradeRule
    bot, device, frame, observation, policy = parts()
    policy = replace(policy, rules=(UpgradeRule("range"),), max_scrolls=2)
    for n in range(8):
        bot.step(frame, device, policy, cash=100, observation=replace(observation, observed_at=100+n))
    assert len(device.actions) <= 4
    observed = bot.state.snapshot()["observations"]
    row = next(r for r in observed if r["upgrade_id"] == "range")
    assert row["status"] == "unknown"


def test_run_end_discards_battle_values_and_pending_purchase() -> None:
    bot, device, frame, observation, policy = parts()
    bot.step(frame, device, policy, cash=100, observation=observation)
    bot.suspend("Run ended", clear_battle=True)
    assert bot.state.snapshot()["observations"] == []
    assert bot.state.snapshot()["next_upgrade_id"] is None


def test_manual_buy_works_once_with_automatic_policy_off() -> None:
    bot, device, frame, observation, policy = parts()
    bot.submit({"action": "buy", "upgrade_id": "damage"}, now=100)
    bot.step(frame, device, replace(policy, enabled=False), cash=100, observation=observation)
    assert len(device.actions) == 1
    for n in range(1, 12):
        bot.step(frame, device, replace(policy, enabled=False), cash=100,
                 observation=replace(observation, observed_at=100+n))
    assert len(device.actions) == 1


def test_expired_manual_command_does_not_execute_in_a_later_run() -> None:
    bot, device, frame, observation, policy = parts()
    bot.submit({"action": "buy", "upgrade_id": "damage"}, now=1)
    bot.step(frame, device, replace(policy, enabled=False), cash=100, observation=observation)
    assert device.actions == []


def test_policy_edit_preserves_pending_purchase_verification() -> None:
    bot, device, frame, observation, policy = parts()
    bot.step(frame, device, policy, cash=100, observation=observation)
    bot.step(frame, device, replace(policy, cash_reserve=1), cash=100,
             observation=replace(observation, observed_at=101))
    assert len(device.actions) == 1
    assert bot.pending is not None


def test_unreadable_panel_times_out_pending_purchase() -> None:
    bot, device, frame, observation, policy = parts()
    bot.step(frame, device, policy, cash=100, observation=observation)
    bot.step(frame, device, policy, cash=100,
             observation=replace(observation, category=None, rows=(), observed_at=110))
    assert bot.pending is None
    assert len(device.actions) == 1


def test_cached_offscreen_target_search_still_has_a_scroll_bound() -> None:
    from policy import UpgradeRule
    bot, device, frame, observation, policy = parts()
    damage = next(r for r in observation.rows if r.upgrade_id == "damage")
    bot.state.observe(replace(observation, rows=(replace(damage, upgrade_id="range", name="Range"),)))
    policy = replace(policy, rules=(UpgradeRule("range"),), max_scrolls=2)
    for n in range(8):
        bot.step(frame, device, policy, cash=100, observation=replace(observation, observed_at=100+n))
    assert len(device.actions) <= 4


def test_pause_retains_pending_evidence_without_more_taps() -> None:
    bot, device, frame, observation, policy = parts()
    bot.step(frame, device, policy, cash=100, observation=observation)
    bot.suspend("Paused")
    bot.step(frame, device, policy, cash=100, observation=replace(observation, observed_at=101))
    assert len(device.actions) == 1


def test_urgent_survival_precedes_discovering_unseen_economy() -> None:
    from policy import UpgradeRule
    bot, device, frame, observation, policy = parts()
    damage = observation.rows[0]
    rows = (replace(damage, upgrade_id="defense_absolute", category="DEFENSE", value=0),
            replace(damage, upgrade_id="defense_percent", category="DEFENSE", value=0))
    observation = replace(observation, category="DEFENSE", rows=rows,
                          combat={"wave": 5, "enemy_damage": 100})
    policy = replace(policy, preset="turtle", rules=tuple(UpgradeRule(i) for i in
                     ("defense_absolute", "defense_percent", "cash_bonus")))
    bot.step(frame, device, policy, cash=100, observation=observation)
    assert bot.pending is not None
    assert bot.pending[0].upgrade_id == "defense_absolute"
