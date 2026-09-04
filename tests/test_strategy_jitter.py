"""The jitter half of a strategy.

Three numbers the browser can POST, validated like every other tunable.
The ceilings matter more than usual here: an over-large tap_jitter_px does
not degrade gracefully, it taps a pixel outside the button and the purchase
silently never happens.
"""

import pytest

import config
import strategy as strategy_mod
from strategy import ControlError, Strategy


# -- defaults ---------------------------------------------------------------
def test_the_defaults_come_from_config() -> None:
    """config stays the origin of what a fresh clone starts from, the same
    way SCAN_INTERVAL_SECONDS and CLICK_COOLDOWN_SECONDS already do."""
    settings = Strategy.from_config()

    assert settings.tap_jitter_px == config.TAP_JITTER_PX
    assert settings.timing_jitter == config.TIMING_JITTER
    assert settings.tap_delay == config.TAP_DELAY_SECONDS


def test_jitter_is_on_by_default() -> None:
    """The point of the feature. A default of zero would ship the code and
    none of the behaviour."""
    settings = Strategy.from_config()

    assert settings.tap_jitter_px > 0
    assert settings.timing_jitter > 0
    assert settings.tap_delay > 0


def test_a_strategy_saved_before_jitter_existed_still_loads() -> None:
    """Every strategies/*.json on disk predates these three fields."""
    raw = Strategy.from_config().to_dict()
    del raw["tap_jitter_px"]
    del raw["timing_jitter"]
    del raw["tap_delay"]

    loaded = Strategy.from_dict(raw)

    assert loaded.tap_jitter_px == config.TAP_JITTER_PX
    assert loaded.validated() is loaded


def test_jitter_round_trips_through_to_dict() -> None:
    original = Strategy.from_config()

    assert Strategy.from_dict(original.to_dict()) == original


# -- the ceiling is derived, not guessed ------------------------------------
def test_the_tap_jitter_ceiling_cannot_exceed_the_tightest_tap_target() -> None:
    """The buy point sits at the centre of the price strip, and that strip
    is the smallest thing the bot ever taps. A radius past half its height
    can land outside the button, where the tap buys nothing and the bot
    reports a successful purchase that never happened.

    Asserted against config rather than a literal so that re-measuring
    PRICE_REGION for a new resolution moves the ceiling with it instead of
    silently invalidating it.
    """
    assert strategy_mod.MAX_TAP_JITTER_PX <= config.PRICE_REGION.h / 2


def test_the_default_leaves_headroom_under_the_ceiling() -> None:
    """The default is meant to be safe against calibration drift, not to
    sit exactly on the boundary."""
    assert config.TAP_JITTER_PX < strategy_mod.MAX_TAP_JITTER_PX


# -- range checks -----------------------------------------------------------
def test_zero_is_legal_for_all_three() -> None:
    """Zero means "off" - the escape hatch back to today's deterministic
    behaviour, reachable from the dashboard without a code change."""
    settings = Strategy.from_config()

    off = settings.merged({"tap_jitter_px": 0.0, "timing_jitter": 0.0, "tap_delay": 0.0})

    assert (off.tap_jitter_px, off.timing_jitter, off.tap_delay) == (0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "field, value",
    [
        ("tap_jitter_px", -1.0),
        ("tap_jitter_px", strategy_mod.MAX_TAP_JITTER_PX + 1),
        ("timing_jitter", -0.1),
        ("timing_jitter", strategy_mod.MAX_TIMING_JITTER + 0.1),
        ("tap_delay", -0.1),
        ("tap_delay", strategy_mod.MAX_TAP_DELAY + 0.1),
    ],
)
def test_out_of_range_jitter_is_rejected(field: str, value: float) -> None:
    with pytest.raises(ControlError) as caught:
        Strategy.from_config().merged({field: value})

    assert caught.value.field == field


@pytest.mark.parametrize("field", ["tap_jitter_px", "timing_jitter", "tap_delay"])
def test_a_non_numeric_jitter_is_rejected(field: str) -> None:
    """Same reasoning as the shopping rules: "8" is not 8, and a strategy
    that multiplies a string by a float is a crash mid-run."""
    with pytest.raises(ControlError) as caught:
        Strategy.from_config().merged({field: "8"})

    assert caught.value.field == field


# -- patchable --------------------------------------------------------------
@pytest.mark.parametrize(
    "field, value",
    [("tap_jitter_px", 4.0), ("timing_jitter", 0.3), ("tap_delay", 0.5)],
)
def test_each_field_is_live_patchable(field: str, value: float) -> None:
    """These three take effect on the next scan, unlike navigation_cooldown
    which configures a collaborator built once at Start."""
    assert field in strategy_mod.PATCHABLE_FIELDS

    patched = Strategy.from_config().merged({field: value})

    assert getattr(patched, field) == value
