"""Tapping the floating gem, and proving the tap landed.

The gem ORBITS. That is the fact this whole state machine is shaped around:
between the frame that saw it and the tap that follows, it has moved, so a
miss is the ordinary case rather than the exceptional one. Hence a retry
budget rather than one shot, and hence a confirmation that reads the gem
counter instead of trusting the tap.
"""

from __future__ import annotations

import events
import gem_claim


class FakeDevice:
    def __init__(self) -> None:
        self.taps: list[tuple[int, int]] = []

    def click(self, x: int, y: int) -> None:
        self.taps.append((x, y))


class RecordingBus:
    def __init__(self) -> None:
        self.published: list[events.Event] = []

    def publish(self, event: events.Event) -> events.Event:
        self.published.append(event)
        return event

    def of(self, kind: type) -> list:
        return [e for e in self.published if isinstance(e, kind)]


class FakeReader:
    """Stands in for digits.NumberReader over the gem counter.

    Takes a list of answers, one per read, so a test can say "60, then 60,
    then 62" - which is exactly the shape a confirmation across scans has.
    """

    def __init__(self, answers: list[int | None]) -> None:
        self.answers = list(answers)
        self.reads = 0

    def read(self, screen, region, anchor, size_class):
        self.reads += 1
        if not self.answers:
            return None
        return self.answers.pop(0)


class Policy:
    """The tuning fields the tap path reads off a Strategy."""

    tap_jitter_px = 0.0
    timing_jitter = 0.0
    tap_delay = 0.0


def claim(answers, sightings):
    """A claim wired to canned counter reads and canned sightings."""
    bus = RecordingBus()
    seen = list(sightings)

    def detect(screen, anchor):
        return seen.pop(0) if seen else None

    return (
        gem_claim.FloatingGemClaim(
            bus=bus, reader=FakeReader(answers), detect=detect, sleep=lambda s: None
        ),
        bus,
    )


SIGHTING = gem_claim.floating_gem.Sighting(point=(560, 700), box=(540, 680, 40, 40), area=900)


def observe(claimer, device, *, now=0.0):
    return claimer.observe(
        screen=object(), anchor=(32, 171), device=device, policy=Policy(), now=now
    )


# -- tapping ----------------------------------------------------------------


def test_taps_the_gem_where_it_was_seen():
    device = FakeDevice()
    claimer, _ = claim([60], [SIGHTING])

    observe(claimer, device)

    assert device.taps == [(560, 700)]


def test_taps_nothing_when_there_is_no_gem():
    device = FakeDevice()
    claimer, bus = claim([60], [None])

    observe(claimer, device)

    assert device.taps == []
    assert bus.published == []


def test_reads_the_counter_before_tapping_not_after():
    # The before-reading is the whole basis of the delta. Taking it after
    # the tap would race the very change it exists to measure.
    device = FakeDevice()
    claimer, _ = claim([60, 62], [SIGHTING, None])

    observe(claimer, device)

    assert device.taps == [(560, 700)]


# -- confirming -------------------------------------------------------------


def test_a_risen_counter_confirms_the_claim():
    device = FakeDevice()
    claimer, bus = claim([60, 62], [SIGHTING, None])

    observe(claimer, device, now=0.0)
    observe(claimer, device, now=2.0)

    claimed = bus.of(events.FloatingGemClaimed)
    assert len(claimed) == 1
    assert claimed[0].gems_before == 60
    assert claimed[0].gems_after == 62
    assert claimed[0].delta == 2


def test_the_claim_carries_the_run_it_happened_in():
    # It is account currency won during a battle, and the ledger line is
    # worth being able to trace back to the run that produced it.
    device = FakeDevice()
    claimer, bus = claim([60, 62], [SIGHTING, None])

    claimer.observe(screen=object(), anchor=(32, 171), device=device,
                    policy=Policy(), now=0.0, run_id=7)
    claimer.observe(screen=object(), anchor=(32, 171), device=device,
                    policy=Policy(), now=2.0, run_id=7)

    assert bus.of(events.FloatingGemClaimed)[0].run_id == 7


def test_an_unchanged_counter_gives_up_as_uncertain_not_as_claimed():
    # A tap that never moved the counter may still have landed - the sprite
    # could have drifted out from under it. ClaimUncertain is the event
    # this codebase reserves for exactly that, and the ledger writes
    # delta=None rather than asserting a 0 that would be a lie.
    device = FakeDevice()
    claimer, bus = claim([60, 60, 60, 60, 60], [SIGHTING, None, None, None, None])

    for scan in range(5):
        observe(claimer, device, now=float(scan) * 2)

    assert bus.of(events.FloatingGemClaimed) == []
    uncertain = bus.of(events.ClaimUncertain)
    assert len(uncertain) == 1
    assert uncertain[0].target == "floating_gem"


def test_an_unreadable_counter_still_taps_the_gem():
    # The counter row is absent when the account holds no gems. That must
    # not stop the bot taking a free gem - it only stops it PROVING it did.
    device = FakeDevice()
    claimer, bus = claim([None, None, None, None, None], [SIGHTING, None, None, None, None])

    for scan in range(5):
        observe(claimer, device, now=float(scan) * 2)

    assert device.taps == [(560, 700)]
    assert bus.of(events.ClaimUncertain)


def test_goes_back_to_idle_after_confirming_so_the_next_gem_is_claimable():
    device = FakeDevice()
    claimer, bus = claim([60, 62, 62, 64], [SIGHTING, None, SIGHTING, None])

    for scan in range(4):
        observe(claimer, device, now=float(scan) * 2)

    assert len(bus.of(events.FloatingGemClaimed)) == 2
    assert len(device.taps) == 2


# -- retrying ---------------------------------------------------------------


def test_retaps_a_gem_that_is_still_there_because_it_moved():
    # The miss case, and the reason this is not one-shot: the gem orbits,
    # so the tap can land where it WAS. If the counter has not moved and a
    # gem is still on screen, that is a miss worth another tap.
    device = FakeDevice()
    claimer, _ = claim([60, 60, 62], [SIGHTING, SIGHTING, None])

    observe(claimer, device, now=0.0)
    observe(claimer, device, now=2.0)

    assert len(device.taps) == 2


def test_stops_retapping_once_the_budget_is_spent():
    # Unbounded retries on a false positive would tap that spot forever.
    device = FakeDevice()
    claimer, bus = claim([60] * 10, [SIGHTING] * 10)

    for scan in range(10):
        observe(claimer, device, now=float(scan) * 2)

    assert len(device.taps) <= 3
    assert bus.of(events.ClaimUncertain)


def test_does_not_tap_again_while_a_confirmation_is_still_open():
    # Without this the machine taps on every scan of the confirm window,
    # which is the same bug as no budget at all.
    device = FakeDevice()
    claimer, _ = claim([60, 60, 62], [SIGHTING, None, None])

    observe(claimer, device, now=0.0)
    observe(claimer, device, now=2.0)

    assert len(device.taps) == 1


# -- the tap itself ---------------------------------------------------------


def test_scatters_the_tap_when_jitter_is_configured():
    # Same treatment as every other tap site: a bot that hits the exact
    # centroid every time is a bot that looks like a bot.
    class Jittered(Policy):
        tap_jitter_px = 8.0

    device = FakeDevice()
    bus = RecordingBus()
    claimer = gem_claim.FloatingGemClaim(
        bus=bus, reader=FakeReader([60]), detect=lambda s, a: SIGHTING,
        sleep=lambda s: None,
    )
    claimer.observe(screen=object(), anchor=(32, 171), device=device,
                    policy=Jittered(), now=0.0)

    x, y = device.taps[0]
    assert abs(x - 560) <= 8 and abs(y - 700) <= 8


def test_tries_again_once_the_cooldown_after_a_failure_expires():
    # The cooldown that stops a false positive being tapped forever must
    # not become a permanent block: a real gem that failed to confirm once
    # is still worth two gems on the next spawn.
    device = FakeDevice()
    claimer, _ = claim([60] * 8, [SIGHTING] * 8)

    for scan in range(4):
        observe(claimer, device, now=float(scan) * 2)
    taps_before = len(device.taps)

    observe(claimer, device, now=1000.0)

    assert len(device.taps) == taps_before + 1
