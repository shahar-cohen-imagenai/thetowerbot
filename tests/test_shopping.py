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
import ocr
import tiles
import events
import shopping as shopping_mod
import vision
from strategy import CardPolicy, Shopping, ShoppingRule

FIXTURES = Path(__file__).parent / "fixtures"


class _NoRowTemplates(vision.TemplateCache):
    """A template cache with the workshop ROW templates removed.

    Deleted-file behaviour without deleting a file: anything under
    workshop/row_ or workshop/unlock_ raises, everything else (nav buttons,
    tab pictograms, page anchors) loads normally.
    """

    def get(self, name: str):
        if name.startswith("workshop/row_") or name.startswith("workshop/unlock_"):
            raise AssertionError(f"the buy path still reads a row template: {name}")
        return super().get(name)


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
    _coins and then asserts on affordability is asserting nothing.
    """
    values = {"coins": 1770, "gems": 40}

    def _header(screen, page, top_left):
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
            ShoppingRule(name="Unlock Cash Bonuses", category="UTILITY"),
            ShoppingRule(name="Health", category="DEFENSE"),
            ShoppingRule(name="Damage", category="ATTACK"),
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


def test_begin_opens_cards_directly_when_only_cards_are_enabled(session) -> None:
    """The untested `else` of begin()'s step assignment: no workshop rows to
    visit, so the visit should go straight to the Cards page rather than
    stopping at Workshop for nothing."""
    policy = a_policy(workshop=(), cards=CardPolicy(enabled=True))
    assert session.begin(policy, run_count=1) is True
    assert session._step is shopping_mod.Step.OPEN_CARDS


def test_a_permanently_disabled_session_announces_it_only_once(session) -> None:
    """begin() must say why nothing is happening the first time it declines
    for disabled_reason, and stay silent every time after - the reason never
    changes once the process has started, so a publish per scan would just
    flood the feed with the same fact forever."""
    session.disabled_reason = "header atlas is missing 2, 3, 5, 6, 9"
    for _ in range(5):
        assert session.begin(a_policy(), run_count=1) is False
    unavailable = session._bus.of_type("ShoppingUnavailable")
    assert len(unavailable) == 1
    assert unavailable[0].reason == session.disabled_reason


# -- turning shopping off mid-visit -----------------------------------------
def test_disabling_shopping_mid_visit_ends_it_instead_of_continuing(session) -> None:
    """The critical bug this closes: shopping.enabled was checked only in
    begin(), never in advance(), so unticking "Shop between runs" while a
    visit was under way left the bot tapping through it to completion. The
    fix routes a disabled policy through the same abort path any other wedge
    takes - a best-effort return tap and an honest ShoppingEnded - rather
    than a silent `_step = IDLE`.
    """
    device = FakeDevice()
    policy = a_policy(enabled=True, armed=False)
    session.begin(policy, run_count=1)
    assert session.active is True

    turned_off = a_policy(enabled=False, armed=False)
    session.advance(frame("menu_workshop_utility"), device, turned_off)

    ended = session._bus.of_type("ShoppingEnded")
    assert ended and ended[-1].aborted
    assert ended[-1].reason == "shopping disabled"
    assert session.active is False
    assert device.taps == [], "unarmed - even the recovery tap must not reach the device"


# -- reading the header ----------------------------------------------------
def test_the_header_reads_coins_and_gems_off_a_workshop_frame() -> None:
    """Real engine, real fixture. The two balances sit on one header row, so
    this is also what proves each region keeps to its own number."""
    cache = vision.TemplateCache(config.TEMPLATE_DIR)
    screen = frame("menu_workshop_attack")
    _, top_left = vision.best_score(screen, cache.get(config.PAGE_ANCHORS["WORKSHOP"]))
    coins, gems = shopping_mod.header_numbers(screen, "WORKSHOP", top_left)
    assert coins == 1770
    assert gems == 40


def test_the_header_reads_a_balance_the_glyph_atlas_could_not(monkeypatch) -> None:
    """2, 3 and 5 are glyphs templates/atlas/header/ has never held, so a
    balance containing them read as None through the atlas and aborted the
    visit. Reading the header with OCR is what retires that failure - and
    with it the harvesting session build_shopping() used to demand.
    """
    def _read(screen):
        return (ocr.TextBox(text="2.35K", confidence=0.99, rect=config.Rect(90, 164, 130, 46)),)

    monkeypatch.setattr(shopping_mod.ocr, "read", _read)
    coins, gems = shopping_mod.header_numbers(None, "WORKSHOP", (32, 244))
    assert coins == 2350
    assert gems is None, "nothing was read in the gem region"


def test_the_header_reads_nothing_off_a_page_that_has_no_header() -> None:
    """MISSIONS, or a frame that failed to classify. Returning a pair of
    Nones rather than raising is what lets the caller treat "no header here"
    and "unreadable header" as the same refusal."""
    assert shopping_mod.header_numbers(None, "MISSIONS", (32, 244)) == (None, None)


# -- page transitions -------------------------------------------------------
def test_a_page_transition_publishes_page_changed(session) -> None:
    """events.PageChanged is defined and store-tested but was never actually
    published anywhere - this is that wiring, on the transition a real
    visit makes crossing from the main menu onto the Workshop page."""
    device = FakeDevice()
    policy = a_policy()
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)  # taps into WORKSHOP
    session.advance(frame("menu_workshop_utility"), device, policy)

    changed = session._bus.of_type("PageChanged")
    assert len(changed) == 1
    assert changed[0].prev_page == "MAIN_MENU"
    assert changed[0].curr_page == "WORKSHOP"


def test_the_first_frame_of_a_visit_publishes_no_transition(session) -> None:
    """There is nothing to have changed FROM on the very first frame -
    ShoppingStarted already marks the beginning, so this must not also fire
    a PageChanged from some leftover state."""
    device = FakeDevice()
    policy = a_policy()
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    assert session._bus.of_type("PageChanged") == []


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
        ShoppingRule(name="Critical Chance", category="ATTACK"),
        ShoppingRule(name="Damage",
                     category="ATTACK"),
    ))
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_attack"), device, policy)
    session.advance(frame("menu_workshop_attack"), device, policy)
    bought = session._bus.of_type("Purchased")
    assert bought[0].item == "Critical Chance"


def test_a_row_is_bought_without_its_template_file(session, fake_header) -> None:
    """Nothing in the workshop buy path reads a row template any more.

    The rule no longer even has a `template` field to carry, so this swaps
    in a cache that raises if the buy path asks for one anyway: if any code
    path still loads a row template, the purchase cannot happen. Tab and nav
    templates are untouched and still load - only the ROW templates are gone.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, workshop=(
        ShoppingRule(name="Damage",
                     category="ATTACK"),
    ))
    session._templates = _NoRowTemplates(config.TEMPLATE_DIR)
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_attack"), device, policy)

    bought = session._bus.of_type("Purchased")
    assert bought and bought[0].item == "Damage"
    assert bought[0].price == 30


def test_a_row_costing_more_than_the_balance_is_skipped_as_unaffordable(
    session, fake_header
) -> None:
    """Poking session._coins directly would assert nothing: BUY_ROWS's
    re-read-every-step rule overwrites it before anything sees it.
    fake_header is what actually makes the session believe the balance is 10.

    A single ATTACK-only policy (rather than the brief's three-category
    default) is used so the two advance() calls land the session in
    BUY_ROWS on the very frame this test controls, instead of stalling on a
    tab-switch toward a different, unvisited category first.
    """
    device = FakeDevice()
    policy = a_policy(workshop=(
        ShoppingRule(name="Damage",
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
    """See the comment on
    test_a_row_costing_more_than_the_balance_is_skipped_as_unaffordable for
    why fake_header replaces a direct session._coins poke, and why this
    uses a single-category policy.

    There is no brightness fallback on a menu page, and guessing is the
    failure mode that costs coins.
    """
    device = FakeDevice()
    policy = a_policy(workshop=(
        ShoppingRule(name="Damage",
                     category="ATTACK"),
    ))
    fake_header["coins"] = None
    session.begin(policy, run_count=1)
    session.advance(frame("menu_workshop_attack"), device, policy)
    ended = session._bus.of_type("ShoppingEnded")
    skipped = session._bus.of_type("PurchaseSkipped")
    assert ended or any(s.reason == "unreadable" for s in skipped)
    assert device.taps == []


def test_a_row_whose_price_cannot_be_read_is_skipped_rather_than_guessed(
    session, monkeypatch
) -> None:
    """The row's own unreadable-PRICE branch, distinct from an unreadable
    balance: coins read fine (real header, not faked), the row is found, and
    its price is not.

    This used to be reached by declaring the wrong `layout` on a rule, which
    made the template reader crop the price from garbage pixels. Addressing
    rows by name off OCR deletes that route - the price comes from the tile
    the name was found in, so there is no offset left to get wrong (spec §7:
    the mode stops existing when tiles are detected rather than assumed).
    The branch itself still matters: a tile whose price box is missing or
    unparseable yields Row.price None, and a refused read must never become
    a guessed purchase.
    """
    device = FakeDevice()
    priceless = tiles.Row(name="Unlock Cash Bonuses", price=None,
                          tap=(540, 600), rect=tiles.Rect(30, 500, 1020, 196),
                          confidence=0.99)
    monkeypatch.setattr(shopping_mod.tiles, "read_rows", lambda screen: (priceless,))
    policy = a_policy(workshop=(
        ShoppingRule(name="Unlock Cash Bonuses",
                     category="UTILITY"),
    ))
    session.begin(policy, run_count=1)
    session.advance(frame("menu_workshop_utility"), device, policy)
    skips = session._bus.of_type("PurchaseSkipped")
    assert any(s.reason == "unreadable" and s.detail == "price" for s in skips)
    assert device.taps == []


# -- category order --------------------------------------------------------
def test_tabs_are_visited_in_the_order_the_rows_imply(session) -> None:
    policy = a_policy()
    session.begin(policy, run_count=1)
    assert session.remaining_categories() == ["UTILITY", "DEFENSE", "ATTACK"]


# -- bailing out -----------------------------------------------------------
def test_the_tap_budget_ends_the_visit(session) -> None:
    """Exactly 2 (the tab-switch attempts that spend the budget) plus 1 (the
    recovery tap on the way out, which is deliberately NOT bound by the same
    cap). A loose `<= 2` bound would also pass if the code made zero taps,
    which hides a too-few-taps bug more serious than a too-many-taps one.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, max_taps_per_visit=2)
    session.begin(policy, run_count=1)
    for _ in range(20):
        session.advance(frame("menu_workshop_attack"), device, policy)
    assert len(device.taps) == 3
    ended = session._bus.of_type("ShoppingEnded")
    assert ended and ended[-1].aborted


def test_a_budget_exhausted_abort_still_attempts_the_return_tap(session) -> None:
    """Round-2 fix (Critical 1): without this, a budget-exhaustion abort -
    the most common abort there is, since the tap budget is the primary
    safety valve - left the bot stranded IDLE on a menu page forever: the
    scan loop no-ops on IDLE, and navigate.Navigator cannot rescue it either
    (it only acts on GAME_OVER and MAIN_MENU, and a menu page classifies
    UNKNOWN to the screen tracker by design).
    """
    device = FakeDevice()
    policy = a_policy(armed=True, max_taps_per_visit=1)
    session.begin(policy, run_count=1)

    session.advance(frame("menu_workshop_attack"), device, policy)
    assert len(device.taps) == 1, "the single tap the budget allows"

    session.advance(frame("menu_workshop_attack"), device, policy)
    assert len(device.taps) == 2, "the recovery tap, one over the cap of 1"
    ended = session._bus.of_type("ShoppingEnded")
    assert ended and ended[-1].aborted
    assert session.active is False


def test_a_double_failure_during_abort_recovery_does_not_crash_the_scan_loop(
    session, monkeypatch
) -> None:
    """Round-2 fix (Critical 2): advance()'s except handler calls _abort,
    which re-runs vision.locate_template against the SAME screen that may
    have just caused the original exception (see _exit_to_battle). If that
    lookup also raises, the second exception used to propagate straight out
    of advance() - breaking the module docstring's own promise that a
    shopping step never crashes the scan loop.
    """
    device = FakeDevice()
    policy = a_policy()
    session.begin(policy, run_count=1)

    def _raise_classify(screen, cache, threshold=None):
        raise RuntimeError("boom: classify")

    def _raise_locate(screen, template, threshold):
        raise RuntimeError("boom: locate")

    monkeypatch.setattr(shopping_mod.pages, "classify_page", _raise_classify)
    monkeypatch.setattr(shopping_mod.vision, "locate_template", _raise_locate)

    session.advance(frame("menu_main"), device, policy)  # must not raise

    assert session.active is False
    assert device.taps == []


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


def test_a_missing_nav_target_ends_the_visit_after_two_misses(session) -> None:
    """A positioning step that cannot find its target must not spin
    silently forever with the tap budget untouched and the event feed
    empty - it is a leg of an errand supposed to be making progress, not an
    opportunistic Navigator tap.

    menu_missions has no bottom tab bar at all, so NAV_TARGETS["BATTLE_TAB"]
    never matches there (measured: 0.31, nowhere near the 0.8 threshold) -
    exactly the "wrong crop, or the game moved the button" scenario this
    guards against.
    """
    device = FakeDevice()
    policy = a_policy()
    session.begin(policy, run_count=1)
    session._step = shopping_mod.Step.RETURN

    session.advance(frame("menu_missions"), device, policy)
    assert session.active is True, "one miss is not enough to give up"
    assert device.taps == []

    session.advance(frame("menu_missions"), device, policy)
    ended = session._bus.of_type("ShoppingEnded")
    assert ended and ended[-1].aborted
    assert "BATTLE_TAB" in ended[-1].reason
    assert session.active is False
    assert device.taps == []


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


def test_a_dismiss_popup_is_tapped_before_reading_the_cards_page(
    session, monkeypatch
) -> None:
    """OPEN_CARDS's popup-dismiss branch, over config.NAV_DISMISS.

    No committed fixture happens to show a first-visit popup mid-flight
    (measured directly: the highest any NAV_DISMISS template scores against
    any committed fixture is 0.624, nowhere near the 0.8 threshold), so the
    popup MATCH is faked here - the same way fake_header fakes a balance no
    fixture happens to show - while the real state machine and the real tap
    path are exercised on a real frame.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, cards=CardPolicy(enabled=True))
    session.begin(policy, run_count=1)
    session._step = shopping_mod.Step.OPEN_CARDS

    real_locate = shopping_mod.vision.locate_template
    dismiss_template = session._templates.get(config.NAV_DISMISS[0])

    def _locate(screen, template, threshold):
        if template is dismiss_template:
            return vision.Match(center=(500, 600), score=1.0, top_left=(400, 550))
        return real_locate(screen, template, threshold)

    monkeypatch.setattr(shopping_mod.vision, "locate_template", _locate)

    session.advance(frame("menu_cards"), device, policy)
    assert device.taps == [(500, 600)]
    assert session._step is shopping_mod.Step.OPEN_CARDS, (
        "a dismiss tap is not arrival - the next scan re-checks the page"
    )


def test_a_missing_card_button_is_skipped_as_no_match(session) -> None:
    """Cards' no_match branch: the configured batch's button template is
    simply not on screen (a real frame with no card buttons at all, rather
    than a faked miss)."""
    device = FakeDevice()
    policy = a_policy(armed=True, cards=CardPolicy(enabled=True))
    session.begin(policy, run_count=1)
    session._step = shopping_mod.Step.BUY_CARDS
    session.advance(frame("menu_workshop_attack"), device, policy)
    skips = session._bus.of_type("PurchaseSkipped")
    assert any(s.reason == "no_match" for s in skips)
    assert device.taps == []


def test_a_card_batch_costing_more_than_the_balance_is_skipped_as_unaffordable(
    session, fake_header
) -> None:
    """Cards' plain unaffordable branch, distinct from the gem-floor
    ("capped") case: the batch costs more than the whole balance, not just
    more than the balance minus the floor."""
    device = FakeDevice()
    policy = a_policy(armed=True, cards=CardPolicy(enabled=True, gem_floor=0))
    session.begin(policy, run_count=1)
    session._step = shopping_mod.Step.BUY_CARDS
    fake_header["gems"] = 10  # x1 costs 20
    session.advance(frame("menu_cards"), device, policy)
    skips = session._bus.of_type("PurchaseSkipped")
    assert any(s.reason == "unaffordable" for s in skips)
    assert device.taps == []


def test_unreadable_gems_stop_the_visit_rather_than_guessing(session, fake_header) -> None:
    """Cards' own unreadable-balance branch - the gem analogue of
    test_an_unreadable_balance_stops_the_visit_rather_than_guessing.

    Unarmed (as that test is): an abort still attempts the recovery tap
    (round-2 fix, Critical 1), and with armed=True that tap would actually
    reach the fake device - this test is about the abort firing, not about
    the recovery tap, so it stays unarmed like its coin counterpart.
    """
    device = FakeDevice()
    policy = a_policy(cards=CardPolicy(enabled=True))
    session.begin(policy, run_count=1)
    session._step = shopping_mod.Step.BUY_CARDS
    fake_header["gems"] = None
    session.advance(frame("menu_cards"), device, policy)
    ended = session._bus.of_type("ShoppingEnded")
    assert ended and ended[-1].aborted
    assert device.taps == []


def test_the_gem_floor_stops_card_buying(session, fake_header) -> None:
    """40 gems, a 20-gem card and a floor of 40: buying would breach it.

    fake_header replaces a direct session._gems poke, for the same reason
    as the workshop tests above.
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
    """fake_header replaces a direct session._gems poke."""
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


# -- where a purchase actually taps -----------------------------------------
def test_a_row_purchase_taps_the_price_panel_not_the_label(
    session, fake_header
) -> None:
    """The label is not a button. Verified on a live device: the bot ran a
    clean armed visit, published Purchased, and bought nothing - coins,
    stat value and price all unchanged - because match.center lands on the
    words "Attack Speed". A tap on the price strip bought it (coins
    1770 -> 1740, price 30 -> 56) at the point PRICE_REGIONS already
    locates from the same anchor.

    This is the trap config.buy_point() exists to avoid for in-run
    upgrades; a workshop tile turns out to share it.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, workshop=(
        ShoppingRule(name="Damage",
                     category="ATTACK"),
    ))
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_attack"), device, policy)

    assert session._bus.of_type("Purchased"), "the row was never bought"
    # Measured off menu_workshop_attack.png: the Damage label matches at
    # (30, 478), so PRICE_REGIONS["row"] centres at (402, 635). The label's
    # own centre - what this used to tap - is (130, 558).
    # taps[0] is the nav tap that opened the Workshop; the purchase is last.
    # (434, 633) is the centre of the price box OCR read on this frame - the
    # tap is derived from the read, so whatever a row charges is what gets
    # tapped. The label's own centre, which this used to tap, is (130, 558).
    assert device.taps[-1] == (434, 633)
    assert (130, 558) not in device.taps, "tapped the label, which buys nothing"


def test_a_price_the_glyph_atlas_cannot_read_is_bought_at_the_ocr_price(
    session, fake_header
) -> None:
    """The case the whole cut-over rests on, in real pixels.

    menu_workshop_attack_escalated.png is a live capture taken after a
    purchase pushed Attack Speed from 30 to 56. The `menu` atlas has never
    held a 6, so the template reader returns None on that price and the row
    was refused as "unreadable" - a row the bot could see, afford and never
    buy. OCR reads 56 off the same pixels.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, workshop=(
        ShoppingRule(name="Attack Speed",
                     category="ATTACK"),
    ))
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_attack_escalated"), device, policy)

    bought = session._bus.of_type("Purchased")
    assert bought, "the row was refused - see PurchaseSkipped"
    assert bought[0].item == "Attack Speed"
    assert bought[0].price == 56
    # The price strip inside the buy panel, read off this very frame.
    assert device.taps[-1] == (952, 634)


# -- a screen the reader cannot see -----------------------------------------
def _two_attack_rows():
    return (
        ShoppingRule(name="Damage",
                     category="ATTACK"),
        ShoppingRule(name="Attack Speed",
                     category="ATTACK"),
    )


def test_a_blinded_screen_does_not_write_the_row_off(session, fake_header) -> None:
    """"I cannot see" is not "it is not here".

    A live armed visit tapped a row's label, which opened an info panel over
    the grid, and then skipped every remaining row as no_match - exhausting
    four rows that were sitting right there, unbought, for the rest of the
    visit. The row must survive an unreadable frame.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, workshop=_two_attack_rows())
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_attack"), device, policy)   # buys Damage
    session.advance(frame("menu_workshop_info_panel"), device, policy)

    assert "Attack Speed" not in session._exhausted, (
        "a row nobody could see was written off for the visit"
    )
    skips = session._bus.of_type("PurchaseSkipped")
    assert any(s.reason == "unreadable" and s.detail == "screen" for s in skips)
    assert not any(s.reason == "no_match" for s in skips), (
        "an unreadable screen was reported as the row being absent"
    )


def test_a_blinded_screen_is_tapped_clear(session, fake_header) -> None:
    """The panel closes on a tap anywhere outside it. The dismiss point sits
    on the page title, ABOVE the tile grid - measured there rather than in
    the empty space below it, because that space fills up as rows unlock and
    a tap that lands on a price box would buy something nobody asked for.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, workshop=_two_attack_rows())
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_attack"), device, policy)
    session.advance(frame("menu_workshop_info_panel"), device, policy)

    assert device.taps[-1] == config.PANEL_DISMISS_POINT


def test_a_screen_that_stays_blind_ends_the_visit(session, fake_header) -> None:
    """The dismiss tap is one attempt, not a loop. If the page is still
    unreadable on the next scan it is something this code does not
    understand, and spinning on it silently is the failure mode
    _off_page_streak exists to prevent everywhere else.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, workshop=_two_attack_rows())
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_attack"), device, policy)
    session.advance(frame("menu_workshop_info_panel"), device, policy)
    session.advance(frame("menu_workshop_info_panel"), device, policy)

    assert session._bus.of_type("ShoppingEnded"), "the visit spun instead of ending"
    assert session.active is False


def test_an_explainer_modal_is_acknowledged_rather_than_tapped_around(
    session, fake_header
) -> None:
    """One-time explainer modals swallow every tap until their OK is pressed.

    menu_workshop_explainer_modal.png is a live capture: unlocking a feature
    queued an "ULTIMATE WEAPONS ... [OK]" dialog that was already up when the
    Workshop opened. The page classifies as WORKSHOP, so positioning carried
    on tapping a tab that could never arrive, and the visit spent its whole
    budget without reading a single row. Nothing here reaches _buy_rows, so
    the blind handling there cannot help - and a tap anywhere outside this
    one does not close it, unlike the info panel.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, workshop=_two_attack_rows())
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_explainer_modal"), device, policy)

    # The OK button, read off that very frame.
    assert device.taps[-1] == (541, 1578)


def test_arrival_is_judged_by_the_page_heading_not_a_row_template(
    session, fake_header
) -> None:
    """A tab whose configured row has been bought must still be recognised.

    Arrival used to be "can I locate one of this category's row templates?".
    That answers "no" forever once the row is bought and gone from the page,
    so the session taps the tab until its budget dies - a live visit spent a
    whole visit doing exactly that on UTILITY after an earlier run bought
    Unlock Cash Bonuses.

    menu_workshop_utility_restocked.png is that page: the configured row's
    template no longer matches anything, and three other rows are sitting
    there. The heading says which tab this is, and OCR can read it.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, workshop=(
        ShoppingRule(name="Unlock Cash Bonuses",
                     category="UTILITY"),
    ))
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_utility_restocked"), device, policy)

    skips = session._bus.of_type("PurchaseSkipped")
    assert any(s.reason == "no_match" for s in skips), (
        "never reached the rows - still waiting to arrive on a tab it is on"
    )
    assert "Unlock Cash Bonuses" in session._exhausted, (
        "a row genuinely gone from the page must be given up on, once"
    )


def test_a_row_ocr_could_not_match_is_published_with_what_was_read(
    session, fake_header
) -> None:
    """A garbled name must be loud, not merely unbought.

    Live evidence that this is real and not defensive: OCR read
    'Damage / Meter C' off the ATTACK tab, the coin glyph having joined the
    row name. A row spelled that way in a strategy would never match, and
    without this event the feed would show nothing at all - the row would
    simply never be bought, forever, for no visible reason.

    menu_workshop_utility_restocked.png is the honest version of the same
    shape: the configured row was bought in an earlier session and is gone,
    and three other rows are on the page.
    """
    device = FakeDevice()
    policy = a_policy(armed=True, workshop=(
        ShoppingRule(name="Unlock Cash Bonuses",
                     category="UTILITY"),
    ))
    session.begin(policy, run_count=1)
    session.advance(frame("menu_main"), device, policy)
    session.advance(frame("menu_workshop_utility_restocked"), device, policy)

    unmatched = session._bus.of_type("RowUnmatched")
    assert unmatched, "nothing said why the row was never bought"
    assert unmatched[0].item == "Unlock Cash Bonuses"
    assert unmatched[0].read == (
        "Cash Bonus", "Cash / Wave", "Unlock Coin Bonuses",
    )
