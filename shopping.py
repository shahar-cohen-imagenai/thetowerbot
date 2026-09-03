"""Walk the Workshop and Cards pages between runs, and decide what to buy.

Unlike navigate.py - which boasts that nothing there spends a permanent
resource - this module's entire job is to spend one: coins and gems that the
game does not hand back. Every safety property that matters here has to be
earned back explicitly, because nothing about "the bot taps templates" is
inherently safe once a tap can cost currency.

`shopping.armed` is the only thing standing between a miscalibrated template
and the user's coins. `_tap()` is therefore the ONLY place `device.tap` is
called anywhere in this module, and it returns before ever reaching the
device when `not shopping.armed`. Every purchase - real or rehearsed - still
publishes `Purchased(dry_run=not shopping.armed)`, so a dry run and a real
run produce identical logs apart from that one flag. That is what makes the
rehearsal worth anything: it is not a different code path, it is the same
code path with the last step removed.

There is no brightness fallback anywhere in this module. Measured on the
real device (see config.PRICE_REGIONS and tests/test_shopping_templates.py),
the game marks an unaffordable button by DESATURATING it, not dimming it -
on the cards page the unaffordable button is actually the BRIGHTER of the
two. A brightness gate calibrated on the affordable style would wave the
unaffordable one straight through, in exactly the wrong direction. Digit
reading is therefore the only gate an affordability decision may use, and
because digits.NumberReader.read is all-or-nothing, a coin or gem balance
that cannot be fully read comes back as None - never a guess, never a
brightness-based guess, and never a stale number left over from a previous
step. A None balance stops the visit rather than approving anything against
it; see BUY_ROWS and BUY_CARDS below. On a live device this will happen
often, precisely because the header atlas only knows the glyphs the
committed fixtures happen to contain (0 1 4 7 8 . K) - a coin balance that
uses 2, 3, 5, 6, 9 or a bigger suffix reads as None today. That is the
designed failure mode, not a bug: an unreadable balance approves nothing.

One step per scan: advance() does at most one capture's worth of work - at
most one tap - and every path through it either makes progress (a state
transition), publishes a skip or purchase, or ends the visit. The bot's scan
loop calls run_once every 2 seconds; a blocking errand here would stall
Pause, freeze the frame stream, and turn a minute-long shopping visit into
one opaque entry in the event feed after the fact. Positioning steps
(OPEN_WORKSHOP, OPEN_TAB, OPEN_CARDS, RETURN) may cascade through several
pure "already there" transitions within a single call - that costs nothing
and would otherwise waste whole scan cycles on bookkeeping - but the moment
one of them taps, publishes, or fails to find its target, that call is done.
Positioning steps that cannot find their target simply try again next scan,
the same way navigate.Navigator does; only BUY_ROWS and BUY_CARDS - the
steps that can actually spend - are held to publishing something every time
they act. The unexpected-page streak and the tap budget are what bound a
visit that never finds its way, in both cases.
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Any

import config
import events
import pages
import vision
from device import Image, tap
from digits import NumberReader
from strategy import Shopping

logger = logging.getLogger("tower_bot.shopping")


class Step(Enum):
    IDLE = auto()
    OPEN_WORKSHOP = auto()
    OPEN_TAB = auto()
    BUY_ROWS = auto()
    OPEN_CARDS = auto()
    BUY_CARDS = auto()
    RETURN = auto()


def header_numbers(
    screen: Image, page: str, top_left: tuple[int, int] | None, reader: NumberReader
) -> tuple[int | None, int | None]:
    """Coins and gems off the menu header, or (None, None) off a page that
    has no header regions (MISSIONS, or a page that failed to classify)."""
    regions = config.HEADER_REGIONS.get(page)
    if regions is None or top_left is None:
        return None, None
    coins_region, gems_region = regions
    coins = reader.read(screen, coins_region, top_left, "header")
    gems = reader.read(screen, gems_region, top_left, "header")
    return coins, gems


class ShoppingSession:
    """Walks the Workshop and Cards pages, buying what the policy allows.

    `shopping` is accepted at construction for symmetry with the reader and
    template cache, but every method that decides anything is handed the
    live policy explicitly - the same way the rest of the scan loop always
    reads Shopping fresh rather than trusting a copy made when the session
    was built.
    """

    def __init__(
        self,
        templates: vision.TemplateCache,
        bus: Any,
        reader: NumberReader,
        shopping: Shopping | None = None,
        threshold: float = 0.8,
    ) -> None:
        self._templates = templates
        self._bus = bus
        self._reader = reader
        self._shopping = shopping
        self._threshold = threshold

        self._step: Step = Step.IDLE
        self._visit = 0
        self._categories: list[str] = []
        self._taps = 0
        self._bought = 0
        self._spent = 0
        self._cards_bought = 0
        self._coins: int | None = None
        self._gems: int | None = None
        # Rows that no longer match (a mis-cut template) or that were just
        # bought, or found unaffordable, this visit. Not retried: a row that
        # cannot be found loops until the tap budget runs out otherwise, and
        # a row that was just bought should not be bought again every 2s
        # forever just because a static frame never visibly changes.
        self._exhausted: set[str] = set()
        self._last_run_count: int | None = None
        self._off_page_streak = 0

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
        """
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
        self._cards_bought = 0
        self._coins = None
        self._gems = None
        self._exhausted = set()
        self._off_page_streak = 0
        self._last_run_count = run_count
        self._step = Step.OPEN_WORKSHOP if categories else Step.OPEN_CARDS

        self._bus.publish(events.ShoppingStarted(
            visit=self._visit, coins=None, gems=None, dry_run=not shopping.armed,
        ))
        return True

    def reset(self) -> None:
        """Return to idle without ending a visit through the normal path.

        Cadence memory (_last_run_count) is deliberately NOT cleared here -
        it has to survive across visits to know how long it has been.
        """
        self._step = Step.IDLE
        self._categories = []

    # -- one step ------------------------------------------------------------

    def advance(self, screen: Image, device: Any, shopping: Shopping) -> None:
        """One step. At most one tap, and only when shopping.armed.

        Every path through here must either make progress, publish a skip,
        or end the visit. A step that silently does nothing is how a session
        wedges, which is what _off_page_streak exists to catch.
        """
        if self._step is Step.IDLE:
            return

        try:
            reading = pages.classify_page(screen, self._templates)
            if reading.page == pages.UNKNOWN:
                self._off_page_streak += 1
                if self._off_page_streak >= 2:
                    self._abort(device, shopping, screen, "unexpected page")
                return
            self._off_page_streak = 0
            self._dispatch(reading, screen, device, shopping)
        except Exception as exc:  # noqa: BLE001 - a shopping step must never
            # crash the scan loop; give the coins back to the user's control
            # instead by ending the visit and saying why.
            logger.exception("shopping step raised; ending the visit")
            self._abort(device, shopping, screen, f"error: {exc}")

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
                self._buy_rows(reading, screen, device, shopping)
                return
            if step is Step.OPEN_CARDS:
                if self._open_cards(reading, screen, device, shopping):
                    continue
                return
            if step is Step.BUY_CARDS:
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
        if not self._categories:
            self._step = self._next_after_categories(shopping)
            return True
        if reading.page == "WORKSHOP":
            self._step = Step.OPEN_TAB
            return True
        match = vision.locate_template(
            screen, self._templates.get(config.NAV_TARGETS["WORKSHOP"]), self._threshold
        )
        if match is None:
            return False  # not there yet - try again next scan
        x, y = match.center
        self._try_tap(x, y, device, shopping, screen)
        return False

    def _open_tab(self, reading, screen: Image, device: Any, shopping: Shopping) -> bool:
        if not self._categories:
            self._step = self._next_after_categories(shopping)
            return True
        if reading.page != "WORKSHOP":
            return False  # navigation is still in flight - try again next scan

        category = self._categories[0]
        rows = [r for r in shopping.rows_for(category) if r.name not in self._exhausted]
        if not rows:
            # Nothing left to look for on this tab - BUY_ROWS will pop it.
            self._step = Step.BUY_ROWS
            return True
        if any(
            vision.locate_template(screen, self._templates.get(r.template), r.threshold)
            is not None
            for r in rows
        ):
            # Arrival is judged by what we actually came here for, not by
            # the tab button's own look: config.WORKSHOP_TABS crops the tab
            # UNSELECTED, but measured against the real fixtures every tab
            # still scores above threshold on its OWN selected page too (the
            # selected/unselected art is not different enough at this
            # threshold to tell apart) - so "tab button not found" is not a
            # reliable arrival signal. Seeing one of this category's own
            # rows on screen is.
            self._step = Step.BUY_ROWS
            return True

        tab_template = config.WORKSHOP_TABS[category]
        match = vision.locate_template(screen, self._templates.get(tab_template), self._threshold)
        if match is None:
            return False
        x, y = match.center
        self._try_tap(x, y, device, shopping, screen)
        return False

    def _open_cards(self, reading, screen: Image, device: Any, shopping: Shopping) -> bool:
        # First-visit popups (see config.NAV_DISMISS) sit between the Cards
        # tab and the page itself. Tried in order, same as navigate.py would.
        for dismiss_path in config.NAV_DISMISS:
            match = vision.locate_template(
                screen, self._templates.get(dismiss_path), self._threshold
            )
            if match is not None:
                x, y = match.center
                self._try_tap(x, y, device, shopping, screen)
                return False
        if reading.page == "CARDS":
            self._step = Step.BUY_CARDS
            return True
        match = vision.locate_template(
            screen, self._templates.get(config.NAV_TARGETS["CARDS"]), self._threshold
        )
        if match is None:
            return False
        x, y = match.center
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
            return False
        x, y = match.center
        self._try_tap(x, y, device, shopping, screen)
        return False

    # -- buying ----------------------------------------------------------------

    def _buy_rows(self, reading, screen: Image, device: Any, shopping: Shopping) -> None:
        """Evaluate the highest-priority not-yet-exhausted row on this tab.

        Re-reads coins from the header every call, rather than subtracting
        the price just paid: the game is the source of truth, and a running
        subtraction that drifts would spend money the bot does not have.
        """
        if not self._categories:
            self._step = self._next_after_categories(shopping)
            return

        category = self._categories[0]
        rows = [r for r in shopping.rows_for(category) if r.name not in self._exhausted]
        if not rows:
            self._categories.pop(0)
            self._step = Step.OPEN_TAB if self._categories else self._next_after_categories(shopping)
            return

        coins, gems = header_numbers(screen, reading.page, reading.top_left, self._reader)
        self._coins, self._gems = coins, gems
        if coins is None:
            self._abort(device, shopping, screen, "unreadable balance")
            return

        rule = rows[0]
        match = vision.locate_template(screen, self._templates.get(rule.template), rule.threshold)
        if match is None:
            self._bus.publish(events.PurchaseSkipped(item=rule.name, reason="no_match"))
            self._exhausted.add(rule.name)
            return

        price = self._reader.read(screen, config.PRICE_REGIONS[rule.layout], match.top_left, "menu")
        if price is None:
            self._bus.publish(
                events.PurchaseSkipped(item=rule.name, reason="unreadable", detail="price")
            )
            self._exhausted.add(rule.name)
            return

        if price > coins:
            self._bus.publish(events.PurchaseSkipped(item=rule.name, reason="unaffordable"))
            self._exhausted.add(rule.name)
            return

        x, y = match.center
        if not self._try_tap(x, y, device, shopping, screen):
            return

        self._exhausted.add(rule.name)
        self._bought += 1
        self._spent += price
        self._bus.publish(events.Purchased(
            item=rule.name, category=category, price=price,
            coins_before=coins, dry_run=not shopping.armed,
        ))

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

        coins, gems = header_numbers(screen, reading.page, reading.top_left, self._reader)
        self._coins, self._gems = coins, gems
        if gems is None:
            self._abort(device, shopping, screen, "unreadable balance")
            return

        item = cards.batch
        template_path = config.CARD_BUTTONS[item]
        match = vision.locate_template(screen, self._templates.get(template_path), self._threshold)
        if match is None:
            self._bus.publish(events.PurchaseSkipped(item=item, reason="no_match"))
            self._step = Step.RETURN
            return

        price = self._reader.read(screen, config.CARD_PRICE_REGION, match.top_left, "menu")
        if price is None:
            self._bus.publish(events.PurchaseSkipped(item=item, reason="unreadable", detail="price"))
            self._step = Step.RETURN
            return

        if price > gems:
            self._bus.publish(events.PurchaseSkipped(item=item, reason="unaffordable"))
            self._step = Step.RETURN
            return

        if gems - price < cards.gem_floor:
            self._bus.publish(events.PurchaseSkipped(item=item, reason="capped", detail="gem floor"))
            self._step = Step.RETURN
            return

        x, y = match.center
        if not self._try_tap(x, y, device, shopping, screen):
            return

        self._cards_bought += 1
        self._bought += 1
        self._spent += price
        self._bus.publish(events.Purchased(
            item=item, category="CARDS", price=price, coins_before=gems,
            dry_run=not shopping.armed,
        ))

    # -- tapping and ending ------------------------------------------------

    def _tap(self, device: Any, x: int, y: int, shopping: Shopping) -> None:
        """The ONLY place device.tap is called anywhere in this module.

        Counts the attempt regardless of armed, so a rehearsal hits the same
        tap-budget ceiling a real run would - the whole point of a rehearsal
        is that it behaves exactly like the real thing except for this one
        line.
        """
        self._taps += 1
        if not shopping.armed:
            return
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

        Goes through the same budget the rest of the visit does - an abort
        is not licensed to spend one more tap than the cap it exists to
        enforce, so this is silently skipped if the budget is already spent.
        """
        if self._taps >= shopping.max_taps_per_visit:
            return
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
