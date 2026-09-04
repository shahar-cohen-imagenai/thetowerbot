"""Permanent-currency limits, scrolling and purchase acknowledgement."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

import config
import events
import ocr
import shopping
from autopilot import AutopilotState
from perception import Observation, ObservedUpgrade
from strategy import Shopping, ShoppingRule


def row(name: str = "Damage", upgrade_id: str = "damage", *, price: int | None = 30,
        value: float | None = 3, status: str = "available") -> ObservedUpgrade:
    return ObservedUpgrade(upgrade_id, name, "ATTACK", "workshop", value, price,
                           status, 1, config.Rect(20, 320, 450, 196), (350, 480))


def observation(*rows: ObservedUpgrade) -> Observation:
    return Observation("ATTACK", rows, {}, None, 1, 270)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    published: list[events.Event] = []
    taps: list[tuple[int, int]] = []
    state = SimpleNamespace(observation=observation(row()), coins=1000, scrolls=[])
    session = shopping.ShoppingSession(None, SimpleNamespace(publish=published.append), None)
    session.observations = AutopilotState()
    monkeypatch.setattr(shopping, "observe_frame", lambda *_: state.observation, raising=False)
    monkeypatch.setattr(shopping, "header_numbers", lambda *_: (state.coins, 40))
    monkeypatch.setattr(shopping, "tap", lambda _device, x, y: taps.append((x, y)))
    monkeypatch.setattr(session, "_exit_to_battle", lambda *_: None)
    monkeypatch.setattr("autopilot.scroll_panel", lambda *_, down: state.scrolls.append(down))
    screen = np.zeros((2400, 1080, 3), dtype=np.uint8)
    reading = SimpleNamespace(page="WORKSHOP", top_left=(0, 0))

    def start(**changes: object) -> Shopping:
        values = dict(enabled=True, armed=True, coin_budget=100,
                      workshop=(ShoppingRule("Damage", "ATTACK"),))
        policy = Shopping(**(values | changes))
        assert session.begin(policy, (session._last_run_count or 0) + 1)
        session._step = shopping.Step.BUY_ROWS
        return policy

    def step(policy: Shopping) -> None:
        session._buy_rows(reading, screen, None, policy)

    return SimpleNamespace(session=session, state=state, taps=taps, events=published,
                           start=start, step=step)


@pytest.mark.parametrize("limits", [{"coin_budget": 0}, {"coin_budget": 29},
                                    {"coin_reserve": 971}])
def test_limits_deny_permanent_spending(harness: SimpleNamespace, limits: dict) -> None:
    policy = harness.start(**limits)
    harness.step(policy)
    assert harness.taps == []
    assert not any(isinstance(e, events.Purchased) for e in harness.events)


def test_zero_budget_keeps_workshop_purchases_disarmed_even_if_free(harness: SimpleNamespace) -> None:
    harness.state.observation = observation(row(price=0))
    harness.step(harness.start(coin_budget=0))
    assert harness.taps == []


def test_purchase_waits_for_changed_value_or_price(harness: SimpleNamespace) -> None:
    policy = harness.start()
    harness.step(policy)
    assert len(harness.taps) == 1
    assert not any(isinstance(e, events.Purchased) for e in harness.events)
    harness.step(policy)
    assert len(harness.taps) == 1
    harness.state.observation = observation(row(price=33, value=4))
    harness.state.coins = 970
    harness.step(policy)
    purchases = [e for e in harness.events if isinstance(e, events.Purchased)]
    assert len(purchases) == 1
    assert purchases[0].price == 30
    assert harness.session.observations.snapshot()["verified_purchases"] == 1


def test_unchanged_purchase_aborts_without_another_buy(harness: SimpleNamespace) -> None:
    policy = harness.start()
    harness.step(policy)
    for _ in range(3):
        harness.step(policy)
    assert harness.taps == [(350, 480)]
    assert not harness.session.active
    assert not any(isinstance(e, events.Purchased) for e in harness.events)


@pytest.mark.parametrize("value", [3, None])
def test_target_requires_readable_value_below_target(harness: SimpleNamespace,
                                                    value: float | None) -> None:
    harness.state.observation = observation(row(value=value))
    policy = harness.start(workshop=(ShoppingRule("Damage", "ATTACK", target=3),))
    harness.step(policy)
    assert harness.taps == []


@pytest.mark.parametrize("name,upgrade_id", [("Wall Rebuild", "wall_rebuild"),
                                            ("Shockwave Frequency", "shockwave_frequency")])
def test_decreasing_targets_buy_and_confirm_shorter_time(
    harness: SimpleNamespace, name: str, upgrade_id: str,
) -> None:
    before = replace(row(name, upgrade_id, value=5), category="DEFENSE")
    harness.state.observation = replace(observation(before), category="DEFENSE")
    policy = harness.start(workshop=(ShoppingRule(name, "DEFENSE", target=3),))
    harness.step(policy)
    assert len(harness.taps) == 1
    harness.state.observation = replace(observation(replace(before, value=4)), category="DEFENSE")
    harness.step(policy)
    assert len([e for e in harness.events if isinstance(e, events.Purchased)]) == 1


def test_decreasing_target_already_met_does_not_buy(harness: SimpleNamespace) -> None:
    reached = replace(row("Wall Rebuild", "wall_rebuild", value=2), category="DEFENSE")
    harness.state.observation = replace(observation(reached), category="DEFENSE")
    harness.step(harness.start(workshop=(ShoppingRule("Wall Rebuild", "DEFENSE", target=3),)))
    assert harness.taps == []


@pytest.mark.parametrize("status", ["locked", "maxed", "unreadable"])
def test_non_buyable_states_do_not_tap(harness: SimpleNamespace, status: str) -> None:
    harness.state.observation = observation(row(status=status))
    harness.step(harness.start())
    assert harness.taps == []


def test_unlock_requires_permission_and_explicit_transition(harness: SimpleNamespace) -> None:
    unlock = row("Unlock Range Upgrades", "unlock_range_upgrades", value=None)
    harness.state.observation = observation(unlock, row())
    rules = (ShoppingRule("Unlock Range Upgrades", "ATTACK"),)
    policy = harness.start(workshop=rules)
    harness.step(policy)
    assert harness.taps == []
    harness.session.reset()
    policy = harness.start(workshop=rules, allow_unlocks=True)
    harness.step(policy)
    assert len(harness.taps) == 1
    # Merely missing the old tile is not proof: OCR can omit it.
    harness.state.observation = observation(row())
    harness.step(policy)
    assert not any(isinstance(e, events.Purchased) for e in harness.events)
    harness.state.observation = observation(row(), row("Range", "range", price=40))
    harness.state.coins = 970
    harness.step(policy)
    assert len([e for e in harness.events if isinstance(e, events.Purchased)]) == 1
    harness.session.reset()
    harness.step(harness.start(workshop=rules, allow_unlocks=True))
    assert len(harness.taps) == 1, "a verified unlock must not be bought again"
    assert harness.state.scrolls == [], "the completed unlock needs no new search"


def test_rehearsal_ignores_spend_budget_and_never_taps(harness: SimpleNamespace) -> None:
    policy = harness.start(armed=False, coin_budget=0)
    harness.step(policy)
    assert harness.taps == []
    purchases = [e for e in harness.events if isinstance(e, events.Purchased)]
    assert len(purchases) == 1 and purchases[0].dry_run


def test_missing_row_scans_both_directions_and_stays_unknown(harness: SimpleNamespace) -> None:
    harness.state.observation = observation(row("Attack Speed", "attack_speed"))
    policy = harness.start()
    for _ in range(4):
        harness.step(policy)
    assert harness.state.scrolls == [False, True]
    assert harness.taps == []
    assert "Damage" in harness.session._exhausted
    assert not any(r["status"] == "locked"
                   for r in harness.session.observations.snapshot()["observations"])


def test_scroll_respects_tap_budget_and_rehearsal(harness: SimpleNamespace) -> None:
    harness.state.observation = observation(row("Attack Speed", "attack_speed"))
    policy = harness.start(max_taps_per_visit=1)
    harness.step(policy)
    harness.step(policy)
    assert len(harness.state.scrolls) == 1
    assert not harness.session.active
    harness.session.reset()
    harness.state.scrolls.clear()
    policy = harness.start(armed=False)
    harness.step(policy)
    assert harness.state.scrolls == []


def test_confirmed_spending_reduces_remaining_visit_budget(harness: SimpleNamespace) -> None:
    second = row("Attack Speed", "attack_speed", price=30)
    harness.state.observation = observation(row(), second)
    policy = harness.start(coin_budget=50, workshop=(ShoppingRule("Damage", "ATTACK"),
                                                   ShoppingRule("Attack Speed", "ATTACK")))
    harness.step(policy)
    harness.state.observation = observation(row(price=33), second)
    harness.step(policy)
    harness.step(policy)
    assert len(harness.taps) == 1
    assert harness.session._spent == 30


def test_category_mismatch_never_buys(harness: SimpleNamespace) -> None:
    harness.state.observation = replace(observation(row()), category="DEFENSE")
    harness.step(harness.start())
    assert harness.taps == []


@pytest.mark.parametrize("text,confidence,expected", [("9.12T", .99, 9_120_000_000_000),
                                                    ("900", .89, None)])
def test_workshop_balance_uses_high_confidence_ocr_without_an_atlas(
    monkeypatch: pytest.MonkeyPatch, text: str, confidence: float, expected: int | None,
) -> None:
    anchor = (100, 100)
    region = shopping._absolute(config.HEADER_REGIONS["WORKSHOP"][0], anchor)
    boxes = (ocr.TextBox(text, confidence, config.Rect(region.x + 2, region.y + 2, 20, 20)),)
    monkeypatch.setattr(ocr, "read", lambda _: boxes)
    assert shopping.header_numbers(None, "WORKSHOP", anchor)[0] == expected


def test_ambiguous_or_outside_balance_numbers_are_never_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = (100, 100)
    region = shopping._absolute(config.HEADER_REGIONS["WORKSHOP"][0], anchor)
    boxes = (ocr.TextBox("900", .99, config.Rect(region.x + 2, region.y + 2, 20, 20)),
             ocr.TextBox("100", .99, config.Rect(region.x + 24, region.y + 2, 20, 20)),
             ocr.TextBox("40000", .99, config.Rect(500, 500, 50, 20)))
    monkeypatch.setattr(ocr, "read", lambda _: boxes)
    assert shopping.header_numbers(None, "WORKSHOP", anchor) == (None, None)
