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


def test_both_card_buttons_are_found(cache) -> None:
    screen = frame("menu_cards")
    for name in ("x1", "x10"):
        assert vision.locate_template(screen, cache.get(config.CARD_BUTTONS[name]), 0.9) \
            is not None, f"card button {name} not found"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "menu_cards.png does not actually render x10 dimmer than x1: measured "
        "hue/saturation/value are equal within noise for the border, the "
        "label, the price digits and the gem icon (see the comment beside "
        "config.DEFAULT_BRIGHTNESS_RATIO) - only the digits differ. Since "
        "vision.brightness_ratio compares a matched region against the exact "
        "template it was cropped from, self-matching either button against "
        "this one fixture is mathematically guaranteed to score 1.0 "
        "regardless of which pixels are cropped - there is no crop that can "
        "surface a difference that was never rendered. This needs a fixture "
        "where a card button is genuinely painted in its unaffordable style "
        "(or digit-reading, which does not depend on brightness at all)."
    ),
)
def test_the_unaffordable_card_button_is_visibly_dimmer(cache) -> None:
    """The greyed-out 'cannot afford' state the README admits was never
    captured. On this fixture 40 gems buys x1 (20 gems) and not x10 (200), so
    both states sit on ONE frame - and TM_CCOEFF_NORMED is blind to the
    difference, which is exactly why the brightness check exists.

    Record the two measured ratios in a comment beside
    config.DEFAULT_BRIGHTNESS_RATIO, and correct that 0.75 default if this
    measurement says it is wrong.
    """
    screen = frame("menu_cards")
    ratios = {}
    for name in ("x1", "x10"):
        template = cache.get(config.CARD_BUTTONS[name])
        match = vision.locate_template(screen, template, 0.9)
        assert match is not None
        ratios[name] = vision.brightness_ratio(screen, match, template)
    assert ratios["x10"] < ratios["x1"], f"no separation measured: {ratios}"


def test_every_configured_template_exists_on_disk(cache) -> None:
    paths = [t for t, _ in config.WORKSHOP_ROWS.values()]
    paths += list(config.WORKSHOP_TABS.values()) + list(config.CARD_BUTTONS.values())
    for path in paths:
        assert cache.get(path) is not None, f"missing template: {path}"
