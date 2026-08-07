"""Tests for deterministic cover-derived karaoke palettes."""

from itertools import combinations
from pathlib import Path
import sys

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
INTEGRATION_ROOT = ROOT / "integration" / "strangeutagame"
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from scripts import karaoke_cover_palette  # noqa: E402
from scripts.karaoke_cover_palette import (  # noqa: E402
    extract_cover_palette,
    normalize_hex_color,
    validate_palette_report,
)


def _save_split_cover(path: Path) -> None:
    image = Image.new("RGB", (80, 40), (220, 35, 45))
    for y in range(image.height):
        for x in range(image.width // 2, image.width):
            image.putpixel((x, y), (25, 90, 225))
    image.save(path)


def _assert_palette_contract(report: dict, color_count: int = 8) -> None:
    assert report["schema_version"] == "karaoke-cover-palette/v1"
    assert len(report["cover_sha256"]) == 64
    assert len(report["generator_sha256"]) == 64
    assert report["method"]
    assert report["sample"]["sampled_pixel_count"] > 0
    assert report["candidates"]
    assert len(report["colors"]) == color_count
    assert len(set(report["colors"])) == color_count
    assert all(
        len(color) == 7 and color.startswith("#") and color == color.upper()
        for color in report["colors"]
    )
    assert report["primary"] == report["colors"][0]
    assert report["secondary"] == report["colors"][1]
    assert validate_palette_report(report, expected_color_count=color_count) is report


def test_fixed_two_colour_cover_has_distinct_primary_and_secondary(tmp_path: Path):
    cover = tmp_path / "two-colour.png"
    _save_split_cover(cover)

    report = extract_cover_palette(cover, color_count=2)

    _assert_palette_contract(report, color_count=2)
    assert report["primary"] != report["secondary"]
    assert report["fallback_used"] is False


def test_single_colour_cover_fills_eight_stable_colours(tmp_path: Path):
    cover = tmp_path / "single.png"
    Image.new("RGB", (48, 48), (25, 165, 105)).save(cover)

    first = extract_cover_palette(cover)
    second = extract_cover_palette(cover)

    _assert_palette_contract(first)
    assert first["fallback_used"] is True
    assert first["colors"] == second["colors"]
    assert first["candidates"] == second["candidates"]
    assert first["generator_sha256"] == second["generator_sha256"]


def test_single_blue_source_uses_a_soft_orange_complement_for_secondary(
    tmp_path: Path,
):
    cover = tmp_path / "blue.png"
    Image.new("RGB", (40, 40), (40, 90, 190)).save(cover)

    report = extract_cover_palette(cover)

    assert report["colors"][:2] == ["#285ABE", "#D6A33C"]
    primary, secondary = (
        karaoke_cover_palette._hex_to_rgb(color) for color in report["colors"][:2]
    )
    assert karaoke_cover_palette._lab_distance(primary, secondary) >= 28.0


def test_grayscale_cover_still_produces_readable_unique_colours(tmp_path: Path):
    cover = tmp_path / "gray.png"
    image = Image.new("L", (64, 64))
    for y in range(image.height):
        for x in range(image.width):
            image.putpixel((x, y), (x * 4 + y * 2) % 256)
    image.save(cover)

    report = extract_cover_palette(cover)

    _assert_palette_contract(report)
    assert report["fallback_used"] is True
    for color in report["colors"]:
        red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
        assert max(red, green, blue) >= 150
        assert max(red, green, blue) - min(red, green, blue) >= 55


def test_near_monochrome_cover_produces_visually_separated_singer_slots(
    tmp_path: Path,
):
    cover = tmp_path / "near-monochrome.png"
    image = Image.new("RGB", (80, 40))
    source_colors = (
        (190, 35, 45),
        (220, 50, 60),
        (155, 25, 35),
        (235, 70, 75),
    )
    for x in range(image.width):
        for y in range(image.height):
            image.putpixel((x, y), source_colors[x // 20])
    image.save(cover)

    report = extract_cover_palette(cover)
    rgb_colors = [
        karaoke_cover_palette._hex_to_rgb(color) for color in report["colors"]
    ]
    distances = [
        karaoke_cover_palette._lab_distance(left, right)
        for left, right in combinations(rgb_colors, 2)
    ]

    assert min(distances) >= 28.0


def test_same_pixels_at_different_paths_do_not_affect_algorithm(tmp_path: Path):
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "nested" / "renamed.png"
    second_path.parent.mkdir()
    _save_split_cover(first_path)
    second_path.write_bytes(first_path.read_bytes())

    first = extract_cover_palette(first_path)
    second = extract_cover_palette(second_path)

    assert first["colors"] == second["colors"]
    assert first["candidates"] == second["candidates"]
    assert first["cover_sha256"] == second["cover_sha256"]
    assert first["source_path"] != second["source_path"]


@pytest.mark.parametrize("color_count", [0, -1, 33])
def test_invalid_color_count_fails(tmp_path: Path, color_count: int):
    cover = tmp_path / "cover.png"
    Image.new("RGB", (2, 2), "red").save(cover)

    with pytest.raises(ValueError, match="color_count"):
        extract_cover_palette(cover, color_count=color_count)


def test_non_integer_color_count_fails(tmp_path: Path):
    cover = tmp_path / "cover.png"
    Image.new("RGB", (2, 2), "red").save(cover)

    with pytest.raises(TypeError, match="color_count"):
        extract_cover_palette(cover, color_count=True)


def test_missing_cover_fails(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        extract_cover_palette(tmp_path / "missing.png")


def test_normalize_hex_color():
    assert normalize_hex_color(" abc ") == "#AABBCC"
    assert normalize_hex_color("#12ef90") == "#12EF90"
    assert normalize_hex_color((1, 2, 255)) == "#0102FF"
    with pytest.raises(ValueError):
        normalize_hex_color("not-a-colour")
