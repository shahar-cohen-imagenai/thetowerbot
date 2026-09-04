"""Read Workshop upgrades and walk the game's between-run shopping pages.

Armed Workshop purchases require a live OCR price and balance, a positive
per-visit coin budget, the configured reserve and permission for unlock tiles.
A tap creates a pending purchase: another frame must show the changed value,
price or unlock transition before a Purchased event reports success. An
inconclusive acknowledgement ends the visit without another purchase attempt.

Rows are addressed by semantic name and category. Missing rows trigger bounded
scrolling, never an inference that the upgrade is locked. Every navigation,
buy or swipe uses one frame and counts against the visit's operation budget;
aborting allows one recovery tap back to Battle. Unarmed rehearsals perform
no device actions and identify their hypothetical purchases with dry_run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

import config
import events
import jitter
import ocr
import pages
import tiles
import upgrades
import vision
from device import Image, tap
from digits import NumberReader
from perception import Observation, ObservedUpgrade, contains, observe_frame, price_number
from strategy import Shopping, Strategy

if TYPE_CHECKING:
    from autopilot import AutopilotState

logger = logging.getLogger("tower_bot.shopping")

# Labels that mean "I have read this" and nothing else. Deliberately not
# CONFIRM, YES, BUY or CLAIM: those answer a question, and a bot that cannot
# read the question must not answer it.
_MODAL_ACKNOWLEDGEMENTS: frozenset[str] = frozenset({"OK"})


class Step(Enum):
    IDLE = auto()
    OPEN_WORKSHOP = auto()
    OPEN_TAB = auto()
    BUY_ROWS = auto()
    OPEN_CARDS = auto()
    BUY_CARDS = auto()
    RETURN = auto()


def header_numbers(
    screen: Image, page: str, top_left: tuple[int, int] | None
) -> tuple[int | None, int | None]:
    """Coins and gems off the menu header, or (None, None) off a page that
    has no header regions (MISSIONS, or a page that failed to classify).

    Read with OCR rather than the glyph atlas. The atlas could only ever
    read a balance built from glyphs a past play session happened to push
    through it - templates/atlas/header/ has never held 2, 3, 5 or 9 - and
    an unreadable balance aborts the visit, so the bot could not shop until
    somebody harvested them by hand. OCR needs no such session, which is
    why build_shopping() now gates on the engine instead of on the atlas.

    One whole-frame read serves both regions: the two balances share a
    header row, and reading twice would pay for the same pixels twice.
    """
    regions = config.HEADER_REGIONS.get(page)
    if regions is None or top_left is None:
        return None, None
    coins_region, gems_region = regions
    boxes = ocr.read(screen)
    return (
        _balance_in(boxes, _absolute(coins_region, top_left)),
        _balance_in(boxes, _absolute(gems_region, top_left)),
    )


def _balance_in(boxes: tuple[ocr.TextBox, ...], region: config.Rect) -> int | None:
    """A single high-confidence balance in its known header region."""
    values = [value for box in boxes
              if box.confidence >= .9 and contains(region, box.rect)
              and (value := price_number(box.text)) is not None]
    return values[0] if len(values) == 1 else None


def _absolute(region: config.Region, top_left: tuple[int, int]) -> config.Rect:
    """A Region is anchor-relative by design (see config.Region); ocr works
    in frame coordinates. This is the one place the two meet."""
    return config.Rect(top_left[0] + region.dx, top_left[1] + region.dy, region.w, region.h)


def _heading_names(screen: Image, category: str) -> bool:
    """Does this page's heading say it is `category`'s tab?

    Compared with everything but the letters stripped out: OCR renders the
    heading as "ATTACKUPGRADES" on some frames and "ATTACK UPGRADES" on
    others, and neither spelling should decide whether the bot can shop.
    Matching the whole heading rather than searching for the category name
    keeps a row called "Unlock Range Upgrades" from reading as one.
    """
    wanted = _letters(f"{category}UPGRADES")
    return any(_letters(box.text) == wanted for box in ocr.read(screen))


def _letters(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalpha())


def _row_named(name: str, rows: tuple[ObservedUpgrade, ...],
               category: str | None = None) -> ObservedUpgrade | None:
    """The OCR row addressed by `name`, or None if it is not on screen.

    Matched on the normalised name, so spacing and case in a strategy file
    do not have to reproduce what the font renders.
    """
    wanted = tiles.normalise(name)
    entry = upgrades.resolve(name, category)
    for row in rows:
        if tiles.normalise(row.name) == wanted or (entry and row.upgrade_id == entry.id):
            return row
    return None


def _target_reached(upgrade_id: str, value: float, target: float) -> bool:
    if upgrades.by_id(upgrade_id) is not None:
        return upgrades.target_reached(upgrade_id, value, target)
    return value >= target


@dataclass
class PendingPurchase:
    row: ObservedUpgrade
    coins: int
    visible_ids: frozenset[str]
    frames: int = 0


@dataclass
class RowSearch:
    name: str
    down: bool = False
    scrolls: int = 0
    fingerprint: tuple[str, ...] | None = None


class ShoppingSession:
    """Walks the Workshop and Cards pages, buying what the policy allows.

    Every method that decides anything is handed the live policy explicitly
    - the same way the rest of the scan loop always reads Shopping fresh
    rather than trusting a copy made when the session was built. There is
    deliberately no `self.policy` here to go stale.
    """

    def __init__(
        self,
        templates: vision.TemplateCache,
        bus: Any,
        reader: NumberReader,
        threshold: float = 0.8,
        disabled_reason: str | None = None,
    ) -> None:
        self._templates = templates
        self._bus = bus
        self._reader = reader
        self._threshold = threshold
        # Set by each advance() from the caller's snapshot. None until then,
        # and None means "no jitter" - _exit_to_battle can tap via _abort on
        # a path that never reached advance().
        self._tuning: Strategy | None = None
        # Set once by build_shopping when the OCR engine cannot be loaded.
        # An unavailable reader must never start a visit that could spend.
        self.disabled_reason = disabled_reason
        # begin() publishes ShoppingUnavailable the first time it declines
        # for disabled_reason, and never again - a disabled session declines
        # every scan forever, and a per-scan publish would flood the feed
        # with the same fact. See begin().
        self._announced_disabled = False

        self._step: Step = Step.IDLE
        self._visit = 0
        self._categories: list[str] = []
        self._taps = 0
        self._bought = 0
        self._spent = 0
        self._cards_bought = 0
        # Every named rule is evaluated at most once per visit. A tap marks
        # it attempted immediately; acknowledgement decides whether it was
        # actually bought without issuing a duplicate purchase.
        self._exhausted: set[str] = set()
        self._last_run_count: int | None = None
        # The last page seen this visit, or None before the first frame.
        # Compared every advance() against the fresh classification so a
        # real transition (MAIN_MENU -> WORKSHOP -> CARDS -> MAIN_MENU)
        # publishes PageChanged - see advance(). Reset in begin() so a new
        # visit's first frame never diffs against the previous visit's last
        # page.
        self._last_page: str | None = None
        # Consecutive scans with no progress: either the page did not
        # classify at all, or a positioning step's target template could
        # not be found. Reset to 0 the instant anything makes progress; see
        # _register_progress and _miss.
        self._off_page_streak = 0
        # Consecutive scans on which the reader saw nothing at all - see
        # _buy_rows. Separate from _off_page_streak because _dispatch resets
        # that one optimistically before every BUY_ROWS call.
        self._blind_streak = 0
        self.observations: AutopilotState | None = None
        self._pending: PendingPurchase | None = None
        self._search: RowSearch | None = None
        self._coin_spent = 0
        # Permanent unlock evidence outlives an individual shopping visit.
        self._completed_unlocks: set[str] = set()
        self._missing_rows: set[str] = set()

    @property
    def active(self) -> bool:
        return self._step is not Step.IDLE

    def remaining_categories(self) -> list[str]:
        return list(self._categories)

    # -- starting a visit ---------------------------------------------------

    def begin(self, shopping: Shopping, run_count: int) -> bool:
        """Start a visit, or decline with a reason.

        Declines when shopping is off, when nothing is enabled to buy, and
        when the cadence says this run is not a visiting run. Returns a bool
        rather than raising because "not this run" is the normal case, not
        an error.

        Also declines when `disabled_reason` is set because the OCR engine
        could not be loaded. The first such decline publishes
        ShoppingUnavailable so the dashboard says why nothing is happening,
        rather than a feature that looks armed and does nothing forever -
        every decline after that stays silent, since the reason never
        changes once the process has started.
        """
        if self.disabled_reason:
            # bus is None in the one caller that builds a session purely to
            # inspect disabled_reason without wiring the rest of the bot
            # (see tests/test_shopping_loop.py) - nothing else here ever
            # runs for a permanently-disabled session, so that stays a
            # legal, bus-less way to ask "why is this disabled".
            if not self._announced_disabled and self._bus is not None:
                self._announced_disabled = True
                self._bus.publish(events.ShoppingUnavailable(reason=self.disabled_reason))
            return False
        if not shopping.enabled:
            return False

        categories = list(shopping.categories_in_priority_order())
        if not categories and not shopping.cards.enabled:
            return False

        if (
            self._last_run_count is not None
            and run_count - self._last_run_count < shopping.visit_every_n_runs
        ):
            return False

        self._visit += 1
        self._categories = categories
        self._taps = 0
        self._bought = 0
        self._spent = 0
        self._coin_spent = 0
        self._pending = None
        self._search = None
        self._cards_bought = 0
        self._exhausted = set()
        self._missing_rows = set()
        self._off_page_streak = 0
        self._blind_streak = 0
        self._last_page = None
        self._last_run_count = run_count
        self._step = Step.OPEN_WORKSHOP if categories else Step.OPEN_CARDS

        self._bus.publish(events.ShoppingStarted(
            visit=self._visit, dry_run=not shopping.armed,
        ))
        return True

    def reset(self) -> None:
        """Return to idle without ending a visit through the normal path.

        Cadence memory (_last_run_count) is deliberately NOT cleared here -
        it has to survive across visits to know how long it has been.
        """
        self._step = Step.IDLE
        self._categories = []
        self._pending = None
        self._search = None

    # -- one step ------------------------------------------------------------

    def advance(
        self,
        screen: Image,
        device: Any,
        shopping: Shopping,
        tuning: Strategy | None = None,
    ) -> None:
        """One step. At most one tap, and only when shopping.armed.

        `tuning` carries the live jitter policy for this pass. Stashed on
        the session rather than threaded through the eight `_try_tap` call
        sites between here and `_tap`: those all sit on the step-machine's
        own paths, and giving each an extra parameter to forward would be
        eight chances to forget one. `None` leaves taps un-jittered, which
        is how every direct caller behaved before jitter existed.

        Every path through here must either make progress, publish a skip,
        or end the visit. A step that silently does nothing is how a session
        wedges, which is what _off_page_streak exists to catch.
        """
        self._tuning = tuning
        if self._step is Step.IDLE:
            return

        try:
            if not shopping.enabled:
                # Shopping was switched off mid-visit - or this is a stale
                # visit that survived a restart (see BotRunner.start()'s
                # reset() call, which now also catches this case before it
                # gets here). "I turned it off" has to mean the taps stop
                # NOW, not once the visit happens to wind down on its own:
                # this is the one tap path that spends currency, and it is
                # exactly the panic gesture someone reaches for mid-visit.
                # Routed through _abort rather than a bare `self._step =
                # Step.IDLE` so it gets the same recovery tap and the same
                # honest ShoppingEnded any other abort gets, instead of
                # leaving the bot silently stranded on a menu page.
                self._abort(device, shopping, screen, "shopping disabled")
                return
            reading = pages.classify_page(screen, self._templates)
            if reading.page == pages.UNKNOWN:
                self._off_page_streak += 1
                if self._off_page_streak >= 2:
                    self._abort(device, shopping, screen, "unexpected page")
                return
            if self._acknowledge_modal(screen, device, shopping):
                return
            if reading.page != self._last_page:
                # Skip the very first frame of a visit: `_last_page` starts
                # None so there is nothing to have changed FROM, and
                # ShoppingStarted already marks the beginning on the feed.
                if self._last_page is not None:
                    self._bus.publish(events.PageChanged(
                        prev_page=self._last_page, curr_page=reading.page,
                        confidence=reading.confidence,
                    ))
                self._last_page = reading.page
            # Deliberately NOT reset here just because the page classified:
            # _register_progress (called from inside the handlers) is what
            # clears the streak, so a positioning step's own target-miss
            # (recognised page, wrong or absent button) still accumulates
            # across calls instead of being wiped before it can reach 2.
            self._dispatch(reading, screen, device, shopping)
        except Exception as exc:  # noqa: BLE001 - a shopping step must never
            # crash the scan loop; give the coins back to the user's control
            # instead by ending the visit and saying why.
            logger.exception("shopping step raised; ending the visit")
            try:
                self._abort(device, shopping, screen, f"error: {exc}")
            except Exception:  # noqa: BLE001 - the recovery path touches the
                # SAME screen that may have just caused the first exception
                # (_exit_to_battle re-runs vision.locate_template against
                # it), so it can fail too. This module's one promise is that
                # advance() never raises; a bad frame is not licensed to
                # break that promise twice. No ShoppingEnded may have been
                # published if _abort failed before reaching it, so force
                # idle directly rather than leaving the visit stuck retrying
                # the same crash forever.
                logger.exception(
                    "shopping recovery also raised; forcing idle without a return tap"
                )
                self._step = Step.IDLE
                self._categories = []

    def _acknowledge_modal(
        self, screen: Image, device: Any, shopping: Shopping
    ) -> bool:
        """Tap a one-time explainer dialog's OK, if one is up. True if tapped.

        These are queued by the game when a feature is unlocked and appear
        the next time the page is opened - a live visit met "ULTIMATE
        WEAPONS ... [OK]" the moment it reached the Workshop. A modal
        swallows every tap outside itself, so positioning kept tapping a tab
        that could never arrive and the visit spent its whole budget without
        reading a row. It is not the info panel _buy_rows handles: that one
        closes on any tap outside it, this one only on its own button.

        Checked on every step of a visit rather than only where it bit,
        because "a dialog is up" is not a property of any one step. That
        costs one OCR pass per scan; _buy_rows already pays for one, and the
        alternative is a shopping feature that stays dead until a human taps
        OK.

        Only an acknowledgement label counts. Anything offering a choice is
        left alone - this must never be the thing that answers a question
        the bot did not understand.
        """
        for box in ocr.read(screen):
            if box.text.strip().upper() in _MODAL_ACKNOWLEDGEMENTS:
                self._register_progress()
                self._try_tap(
                    box.rect.x + box.rect.w // 2,
                    box.rect.y + box.rect.h // 2,
                    device, shopping, screen,
                )
                return True
        return False

    def _register_progress(self) -> None:
        """Something real happened this call - a tap, an arrival, or a buy
        decision. Clears the no-progress streak so it only ever measures
        CONSECUTIVE stalls, not a lifetime total."""
        self._off_page_streak = 0

    def _miss(self, device: Any, shopping: Shopping, screen: Image, target: str) -> bool:
        """A positioning step could not find `target` this scan.

        Counts toward the same streak an unrecognized page does. One miss is
        an animation still settling; two in a row means the template is
        mis-cut or the game moved the button, and the visit ends rather than
        spinning silently - see the module docstring. Always returns False
        so callers can `return self._miss(...)`.
        """
        self._off_page_streak += 1
        if self._off_page_streak >= 2:
            self._abort(device, shopping, screen, f"{target} not found twice in a row")
        return False

    def _dispatch(self, reading, screen: Image, device: Any, shopping: Shopping) -> None:
        """Cascade through pure positioning transitions, stop at real work.

        A positioning handler returns True when it made a bookkeeping-only
        transition (nothing tapped, nothing published) - safe to keep
        going on the very same frame. BUY_ROWS and BUY_CARDS never cascade:
        each call does exactly one unit of buying work and stops, tapped or
        not, so that re-reading the header always reflects this call's own
        frame.
        """
        while True:
            step = self._step
            if step is Step.OPEN_WORKSHOP:
                if self._open_workshop(reading, screen, device, shopping):
                    continue
                return
            if step is Step.OPEN_TAB:
                if self._open_tab(reading, screen, device, shopping):
                    continue
                return
            if step is Step.BUY_ROWS:
                # A buy step always does something - tap, skip, or a
                # category handoff - so reaching it is progress in itself.
                # This resets the streak OPTIMISTICALLY, before _buy_rows
                # has actually run, rather than only on a confirmed tap or
                # publish. That is safe only because _buy_rows is itself
                # unconditionally self-terminating (every branch taps,
                # publishes, or hands off to the next step) - if a future
                # edit ever adds an early `return` that does none of those,
                # this reset would silently defeat the wedge protection for
                # that branch. Keep that invariant in mind before adding one.
                self._register_progress()
                self._buy_rows(reading, screen, device, shopping)
                return
            if step is Step.OPEN_CARDS:
                if self._open_cards(reading, screen, device, shopping):
                    continue
                return
            if step is Step.BUY_CARDS:
                # See the BUY_ROWS branch above - same optimistic-reset /
                # self-terminating coupling applies to _buy_cards.
                self._register_progress()
                self._buy_cards(reading, screen, device, shopping)
                return
            if step is Step.RETURN:
                if self._return(reading, screen, device, shopping):
                    continue
                return
            return  # IDLE, or reached mid-loop by an end_visit call

    # -- positioning ----------------------------------------------------------

    def _next_after_categories(self, shopping: Shopping) -> Step:
        return Step.OPEN_CARDS if shopping.cards.enabled else Step.RETURN

    def _open_workshop(self, reading, screen: Image, device: Any, shopping: Shopping) -> bool:
        # Defensive only: begin() sets _step to OPEN_WORKSHOP exclusively
        # when categories is non-empty, and nothing else ever routes back
        # here, so this branch is not known to be reachable today. Kept
        # rather than asserted against, so a future caller that DOES reach
        # this step with nothing queued degrades gracefully instead of
        # raising.
        if not self._categories:
            self._step = self._next_after_categories(shopping)
            self._register_progress()
            return True
        if reading.page == "WORKSHOP":
            self._step = Step.OPEN_TAB
            self._register_progress()
            return True
        match = vision.locate_template(
            screen, self._templates.get(config.NAV_TARGETS["WORKSHOP"]), self._threshold
        )
        if match is None:
            return self._miss(device, shopping, screen, "WORKSHOP nav button")
        x, y = match.center
        self._register_progress()
        self._try_tap(x, y, device, shopping, screen)
        return False

    def _open_tab(self, reading, screen: Image, device: Any, shopping: Shopping) -> bool:
        # Defensive only, same as OPEN_WORKSHOP's identical guard above:
        # _step becomes OPEN_TAB only via a cascade or a BUY_ROWS handoff
        # that both already confirmed _categories is non-empty. Not known
        # to be reachable today.
        if not self._categories:
            self._step = self._next_after_categories(shopping)
            self._register_progress()
            return True
        if reading.page != "WORKSHOP":
            # Navigation should already have landed us on WORKSHOP by the
            # time OPEN_TAB runs (see OPEN_WORKSHOP) - if it has not, that is
            # not a transient "still loading" state, it is a wedge.
            return self._miss(device, shopping, screen, "workshop page")

        category = self._categories[0]
        rows = [r for r in shopping.rows_for(category) if r.name not in self._exhausted]
        if not rows:
            # Nothing left to look for on this tab - BUY_ROWS will pop it.
            self._step = Step.BUY_ROWS
            self._register_progress()
            return True
        if _heading_names(screen, category):
            # Arrival is judged by the page's own heading - "UTILITY
            # UPGRADES" - which names the tab outright.
            #
            # Not by the tab button's look: every tab template still matches
            # its own selected page well above threshold (measured 0.955 on
            # the tab it is standing on), so its absence cannot signal
            # arrival. And no longer by locating one of this category's row
            # templates either: that answers "no" forever once the row is
            # bought and gone, and a live visit spent its entire budget
            # tapping the UTILITY tab it was already standing on for exactly
            # that reason. The heading is there whatever is left to buy.
            self._step = Step.BUY_ROWS
            self._register_progress()
            return True

        tab_template = config.WORKSHOP_TABS[category]
        match = vision.locate_template(screen, self._templates.get(tab_template), self._threshold)
        if match is None:
            return self._miss(device, shopping, screen, f"{category} tab button")
        x, y = match.center
        self._register_progress()
        self._try_tap(x, y, device, shopping, screen)
        return False

    def _open_cards(self, reading, screen: Image, device: Any, shopping: Shopping) -> bool:
        # First-visit popups (see config.NAV_DISMISS) sit between the Cards
        # tab and the page itself. Tried in order, same as navigate.py would.
        # Their absence is normal (most visits see no popup at all), so it
        # does not count as a miss.
        for dismiss_path in config.NAV_DISMISS:
            match = vision.locate_template(
                screen, self._templates.get(dismiss_path), self._threshold
            )
            if match is not None:
                x, y = match.center
                self._register_progress()
                self._try_tap(x, y, device, shopping, screen)
                return False
        if reading.page == "CARDS":
            self._step = Step.BUY_CARDS
            self._register_progress()
            return True
        match = vision.locate_template(
            screen, self._templates.get(config.NAV_TARGETS["CARDS"]), self._threshold
        )
        if match is None:
            return self._miss(device, shopping, screen, "CARDS nav button")
        x, y = match.center
        self._register_progress()
        self._try_tap(x, y, device, shopping, screen)
        return False

    def _return(self, reading, screen: Image, device: Any, shopping: Shopping) -> bool:
        if reading.page == "MAIN_MENU":
            self._end_visit(device, shopping, screen, aborted=False)
            return False
        match = vision.locate_template(
            screen, self._templates.get(config.NAV_TARGETS["BATTLE_TAB"]), self._threshold
        )
        if match is None:
            return self._miss(device, shopping, screen, "BATTLE_TAB nav button")
        x, y = match.center
        self._register_progress()
        self._try_tap(x, y, device, shopping, screen)
        return False

    # -- buying ----------------------------------------------------------------

    def _buy_rows(self, reading: Any, screen: Image, device: Any, shopping: Shopping) -> None:
        """Read, decide, then wait for another frame to acknowledge a purchase."""
        if not self._categories:
            self._step = self._next_after_categories(shopping)
            return
        category = self._categories[0]
        observation = observe_frame(screen, "workshop")
        if self.observations is not None:
            self.observations.observe(observation)
        coins, _gems = header_numbers(screen, reading.page, reading.top_left)
        if self._pending is not None:
            self._confirm_purchase(observation, coins, device, shopping, screen)
            return
        rules = [r for r in shopping.workshop if r.enabled and r.name not in self._exhausted]
        if not rules:
            self._categories = []
            self._search = None
            self._step = self._next_after_categories(shopping)
            return
        rule = rules[0]
        self._categories = list(dict.fromkeys(r.category for r in rules))
        if rule.category != category:
            self._search = None
            self._step = Step.OPEN_TAB
            return
        entry = upgrades.resolve(rule.name, category)
        rule_id = entry.id if entry is not None else "discovered:" + tiles.normalise(rule.name)
        if rule_id in self._completed_unlocks:
            self._exhausted.add(rule.name)
            self._bus.publish(events.PurchaseSkipped(item=rule.name, reason="already_unlocked"))
            return
        if coins is None:
            self._abort(device, shopping, screen, "unreadable balance")
            return
        visible = observation.rows
        if not visible:
            self._blind_streak += 1
            if self._blind_streak >= 2:
                self._abort(device, shopping, screen, "nothing readable on the page")
                return
            self._try_tap(*config.PANEL_DISMISS_POINT, device, shopping, screen)
            self._bus.publish(events.PurchaseSkipped(item=rule.name, reason="unreadable", detail="screen"))
            return
        self._blind_streak = 0
        if observation.category != category:
            self._abort(device, shopping, screen, f"{category} heading not confirmed")
            return
        seen = _row_named(rule.name, visible, category)
        if seen is None:
            if entry is not None and self._child_proves_unlocked(entry, observation):
                self._completed_unlocks.add(entry.id)
                self._exhausted.add(rule.name)
                self._search = None
                self._bus.publish(events.PurchaseSkipped(
                    item=rule.name, reason="already_unlocked", detail="an unlocked child upgrade was observed",
                ))
                if self.observations is not None:
                    completed = ObservedUpgrade(entry.id, entry.name, entry.category, "workshop",
                                                None, None, "unlocked", observation.observed_at,
                                                config.Rect(0, 0, 0, 0), None)
                    self.observations.observe(replace(observation, rows=(*observation.rows, completed)))
                return
            self._find_row(rule.name, observation, device, shopping, screen, coins)
            return
        self._search = None
        entry = upgrades.resolve(seen.name, category)
        is_unlock = (entry is not None and entry.unlock) or tiles.normalise(seen.name).startswith("unlock")
        reason = None
        detail = ""
        if is_unlock and not shopping.allow_unlocks:
            reason, detail = "disabled", "unlock permission is off"
        elif seen.status != "available" or seen.price is None or seen.tap is None:
            reason, detail = "unreadable", "price" if seen.price is None else seen.status
        elif rule.target is not None and seen.value is None:
            reason, detail = "unreadable", "target requires a readable current value"
        elif rule.target is not None and _target_reached(seen.upgrade_id, seen.value, rule.target):
            reason, detail = "target_reached", f"current value {seen.value} meets target {rule.target}"
        elif seen.price > coins:
            reason = "unaffordable"
        elif coins - seen.price < shopping.coin_reserve:
            reason, detail = "reserve", "purchase would cross the coin reserve"
        elif shopping.armed and (shopping.coin_budget == 0
                                 or self._coin_spent + seen.price > shopping.coin_budget):
            reason, detail = "budget", "purchase would exceed the Workshop visit budget"
        if reason is not None:
            saving_unlock = entry is not None and entry.unlock and reason in {"unaffordable", "reserve", "budget"}
            if saving_unlock:
                detail = (f"Workshop visit budget cannot cover {seen.name}; lower-priority purchases are deferred"
                          if reason == "budget" else
                          f"Saving coins for {seen.name}; lower-priority Workshop purchases are deferred")
            self._bus.publish(events.PurchaseSkipped(item=rule.name, reason=reason,
                                                    detail=detail, coins_before=coins))
            self._exhausted.add(rule.name)
            if saving_unlock:
                self._categories = []
                self._search = None
                self._step = self._next_after_categories(shopping)
                if self.observations is not None:
                    phase = "blocked" if reason == "budget" else "saving"
                    self.observations.decision(phase, detail, seen.upgrade_id)
            return
        if not self._try_tap(*seen.tap, device, shopping, screen):
            return
        self._exhausted.add(rule.name)
        if shopping.armed:
            self._pending = PendingPurchase(seen, coins, frozenset(r.upgrade_id for r in visible))
            if self.observations is not None:
                self.observations.decision("verifying", f"Confirming Workshop purchase: {seen.name}", seen.upgrade_id)
        else:
            self._record_purchase(seen, coins, dry_run=True)

    def _child_proves_unlocked(self, entry: upgrades.Upgrade, observation: Observation) -> bool:
        """Permanent Workshop observations may prove a vanished prerequisite complete."""
        if not entry.unlocks:
            return False
        candidates = [row.payload() for row in observation.rows]
        if self.observations is not None:
            candidates.extend(self.observations.snapshot()["observations"])
        return any(row["context"] == "workshop" and row["category"] == entry.category
                   and row["upgrade_id"] in entry.unlocks
                   and row["status"] in {"available", "maxed"} for row in candidates)

    def _record_purchase(self, row: ObservedUpgrade, coins: int, *, dry_run: bool,
                         verified: ObservedUpgrade | None = None) -> None:
        self._bought += 1
        self._spent += row.price
        self._coin_spent += row.price
        self._bus.publish(events.Purchased(item=row.name, category=row.category, price=row.price,
                                           coins_before=coins, dry_run=dry_run))
        if not dry_run and self.observations is not None:
            self.observations.verified(verified or row)
            self.observations.decision("workshop", f"Verified Workshop purchase: {row.name}")

    def _confirm_purchase(self, observation: Observation, coins: int | None, device: Any,
                          shopping: Shopping, screen: Image) -> None:
        pending = self._pending
        before = pending.row
        after = next((r for r in observation.rows if r.upgrade_id == before.upgrade_id), None)
        same_category = observation.category == before.category
        changed = same_category and after is not None and (
            after.status == "maxed"
            or (after.price is not None and before.price is not None and after.price > before.price)
            or (after.value is not None and before.value is not None
                and after.value != before.value
                and _target_reached(before.upgrade_id, after.value, before.value))
        )
        entry = upgrades.resolve(before.name, before.category)
        is_unlock = (entry is not None and entry.unlock) or tiles.normalise(before.name).startswith("unlock")
        # A missing tile alone can be an OCR miss. Known unlocks must expose
        # their own child upgrade, alongside the matching coin debit.
        newly_visible = [r for r in observation.rows if r.upgrade_id not in pending.visible_ids
                         and r.status in {"available", "maxed"}]
        child_appeared = bool(newly_visible)
        if entry is not None and entry.unlocks:
            child_appeared = any(r.upgrade_id in entry.unlocks for r in newly_visible)
        unlocked = (same_category and is_unlock and after is None and coins is not None
                    and coins <= pending.coins - before.price
                    and child_appeared)
        if changed or unlocked:
            confirmed = after if changed else replace(before, status="unlocked", price=None,
                                                     observed_at=observation.observed_at)
            if unlocked and self.observations is not None:
                self.observations.observe(replace(observation, rows=(*observation.rows, confirmed)))
            if unlocked:
                self._completed_unlocks.add(before.upgrade_id)
                if entry is not None:
                    children = set(entry.unlocks)
                    for rule in shopping.workshop:
                        child = upgrades.resolve(rule.name, rule.category)
                        if child is not None and child.id in children and rule.name in self._missing_rows:
                            self._exhausted.discard(rule.name)
                            self._missing_rows.discard(rule.name)
            self._record_purchase(before, pending.coins, dry_run=False, verified=confirmed)
            self._pending = None
            return
        pending.frames += 1
        if pending.frames >= 3:
            self._bus.publish(events.PurchaseSkipped(item=before.name, reason="unconfirmed",
                                                    detail="purchase did not produce a readable change"))
            if self.observations is not None:
                self.observations.decision("blocked", f"Workshop purchase of {before.name} was not confirmed")
            self._abort(device, shopping, screen, "purchase acknowledgement was inconclusive")
        elif self.observations is not None:
            self.observations.decision("verifying", f"Waiting for {before.name} to change", before.upgrade_id)

    def _find_row(self, name: str, observation: Observation, device: Any,
                  shopping: Shopping, screen: Image, coins: int) -> None:
        """Find a hidden row with a bounded top-to-bottom scan of this category."""
        if self._search is None or self._search.name != name:
            self._search = RowSearch(name)
        search = self._search
        fingerprint = tuple(r.upgrade_id for r in observation.rows)
        limit = self._tuning.autopilot.max_scrolls if self._tuning is not None else 8
        at_end = fingerprint == search.fingerprint or search.scrolls >= limit
        if at_end and not search.down:
            search.down, search.scrolls = True, 0
        elif at_end or observation.heading_y is None or not shopping.armed:
            self._bus.publish(events.RowUnmatched(item=name, read=tuple(r.name for r in observation.rows)))
            self._bus.publish(events.PurchaseSkipped(item=name, reason="no_match", coins_before=coins))
            self._exhausted.add(name)
            self._missing_rows.add(name)
            entry = upgrades.resolve(name, observation.category)
            if self.observations is not None:
                if entry is not None:
                    self.observations.unknown(entry, "workshop", observation.observed_at)
                self.observations.decision("workshop", f"{name} was not found; availability is unknown")
            self._search = None
            return
        if self._taps >= shopping.max_taps_per_visit:
            self._abort(device, shopping, screen, "tap budget exhausted")
            return
        from autopilot import scroll_panel
        self._taps += 1
        scroll_panel(device, screen, observation.heading_y, down=search.down)
        search.scrolls += 1
        search.fingerprint = fingerprint
        if self.observations is not None:
            self.observations.decision("discovering", f"Scanning Workshop for {name}")

    def _buy_cards(self, reading, screen: Image, device: Any, shopping: Shopping) -> None:
        """Buy the configured batch, repeatedly, up to the per-visit cap.

        Unlike a workshop row, a card batch is not "used up" by one
        purchase - the game lets the same button be bought again - so this
        does not add to _exhausted after a successful buy. _cards_bought and
        max_per_visit are what bound it instead.
        """
        cards = shopping.cards
        if not cards.enabled:
            self._step = Step.RETURN
            return
        if self._cards_bought >= cards.max_per_visit:
            self._step = Step.RETURN
            return

        # Coins are irrelevant to a card purchase (it spends gems) - the
        # header is always read as a pair, so the unused half is discarded
        # rather than stored on an attribute nothing ever reads back.
        _coins, gems = header_numbers(screen, reading.page, reading.top_left)
        if gems is None:
            self._abort(device, shopping, screen, "unreadable balance")
            return

        item = cards.batch
        template_path = config.CARD_BUTTONS[item]
        match = vision.locate_template(screen, self._templates.get(template_path), self._threshold)
        if match is None:
            self._bus.publish(
                events.PurchaseSkipped(
                    item=item, reason="no_match", gems_before=gems,
                )
            )
            self._step = Step.RETURN
            return

        price = self._reader.read(screen, config.CARD_PRICE_REGION, match.top_left, "menu")
        if price is None:
            self._bus.publish(
                events.PurchaseSkipped(item=item, reason="unreadable", detail="price",
                    gems_before=gems,
                )
            )
            self._step = Step.RETURN
            return

        if price > gems:
            self._bus.publish(
                events.PurchaseSkipped(item=item, reason="unaffordable",
                    gems_before=gems,
                )
            )
            self._step = Step.RETURN
            return

        if gems - price < cards.gem_floor:
            self._bus.publish(
                events.PurchaseSkipped(
                    item=item, reason="capped", detail="gem floor",
                    gems_before=gems,
                )
            )
            self._step = Step.RETURN
            return

        x, y = match.center
        if not self._try_tap(x, y, device, shopping, screen):
            return

        self._cards_bought += 1
        self._bought += 1
        self._spent += price
        self._bus.publish(events.Purchased(
            # A card purchase spends gems, not coins - gems_before is the
            # honest field for it. coins_before stays at its default None
            # here rather than being reused for the wrong currency (see
            # events.Purchased's own docstring).
            item=item, category="CARDS", price=price, gems_before=gems,
            dry_run=not shopping.armed,
        ))

    # -- tapping and ending ------------------------------------------------

    def _tap(self, device: Any, x: int, y: int, shopping: Shopping) -> None:
        """The ONLY place device.tap is called anywhere in this module.

        Counts the attempt regardless of armed, so a rehearsal hits the same
        tap-budget ceiling a real run would - the whole point of a rehearsal
        is that it behaves exactly like the real thing except for this one
        line.

        Being the only tap site is also what makes jitter here cover every
        workshop tab, workshop row, card buy and exit-to-battle tap at once.
        The offset and the pause are applied AFTER the armed check, so a
        rehearsal stays instant instead of paying a per-tap pause for taps
        it is not going to send.
        """
        self._taps += 1
        if not shopping.armed:
            return
        if self._tuning is not None:
            x, y = jitter.point(x, y, self._tuning.tap_jitter_px)
            jitter.pause(self._tuning.tap_delay, self._tuning.timing_jitter)
        tap(device, x, y)

    def _try_tap(
        self, x: int, y: int, device: Any, shopping: Shopping, screen: Image
    ) -> bool:
        """Tap if the budget allows it; abort the visit if it does not.

        Checked before tapping, not after: incrementing first and checking
        next call would let one call slip a tap past the cap.
        """
        if self._taps >= shopping.max_taps_per_visit:
            self._abort(device, shopping, screen, "tap budget exhausted")
            return False
        self._tap(device, x, y, shopping)
        return True

    def _exit_to_battle(self, device: Any, shopping: Shopping, screen: Image) -> None:
        """Best-effort return tap used by every abort path.

        Deliberately NOT gated by the tap budget (round-2 fix: the first
        cut of this method WAS gated, which meant a budget-exhaustion abort
        - the most common abort there is, since the budget is the primary
        safety valve - could never make this tap. The result was worse than
        untidy: with _step forced to IDLE, advance() no-ops forever;
        navigate.Navigator cannot rescue it either, since it only acts on
        GAME_OVER and MAIN_MENU and a menu page classifies UNKNOWN to the
        screen tracker by design. The bot would sit on the Workshop or
        Cards page indefinitely, tapping nothing, while unknown-screen
        snapshotting quietly filled up with pictures of it.

        The cap exists to stop an errand from tapping indefinitely, not to
        strand the bot somewhere it cannot leave once the cap trips. This is
        the one tap explicitly licensed past the ceiling - one tap over a
        40-tap budget is not the risk the ceiling guards against.
        """
        match = vision.locate_template(
            screen, self._templates.get(config.NAV_TARGETS["BATTLE_TAB"]), self._threshold
        )
        if match is not None:
            x, y = match.center
            self._tap(device, x, y, shopping)

    def _abort(self, device: Any, shopping: Shopping, screen: Image, reason: str) -> None:
        self._end_visit(device, shopping, screen, aborted=True, reason=reason)

    def _end_visit(
        self, device: Any, shopping: Shopping, screen: Image, *, aborted: bool, reason: str = ""
    ) -> None:
        if aborted:
            self._exit_to_battle(device, shopping, screen)
        self._bus.publish(events.ShoppingEnded(
            visit=self._visit, bought=self._bought, spent=self._spent,
            aborted=aborted, reason=reason,
        ))
        self._step = Step.IDLE
        self._categories = []
        self._pending = None
        self._search = None
