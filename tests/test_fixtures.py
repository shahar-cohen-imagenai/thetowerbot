"""Fixtures are golden data. If these fail, a template or capture drifted."""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = Path(__file__).parent.parent / "templates"
EXPECTED_RESOLUTION = (1080, 2400)  # width, height


@pytest.mark.parametrize(
    "name", ["main_menu", "in_run_lit", "game_over", "game_over_fade"]
)
def test_fixture_resolution(name: str) -> None:
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    height, width = img.shape[:2]
    assert (width, height) == EXPECTED_RESOLUTION


def test_upgrade_templates_are_lit_not_dimmed() -> None:
    """Regression guard for the originating bug.

    The original templates were cropped from the death modal, where every
    label sits in a dimmed backdrop. A lit crop of these labels measures
    ~50-58 mean grey; the dimmed equivalent measures ~14. If someone re-cuts
    a template from a game-over frame, this catches it.
    """
    for path in sorted(TEMPLATES.glob("upgrade_*.png")):
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        assert img is not None, f"unreadable template: {path}"
        grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean()
        assert grey > 40.0, (
            f"{path.name} has mean grey {grey:.2f} - it looks like it was "
            "cropped from a dimmed frame. Re-cut it from a live run."
        )


# --- Replay coverage ---------------------------------------------------------
#
# B08 asks for one thing the golden checks above cannot give: proof that every
# reader the bot has actually enabled has been shown a state it must NOT act
# on, not merely a state it reads well. A capture of a page working is the
# easy half. The half that decides whether an autonomous purchase loop is safe
# is what the reader does when a row is maxed, when a label is garbled, when a
# control is drawn twice, or when the page it is standing on is not the page it
# thinks it is.
#
# `tests/fixtures/replay/manifest.json` names, for each enabled capability,
# three examples in one vocabulary:
#
#   positive    - at least one observation the runtime may act on or record.
#   unavailable - at least one observation the reader names as present and NOT
#                 actionable (locked, maxed, unavailable, insufficient_data,
#                 absent). Never an error, never a target.
#   ambiguous   - at least one observation the reader read and could not trust
#                 (unreadable, ambiguous, uncatalogued), or a page it refused
#                 outright. Never a target, never a trusted value.
#
# An example is `recorded` when a capture of that state exists, and `derived`
# when it is built by editing the OCR of a recorded capture. Derived examples
# are the established idiom in tests/test_screen_discovery.py; what is new here
# is that each one declares the exact box it edits and what that box said, so a
# derived example cannot quietly drift into fiction.

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import config
import ocr

REPLAY = FIXTURES / "replay" / "manifest.json"
ACCOUNT_SNAPSHOTS = FIXTURES / "account"
NOW = 1_700_000_000.0

# Classes an example may claim, and the reader statuses that satisfy each.
# 'unknown' is deliberately in none of them: a card nobody looked at is unseen,
# which is neither a refusal to act nor a failed read.
_SATISFIES = {
    "positive": ("available", "complete", "observed", "located"),
    "unavailable": ("locked", "maxed", "unavailable", "insufficient_data", "absent"),
    "ambiguous": ("unreadable", "ambiguous", "uncatalogued", "refused"),
}

# The two statuses that may still carry a value while the observation is not
# actionable. 'uncatalogued' is a row whose PRICE read perfectly and whose
# identity did not, so suppressing the price would throw away a good read to
# punish a bad one. 'insufficient_data' is the game writing "Need more data"
# in the value's own place - the reader keeps those words verbatim rather
# than replacing them with a number nobody was given. Every other
# not-actionable status must leave the value absent, never zero.
_VALUE_ALLOWED = ("uncatalogued", "insufficient_data")


@dataclass(frozen=True)
class Replay:
    """One frame, read through one capability, in a shared vocabulary.

    Keeping the four buckets apart is the whole point: a reader that answers
    "I cannot see it" and a reader that answers "it is not buyable" have said
    two different things, and only `targets` may ever reach a device.
    """

    screen_id: str | None
    positive: dict[str, str] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)
    ambiguous: dict[str, str] = field(default_factory=dict)
    targets: dict[str, tuple[int, int]] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)

    def bucket(self, name: str) -> dict[str, str]:
        return getattr(self, name)


def manifest() -> dict[str, Any]:
    """The declared coverage. Absent means the gate below fails, loudly."""
    if not REPLAY.exists():
        return {"capabilities": {}, "sequences": []}
    return json.loads(REPLAY.read_text())


def _boxes(name: str, folder: str) -> tuple[ocr.TextBox, ...]:
    path = FIXTURES / folder / f"{name}.json"
    return tuple(ocr.TextBox(b["text"], b["confidence"], config.Rect(*b["rect"]))
                 for b in json.loads(path.read_text()))


def _frame(name: str, folder: str):
    path = FIXTURES / (f"{name}.png" if folder == "ocr" else f"{folder}/{name}.png")
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert image is not None, f"missing capture: {path}"
    return image


def _edited(boxes: tuple[ocr.TextBox, ...],
            edits: tuple[dict[str, Any], ...]) -> tuple[ocr.TextBox, ...]:
    """Apply declared edits, refusing any that does not match what it claims.

    Every edit names the exact rect it touches AND the text that rect held.
    An edit whose rect has moved, or whose text has changed, is a manifest
    that no longer describes the capture underneath it, and this raises rather
    than silently deriving a different example.
    """
    for edit in edits:
        target = tuple(edit["at"])
        matches = [b for b in boxes
                   if (b.rect.x, b.rect.y, b.rect.w, b.rect.h) == target]
        assert len(matches) == 1, f"edit at {target} matched {len(matches)} boxes"
        assert matches[0].text == edit["was"], (
            f"edit at {target} expected {edit['was']!r}, capture says "
            f"{matches[0].text!r}")
        replaced: list[ocr.TextBox] = []
        for box in boxes:
            if (box.rect.x, box.rect.y, box.rect.w, box.rect.h) != target:
                replaced.append(box)
                continue
            if edit.get("drop"):
                continue
            replaced.append(ocr.TextBox(
                edit.get("set_text", box.text),
                edit.get("set_confidence", box.confidence), box.rect))
            if "duplicate_at" in edit:
                replaced.append(ocr.TextBox(box.text, box.confidence,
                                            config.Rect(*edit["duplicate_at"])))
        boxes = tuple(replaced)
    return boxes


def read_example(spec: dict[str, Any], capability: dict[str, Any]) -> Replay:
    """Replay one manifest example through its capability's adapter."""
    folder = spec.get("folder", capability.get("folder", "ocr"))
    boxes_folder = "account_screens" if folder == "account_screens" else "ocr"
    boxes = _boxes(spec.get("boxes", spec["frame"]), boxes_folder)
    boxes = _edited(boxes, tuple(spec.get("edits", ())))
    return _ADAPTERS[capability["adapter"]](
        _frame(spec["frame"], folder), boxes, capability)


def _grid(frame, boxes, capability) -> Replay:
    import perception
    import screen_discovery
    context = capability["context"]
    discovery = screen_discovery.discover(frame, boxes, context)
    rows = perception.parse_frame(frame, boxes, context).rows
    positive, unavailable, ambiguous, targets, values = {}, {}, {}, {}, {}
    for row in rows:
        # An uncatalogued row reads as 'available' and is denied a tap. That
        # is not availability - it is the reader saying it saw something it
        # cannot name - so it is recorded as ambiguous, by the same fact that
        # denies it a target.
        if row.upgrade_id.startswith("discovered:"):
            ambiguous[row.upgrade_id] = "uncatalogued"
        elif row.status in _SATISFIES["unavailable"]:
            unavailable[row.upgrade_id] = row.status
        elif row.status in _SATISFIES["ambiguous"]:
            ambiguous[row.upgrade_id] = row.status
        elif row.tap is not None:
            positive[row.upgrade_id] = row.status
        if row.tap is not None:
            targets[row.upgrade_id] = row.tap
        if row.price is not None:
            values[row.upgrade_id] = row.price
    return Replay(discovery.screen_id if discovery.readable else None,
                  positive, unavailable, ambiguous, targets, values)


def _missions(frame, boxes, capability) -> Replay:
    import missions_screen
    reading = missions_screen.parse_frame(frame, boxes, now=NOW)
    if reading is None:
        return Replay(None, ambiguous={"missions.page": "refused"})
    positive, ambiguous, values = {}, {}, {}
    for index, mission in enumerate(reading.missions):
        # A card whose text was not trusted has NO identity - see MissionEntry
        # - so it is addressed by where it sits. Giving it a made-up id here
        # would be the guess the reader refused to make.
        if mission.status in _SATISFIES["positive"]:
            positive[f"mission:{mission.mission_id}"] = mission.status
            values[f"mission:{mission.mission_id}"] = (mission.progress, mission.target)
        else:
            ambiguous[f"card:{index}"] = mission.status
    unavailable = {f"milestone:{m.threshold}": m.status for m in reading.milestones
                   if m.status in _SATISFIES["unavailable"]}
    # Deliberately empty. Claiming a mission is T01's, and a reader that
    # published a claim point would be that task landing here by accident.
    return Replay(reading.screen_id, positive, unavailable, ambiguous, {}, values)


def _cards(frame, boxes, capability) -> Replay:
    import cards as cards_module
    reading = cards_module.parse_frame(frame, boxes, now=NOW)
    if reading is None:
        return Replay(None, ambiguous={"cards.page": "refused"})
    positive, unavailable, values = {}, {}, {}
    if reading.slots.status == "observed":
        positive["cards.slots"] = reading.slots.status
        values["cards.slots"] = (reading.slots.equipped, reading.slots.capacity)
    if reading.slots.next_slot == "locked":
        unavailable["cards.next_slot"] = "locked"
    return Replay(reading.screen_id, positive, unavailable, {},
                  {f"action:{index}": a for index, a in
                   enumerate(cards_module.actions(reading))}, values)


def _account(frame, boxes, capability) -> Replay:
    import account_screens
    reading = account_screens.parse_frame(frame, boxes, now=NOW)
    if reading is None:
        return Replay(None, ambiguous={"account.page": "refused"})
    observations: list[tuple[str, str, Any]] = [
        (f"field:{f.key}", f.status, f.raw_value) for f in reading.fields]
    for tier in reading.tiers:
        for cell in (tier.wave, tier.coins, tier.cells):
            observations.append(
                (f"tier:{tier.tier}.{cell.key}", cell.status, cell.raw_value))
    controls = account_screens.control_targets(reading.screen_id, boxes)
    observations += [(f"control:{name}", t.status, None)
                     for name, t in controls.items()]
    positive, unavailable, ambiguous, values = {}, {}, {}, {}
    for key, status, raw in observations:
        if status in _SATISFIES["positive"]:
            positive[key] = status
            if raw is not None:
                values[key] = raw
        elif status in _SATISFIES["unavailable"]:
            unavailable[key] = status
        elif status in _SATISFIES["ambiguous"]:
            ambiguous[key] = status
    targets = {f"control:{name}": t.point for name, t in controls.items()
               if t.point is not None}
    return Replay(reading.screen_id, positive, unavailable, ambiguous, targets, values)


_ADAPTERS: dict[str, Callable[..., Replay]] = {
    "grid": _grid, "missions": _missions, "cards": _cards, "account": _account,
}


def enabled_capabilities() -> set[str]:
    import screen_discovery
    matrix = screen_discovery.capabilities()
    return set(matrix["readers"]) | set(matrix["account_screens"])


def test_the_replay_manifest_names_exactly_the_capabilities_that_are_enabled() -> None:
    """The list cannot be a subset anyone forgot to extend.

    A reader added to the support matrix without an entry here would be an
    enabled capability with no proof of what it does on a state it must not
    act on, which is the gap this fixture task exists to close.
    """
    assert set(manifest()["capabilities"]) == enabled_capabilities()


def _examples() -> list[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    out = []
    for name, capability in sorted(manifest()["capabilities"].items()):
        for kind in ("positive", "unavailable", "ambiguous"):
            spec = capability["examples"][kind]
            if spec.get("kind") != "gap":
                out.append((name, kind, spec, capability))
    return out


@pytest.mark.parametrize("name,kind,spec,capability", _examples(),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_every_declared_example_shows_the_state_it_claims(
    name: str, kind: str, spec: dict[str, Any], capability: dict[str, Any],
) -> None:
    replay = read_example(spec, capability)
    observation = spec["observation"]
    bucket = replay.bucket(kind)
    assert observation in bucket, (
        f"{name}.{kind}: {observation} is not {kind} - "
        f"positive={sorted(replay.positive)} unavailable={sorted(replay.unavailable)} "
        f"ambiguous={sorted(replay.ambiguous)}")
    assert bucket[observation] == spec["expect_status"]


@pytest.mark.parametrize("name,kind,spec,capability",
                         [row for row in _examples() if row[1] != "positive"],
                         ids=lambda v: v if isinstance(v, str) else "")
def test_a_state_the_reader_cannot_act_on_never_becomes_a_target(
    name: str, kind: str, spec: dict[str, Any], capability: dict[str, Any],
) -> None:
    """The acceptance gate's teeth.

    Naming a row 'maxed' and then handing the purchase loop a point inside it
    is worse than not reading the row at all, because everything downstream
    would be entitled to trust it.
    """
    replay = read_example(spec, capability)
    observation = spec["observation"]
    assert observation not in replay.targets, (
        f"{name}.{kind}: {observation} is {spec['expect_status']} and was still "
        "handed a point to tap")
    if spec["expect_status"] not in _VALUE_ALLOWED:
        assert observation not in replay.values, (
            f"{name}.{kind}: {observation} was reported with a value it could "
            "not read")


def test_a_capability_with_no_example_of_a_state_declares_it_as_an_owned_gap() -> None:
    """A missing example must be a stated finding, not a blank cell.

    account.stats.tiers is the one: the game draws '0' in a tier nobody has
    reached and drops the cell entirely when it will not read, and the reader
    calls both 'unreadable'. So there is no state on that screen that means
    "present and not usable", and inventing one - a tier cell edited to say
    something the game has never been seen to say - would put fiction in the
    fixture set. The honest entry is a gap with an owner.
    """
    import screen_discovery
    gaps = {f"{name}.{kind}": spec
            for name, capability in manifest()["capabilities"].items()
            for kind, spec in capability["examples"].items()
            if spec.get("kind") == "gap"}
    assert gaps, "the gap table is what keeps a blank cell from passing"
    for key, spec in gaps.items():
        assert spec["why"] and spec["owner"]
    assert screen_discovery.capabilities()["replay_coverage"]["gaps"] == {
        key: spec["owner"] for key, spec in gaps.items()}


def test_the_support_matrix_and_the_manifest_cannot_drift_apart() -> None:
    import screen_discovery
    coverage = screen_discovery.capabilities()["replay_coverage"]
    assert coverage["manifest"] == "tests/fixtures/replay/manifest.json"
    assert (FIXTURES.parent.parent / coverage["manifest"]).exists()
    assert (FIXTURES.parent.parent / coverage["account_snapshots"]).is_dir()
    covered = {name for name, capability in manifest()["capabilities"].items()
               if all(spec.get("kind") != "gap"
                      for spec in capability["examples"].values())}
    assert set(coverage["covered"]) == covered
    assert set(coverage["covered"]) <= enabled_capabilities()


def test_no_example_or_sequence_claims_a_capability_the_matrix_calls_unsupported() -> None:
    import screen_discovery
    unsupported = set(screen_discovery.capabilities()["unsupported"])
    data = manifest()
    assert not set(data["capabilities"]) & unsupported
    for sequence in data["sequences"]:
        for step in sequence["steps"]:
            assert step["capability"] in data["capabilities"]


@pytest.mark.parametrize("name,kind,spec,capability",
                         [row for row in _examples() if row[2].get("edits")],
                         ids=lambda v: v if isinstance(v, str) else "")
def test_a_derived_example_edits_only_the_boxes_it_declares(
    name: str, kind: str, spec: dict[str, Any], capability: dict[str, Any],
) -> None:
    """Provenance, so a derived example stays tied to a real capture.

    _edited() already refuses an edit whose rect or text has moved. This adds
    the other half: the derived boxes may differ from the recording ONLY at
    the rects the manifest names, so an example cannot grow a second, quiet
    change that is doing the actual work.
    """
    assert spec["recording"] == "derived", (
        f"{name}.{kind} edits a capture while claiming to be a recording")
    folder = spec.get("folder", capability.get("folder", "ocr"))
    original = _boxes(spec.get("boxes", spec["frame"]),
                      "account_screens" if folder == "account_screens" else "ocr")
    derived = _edited(original, tuple(spec["edits"]))
    declared = {tuple(edit["at"]) for edit in spec["edits"]}
    declared |= {tuple(edit["duplicate_at"]) for edit in spec["edits"]
                 if "duplicate_at" in edit}
    def outside(boxes):
        return sorted((b.text, b.confidence, b.rect.x, b.rect.y, b.rect.w, b.rect.h)
                      for b in boxes
                      if (b.rect.x, b.rect.y, b.rect.w, b.rect.h) not in declared)
    assert outside(original) == outside(derived)


# --- Before and after --------------------------------------------------------

def _sequence_side(step: dict[str, Any], data: dict[str, Any]) -> Replay:
    """Read one side of a pair through the capability that step names."""
    return read_example(step, data["capabilities"][step["capability"]])


@pytest.mark.parametrize("sequence", manifest()["sequences"],
                         ids=lambda s: s["name"])
def test_a_recorded_action_sequence_replays_the_change_it_declares(
    sequence: dict[str, Any],
) -> None:
    """Two captures, the action between them, and what must have changed.

    A pair of frames is only evidence if the reader is made to say what
    changed. The five shapes below are the ones the recordings actually
    support: a row that advanced, a tab that restocked, a page that revealed
    more of itself, a menu that led somewhere else, and a grid that went
    blind.
    """
    data = manifest()
    before_step, after_step = sequence["steps"]
    before = _sequence_side(before_step, data)
    after = _sequence_side(after_step, data)
    expect = sequence["expect"]

    assert before.screen_id == before_step["capability"]
    if expect["kind"] == "blind":
        assert after.screen_id is None
        assert not after.positive and not after.targets
        return
    assert after.screen_id == after_step["capability"]

    if expect["kind"] == "advanced":
        for key, change in expect["observations"].items():
            # Both prices are read off their own frame. The declared pair is
            # what makes a stale binding visible: a target taken from the
            # earlier frame would still buy, and would buy at a price that no
            # longer exists.
            assert before.values[key] == change["price_before"]
            assert after.values[key] == change["price_after"]
            assert change["price_after"] > change["price_before"], (
                f"{key} is claimed to have advanced without getting dearer")
            assert key in before.targets and key in after.targets
    elif expect["kind"] == "restocked":
        for key in expect["gone"]:
            assert key in before.positive and key not in after.positive
        for key in expect["appeared"]:
            assert key not in before.positive and key in after.positive
    elif expect["kind"] == "revealed":
        assert len(after.positive) + len(after.ambiguous) > len(before.positive) + len(before.ambiguous)
    elif expect["kind"] == "navigated":
        assert after.screen_id != before.screen_id
    else:
        raise AssertionError(f"unknown expectation {expect['kind']!r}")


@pytest.mark.parametrize("sequence", manifest()["sequences"],
                         ids=lambda s: s["name"])
def test_a_sequence_takes_one_action_and_takes_it_from_the_before_frame_alone(
    sequence: dict[str, Any],
) -> None:
    """Fresh, screen-specific evidence, stated as a property of the pair.

    The action's point has to exist in the BEFORE frame's own reading and to
    sit inside the observation that produced it. A point carried in from the
    after frame - or from the manifest - would be exactly the stale-evidence
    tap the runtime rules forbid.
    """
    data = manifest()
    before_step, _ = sequence["steps"]
    before = _sequence_side(before_step, data)
    action = sequence["action"]
    assert isinstance(action, dict) and "kind" in action
    if action["kind"] == "recorded_by_hand":
        # The interval between these two captures was not driven by a reader,
        # and saying so is the point: a scroll has no published target, and
        # the info panel opened on a tap the reader does not publish at all.
        assert action["note"] and "point" not in action
        return
    key = action["observation"]
    assert key in before.targets, f"{sequence['name']}: nothing on the before frame offers {key}"
    assert tuple(action["point"]) == before.targets[key]


# --- Account snapshots -------------------------------------------------------

def _snapshot(name: str) -> dict[str, Any]:
    import account_screens
    frame = _frame(name, "account_screens")
    boxes = _boxes(name, "account_screens")
    reading = account_screens.parse_frame(frame, boxes, now=NOW)
    assert reading is not None, f"{name} no longer parses as an account screen"
    return {
        "screen_id": reading.screen_id,
        "frame_digest": reading.frame_digest,
        "fields": [[f.key, f.raw_value, f.status] for f in reading.fields],
        "tiers": [[t.tier] + [[c.key, c.raw_value, c.status]
                              for c in (t.wave, t.coins, t.cells)]
                  for t in reading.tiers],
        "controls": {n: [t.status, list(t.point) if t.point else None]
                     for n, t in account_screens.control_targets(
                         reading.screen_id, boxes).items()},
    }


@pytest.mark.parametrize("name", sorted(
    p.stem for p in (FIXTURES / "account_screens").glob("*.png")))
def test_every_recorded_account_capture_has_a_golden_snapshot(name: str) -> None:
    """The account subsystem's before/after evidence, frozen.

    A capture on its own says nothing about whether the reader still reads it
    the same way. The snapshot pins the whole reading, digest included, so a
    change to account_screens.py that quietly re-classifies a field shows up
    here rather than in a run.
    """
    path = ACCOUNT_SNAPSHOTS / f"{name}.json"
    assert path.exists(), f"no golden snapshot for {name}"
    assert json.loads(path.read_text()) == _snapshot(name)
