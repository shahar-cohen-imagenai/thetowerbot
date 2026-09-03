"""Workshop and cards templates, against committed captures.

Golden data. A failure here means a template was re-cut badly or the game's UI
moved - both of which send taps to the wrong place, on pages where a tap
spends a currency that cannot be earned back quickly.
"""

from pathlib import Path

import cv2
import pytest

import config
import vision

FIXTURES = Path(__file__).parent / "fixtures"

ROWS_BY_FIXTURE = {
    "menu_workshop_attack": [
        "Damage", "Attack Speed", "Critical Chance", "Critical Factor",
        "Unlock Range Upgrades",
    ],
    "menu_workshop_defense": ["Health", "Health Regen", "Unlock Defense Upgrades"],
    "menu_workshop_utility": ["Unlock Cash Bonuses"],
}
# Which tab is SELECTED on each fixture - the tab template is cut unselected,
# so its own page is the one place it is allowed not to match.
SELECTED_TAB = {
    "menu_workshop_attack": "ATTACK",
    "menu_workshop_defense": "DEFENSE",
    "menu_workshop_utility": "UTILITY",
}


def frame(name: str):
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture: {name}.png"
    return img


@pytest.fixture
def cache():
    return vision.TemplateCache(config.TEMPLATE_DIR)


@pytest.mark.parametrize("fixture,rows", ROWS_BY_FIXTURE.items())
def test_every_row_is_found_on_its_own_tab(fixture, rows, cache) -> None:
    screen = frame(fixture)
    for name in rows:
        template, _ = config.WORKSHOP_ROWS[name]
        assert vision.locate_template(screen, cache.get(template), 0.9) is not None, \
            f"{name} not found on {fixture}"


@pytest.mark.parametrize("fixture,rows", ROWS_BY_FIXTURE.items())
def test_rows_are_absent_from_the_other_tabs(fixture, rows, cache) -> None:
    """A row matching on a tab it does not live on would be bought there.
    The three Unlock tiles share styling, so this is the test that catches
    them cross-matching."""
    screen = frame(fixture)
    for name in set(config.WORKSHOP_ROWS) - set(rows):
        template, _ = config.WORKSHOP_ROWS[name]
        score, _ = vision.best_score(screen, cache.get(template))
        assert score < 0.9, f"{name} scored {score:.3f} on {fixture}, where it is absent"


def test_every_row_declares_a_layout_that_exists() -> None:
    for name, (_, layout) in config.WORKSHOP_ROWS.items():
        assert layout in config.LAYOUTS, f"{name} has unknown layout {layout!r}"
        assert layout in config.PRICE_REGIONS, f"no price region for layout {layout!r}"


def test_unlock_tiles_use_the_tile_layout() -> None:
    """The full-width tiles centre their price under the label; the half-width
    upgrade rows put it beside. Getting this backwards reads the wrong pixels."""
    for name, (_, layout) in config.WORKSHOP_ROWS.items():
        expected = "tile" if name.startswith("Unlock ") else "row"
        assert layout == expected, f"{name} declares {layout!r}, expected {expected!r}"


@pytest.mark.parametrize("tab", ["ATTACK", "DEFENSE", "UTILITY"])
def test_each_tab_is_found_on_the_pages_where_it_is_unselected(tab, cache) -> None:
    """Templates are cut unselected, so they must match wherever that tab is
    not the current one. That is what makes switching tabs possible from
    whichever tab the visit is currently on."""
    for fixture, selected in SELECTED_TAB.items():
        if selected == tab:
            continue
        assert vision.locate_template(
            frame(fixture), cache.get(config.WORKSHOP_TABS[tab]), 0.8
        ) is not None, f"{tab} tab not found on {fixture}"


@pytest.mark.parametrize("fixture,tab", SELECTED_TAB.items())
def test_a_tab_template_also_matches_its_own_selected_page(fixture, tab, cache) -> None:
    """The intuitive - and WRONG - arrival check: "the tab template is cut
    unselected, so if it stops matching we must already be on it."

    Measured directly: it does not stop matching. TM_CCOEFF_NORMED
    normalises away the brightness difference between selected and
    unselected art, so each tab's own "unselected" crop still scores well
    above threshold on the very page where that tab IS selected:

        ATTACK   on menu_workshop_attack   -> 0.954
        DEFENSE  on menu_workshop_defense  -> 0.934
        UTILITY  on menu_workshop_utility  -> 0.955

    All comfortably above both 0.8 and the stricter 0.9. A tab template's
    absence is therefore not a usable "we have arrived" signal - shopping.py
    judges arrival by whether the target category's own ROW templates are
    visible instead (see ShoppingSession._open_tab), which the tests above
    already prove are absent from every tab but their own.
    """
    score, _ = vision.best_score(frame(fixture), cache.get(config.WORKSHOP_TABS[tab]))
    assert score >= 0.9, (
        f"{tab} scored {score:.3f} on its own selected page ({fixture}) - "
        "expected it to still match well above threshold, proving tab "
        "template absence cannot signal arrival"
    )


def test_both_card_buttons_are_found(cache) -> None:
    screen = frame("menu_cards")
    for name in ("x1", "x10"):
        assert vision.locate_template(screen, cache.get(config.CARD_BUTTONS[name]), 0.9) \
            is not None, f"card button {name} not found"


def test_the_unaffordable_card_button_is_desaturated_not_dimmed(cache) -> None:
    """The greyed-out "cannot afford" state the README admits was never
    captured - now measured, and it is not a dimming at all.

    On menu_cards.png (40 gems: x1 costs 20 and is affordable, x10 costs 200
    and is not), CARD_PRICE_REGION sliced from each matched button measures:

        button   mean grey   saturation (lit pixels only)
        x1       52.5        46.2
        x10      65.1        37.7

    The unaffordable button (x10) is BRIGHTER, not dimmer, and less
    saturated - the game signals "cannot afford" by desaturating the price
    and gem icon toward grey, not by darkening anything. Consequence: a
    vision.brightness_ratio gate calibrated on x1 (the affordable style) as
    its reference template scores x10 at roughly 1.24 - comfortably ABOVE
    1.0, let alone config.DEFAULT_BRIGHTNESS_RATIO (0.75). It would have
    PASSED this unaffordable button as affordable, in the wrong direction
    from a false rejection. Brightness cannot do this job for cards; only
    digit-reading (Task 5b) can.
    """
    screen = frame("menu_cards")
    x1_match = vision.locate_template(screen, cache.get(config.CARD_BUTTONS["x1"]), 0.9)
    x10_match = vision.locate_template(screen, cache.get(config.CARD_BUTTONS["x10"]), 0.9)
    assert x1_match is not None and x10_match is not None

    region = config.CARD_PRICE_REGION

    def price_area(match: vision.Match):
        x, y = match.top_left
        return screen[y + region.dy : y + region.dy + region.h, x + region.dx : x + region.dx + region.w]

    def mean_saturation(area) -> float:
        gray = cv2.cvtColor(area, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(area, cv2.COLOR_BGR2HSV)
        lit = gray > 40  # exclude near-black background: HSV saturation is
        # numerically meaningless (noise-dominated) when value is near zero.
        return float(hsv[:, :, 1][lit].mean())

    x1_area, x10_area = price_area(x1_match), price_area(x10_match)
    x1_grey, x10_grey = vision.mean_brightness(x1_area), vision.mean_brightness(x10_area)
    x1_sat, x10_sat = mean_saturation(x1_area), mean_saturation(x10_area)

    assert x10_grey >= x1_grey, (
        f"expected the unaffordable button to be at least as bright as the "
        f"affordable one, not dimmer: x1={x1_grey:.1f} x10={x10_grey:.1f}"
    )
    assert x10_sat < x1_sat, (
        f"expected the unaffordable button to be measurably less saturated: "
        f"x1={x1_sat:.1f} x10={x10_sat:.1f}"
    )

    # The consequence, using the real production function: calibrate a
    # brightness gate on x1's price area as the "affordable" reference
    # template, then check what it would have measured at x10's position.
    x, y = x10_match.top_left
    x10_top_left = (x + region.dx, y + region.dy)
    as_if_gated = vision.Match(center=x10_top_left, score=1.0, top_left=x10_top_left)
    ratio = vision.brightness_ratio(screen, as_if_gated, x1_area)
    assert ratio >= config.DEFAULT_BRIGHTNESS_RATIO, (
        f"a brightness gate calibrated on the affordable style should have "
        f"let the unaffordable button through (it cannot tell them apart) - "
        f"measured ratio {ratio:.2f}"
    )


def test_every_configured_template_exists_on_disk(cache) -> None:
    paths = [t for t, _ in config.WORKSHOP_ROWS.values()]
    paths += list(config.WORKSHOP_TABS.values()) + list(config.CARD_BUTTONS.values())
    for path in paths:
        assert cache.get(path) is not None, f"missing template: {path}"
