"""The shopping state machine, with no device and no emulator.

Frames come from the committed fixtures, so this exercises the real matcher
and the real digit reader against real pixels - only the tapping is faked.

Every dry-run test asserts the fake device recorded ZERO taps. That is the
property the whole rehearsal rests on, so it is asserted directly rather than
inferred from the events.
"""

from pathlib import Path

import cv2
import pytest

import config
import digits
import events
import shopping as shopping_mod
import vision
from strategy import CardPolicy, Shopping, ShoppingRule

FIXTURES = Path(__file__).parent / "fixtures"


class FakeDevice:
    """Records taps instead of sending them."""

    def __init__(self) -> None:
        self.taps: list[tuple[int, int]] = []


@pytest.fixture(autouse=True)
def capture_taps(monkeypatch):
    """device.tap is module-level in shopping.py, so patch it there."""
    def _tap(device, x, y):
        device.taps.append((x, y))

    monkeypatch.setattr(shopping_mod, "tap", _tap)


@pytest.fixture
def fake_header(monkeypatch):
    """Force what the session believes the balances are.

    Patched at module scope in shopping.py, not by setting session state: the
    session re-reads the header every step, so an attribute poked before
    advance() is overwritten before anything reads it. A test that sets
    _coins and then asserts on affordability is asserting nothing - see
    task-8-overrides.md, override 1.
    """
    values = {"coins": 1770, "gems": 40}

    def _header(screen, page, top_left, reader):
        return values["coins"], values["gems"]

    monkeypatch.setattr(shopping_mod, "header_numbers", _header)
    return values


class Recorder:
    """A bus that keeps what it was given."""

    def __init__(self) -> None:
        self.published: list[events.Event] = []

    def publish(self, event):
        self.published.append(event)
        return event

    def of_type(self, name: str):
        return [e for e in self.published if e.type == name]


def frame(name: str):
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    return img


def a_policy(**over) -> Shopping:
    base = dict(
        enabled=True,
        armed=False,
        workshop=(
            # layout="tile": this is an unlock tile, not an upgrade row - the
            # brief's own draft of this fixture omitted it, which reads the
            # price from PRICE_REGIONS["row"] instead of ["tile"] and fails
            # every read on this rule (see task-8-overrides.md, override 2:
            # ShoppingRule.layout is never inferred from the name, only
            # declared).
            ShoppingRule(name="Unlock Cash Bonuses",
                         template="workshop/unlock_cash_bonuses.png",
                         category="UTILITY", layout="tile"),
            ShoppingRule(name="Health", template="workshop/row_health.png",
                         category="DEFENSE"),
            ShoppingRule(name="Damage", template="workshop/row_damage.png",
                         category="ATTACK"),
        ),
    )
    return Shopping(**{**base, **over})


@pytest.fixture
def session():
    return shopping_mod.ShoppingSession(
        templates=vision.TemplateCache(config.TEMPLATE_DIR),
        bus=Recorder(),
        reader=digits.NumberReader(),
    )


# -- starting a visit ------------------------------------------------------
def test_a_disabled_policy_never_starts_a_visit(session) -> None:
    assert session.begin(a_policy(enabled=False), run_count=1) is False
    assert session.active is False


def test_an_enabled_policy_starts_a_visit(session) -> None:
    assert session.begin(a_policy(), run_count=1) is True
    assert session.active is True


def test_the_cadence_skips_runs_between_visits(session) -> None:
    policy = a_policy(visit_every_n_runs=3)
    assert session.begin(policy, run_count=1) is True
    session.reset()
    assert session.begin(policy, run_count=2) is False
    assert session.begin(policy, run_count=3) is False
    assert session.begin(policy, run_count=4) is True


def test_a_policy_with_no_enabled_rows_and_no_cards_starts_nothing(session) -> None:
    """A visit that would buy nothing is a minute of tab-tapping for free."""
    policy = a_policy(workshop=(), cards=CardPolicy(enabled=False))
    assert session.begin(policy, run_count=1) is False


# -- reading the header ----------------------------------------------------
def test_the_header_reads_coins_and_gems_off_a_workshop_frame() -> None:
    reader = digits.NumberReader()
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    screen = frame("menu_workshop_attack")
    _, top_left = vision.best_score(screen, cache.get(config.PAGE_ANCHORS["WORKSHOP"]))
    coins, gems = shopping_mod.header_numbers(screen, "WORKSHOP", top_left, reader)
    assert coins == 1770
    assert gems == 40


# -- the dry run taps nothing ---------------------------------------------
def test_a_whole_unarmed_visit_taps_nothing(session) -> None:
    """The property the rehearsal exists for, asserted directly."""
    device = FakeDevice()
    policy = a_policy(armed=False)
    session.begin(policy, run_count=1)
    for name in ("menu_main", "menu_workshop_utility", "menu_workshop_defense",
                 "menu_workshop_attack", "menu_cards", "menu_main"):
        for _ in range(4):
            session.advance(frame(name), device, policy)
    assert device.taps == []


def test_an_unarmed_visit_still_reports_what_it_would_buy(session) -> None:
    device = FakeDevice()
    policy = a_policy(armed=False)
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_utility"), device, policy)
    session.advance(frame("menu_workshop_utility"), device, policy)
    purchases = session._bus.of_type("Purchased")
    assert purchases, "a rehearsal that reports nothing proves nothing"
    assert all(p.dry_run for p in purchases)
    assert purchases[0].item == "Unlock Cash Bonuses"
    assert purchases[0].price == 40


# -- the buying rule -------------------------------------------------------
def test_the_highest_priority_affordable_row_wins_not_the_cheapest(session) -> None:
    """Order is the whole policy. Critical Chance costs 50 and Damage 30;
    with Critical Chance listed first it must be the one chosen."""
    device = FakeDevice()
    policy = a_policy(workshop=(
        ShoppingRule(name="Critical Chance",
                     template="workshop/row_critical_chance.png", category="ATTACK"),
        ShoppingRule(name="Damage", template="workshop/row_damage.png",
                     category="ATTACK"),
    ))
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_attack"), device, policy)
    session.advance(frame("menu_workshop_attack"), device, policy)
    bought = session._bus.of_type("Purchased")
    assert bought[0].item == "Critical Chance"


def test_a_row_costing_more_than_the_balance_is_skipped_as_unaffordable(
    session, fake_header
) -> None:
    """Rewritten per task-8-overrides.md, override 1: the brief's own version
    poked session._coins, which BUY_ROWS's re-read-every-step rule overwrites
    before anything sees it. fake_header is what actually makes the session
    believe the balance is 10.

    A single ATTACK-only policy (rather than the brief's three-category
    default) is used so the two advance() calls land the session in
    BUY_ROWS on the very frame this test controls, instead of stalling on a
    tab-switch toward a different, unvisited category first.
    """
    device = FakeDevice()
    policy = a_policy(workshop=(
        ShoppingRule(name="Damage", template="workshop/row_damage.png",
                     category="ATTACK"),
    ))
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    fake_header["coins"] = 10
    session.advance(frame("menu_workshop_attack"), device, policy)
    skips = session._bus.of_type("PurchaseSkipped")
    assert any(s.reason == "unaffordable" for s in skips)


def test_an_unreadable_balance_stops_the_visit_rather_than_guessing(
    session, fake_header
) -> None:
    """Rewritten per task-8-overrides.md, override 1 (see the comment on
    test_a_row_costing_more_than_the_balance_is_skipped_as_unaffordable for
    why fake_header replaces the brief's session._coins poke, and why this
    uses a single-category policy).

    There is no brightness fallback on a menu page, and guessing is the
    failure mode that costs coins.
    """
    device = FakeDevice()
    policy = a_policy(workshop=(
        ShoppingRule(name="Damage", template="workshop/row_damage.png",
                     category="ATTACK"),
    ))
    fake_header["coins"] = None
    session.begin(policy, run_count=1)
    session.advance(frame("menu_workshop_attack"), device, policy)
    ended = session._bus.of_type("ShoppingEnded")
    skipped = session._bus.of_type("PurchaseSkipped")
    assert ended or any(s.reason == "unreadable" for s in skipped)
    assert device.taps == []


# -- category order --------------------------------------------------------
def test_tabs_are_visited_in_the_order_the_rows_imply(session) -> None:
    policy = a_policy()
    session.begin(policy, run_count=1)
    assert session.remaining_categories() == ["UTILITY", "DEFENSE", "ATTACK"]


# -- bailing out -----------------------------------------------------------
def test_the_tap_budget_ends_the_visit(session) -> None:
    device = FakeDevice()
    policy = a_policy(armed=True, max_taps_per_visit=2)
    session.begin(policy, run_count=1)
    for _ in range(20):
        session.advance(frame("menu_workshop_attack"), device, policy)
    assert len(device.taps) <= 2
    ended = session._bus.of_type("ShoppingEnded")
    assert ended and ended[-1].aborted


def test_an_unexpected_page_twice_running_ends_the_visit(session) -> None:
    """Once is an animation. Twice is lost."""
    device = FakeDevice()
    policy = a_policy()
    session.begin(policy, run_count=1)
    session.advance(frame("in_run_lit"), device, policy)
    assert session.active is True, "one odd frame is not enough to give up"
    session.advance(frame("in_run_lit"), device, policy)
    ended = session._bus.of_type("ShoppingEnded")
    assert ended and ended[-1].aborted
    assert session.active is False


def test_a_finished_visit_returns_to_idle(session) -> None:
    device = FakeDevice()
    policy = a_policy(cards=CardPolicy(enabled=False))
    session.begin(policy, run_count=1)
    for name in ("menu_main", "menu_workshop_utility", "menu_workshop_defense",
                 "menu_workshop_attack", "menu_main"):
        for _ in range(6):
            session.advance(frame(name), device, policy)
    assert session.active is False


# -- cards -----------------------------------------------------------------
def test_cards_are_not_bought_when_the_policy_is_off(session) -> None:
    device = FakeDevice()
    policy = a_policy(armed=True, cards=CardPolicy(enabled=False))
    session.begin(policy, run_count=1)
    session._step = shopping_mod.Step.BUY_CARDS
    session.advance(frame("menu_cards"), device, policy)
    assert device.taps == []


def test_the_gem_floor_stops_card_buying(session, fake_header) -> None:
    """40 gems, a 20-gem card and a floor of 40: buying would breach it.

    Rewritten per task-8-overrides.md, override 1: fake_header replaces the
    brief's session._gems poke, for the same reason as the workshop tests
    above.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, cards=CardPolicy(enabled=True, gem_floor=40))
    session.begin(policy, run_count=1)
    session._step = shopping_mod.Step.BUY_CARDS
    fake_header["gems"] = 40
    session.advance(frame("menu_cards"), device, policy)
    assert device.taps == []
    skips = session._bus.of_type("PurchaseSkipped")
    assert any(s.reason == "capped" for s in skips)


def test_card_buying_stops_at_the_per_visit_cap(session, fake_header) -> None:
    """Rewritten per task-8-overrides.md, override 1: fake_header replaces
    the brief's session._gems poke."""
    device = FakeDevice()
    policy = a_policy(armed=True, cards=CardPolicy(
        enabled=True, gem_floor=0, max_per_visit=1
    ))
    session.begin(policy, run_count=1)
    session._step = shopping_mod.Step.BUY_CARDS
    fake_header["gems"] = 400
    for _ in range(5):
        session.advance(frame("menu_cards"), device, policy)
    bought = [e for e in session._bus.of_type("Purchased") if e.category == "CARDS"]
    assert len(bought) == 1


def test_the_bot_never_taps_unlock_new_slot(session) -> None:
    """Slots are out of scope by design - the community gem order puts lab
    slots above them and the bot cannot see labs. Guarded by the fact that no
    slot template exists at all, which this pins."""
    assert not any("slot" in path.lower() for path in config.CARD_BUTTONS.values())
