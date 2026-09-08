"""The director: one pure function from an account snapshot to a ranked plan.

Arms nothing, taps nothing, spends nothing. It is a recommendation and an
explanation, and every candidate carries the knowledge ids it rests on so a
human can check the reasoning against the wiki.

Two rules the tests exist to protect:
  - An unscoreable objective sorts LAST, never first. A None horizon means
    "we cannot tell how long", which is not a reason to do something now.
  - A conflicted or unverified knowledge fact HOLDS its objective. The plan
    says so, and quotes the conflict.
"""
from __future__ import annotations

import dataclasses
import math

import pytest

import director
import knowledge
import objectives
from account_state import AccountRevision, Evidence, Fact
from progression import CurrencyRates
from strategy import Strategy


def fact(concept_id: str, value: object) -> Fact:
    return Fact(concept_id, value, "verified", Evidence(
        1., .99, concept_id, str(value), (0, 0, 1, 1), 1080, 2400, "abc"))


def revision(**overrides: object) -> AccountRevision:
    return dataclasses.replace(AccountRevision(), **overrides)  # type: ignore[arg-type]


def strategy() -> Strategy:
    return Strategy.from_dict({"name": "t", "actions": [
        {"name": "Damage", "template": "upgrade_damage.png"}]})


def plan_for(rev: AccountRevision, rate: float | None = 1000.) -> director.Plan:
    return director.plan(
        rev, knowledge=knowledge.KNOWLEDGE, graph=objectives.GRAPH,
        rates=CurrencyRates(rate, 1, 3, "test"), strategy=strategy())


# -- shape ---------------------------------------------------------------
def test_a_plan_names_every_objective_exactly_once() -> None:
    result = plan_for(revision())
    ids = [c.objective_id for c in result.candidates]
    assert len(ids) == len(set(ids)) == len(objectives.GRAPH)


def test_a_plan_records_which_revision_it_read() -> None:
    """A recommendation with no snapshot id cannot be checked afterwards."""
    result = plan_for(revision(revision_id=7))
    assert result.revision_id == 7


def test_every_candidate_carries_its_knowledge_citations() -> None:
    for candidate in plan_for(revision()).candidates:
        assert candidate.knowledge_refs


def test_every_candidate_explains_itself_in_words() -> None:
    """The dashboard shows this. "score 0.41" is not an explanation."""
    for candidate in plan_for(revision()).candidates:
        assert candidate.why and candidate.why.strip()


# -- ranking -------------------------------------------------------------
def test_the_top_candidate_is_ready_rather_than_blocked() -> None:
    result = plan_for(revision())
    assert result.top is None or result.top.status == "ready"


def test_a_sooner_objective_outranks_a_theoretically_better_distant_one() -> None:
    """The scoring rule, stated as a test: payoff over time-to-afford, so an
    eleven-day optimum loses to something that lands this afternoon."""
    soon = director.Candidate("a", "ready", None, .5, 100, "coins", (), (), "w", ("x",))
    distant = director.Candidate("b", "ready", None, 264., 100, "coins", (), (), "w", ("x",))
    scored = director.rank((
        dataclasses.replace(soon, score=director.score(1.0, soon.hours_to_afford)),
        dataclasses.replace(distant, score=director.score(5.0, distant.hours_to_afford)),
    ))
    assert scored[0].objective_id == "a"


def test_an_unscoreable_objective_sorts_last_rather_than_first() -> None:
    """A None horizon means "we cannot tell how long". That is not a reason to
    do something now, and a naive sort would put None at the front."""
    known = director.Candidate("a", "ready", 1., 1., 100, "coins", (), (), "w", ("x",))
    unknown = director.Candidate("b", "ready", None, None, None, "coins", (), (), "w", ("x",))
    assert [c.objective_id for c in director.rank((unknown, known))] == ["a", "b"]


def test_a_blocked_objective_never_outranks_a_ready_one() -> None:
    blocked = director.Candidate("a", "blocked", 99., 0., 1, "coins", ("dep",), (), "w", ("x",))
    ready = director.Candidate("b", "ready", .1, 10., 1, "coins", (), (), "w", ("x",))
    assert director.rank((blocked, ready))[0].objective_id == "b"


def test_an_already_affordable_objective_does_not_divide_by_zero() -> None:
    assert director.score(1.0, 0.) is not None


# -- holds ---------------------------------------------------------------
def test_an_objective_resting_on_an_unverified_fact_is_held() -> None:
    """rule_verified is false across the whole committed pack today, so this
    is the ordinary case on day one - and the plan must say so plainly rather
    than recommending a spend it cannot justify."""
    result = plan_for(revision())
    held = [c for c in result.candidates if c.held_by]
    assert held
    assert any("verified" in reason.lower() for c in held for reason in c.held_by)


def test_a_conflicted_fact_holds_its_objective_and_quotes_the_conflict() -> None:
    """The design's own rule: a fact with an unresolved conflict never
    authorises a spend; it surfaces as a held objective with the conflict
    quoted."""
    tainted = {t for c in knowledge.KNOWLEDGE.conflicts for t in c.tainted}
    result = plan_for(revision())
    affected = [c for c in result.candidates
                if set(c.knowledge_refs) & tainted]
    assert affected
    for candidate in affected:
        assert candidate.held_by
        assert any(len(reason) > 20 for reason in candidate.held_by)


def test_a_held_objective_is_never_the_top_candidate() -> None:
    result = plan_for(revision())
    assert result.top is None or not result.top.held_by


# -- the live account ----------------------------------------------------
def test_the_live_account_shape_produces_a_plan_with_a_stated_reason() -> None:
    """Reproduces the real database: only workshop_stats populated. The plan
    must be honest rather than empty - "nothing is actionable and here is
    why" is a useful answer."""
    live = revision(workshop_stats=(fact("stats.health", 100.),))
    result = plan_for(live, rate=None)
    assert result.reason and result.reason.strip()
    assert result.candidates


def test_the_plan_is_pure() -> None:
    """Called twice on the same inputs, byte-identical. A plan that read a
    clock or a database could not be reviewed against a recorded snapshot."""
    live = revision(workshop_stats=(fact("stats.health", 100.),))
    assert plan_for(live) == plan_for(live)


def test_the_plan_arms_nothing() -> None:
    """director imports nothing that can touch a device. Enforced rather than
    trusted - this is the whole promise of an advisory-only phase."""
    import inspect

    source = inspect.getsource(director)
    for forbidden in ("import device", "import shopping", "import transactions",
                      "device.tap", ".request(", "import tower_bot"):
        assert forbidden not in source, forbidden


# ==========================================================================
# The four items earlier tasks and their reviews explicitly handed to
# task 7 (see task-7-brief.md). Each of the following pins one of them
# directly, beyond what the tests above already exercise incidentally.
# ==========================================================================

# -- (1) cards.unlock.* and claim.* can never be satisfied -----------------
def test_the_never_satisfiable_families_are_held_and_never_top() -> None:
    """cards.unlock.* (no per-card unlock signal exists) and claim.* (needs
    a clock satisfied_by cannot take) are permanently "ready" once reachable
    and never "done". Without an explicit hold they would squat on `top`
    forever - Task 4's review approved their low value ON THE CONDITION that
    this task deprioritise them explicitly. This is that check, run against
    a revision where cards.slots.3 is done so cards.unlock.* actually
    reaches "ready" rather than staying "blocked" by coincidence."""
    live = revision(cards=(fact("cards.slots.capacity", 3),))
    result = plan_for(live)
    by_id = {c.objective_id: c for c in result.candidates}

    never_satisfiable_ids = [o.id for o in objectives.GRAPH
                              if o.id.startswith(("cards.unlock.", "claim."))]
    assert never_satisfiable_ids  # sanity: both families exist in the graph

    for oid in never_satisfiable_ids:
        candidate = by_id[oid]
        assert candidate.held_by, oid
        assert any("never return true" in r.lower() or "can never" in r.lower()
                   for r in candidate.held_by), (oid, candidate.held_by)

    assert result.top is None or result.top.objective_id not in never_satisfiable_ids


def test_a_one_way_objective_is_held_without_a_matching_pre_approval() -> None:
    """Ultimate Weapon picks and the Black Hole Damage lab are permanent
    (objectives.py's own RiskClass docstring). Held unless the strategy's
    arming ladder already names them - which the fixture strategy (one
    action named "Damage") never does."""
    result = plan_for(revision())
    one_way_ids = {o.id for o in objectives.GRAPH if o.risk == "one_way"}
    assert one_way_ids  # sanity
    held_ids = {c.objective_id for c in result.candidates if c.held_by}
    assert one_way_ids <= held_ids


def _one_way_objective() -> objectives.Objective:
    """A synthetic one-way objective with no price and an untainted,
    unverified citation - isolates the one-way/pre-approval hold from the
    other two hold reasons (taint, priced-and-unverified) so these two
    tests pin exactly one mechanism each."""
    return objectives.Objective(
        id="test.one_way", requires=(), grants=(), satisfied_by=lambda r: False,
        actions=(objectives.Action(executor="test.one_way.exec"),),
        value=1.0, risk="one_way", knowledge_refs=("tier.2.unlock_wave",),
    )


def test_a_matching_pre_approval_lifts_the_one_way_hold() -> None:
    """The positive half of _has_pre_approval, previously untested: only the
    "no strategy names this" path was exercised anywhere, because no
    committed strategy maps to any real objective. A false negative there
    (never lifting the hold) would be safe but useless; a false positive
    would unhold a genuinely irreversible objective - this proves a real
    match actually clears the one-way reason, not just that a non-match
    fails to add it."""
    obj = _one_way_objective()
    matching_strategy = Strategy.from_dict({"name": "t", "actions": [
        {"name": "test.one_way.exec", "template": "upgrade_damage.png"}]})
    result = director.plan(
        revision(), knowledge=knowledge.KNOWLEDGE, graph=(obj,),
        rates=CurrencyRates(1000., 1, 3, "test"), strategy=matching_strategy)
    candidate = result.candidates[0]
    assert not any("one-way" in r.lower() for r in candidate.held_by)


def test_a_non_matching_strategy_still_holds_the_one_way_objective() -> None:
    """The negative half, pinned directly against the same synthetic
    objective the positive test above uses, rather than only incidentally
    through the real GRAPH."""
    obj = _one_way_objective()
    result = director.plan(
        revision(), knowledge=knowledge.KNOWLEDGE, graph=(obj,),
        rates=CurrencyRates(1000., 1, 3, "test"), strategy=strategy())
    candidate = result.candidates[0]
    assert any("one-way" in r.lower() for r in candidate.held_by)


# -- (2) math.inf must never be confused with None --------------------------
def test_score_of_a_measured_infinite_horizon_is_finite_not_inf_or_nan() -> None:
    """value / math.inf is ordinary float arithmetic (0.0) - never nan,
    never inf itself. A future "helpful" special-case for math.inf is
    exactly the kind of edit that could reintroduce a collapse."""
    result = director.score(1.0, math.inf)
    assert result == 0.0
    assert result is not None
    assert not math.isnan(result)
    assert not math.isinf(result)


def _priced_objective(cost_param: tuple[str, int] = ("coin_cost", 100)) -> objectives.Objective:
    """A minimal synthetic objective with a real currency price - the real
    GRAPH has no coin-priced objective at all (only uw.slot.* is priced, in
    stones, which `rates` never measures), so exercising the coins-horizon
    branches end-to-end through plan() needs a graph of our own."""
    return objectives.Objective(
        id="test.priced", requires=(), grants=(), satisfied_by=lambda r: False,
        actions=(objectives.Action(executor="test.exec", params=(cost_param,)),),
        value=1.0, risk="reversible", knowledge_refs=("tier.2.unlock_wave",),
    )


def test_a_measured_zero_rate_reads_as_never_not_unknown_in_the_plan() -> None:
    """CurrencyRates.coins_per_hour == 0.0 is a MEASUREMENT (three-plus farm
    runs, all zero income) - the horizon is math.inf, and the plan's own
    words must say "never", never "unknown"."""
    obj = _priced_objective()
    rev = revision(inventory=(fact("currencies.coins", 0),))
    result = director.plan(
        rev, knowledge=knowledge.KNOWLEDGE, graph=(obj,),
        rates=CurrencyRates(0.0, 1, 3, "zero-rate fixture"), strategy=strategy())
    candidate = result.candidates[0]
    assert candidate.hours_to_afford == math.inf
    assert "unknown" not in candidate.why.lower()
    assert "never" in candidate.why.lower()


def test_an_unmeasured_rate_reads_as_unknown_and_cites_the_rates_reason() -> None:
    """CurrencyRates.coins_per_hour is None - nobody has measured a baseline
    yet. The horizon is None (never math.inf), the plan's words say
    "unknown", and (item 3) the explanation quotes CurrencyRates.reason
    verbatim rather than a generic placeholder."""
    obj = _priced_objective()
    rev = revision(inventory=(fact("currencies.coins", 0),))
    result = director.plan(
        rev, knowledge=knowledge.KNOWLEDGE, graph=(obj,),
        rates=CurrencyRates(None, 1, 0, "UNMEASURED_BASELINE_TOKEN"), strategy=strategy())
    candidate = result.candidates[0]
    assert candidate.hours_to_afford is None
    assert candidate.score is None
    assert "unknown" in candidate.why.lower()
    assert "UNMEASURED_BASELINE_TOKEN" in candidate.why


# -- (3) CurrencyRates.reason is retained, not discarded ---------------------
def test_the_plan_reason_cites_the_rates_reason_when_nothing_is_top() -> None:
    """coins_per_hour alone cannot explain an unscoreable plan to a human -
    only CurrencyRates.reason can. Plan.reason must carry it forward
    verbatim, not re-derive a generic string that loses the "why". Uses a
    single-objective graph (an unverified, priced, unheld-only-by-price
    objective) so `top` is guaranteed None regardless of GRAPH's real
    contents - the real GRAPH's own "no top" scenarios are already covered
    by the tests above, and this isolates the reason-citation behaviour
    from which objective happens to win the tie-break today."""
    obj = _priced_objective()
    result = director.plan(
        revision(), knowledge=knowledge.KNOWLEDGE, graph=(obj,),
        rates=CurrencyRates(None, 1, 0, "RATES_REASON_TOKEN"), strategy=strategy())
    assert result.top is None
    assert "RATES_REASON_TOKEN" in result.reason


# -- (4) unknown must block: never satisfy, never rank as ready -------------
def test_an_unread_balance_never_produces_a_score() -> None:
    """revision.inventory is None on the live account today (Phase 0b has
    not shipped a writer for it yet). A priced objective's balance must
    stay unread - None - never silently treated as a known zero, even
    though a known zero and an unread balance can look similar downstream.
    An unread balance must never manufacture a knowable horizon."""
    obj = _priced_objective()
    rev = revision()  # inventory=None
    result = director.plan(
        rev, knowledge=knowledge.KNOWLEDGE, graph=(obj,),
        rates=CurrencyRates(1000., 1, 3, "plenty of income"), strategy=strategy())
    candidate = result.candidates[0]
    assert candidate.hours_to_afford is None
    assert candidate.score is None


def test_director_module_imports_nothing_that_touches_a_device() -> None:
    """Enforced, not trusted: an allowlist over director.py's own import
    statements (not its transitive closure), the same discipline
    objectives.py and affordability_horizon.py apply to themselves."""
    import ast
    from pathlib import Path

    source = Path(director.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".")[0])

    allowed = {"__future__", "math", "dataclasses", "knowledge", "objectives",
               "account_state", "affordability_horizon", "progression", "strategy"}
    assert imported <= allowed, imported - allowed
    forbidden = {"device", "shopping", "transactions", "time", "os", "socket",
                 "ultimate_weapons", "cards", "ocr", "screen_discovery", "db",
                 "tower_bot"}
    assert not (imported & forbidden), imported & forbidden
