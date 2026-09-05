"""Focused checks for the recorded Daily Missions reader.

Every case is driven from the committed 1080x2400 capture
`tests/fixtures/menu_missions.png` and its recorded OCR, mutated in place the
way the existing screen-discovery tests mutate recorded boxes. Nothing here
claims coverage of an unlock stage that was never captured.
"""
from __future__ import annotations

import json
from dataclasses import fields, replace
from pathlib import Path

import cv2
import pytest

import config
import missions_screen
import ocr
from device import Image

FIXTURES = Path(__file__).parent / 'fixtures'

# Measured on menu_missions.png: the two mission cards tiles.candidates finds.
CARD_ONE = config.Rect(17, 738, 1046, 242)
CARD_TWO = config.Rect(17, 1003, 1046, 242)


def frame() -> Image:
    return cv2.imread(str(FIXTURES / 'menu_missions.png'))


def recorded(name: str = 'menu_missions') -> tuple[ocr.TextBox, ...]:
    return tuple(ocr.TextBox(b['text'], b['confidence'], config.Rect(*b['rect']))
                 for b in json.loads((FIXTURES / 'ocr' / f'{name}.json').read_text()))


def without(boxes: tuple[ocr.TextBox, ...], *texts: str) -> tuple[ocr.TextBox, ...]:
    return tuple(b for b in boxes if b.text not in texts)


def without_in(boxes: tuple[ocr.TextBox, ...], card: config.Rect,
               *texts: str) -> tuple[ocr.TextBox, ...]:
    """Drop these texts from ONE card only.

    '25' and '3' appear on both mission cards AND in the milestone strip, so
    a frame-wide drop would change three things and describe one.
    """
    return tuple(b for b in boxes if not (
        b.text in texts and card.y <= b.rect.y < card.y + card.h))


def retext(boxes: tuple[ocr.TextBox, ...], old: str, new: str,
           confidence: float | None = None) -> tuple[ocr.TextBox, ...]:
    return tuple(replace(b, text=new,
                         confidence=b.confidence if confidence is None else confidence)
                 if b.text == old else b for b in boxes)


def moved(box: ocr.TextBox, dy: int) -> ocr.TextBox:
    """The same box, dy pixels down. config.Rect is a NamedTuple, not frozen."""
    return replace(box, rect=config.Rect(box.rect.x, box.rect.y + dy, box.rect.w, box.rect.h))


def in_card(boxes: tuple[ocr.TextBox, ...], card: config.Rect) -> tuple[ocr.TextBox, ...]:
    return tuple(b for b in boxes if card.y <= b.rect.y < card.y + card.h)


def read(boxes: tuple[ocr.TextBox, ...] | None = None) -> missions_screen.MissionsReading | None:
    return missions_screen.parse_frame(frame(), recorded() if boxes is None else boxes, now=100.)


def entry(reading: missions_screen.MissionsReading,
          mission_id: str) -> missions_screen.MissionEntry:
    return next(m for m in reading.missions if m.mission_id == mission_id)


def as_mission(boxes: tuple[ocr.TextBox, ...], card: config.Rect,
               text: str, bar: str) -> tuple[ocr.TextBox, ...]:
    """Rewrite one card's mission text and progress bar, leaving the rest."""
    inside = in_card(boxes, card)
    old_text = next(b.text for b in inside if b.rect.x < card.x + card.w * .85
                    and b.rect.y < card.y + card.h * .5)
    old_bar = next(b.text for b in inside if '/' in b.text)
    return retext(retext(boxes, old_text, text), old_bar, bar)


# --- identity -------------------------------------------------------------

def test_recorded_missions_page_resolves_its_own_screen_id() -> None:
    import screen_discovery
    result = screen_discovery.discover(frame(), recorded(), 'missions')
    assert result == screen_discovery.ScreenDiscovery('missions.daily', True, 'recorded_layout')


def test_missions_page_is_not_readable_as_an_upgrade_screen() -> None:
    """The upgrade contexts must not claim a page with no upgrade heading."""
    import perception
    import screen_discovery
    for context in ('workshop', 'battle'):
        assert not screen_discovery.discover(frame(), recorded(), context).readable
        assert perception.parse_frame(frame(), recorded(), context).rows == ()


def test_a_workshop_capture_is_never_read_as_the_missions_page() -> None:
    import screen_discovery
    workshop = cv2.imread(str(FIXTURES / 'menu_workshop_attack.png'))
    boxes = recorded('menu_workshop_attack')
    assert not screen_discovery.discover(workshop, boxes, 'missions').readable
    import missions_screen
    assert missions_screen.parse_frame(workshop, boxes, now=100.) is None


# --- success --------------------------------------------------------------

def test_recorded_missions_are_read_by_identity_with_progress_and_rewards() -> None:
    reading = read()
    assert reading.screen_id == 'missions.daily'
    assert reading.completed == 0 and reading.completed_target == 35
    assert reading.shown == 2 and reading.offered == 8 and reading.unseen == 6
    assert len(reading.missions) == 2

    cards = entry(reading, 'buy_cards')
    assert (cards.raw_text, cards.progress, cards.target) == ('Buy 1 cards', 0, 1)
    assert cards.status == 'available'
    assert cards.reward_values == (25, 3) and cards.rewards_status == 'observed'

    # The engine garbles the first card's text on this capture, so nothing
    # here pins its spelling: an engine that later reads the string correctly
    # must improve the result, not break this test. What the identity rule
    # itself guarantees is pinned by the parametrised and end-to-end checks
    # below, which a strip-every-digit rule fails.
    kills = reading.missions[0]
    assert kills.raw_text.startswith('Kill')
    assert (kills.progress, kills.target, kills.status) == (0, 200, 'available')
    assert kills.mission_id not in (None, 'buy_cards')


def test_a_garbled_read_can_never_masquerade_as_the_clean_mission() -> None:
    """Real evidence: 'Kill 200 basic enemies' reads as 'Kill zo0basic enemies'.

    The misread must land on its own identity rather than silently claiming
    the identity of the mission it was meant to be.
    """
    clean = read(retext(recorded(), 'Kill zo0basic enemies', 'Kill 200 basic enemies'))
    assert entry(clean, 'kill_basic_enemies').target == 200
    assert read().status_for('kill_basic_enemies') == 'unseen'


# --- a digit that is not the goal is part of the subject ------------------

@pytest.mark.parametrize('text,target,expected', [
    # The goal count is dropped, so the same daily asking for more is one id.
    ('Kill 200 basic enemies', 200, 'kill_basic_enemies'),
    ('Kill 500 basic enemies', 500, 'kill_basic_enemies'),
    ('Buy 1 cards', 1, 'buy_cards'),
    ('Buy 5 cards', 5, 'buy_cards'),
    # A digit that is NOT the goal names what the mission is about, and must
    # survive: these two objectives may never collapse onto one id.
    ('Reach wave 100 in tier 3', 100, 'reach_wave_in_tier_3'),
    ('Reach wave 100 in tier 5', 100, 'reach_wave_in_tier_5'),
    # No unique goal match - a garbled count, or a count that appears twice -
    # keeps every digit. Too specific splits one mission honestly in two;
    # too loose would merge two missions and say nothing.
    ('Kill zo0basic enemies', 200, 'kill_zo_0_basic_enemies'),
    ('Kill 5 enemies in 5 waves', 5, 'kill_5_enemies_in_5_waves'),
    # No progress bar is NOT that case: with no target there is no evidence
    # at all about which digit is the quantity, so a text carrying digits has
    # no identity rather than one asserted from an unsupported assumption.
    ('Buy 1 cards', None, None),
    ('Reach wave 100 in tier 3', None, None),
    # ...but a text with no digits has nothing to split, target or not.
    ('Watch an ad', None, 'watch_an_ad'),
    ('Watch an ad', 3, 'watch_an_ad'),
    # A text with no word left names nothing and would collide with the next
    # numeric garble, so it has no identity either.
    ('200 300', 5, None),
    ('300', 300, None),
    ('', 1, None),
])
def test_only_the_goal_count_is_dropped_from_an_identity(
    text: str, target: int | None, expected: str | None,
) -> None:
    assert missions_screen.identity_of(text, target) == expected


def test_a_numeric_garble_is_unreadable_rather_than_a_bare_number_id() -> None:
    """End to end: a card whose text read as digits names no mission.

    A bare-number id would report `available` for something nobody can act
    on, and would collide with the next card the engine garbles the same way.
    """
    reading = read(as_mission(recorded(), CARD_TWO, '200 300', '0/5'))
    row = reading.missions[1]
    assert row.raw_text == '200 300'
    assert row.mission_id is None and row.status == 'unreadable'
    # An unidentified card on the page also means absence is no longer
    # knowable for anything else.
    assert reading.status_for('buy_cards') == 'unreadable'


def test_two_missions_differing_only_by_a_subject_digit_stay_apart() -> None:
    """End to end: the tier-3 and tier-5 dailies are two rows, not one.

    Stripping every digit made both `reach_wave_in_tier`, which this reader
    would report as `ambiguous` on one frame and - far worse - as the same
    mission across two frames.
    """
    boxes = as_mission(recorded(), CARD_ONE, 'Reach wave 100 in tier 3', '0/100')
    boxes = as_mission(boxes, CARD_TWO, 'Reach wave 100 in tier 5', '0/100')
    reading = read(boxes)
    assert [m.mission_id for m in reading.missions] == [
        'reach_wave_in_tier_3', 'reach_wave_in_tier_5']
    assert [m.status for m in reading.missions] == ['available', 'available']
    assert reading.status_for('reach_wave_in_tier') == 'unseen'


def test_a_goal_that_is_also_the_subject_shares_an_id_and_differs_by_target() -> None:
    """'Reach tier 5' and 'Reach tier 10' are one recurring objective here.

    The goal digit IS the subject - the bar literally counts tiers - so
    dropping it leaves `reach_tier` for every tier. That is the documented
    limit of `mission_id` alone, and status_for takes the target so a caller
    that cares which objective it is can actually ask.
    """
    five = read(as_mission(recorded(), CARD_ONE, 'Reach tier 5', '0/5'))
    ten = read(as_mission(recorded(), CARD_ONE, 'Reach tier 10', '0/10'))
    assert five.missions[0].mission_id == ten.missions[0].mission_id == 'reach_tier'
    assert (five.missions[0].target, ten.missions[0].target) == (5, 10)
    # The objective query separates them; the bare id deliberately does not.
    assert five.status_for('reach_tier', 5) == 'available'
    assert five.status_for('reach_tier', 10) == 'unseen'
    assert ten.status_for('reach_tier', 10) == 'available'
    assert ten.status_for('reach_tier', 5) == 'unseen'
    assert five.status_for('reach_tier') == ten.status_for('reach_tier') == 'available'


def test_two_tiers_on_one_frame_are_refused_rather_than_merged() -> None:
    """The same-frame case: both cards share `reach_tier`, so the frame refuses.

    Ambiguity is a refusal, not a merge - and asking by objective must not
    smuggle a definite answer past it either.
    """
    boxes = as_mission(recorded(), CARD_ONE, 'Reach tier 5', '0/5')
    boxes = as_mission(boxes, CARD_TWO, 'Reach tier 10', '0/10')
    reading = read(boxes)
    assert [m.mission_id for m in reading.missions] == ['reach_tier', 'reach_tier']
    assert [m.status for m in reading.missions] == ['ambiguous', 'ambiguous']
    assert [m.target for m in reading.missions] == [5, 10]
    assert reading.status_for('reach_tier') == 'ambiguous'
    assert reading.status_for('reach_tier', 5) == 'ambiguous'
    assert reading.status_for('reach_tier', 10) == 'ambiguous'


# --- not keyed off row position -------------------------------------------
#
# These two checks show the reader binds every number to the card its TEXT
# came from, so it cannot be keying off row index. They are NOT a second
# recorded ordering: the boxes are the recorded ones translated +/-265px
# between the two card rectangles, and the recorded page holds one ordering
# of two missions. B04's "stable IDs without fixed row ordering" is therefore
# consistent with this evidence, not demonstrated by it - a multi-mission
# capture of the same set in a different order is what would settle it. The
# same limitation is declared in screen_discovery.capabilities()['unproven'].


def test_identity_follows_the_text_when_card_contents_are_translated() -> None:
    """Move each card's boxes into the other rectangle; ids move with them."""
    boxes = retext(recorded(), 'Kill zo0basic enemies', 'Kill 200 basic enemies')
    offset = CARD_TWO.y - CARD_ONE.y
    elsewhere = tuple(b for b in boxes if b not in in_card(boxes, CARD_ONE)
                      and b not in in_card(boxes, CARD_TWO))
    swapped = (elsewhere
               + tuple(moved(b, offset) for b in in_card(boxes, CARD_ONE))
               + tuple(moved(b, -offset) for b in in_card(boxes, CARD_TWO)))
    reading = read(swapped)
    assert [m.mission_id for m in reading.missions] == ['buy_cards', 'kill_basic_enemies']
    assert entry(reading, 'kill_basic_enemies').target == 200
    assert entry(reading, 'buy_cards').target == 1


def test_a_card_translated_down_a_slot_keeps_its_identity_and_goal() -> None:
    """A new mission taking the top slot pushes the rest down a card.

    The moved card must keep its identity, its goal and its status: what a
    later step would act on has to follow the text, not the slot. Synthetic
    for the same reason as the check above - the recorded page has one
    ordering - so this shows the binding, not stability across a real reorder.
    """
    boxes = retext(recorded(), 'Kill zo0basic enemies', 'Kill 200 basic enemies')
    before = read(boxes)
    offset = CARD_TWO.y - CARD_ONE.y
    elsewhere = tuple(b for b in boxes if b not in in_card(boxes, CARD_ONE)
                      and b not in in_card(boxes, CARD_TWO))
    pushed = tuple(moved(b, offset) for b in in_card(boxes, CARD_ONE))
    arrived = retext(retext(in_card(boxes, CARD_ONE), 'Kill 200 basic enemies', 'Watch 3 ads'),
                     '0/200', '0/3')
    after = read(elsewhere + arrived + pushed)

    assert [m.mission_id for m in after.missions] == ['watch_ads', 'kill_basic_enemies']
    moved_row, was = entry(after, 'kill_basic_enemies'), entry(before, 'kill_basic_enemies')
    assert (moved_row.target, moved_row.progress, moved_row.status) == (
        was.target, was.progress, was.status)
    assert moved_row.rect != was.rect  # it really did change slot
    # The mission pushed off the bottom is unseen, never done and never zero.
    assert after.status_for('buy_cards') == 'unseen'


def test_the_target_count_is_not_part_of_the_identity() -> None:
    """A daily mission that returns with a bigger goal keeps its identity."""
    boxes = retext(retext(recorded(), 'Buy 1 cards', 'Buy 5 cards'), '0/1', '0/5')
    assert entry(read(boxes), 'buy_cards').target == 5


# --- locked ---------------------------------------------------------------

def test_weekly_milestones_are_locked_from_the_completed_count_not_from_icons() -> None:
    reading = read()
    assert [(m.threshold, m.status) for m in reading.milestones] == [
        (5, 'locked'), (10, 'locked'), (15, 'locked'), (20, 'locked'), (25, 'locked')]
    # The strip runs off the right edge of the capture, so this is not the
    # whole ladder and the reading must say so.
    assert reading.complete is False


def test_an_unreadable_completed_count_never_becomes_zero_or_locked() -> None:
    reading = read(without(recorded(), 'completed 0/35'))
    assert reading.completed is None and reading.completed_target is None
    assert {m.status for m in reading.milestones} == {'unreadable'}


# --- unavailable / maxed --------------------------------------------------

def test_a_reached_milestone_is_unlocked_and_stays_distinct_from_locked() -> None:
    reading = read(retext(recorded(), 'completed 0/35', 'completed 20/35'))
    assert [(m.threshold, m.status) for m in reading.milestones] == [
        (5, 'unlocked'), (10, 'unlocked'), (15, 'unlocked'),
        (20, 'unlocked'), (25, 'locked')]


def test_a_finished_mission_is_complete_not_available_and_offers_no_action() -> None:
    reading = read(retext(recorded(), '0/1', '1/1'))
    cards = entry(reading, 'buy_cards')
    assert cards.status == 'complete'
    assert (cards.progress, cards.target) == (1, 1)
    assert not hasattr(cards, 'tap')


def test_missions_not_on_the_page_are_unseen_rather_than_locked_or_absent() -> None:
    reading = read()
    assert reading.status_for('buy_cards') == 'available'
    # A real id the reader could emit for a mission that is simply not drawn
    # here - 'Collect 4 gems' against a 0/4 bar - rather than a shape the
    # reader can never produce, which would pass for any string at all.
    assert missions_screen.identity_of('Collect 4 gems', 4) == 'collect_gems'
    assert reading.status_for('collect_gems') == 'unseen'
    assert reading.unseen == 6


# --- ambiguous ------------------------------------------------------------

def test_two_cards_with_one_identity_are_ambiguous_not_silently_merged() -> None:
    reading = read(as_mission(recorded(), CARD_ONE, 'Buy 1 cards', '0/1'))
    assert [m.mission_id for m in reading.missions] == ['buy_cards', 'buy_cards']
    assert [m.status for m in reading.missions] == ['ambiguous', 'ambiguous']
    assert reading.status_for('buy_cards') == 'ambiguous'


def test_a_duplicated_milestone_threshold_is_unreadable_not_doubled() -> None:
    reading = read(retext(recorded(), '10', '5'))
    fives = [m for m in reading.milestones if m.threshold == 5]
    assert len(fives) == 2 and {m.status for m in fives} == {'unreadable'}


def test_two_page_titles_stop_the_reader_before_any_row_is_exposed() -> None:
    import screen_discovery
    boxes = recorded() + (ocr.TextBox('DAILYMISSIONS', .99, config.Rect(32, 249, 433, 40)),)
    assert not screen_discovery.discover(frame(), boxes, 'missions').readable
    assert read(boxes) is None


# --- unreadable -----------------------------------------------------------

@pytest.mark.parametrize('confidence', [.5, .89])
def test_low_confidence_mission_text_yields_no_identity(confidence: float) -> None:
    boxes = retext(recorded(), 'Buy 1 cards', 'Buy 1 cards', confidence)
    row = next(m for m in read(boxes).missions if m.raw_text == 'Buy 1 cards')
    assert row.status == 'unreadable' and row.mission_id is None


def test_a_missing_progress_bar_is_unknown_and_never_zero() -> None:
    reading = read(without(recorded(), '0/1'))
    row = next(m for m in reading.missions if m.raw_text == 'Buy 1 cards')
    assert row.progress is None and row.target is None
    assert row.status == 'unreadable'
    # With no bar there is no evidence about which digit is the quantity, so
    # the card has no identity rather than a guessed one.
    assert row.mission_id is None


def test_a_mission_we_failed_to_name_is_never_reported_as_absent() -> None:
    """`unseen` is a claim about the page; `unreadable` about our evidence.

    The 'Buy 1 cards' card is visibly on screen with its bar missing. Saying
    `unseen` for it would assert absence from a failure to read - the exact
    coercion of a missing observation into a definite answer the reader is
    required never to make.
    """
    reading = read(without(recorded(), '0/1'))
    assert any(m.raw_text == 'Buy 1 cards' for m in reading.missions)
    assert reading.status_for('buy_cards') == 'unreadable'
    # And nothing else can be ruled absent while a card sits unidentified.
    assert reading.status_for('collect_gems') == 'unreadable'


def test_absence_is_not_claimed_when_fewer_cards_were_read_than_are_drawn() -> None:
    """The page says it is showing two; if only one parsed, absence is unknown."""
    reading = read(tuple(b for b in recorded() if b not in in_card(recorded(), CARD_TWO)))
    assert reading.shown == 2 and len(reading.missions) == 1
    assert reading.status_for('buy_cards') == 'unreadable'


def test_an_unreadable_shown_count_stops_the_reader_before_any_reading() -> None:
    """The count band is one of the three identity anchors, so it cannot be
    missing from a reading that exists. status_for still guards against a
    None `shown`, but this is why that guard has no reachable case today."""
    assert read(retext(recorded(), '2/8 Missions', '2/8 Missions', .5)) is None


def test_missing_reward_numbers_are_unreadable_rather_than_an_empty_reward() -> None:
    reading = read(without_in(recorded(), CARD_TWO, '25', '3'))
    row = entry(reading, 'buy_cards')
    assert row.reward_values == () and row.rewards_status == 'unreadable'
    # Losing the rewards must not cost the identity or the progress.
    assert row.status == 'available' and row.target == 1
    # ...and must not have touched the milestone strip, which also holds a 25.
    assert [m.threshold for m in reading.milestones] == [5, 10, 15, 20, 25]


def test_the_wrong_geometry_is_never_read_at_all() -> None:
    assert missions_screen.parse_frame(frame()[:2300], recorded(), now=100.) is None
    assert missions_screen.parse_frame(frame(), recorded(), now=100., locale='de') is None


# --- capability matrix ----------------------------------------------------

def test_capabilities_declare_the_missions_reader_and_what_it_cannot_do() -> None:
    import screen_discovery
    capabilities = screen_discovery.capabilities()
    assert capabilities['readers']['missions.daily'] == 'menu_missions'
    assert 'missions.daily' in capabilities['recorded_verified']
    for name in ('missions_claim_actions', 'missions_reward_currency',
                 'missions_milestone_claim_state', 'missions_beyond_the_recorded_strip'):
        assert name in capabilities['unsupported']
    assert (FIXTURES / 'ocr' / 'menu_missions.json').exists()


def test_capabilities_declare_what_this_capture_cannot_prove() -> None:
    """A declared reader must also declare the properties it only asserts."""
    import screen_discovery
    unproven = screen_discovery.capabilities()['unproven']
    for name in ('missions.identity_across_reordering',
                 'missions.identity_across_ocr_jitter',
                 'missions.shown_of_offered'):
        assert name in unproven and unproven[name]
    # These are unverified, not unsupported: the reader really does run.
    assert not any(name in screen_discovery.capabilities()['unsupported']
                   for name in unproven)


def test_the_missions_reader_exposes_no_field_a_tap_could_be_read_from() -> None:
    """Read-only slice: the entry shape is pinned so a tap cannot be added.

    An exact field-set rather than a name check - adding `tap`, `point` or
    `price` to either entry must fail here rather than pass unnoticed. `rect`
    is deliberately present as evidence, the way ObservedUpgrade.rect and
    ScreenField.rect are; in the house style a tap is always its own field.
    """
    assert {f.name for f in fields(missions_screen.MissionEntry)} == {
        'mission_id', 'raw_text', 'progress', 'target', 'status',
        'reward_values', 'rewards_status', 'confidence', 'rect'}
    assert {f.name for f in fields(missions_screen.MilestoneEntry)} == {
        'threshold', 'status', 'confidence', 'rect'}
