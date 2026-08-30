"""Digit reading. Fixtures are golden data - failures mean a capture drifted."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

import config
import digits

FIXTURES = Path(__file__).parent / "fixtures"


def test_crop_is_relative_to_the_anchor() -> None:
    screen = np.zeros((100, 100, 3), dtype=np.uint8)
    screen[30:40, 50:70] = 255  # a white bar at (50,30), 20x10

    region = config.Region(dx=10, dy=5, w=20, h=10)
    crop = digits.crop(screen, region, anchor=(40, 25))

    assert crop is not None
    assert crop.shape[:2] == (10, 20)
    assert crop.mean() == 255.0  # we landed exactly on the bar


def test_crop_off_screen_returns_none() -> None:
    screen = np.zeros((100, 100, 3), dtype=np.uint8)
    region = config.Region(dx=0, dy=0, w=20, h=10)
    assert digits.crop(screen, region, anchor=(95, 95)) is None


def test_crop_with_negative_offsets() -> None:
    """The anchor is not always above-left of the number it locates."""
    screen = np.zeros((100, 100, 3), dtype=np.uint8)
    screen[10:20, 10:30] = 255
    region = config.Region(dx=-30, dy=-30, w=20, h=10)
    crop = digits.crop(screen, region, anchor=(40, 40))
    assert crop is not None
    assert crop.mean() == 255.0


def render_text(text: str, scale: float = 2.0, thickness: int = 3) -> np.ndarray:
    """White text on black, the same polarity as the game's HUD.

    Synthetic on purpose: it exercises the segmentation algorithm without an
    emulator, which the suite must run without.
    """
    canvas = np.zeros((80, 60 * len(text) + 40, 3), dtype=np.uint8)
    cv2.putText(
        canvas, text, (20, 55), cv2.FONT_HERSHEY_SIMPLEX, scale,
        (255, 255, 255), thickness, cv2.LINE_AA,
    )
    return canvas


def test_binarize_splits_light_glyphs_from_dark_background() -> None:
    binary = digits.binarize(render_text("8"))
    assert set(np.unique(binary)) <= {0, 255}
    assert (binary > 0).any()


def test_segments_each_digit_separately() -> None:
    binary = digits.binarize(render_text("1234"))
    spans = digits.glyph_spans(binary)
    assert len(spans) == 4


def test_segments_keep_left_to_right_order() -> None:
    binary = digits.binarize(render_text("1234"))
    spans = digits.glyph_spans(binary)
    assert spans == sorted(spans)
    assert all(x0 < x1 for x0, x1 in spans)


def test_decimal_point_survives_the_min_width_filter() -> None:
    """The dot is the narrowest real glyph. Filter it out and 1.5K reads 15K."""
    binary = digits.binarize(render_text("1.5K"))
    assert len(digits.glyph_spans(binary)) == 4


def test_split_glyphs_trims_vertically() -> None:
    glyphs = digits.split_glyphs(digits.binarize(render_text("7")))
    assert len(glyphs) == 1
    glyph = glyphs[0]
    assert (glyph[0, :] > 0).any(), "top row should touch the glyph"
    assert (glyph[-1, :] > 0).any(), "bottom row should touch the glyph"


def test_empty_region_yields_no_glyphs() -> None:
    blank = np.zeros((40, 100, 3), dtype=np.uint8)
    assert digits.split_glyphs(digits.binarize(blank)) == []


def build_synthetic_atlas(directory: Path, text: str = "0123456789") -> None:
    """Cut glyphs out of a render and save them under their labels.

    Round-tripping the same renderer proves the matcher, without needing the
    game's font committed to the repo.
    """
    directory.mkdir(parents=True, exist_ok=True)
    glyphs = split_glyphs_of(text)
    assert len(glyphs) == len(text), f"renderer produced {len(glyphs)} glyphs"
    for char, glyph in zip(text, glyphs):
        cv2.imwrite(str(directory / f"{digits.GLYPH_FILENAMES[char]}.png"), glyph)


def split_glyphs_of(text: str) -> list[np.ndarray]:
    return digits.split_glyphs(digits.binarize(render_text(text)))


def test_atlas_matches_every_digit_it_was_built_from(tmp_path: Path) -> None:
    build_synthetic_atlas(tmp_path)
    atlas = digits.Atlas(tmp_path)
    assert [atlas.match(g) for g in split_glyphs_of("0123456789")] == list("0123456789")


def test_atlas_rejects_a_glyph_below_threshold(tmp_path: Path) -> None:
    build_synthetic_atlas(tmp_path, text="1")
    atlas = digits.Atlas(tmp_path)
    noise = np.random.default_rng(0).integers(0, 2, (30, 20), dtype=np.uint8) * 255
    assert atlas.match(noise, threshold=0.99) is None


def test_atlas_loads_punctuation_by_filename(tmp_path: Path) -> None:
    """'.' and ',' cannot be filenames, so the atlas maps names to chars."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    for name, glyph in zip(["1", "dot", "5"], split_glyphs_of("1.5")):
        cv2.imwrite(str(tmp_path / f"{name}.png"), glyph)
    assert digits.Atlas(tmp_path).labels == {"1", ".", "5"}


def test_missing_atlas_directory_returns_none(tmp_path: Path) -> None:
    assert digits.AtlasCache(tmp_path).get("wallet") is None


def test_atlas_cache_loads_each_size_class_once(tmp_path: Path) -> None:
    build_synthetic_atlas(tmp_path / "wallet")
    cache = digits.AtlasCache(tmp_path)
    assert cache.get("wallet") is cache.get("wallet")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0),
        ("7", 7),
        ("1234", 1234),
        ("1,234", 1234),
        ("$980", 980),
        ("$1,234,567", 1234567),
        ("1.23K", 1230),
        ("4.5M", 4500000),
        ("2B", 2000000000),
        ("1.5T", 1500000000000),
        ("$12.75K", 12750),
    ],
)
def test_parses_the_games_number_formats(text: str, expected: int) -> None:
    assert digits.parse_number(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "$", "K", "..", "12..3", "1.2X", "abc"])
def test_unparseable_text_returns_none(text: str) -> None:
    assert digits.parse_number(text) is None


def test_suffix_is_not_silently_dropped() -> None:
    """A digit-only parser reads 1.23K as 123 and looks fine for an hour."""
    assert digits.parse_number("1.23K") != 123


def paste(screen: np.ndarray, patch: np.ndarray, at: tuple[int, int]) -> None:
    x, y = at
    screen[y : y + patch.shape[0], x : x + patch.shape[1]] = patch


def test_reads_a_number_off_a_synthetic_screen(tmp_path: Path) -> None:
    build_synthetic_atlas(tmp_path / "wallet")
    reader = digits.NumberReader(digits.AtlasCache(tmp_path))

    rendered = render_text("4271")
    screen = np.zeros((400, 400, 3), dtype=np.uint8)
    paste(screen, rendered, (100, 100))

    region = config.Region(dx=100, dy=100, w=rendered.shape[1], h=rendered.shape[0])
    assert reader.read(screen, region, anchor=(0, 0), size_class="wallet") == 4271


def test_read_without_an_atlas_returns_none(tmp_path: Path) -> None:
    reader = digits.NumberReader(digits.AtlasCache(tmp_path))
    screen = np.zeros((400, 400, 3), dtype=np.uint8)
    region = config.Region(dx=0, dy=0, w=100, h=40)
    assert reader.read(screen, region, anchor=(0, 0), size_class="wallet") is None


def test_read_off_screen_returns_none(tmp_path: Path) -> None:
    build_synthetic_atlas(tmp_path / "wallet")
    reader = digits.NumberReader(digits.AtlasCache(tmp_path))
    screen = np.zeros((50, 50, 3), dtype=np.uint8)
    region = config.Region(dx=0, dy=0, w=100, h=40)
    assert reader.read(screen, region, anchor=(0, 0), size_class="wallet") is None


def test_one_unrecognised_glyph_fails_the_whole_read(tmp_path: Path) -> None:
    """A partial read is worse than none: 1?34 must not become 134.

    That is the failure that would let the bot buy an upgrade it cannot
    afford while reporting a plausible price.
    """
    build_synthetic_atlas(tmp_path / "wallet", text="0123456789")
    reader = digits.NumberReader(digits.AtlasCache(tmp_path))

    rendered = render_text("12")
    rendered[10:70, 80:130] = 255  # solid block no digit matches
    screen = np.zeros((400, 400, 3), dtype=np.uint8)
    paste(screen, rendered, (0, 0))

    region = config.Region(dx=0, dy=0, w=rendered.shape[1], h=rendered.shape[0])
    assert reader.read(screen, region, anchor=(0, 0), size_class="wallet") is None
