"""Passive, read-only reading of the recorded English Daily Missions page.

Identity comes from the mission's own text and never from its row position:
the same mission keeps its id when the list reorders and when a new mission
appears above it. That is the whole point of this reader, so `parse_frame`
binds every number to the card the text was read from.

Nothing here produces a tap. Claiming a mission reward, claiming a weekly
milestone and buying anything a mission asks for are all out of scope; the
recorded capture shows no claimable state to verify one against.

Bounds are supported only by the native 1080x2400 capture
`tests/fixtures/menu_missions.png` and its recorded OCR.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, replace

import ocr
import screen_discovery
import tiles
from config import Rect
from device import Image

_MIN_CONFIDENCE = .90

# "completed 0/35" measured at (796, 326, 261, 36) - the daily counter, and
# the only evidence this reader has for whether a weekly milestone is reached.
_COMPLETED = Rect(700, 306, 380, 76)
# Matched against the raw text: tiles.normalise strips the slash, which would
# turn "completed 0/35" into the unreadable "completed035".
_COMPLETED_TEXT = re.compile(r'completed\s*(\d+)\s*/\s*(\d+)', re.I)

# The weekly milestone thresholds, measured as a single row of bare integers
# at y 577-621 spanning x 133-961. The strip runs off the right edge of the
# capture, so what it holds is a prefix of the ladder and never all of it.
_MILESTONES = Rect(60, 555, 950, 90)

# The two mission cards tiles.candidates finds at (17, 738, 1046, 242) and
# (17, 1003, 1046, 242). Deliberately outside config.TILE_MIN_H/TILE_MAX_H:
# find_tiles must keep refusing these, so a missions frame can never turn
# into a grid of purchasable upgrade rows.
_CARD_MIN_W, _CARD_MAX_W = 1000, 1080
_CARD_MIN_H, _CARD_MAX_H = 220, 270

# Measured fractions of a card, consistent across both recorded cards: the
# reward column sits right of .85, the progress bar is centred below .5, and
# the mission text is what remains in the upper left.
_REWARD_LEFT_FRACTION = .85
_PROGRESS_TOP_FRACTION = .5
_PROGRESS = re.compile(r'(\d+)\s*/\s*(\d+)')
_INTEGER = re.compile(r'\d+')


@dataclass(frozen=True)
class MissionEntry:
    """One mission card, addressed by what is written on it.

    `mission_id` is the text with its goal count removed - see identity_of
    for which digit that is and which digits stay - so a daily that returns
    asking for a bigger number is still the same mission. It is None whenever
    the text could not be trusted: an unreadable card has no identity rather
    than a guessed one. Comparing OBJECTIVES across frames means comparing
    `(mission_id, target)` - MissionsReading.status_for takes both for
    exactly that reason; `mission_id` alone means "the same recurring
    mission", which is a weaker claim.

    `status` keeps available, complete, ambiguous and unreadable apart. A
    card whose numbers were not read leaves `progress` and `target` None; a
    missing observation is never written down as zero.
    """

    mission_id: str | None
    raw_text: str
    progress: int | None
    target: int | None
    status: str
    reward_values: tuple[int, ...]
    rewards_status: str
    confidence: float
    rect: tuple[int, int, int, int]


@dataclass(frozen=True)
class MilestoneEntry:
    """One weekly-challenge chest, addressed by its threshold.

    `status` is derived from the completed counter, never from the padlock
    art: 'locked' means the counter was read and has not reached this
    threshold, 'unlocked' means it has, and 'unreadable' means the counter or
    the threshold itself could not be trusted. Whether an unlocked chest is
    still claimable or was already taken is not observable here.
    """

    threshold: int
    status: str
    confidence: float
    rect: tuple[int, int, int, int]


@dataclass(frozen=True)
class MissionsReading:
    """One frame of the Daily Missions page.

    `shown`/`offered` are the two halves of the "N/M Missions" band and
    `unseen` is their difference. That the band means drawn-of-offered is an
    INFERENCE, not something the page states: the separate "completed 0/35"
    counter rules out the competing reading (that N counts finished
    missions), but only a capture with a different draw count settles it.
    """

    screen_id: str
    observed_at: float
    frame_width: int
    frame_height: int
    frame_digest: str
    completed: int | None
    completed_target: int | None
    shown: int | None
    offered: int | None
    unseen: int | None
    missions: tuple[MissionEntry, ...]
    milestones: tuple[MilestoneEntry, ...]
    complete: bool

    def status_for(self, mission_id: str, target: int | None = None) -> str:
        """What this frame says about one mission, keeping absence from doubt.

        Pass `target` as well to ask about an OBJECTIVE rather than about a
        recurring mission: 'Reach tier 5' and 'Reach tier 10' share the id
        `reach_tier` and are told apart only by their goal, so a caller that
        cares which one it is must say so. Omitting it asks the weaker
        question, "is this mission on the page at all".

        'unseen' is a claim about the WORLD - this mission is not on the
        page - and may only be made once the page was read completely: every
        card the page says it is showing was parsed, and every parsed card
        was identified. If any card's identity is unknown, or fewer cards
        were read than the page says are drawn, then this mission cannot be
        ruled out, and the answer is 'unreadable' - a claim about our
        EVIDENCE. A card that is visibly on screen must never be reported as
        absent just because we failed to name it.
        """
        matches = [m for m in self.missions if m.mission_id == mission_id
                   and (target is None or m.target == target)]
        if len(matches) == 1:
            return matches[0].status
        if matches:
            return 'ambiguous'
        if (self.shown is None or len(self.missions) != self.shown
                or any(m.mission_id is None for m in self.missions)):
            return 'unreadable'
        return 'unseen'


def _trusted(box: ocr.TextBox) -> bool:
    return math.isfinite(box.confidence) and _MIN_CONFIDENCE <= box.confidence <= 1.


def _inside(rect: Rect, box: Rect) -> bool:
    x, y = box.x + box.w / 2, box.y + box.h / 2
    return rect.x <= x < rect.x + rect.w and rect.y <= y < rect.y + rect.h


def identity_of(text: str, target: int | None) -> str | None:
    """The mission's identity: its text with the goal count removed.

    The goal is the one digit run the progress bar agrees with, and only
    that one. 'Kill 200 basic enemies' against a 0/200 bar is
    `kill_basic_enemies`, so the same daily returning to ask for 500 keeps
    its identity while `target` carries what changed.

    Every OTHER digit run stays in the id, because it is part of what the
    mission is about rather than how much of it is wanted. 'Reach wave 100
    in tier 3' and 'Reach wave 100 in tier 5' are two different objectives:
    dropping every digit collapses them onto one id, which fails closed
    within a frame (both come back `ambiguous`) but silently conflates them
    ACROSS frames, where nothing is left to show the objective changed.

    When the target matches no digit run - a garbled read - or matches more
    than one, the goal cannot be singled out and every digit is kept. That
    direction is deliberate: an id that is too specific splits one mission
    into two, and each half is honestly reported, while an id that is too
    loose merges two missions into one and says nothing about it.

    No target at all is a DIFFERENT case and gets a different answer. With no
    bar there is no evidence whatever about which digit is the quantity, so a
    text carrying digits has no identity here rather than a guessed one -
    `None`, not a digit-preserving slug, because such a slug would be an
    identity asserted from an assumption nothing on the frame supports. A
    text with no digits at all has nothing to split and is answered normally.

    A text with no letters left is likewise `None`: a bare-number id could
    collide with any other numeric garble and names no mission.

    Known and accepted: when the goal digit IS the subject - 'Reach tier 5'
    against a 0/5 bar, where the bar literally counts tiers - stripping it
    gives `reach_tier` for every tier. That is the same recurring objective
    at a different goal, and `target` is what tells them apart. Callers
    comparing OBJECTIVES pass both to MissionsReading.status_for.
    """
    if target is None and re.search(r'\d', text):
        return None
    runs = list(re.finditer(r'\d+', text))
    goals = [run for run in runs if target is not None and int(run[0]) == target]
    dropped = goals[0].span() if len(goals) == 1 else None
    pieces: list[str] = []
    cursor = 0
    for run in runs:
        pieces.append(text[cursor:run.start()])
        pieces.append(' ' if run.span() == dropped else run[0])
        cursor = run.end()
    pieces.append(text[cursor:])
    tokens = re.findall(r'[a-z]+|\d+', ''.join(pieces).casefold())
    # A bare-number id names nothing and would collide with the next numeric
    # garble, so a text with no surviving word has no identity.
    return '_'.join(tokens) if any(t.isalpha() for t in tokens) else None


def _counter(boxes: tuple[ocr.TextBox, ...]) -> tuple[int | None, int | None]:
    """The 'completed N/M' pair, or (None, None) when it is not certain."""
    matches = [m for m in (_COMPLETED_TEXT.fullmatch(b.text.strip())
                           for b in boxes if _trusted(b) and _inside(_COMPLETED, b.rect)) if m]
    if len(matches) != 1:
        return None, None
    done, total = int(matches[0][1]), int(matches[0][2])
    return (done, total) if done <= total else (None, None)


def _milestones(boxes: tuple[ocr.TextBox, ...], completed: int | None) -> tuple[MilestoneEntry, ...]:
    found = [b for b in boxes if _inside(_MILESTONES, b.rect)
             and _INTEGER.fullmatch(b.text.strip())]
    thresholds = Counter(int(b.text) for b in found if _trusted(b))
    entries = []
    for box in sorted(found, key=lambda b: b.rect.x):
        threshold = int(box.text)
        # A repeated threshold is two chests claiming one identity, and an
        # unread counter is no evidence at all. Neither may become 'locked'.
        status = ('unreadable' if not _trusted(box) or thresholds[threshold] > 1
                  or completed is None else
                  'unlocked' if completed >= threshold else 'locked')
        entries.append(MilestoneEntry(
            threshold, status, box.confidence if _trusted(box) else 0.,
            (box.rect.x, box.rect.y, box.rect.w, box.rect.h)))
    return tuple(entries)


def _cards(screen: Image) -> tuple[Rect, ...]:
    """The mission cards, top to bottom, by their measured bordered bounds."""
    return tuple(sorted(
        (rect for rect in tiles.candidates(screen)
         if _CARD_MIN_W <= rect.w <= _CARD_MAX_W and _CARD_MIN_H <= rect.h <= _CARD_MAX_H),
        key=lambda rect: rect.y))


def _mission(card: Rect, boxes: tuple[ocr.TextBox, ...]) -> MissionEntry | None:
    inside = sorted((b for b in boxes if _inside(card, b.rect)),
                    key=lambda b: (b.rect.y, b.rect.x))
    if not inside:
        return None
    reward_edge = card.x + card.w * _REWARD_LEFT_FRACTION
    progress_edge = card.y + card.h * _PROGRESS_TOP_FRACTION

    reward_boxes = [b for b in inside if b.rect.x >= reward_edge]
    rewards = tuple(int(b.text) for b in reward_boxes
                    if _trusted(b) and _INTEGER.fullmatch(b.text.strip()))
    # The two reward numbers are told apart by their icons, which this reader
    # does not read. They are reported as values in the order they appear and
    # no currency is named for them.
    rewards_status = ('observed' if reward_boxes and len(rewards) == len(reward_boxes)
                      else 'unreadable')

    bars = [m for m in (_PROGRESS.fullmatch(b.text.strip().replace(' ', ''))
                        for b in inside
                        if b.rect.x < reward_edge and b.rect.y >= progress_edge
                        and _trusted(b)) if m]
    progress, target = (int(bars[0][1]), int(bars[0][2])) if len(bars) == 1 else (None, None)
    if progress is not None and target is not None and (target <= 0 or progress > target):
        progress, target = None, None

    text_boxes = [b for b in inside if b.rect.x < reward_edge and b.rect.y < progress_edge]
    raw_text = ' '.join(b.text for b in text_boxes)
    trusted_text = bool(text_boxes) and all(_trusted(b) for b in text_boxes)
    mission_id = identity_of(raw_text, target) if trusted_text else None

    if mission_id is None or progress is None or target is None:
        status = 'unreadable'
    else:
        status = 'available' if progress < target else 'complete'
    return MissionEntry(
        mission_id, raw_text, progress, target, status, rewards, rewards_status,
        min((b.confidence for b in text_boxes), default=0.) if trusted_text else 0.,
        (card.x, card.y, card.w, card.h))


def parse_frame(screen: Image, boxes: tuple[ocr.TextBox, ...], *,
                now: float | None = None, locale: str = 'en') -> MissionsReading | None:
    """Read the recorded Daily Missions page, or nothing at all.

    The page must first identify itself through screen_discovery, so an
    unrecognised frame yields None rather than a partial reading assembled
    from whatever text happened to land in the measured bands.
    """
    observed_at = time.time() if now is None else now
    if not math.isfinite(observed_at):
        return None
    discovery = screen_discovery.discover(screen, boxes, 'missions', locale=locale)
    if not discovery.readable or discovery.screen_id != 'missions.daily':
        return None
    completed, completed_target = _counter(boxes)
    counted = screen_discovery.missions_count(boxes)
    shown, offered = counted if counted else (None, None)
    missions = tuple(m for m in (_mission(card, boxes) for card in _cards(screen)) if m)
    # Two cards under one identity are ambiguous, and ambiguity is its own
    # answer: it is not 'unreadable' (both were read) and not 'available'.
    repeated = Counter(m.mission_id for m in missions if m.mission_id is not None)
    missions = tuple(replace(m, status='ambiguous')
                     if m.mission_id is not None and repeated[m.mission_id] > 1 else m
                     for m in missions)
    return MissionsReading(
        'missions.daily', observed_at, screen.shape[1], screen.shape[0],
        hashlib.sha256(screen.tobytes()).hexdigest(),
        completed, completed_target, shown, offered,
        offered - shown if shown is not None and offered is not None else None,
        missions, _milestones(boxes, completed),
        # Never complete: the weekly strip is cut off by the right edge of the
        # capture and only `shown` of `offered` missions are on the page.
        complete=False)
