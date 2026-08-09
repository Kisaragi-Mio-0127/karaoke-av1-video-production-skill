"""Tests for deterministic cover-derived karaoke palettes."""

from pathlib import Path

import pytest
from PIL import Image

from scripts.karaoke_cover_palette import (
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


def test_low_saturation_cover_uses_warm_and_muted_cover_colours(tmp_path: Path):
    cover = tmp_path / "muted-illustration.png"
    image = Image.new("RGB", (100, 100), (220, 203, 196))
    for y in range(image.height):
        for x in range(30):
            image.putpixel((x, y), (61, 59, 74))
    image.save(cover)

    report = extract_cover_palette(cover)

    _assert_palette_contract(report)
    assert report["primary"] == "#DC9E84"
    assert report["secondary"] == "#756BB2"
    assert report["fallback_used"] is True
    assert [candidate["eligible"] for candidate in report["candidates"]] == [
        True,
        True,
    ]


def test_pale_peach_gradient_beats_tiny_near_black_hue_noise(tmp_path: Path):
    cover = tmp_path / "peach-gradient-with-dark-noise.png"
    image = Image.new("RGB", (128, 128))
    for y in range(image.height):
        for x in range(image.width):
            image.putpixel(
                (x, y),
                (210 + x % 32, 140 + y % 32, 115 + (x + y) % 24),
            )
    for y in range(16):
        for x in range(16):
            image.putpixel((x, y), (4, 1, 2))
    image.save(cover)

    first = extract_cover_palette(cover)
    second = extract_cover_palette(cover)

    _assert_palette_contract(first)
    red, green, blue = (
        int(first["primary"][index : index + 2], 16) for index in (1, 3, 5)
    )
    assert red > green > blue
    assert red >= 200 and green >= 130 and blue >= 100
    assert first["colors"] == second["colors"]
    dark_noise = next(
        candidate
        for candidate in first["candidates"]
        if candidate["source_color"] == "#040102"
    )
    assert dark_noise["eligible"] is False
    assert dark_noise["chroma"] < 0.06


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
