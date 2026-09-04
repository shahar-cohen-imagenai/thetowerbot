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

A positioning step that cannot find its target is NOT allowed to retry
silently forever the way navigate.Navigator does: Navigator sits on a screen
the bot is happy to stay on and taps opportunistically, but a positioning
step here is one leg of an errand that is supposed to be making progress. A
miss counts toward the same _off_page_streak an unrecognized page does - one
miss may be an animation frame still settling, but two in a row means a
mis-cut or renamed nav template, and the visit ends with
`ShoppingEnded(aborted=True, reason=...)` naming the template that could not
be found, rather than spinning until an operator notices a visit that
appears to be running and is not. Every step still resets that streak the
moment it makes real progress - a tap, an arrival, or a BUY_ROWS/BUY_CARDS
decision - so a bot healthily bouncing between recognised pages never trips
it.

The tab-arrival check deliberately does NOT use "the tab button's own
template stopped matching" - the intuitive reading of config.WORKSHOP_TABS's
"cut unselected" comment, and the first thing anyone re-deriving this will
reach for. Measured directly against the committed fixtures (see
tests/test_shopping_templates.py's
test_a_tab_template_also_matches_its_own_selected_page): each tab's own
"unselected" crop still scores 0.93-0.96 on the very page where that tab IS
selected - comfortably above both 0.8 and 0.9. TM_CCOEFF_NORMED normalises
away the brightness difference between the two states, so a tab template's
absence is never a usable "we have arrived" signal. Arrival is judged by
whether the target category's own ROW templates are visible instead - page
content, not tab-button absence.
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Any

import config
import events
import pages
import tiles
import vision
from device import Image, tap
from digits import NumberReader
from strategy import Shopping

logger = logging.getLogger("tower_bot.shopping")

# The floor for the arrival check in _open_tab (see ShoppingSession._open_tab).
# tests/test_shopping_templates.py::test_rows_are_absent_from_the_other_tabs
# proves a row scores BELOW 0.9 on every tab it does not live on - it proves
# nothing at a lower threshold. ShoppingRule.threshold is a per-row knob for a
# different question (how sure BUY_ROWS must be before spending on this row)
# and is only validated as 0 < t <= 1, so a hand-edited strategy - or a future
# editor UI - could set it below 0.9. Arrival detection borrows the measured
# absence guarantee, so it must never be loosened by that per-row tuning:
# a spurious "arrival" on the wrong tab would not cause a wrong purchase
# (BUY_ROWS re-verifies the specific row against its own threshold), but it
# would mark a row exhausted without ever having been attempted on its real
# tab, quietly defeating "highest-priority row wins" for the rest of the visit.
ARRIVAL_ABSENCE_THRESHOLD: float = 0.9


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


# --- Phase 1 A/B scaffolding ---------------------------------------------
# THROWAWAY. Exists to compare the OCR reader against the template reader on
# live prices before any coin is risked on the former. Deleted in Phase 2
# along with the template path itself - see the OCR row addressing spec §12.
ab_logger = logging.getLogger("tower_bot.ocr_ab")


def compare_readers(
    rule: Any, template_price: int | None, rows: tuple[tiles.Row, ...]
) -> str | None:
    """Describe how the two readers disagree about `rule`, or None.

    Compares on the normalised name, so a difference of case or spacing is
    not reported as a disagreement - only a different row or a different
    number is.
    """
    wanted = tiles.normalise(rule.name)
    seen = [row for row in rows if tiles.normalise(row.name) == wanted]
    if not seen:
        read = ", ".join(repr(row.name) for row in rows) or "nothing"
        return f"{rule.name}: OCR did not find it; read {read}"
    ocr_price = seen[0].price
    if ocr_price is None:
        return f"{rule.name}: template read {template_price}, OCR could not read a price"
    if ocr_price != template_price:
        return f"{rule.name}: template read {template_price}, OCR read {ocr_price}"
    return None


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
        # Set once, by tower_bot.build_shopping(), when this machine's header
        # atlas cannot support reading a balance. A non-empty reason makes
        # begin() decline forever rather than starting a visit that could
        # never approve a purchase - see the module docstring's paragraph on
        # why an unreadable balance must stop the visit, not guess.
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
        # Rows that no longer match (a mis-cut template) or that were just
        # bought, or found unaffordable, this visit. Not retried: a row that
        # cannot be found loops until the tap budget runs out otherwise, and
        # a row that was just bought should not be bought again every 2s
        # forever just because a static frame never visibly changes.
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

        Also declines - permanently - when `disabled_reason` is set: this
        machine's header atlas cannot read a balance, and a visit that could
        never approve a purchase is a minute of tab-tapping for nothing. See
        tower_bot.build_shopping(). The first such decline publishes
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
        self._cards_bought = 0
        self._exhausted = set()
        self._off_page_streak = 0
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
        if any(
            vision.locate_template(
                screen, self._templates.get(r.template),
                max(r.threshold, ARRIVAL_ABSENCE_THRESHOLD),
            ) is not None
            for r in rows
        ):
            # Arrival is judged by what we actually came here for, not by
            # the tab button's own look - see the module docstring for the
            # measurement: every tab template still matches its own
            # selected page well above threshold, so its absence cannot
            # signal arrival. Seeing one of this category's own rows can.
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

    def _buy_rows(self, reading, screen: Image, device: Any, shopping: Shopping) -> None:
        """Evaluate the highest-priority not-yet-exhausted row on this tab.

        Re-reads coins from the header every call, rather than subtracting
        the price just paid: the game is the source of truth, and a running
        subtraction that drifts would spend money the bot does not have.
        """
        # Defensive only: _step becomes BUY_ROWS only from OPEN_TAB, which
        # only reaches either of its two BUY_ROWS transitions after already
        # confirming _categories is non-empty. Not known to be reachable
        # today.
        if not self._categories:
            self._step = self._next_after_categories(shopping)
            return

        category = self._categories[0]
        rows = [r for r in shopping.rows_for(category) if r.name not in self._exhausted]
        if not rows:
            self._categories.pop(0)
            self._step = Step.OPEN_TAB if self._categories else self._next_after_categories(shopping)
            return

        # Gems are irrelevant to a workshop row (it spends coins) - see the
        # matching comment in _buy_cards for why the unused half of the pair
        # is discarded rather than stored.
        coins, _gems = header_numbers(screen, reading.page, reading.top_left, self._reader)
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

        # Phase 1 A/B - observation only, never a decision. Deleted in
        # Phase 2. Wrapped because a reader under evaluation must not be
        # able to break the reader in production.
        try:
            note = compare_readers(rule, price, tiles.read_rows(screen))
            if note is not None:
                ab_logger.warning("%s", note)
            else:
                ab_logger.info("%s: readers agree on %s", rule.name, price)
        except Exception:
            ab_logger.exception("the OCR comparison itself failed")

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

        # Coins are irrelevant to a card purchase (it spends gems) - the
        # header is always read as a pair, so the unused half is discarded
        # rather than stored on an attribute nothing ever reads back.
        _coins, gems = header_numbers(screen, reading.page, reading.top_left, self._reader)
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
