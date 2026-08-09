#!/usr/bin/env python3
"""Deterministically extract readable karaoke colours from local cover art."""

from __future__ import annotations

import colorsys
import hashlib
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

SCHEMA_VERSION = "karaoke-cover-palette/v1"
METHOD = "fixed-grid-rgb5-lab-neighborhood-readable-hsv-fallback/v3"
_HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")
_MAX_COLOR_COUNT = 32
_MAX_SAMPLE_AXIS = 256
_MIN_SOURCE_SATURATION = 0.10
_MIN_SOURCE_CHROMA = 15 / 255
_MIN_LAB_DISTANCE = 28.0
_PRIMARY_NEIGHBOR_LAB_DISTANCE = 18.0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_hex_color(value: Any) -> str:
    """Return *value* as an uppercase ``#RRGGBB`` colour.

    Strings may use three- or six-digit CSS hex notation. RGB sequences are
    accepted to make the helper convenient at Pillow call sites.
    """

    if isinstance(value, str):
        match = _HEX_COLOR.fullmatch(value.strip())
        if match is None:
            raise ValueError(f"invalid hex colour: {value!r}")
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(channel * 2 for channel in digits)
        return f"#{digits.upper()}"

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if len(value) != 3:
            raise ValueError("an RGB colour must contain exactly three channels")
        channels = []
        for channel in value:
            if isinstance(channel, bool) or not isinstance(channel, int):
                raise TypeError("RGB channels must be integers")
            if not 0 <= channel <= 255:
                raise ValueError("RGB channels must be between 0 and 255")
            channels.append(channel)
        return "#{:02X}{:02X}{:02X}".format(*channels)

    raise TypeError("colour must be a hex string or a three-channel RGB sequence")


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    normalized = normalize_hex_color(value)
    return tuple(int(normalized[index : index + 2], 16) for index in (1, 3, 5))


def _rgb_to_hex(rgb: Iterable[int]) -> str:
    return normalize_hex_color(tuple(rgb))


def _readable_rgb(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    red, green, blue = (channel / 255.0 for channel in rgb)
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    saturation = min(0.88, max(0.40, saturation))
    value = min(0.90, max(0.70, value))
    return tuple(
        round(channel * 255) for channel in colorsys.hsv_to_rgb(hue, saturation, value)
    )


def _linear_channel(channel: int) -> float:
    value = channel / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def _rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    red, green, blue = (_linear_channel(channel) for channel in rgb)
    x = (0.4124564 * red + 0.3575761 * green + 0.1804375 * blue) / 0.95047
    y = 0.2126729 * red + 0.7151522 * green + 0.0721750 * blue
    z = (0.0193339 * red + 0.1191920 * green + 0.9503041 * blue) / 1.08883

    def transform(value: float) -> float:
        if value > 216 / 24389:
            return value ** (1 / 3)
        return (24389 / 27 * value + 16) / 116

    fx, fy, fz = transform(x), transform(y), transform(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _lab_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    left_lab = _rgb_to_lab(left)
    right_lab = _rgb_to_lab(right)
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left_lab, right_lab)))


def _sample_pixels(
    image: Image.Image,
) -> tuple[list[tuple[int, int, int]], dict[str, Any]]:
    rgba = ImageOps.exif_transpose(image).convert("RGBA")
    width, height = rgba.size
    sample_width = min(width, _MAX_SAMPLE_AXIS)
    sample_height = min(height, _MAX_SAMPLE_AXIS)
    pixels = rgba.load()
    sampled: list[tuple[int, int, int]] = []
    transparent_count = 0
    partial_alpha_count = 0

    for sample_y in range(sample_height):
        y = min(height - 1, sample_y * height // sample_height)
        for sample_x in range(sample_width):
            x = min(width - 1, sample_x * width // sample_width)
            red, green, blue, alpha = pixels[x, y]
            if alpha < 32:
                transparent_count += 1
                continue
            if alpha < 255:
                partial_alpha_count += 1
                # Composite translucent artwork over the intended dark context.
                red = (red * alpha + 24 * (255 - alpha)) // 255
                green = (green * alpha + 24 * (255 - alpha)) // 255
                blue = (blue * alpha + 24 * (255 - alpha)) // 255
            sampled.append((red, green, blue))

    return sampled, {
        "image_size": [width, height],
        "grid_size": [sample_width, sample_height],
        "grid_pixel_count": sample_width * sample_height,
        "sampled_pixel_count": len(sampled),
        "transparent_skipped": transparent_count,
        "partial_alpha_composited": partial_alpha_count,
        "quantization_bits_per_channel": 5,
    }


def _build_candidates(
    pixels: list[tuple[int, int, int]],
) -> list[dict[str, Any]]:
    bins: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for red, green, blue in pixels:
        key = (red >> 3) << 10 | (green >> 3) << 5 | (blue >> 3)
        aggregate = bins[key]
        aggregate[0] += 1
        aggregate[1] += red
        aggregate[2] += green
        aggregate[3] += blue

    total = max(1, len(pixels))
    raw_candidates = []
    for key, (count, red_sum, green_sum, blue_sum) in bins.items():
        rgb = (
            round(red_sum / count),
            round(green_sum / count),
            round(blue_sum / count),
        )
        _, saturation, value = colorsys.rgb_to_hsv(
            *(channel / 255.0 for channel in rgb)
        )
        chroma = (max(rgb) - min(rgb)) / 255.0
        raw_candidates.append((key, count, rgb, saturation, value, chroma))

    raw_candidates.sort(key=lambda item: (-item[1], -item[3], item[0]))
    candidates = []
    for key, count, rgb, saturation, value, chroma in raw_candidates[:64]:
        readable = _readable_rgb(rgb)
        candidates.append(
            {
                "bin": f"{key:04X}",
                "source_color": _rgb_to_hex(rgb),
                "color": _rgb_to_hex(readable),
                "population": count,
                "share": round(count / total, 8),
                "saturation": round(saturation, 6),
                "value": round(value, 6),
                "chroma": round(chroma, 6),
                "eligible": (
                    saturation >= _MIN_SOURCE_SATURATION
                    and chroma >= _MIN_SOURCE_CHROMA
                ),
                "readability_adjusted": readable != rgb,
            }
        )

    # A smooth sky or skin-tone region is normally split across many RGB5 bins.
    # Rank its representative by the total nearby Lab population instead of
    # letting one tiny, highly saturated (often near-black) bin dominate.
    eligible = [candidate for candidate in candidates if candidate["eligible"]]
    for candidate in candidates:
        neighborhood_share = 0.0
        primary_score = 0.0
        if candidate["eligible"]:
            source_rgb = _hex_to_rgb(candidate["source_color"])
            neighborhood_share = sum(
                neighbor["share"]
                for neighbor in eligible
                if _lab_distance(
                    source_rgb, _hex_to_rgb(neighbor["source_color"])
                )
                <= _PRIMARY_NEIGHBOR_LAB_DISTANCE
            )
            value_fit = max(0.35, 1.0 - 1.8 * abs(candidate["value"] - 0.86))
            colorfulness = 0.10 + 0.90 * candidate["saturation"]
            primary_score = neighborhood_share * colorfulness * value_fit
        candidate["neighborhood_share"] = round(neighborhood_share, 8)
        candidate["primary_score"] = round(primary_score, 10)
    return candidates


def _select_source_colours(
    candidates: list[dict[str, Any]], color_count: int
) -> tuple[list[tuple[int, int, int]], list[dict[str, Any]]]:
    eligible = [candidate for candidate in candidates if candidate["eligible"]]
    unique: dict[str, dict[str, Any]] = {}
    for candidate in eligible:
        current = unique.get(candidate["color"])
        if current is None or candidate["population"] > current["population"]:
            unique[candidate["color"]] = candidate
    pool = list(unique.values())
    if not pool:
        return [], []

    pool.sort(
        key=lambda candidate: (
            -candidate["primary_score"],
            -candidate["population"],
            candidate["color"],
        )
    )
    selected_candidates = [pool.pop(0)]
    selected = [_hex_to_rgb(selected_candidates[0]["color"])]

    while pool and len(selected) < color_count:
        ranked = []
        for candidate in pool:
            rgb = _hex_to_rgb(candidate["color"])
            distance = min(_lab_distance(rgb, chosen) for chosen in selected)
            score = 0.45 * min(1.0, candidate["share"] * 12) + 0.55 * min(
                1.0, distance / 70.0
            )
            ranked.append((score, distance, candidate["population"], candidate, rgb))
        ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]["color"]))
        _, distance, _, candidate, rgb = ranked[0]
        pool.remove(candidate)
        if distance < _MIN_LAB_DISTANCE:
            continue
        selected_candidates.append(candidate)
        selected.append(rgb)

    return selected, selected_candidates


def _fallback_colours(
    selected: list[tuple[int, int, int]], color_count: int, seed_hex: str
) -> list[tuple[int, int, int]]:
    seed = int(seed_hex[:16], 16)
    base_hue = (seed % 36000) / 36000.0
    generated: list[tuple[int, int, int]] = []
    existing = list(selected)

    while len(existing) < color_count:
        if len(existing) == 1 and not generated:
            red, green, blue = (channel / 255.0 for channel in existing[0])
            hue, saturation, _value = colorsys.rgb_to_hsv(red, green, blue)
            complement = tuple(
                round(channel * 255)
                for channel in colorsys.hsv_to_rgb(
                    (hue + 0.5) % 1.0,
                    min(0.72, max(0.58, saturation)),
                    0.84,
                )
            )
            if _lab_distance(complement, existing[0]) >= _MIN_LAB_DISTANCE:
                generated.append(complement)
                existing.append(complement)
                continue
        pool = []
        start = len(generated)
        for offset in range(96):
            index = start + offset
            hue = (base_hue + index * 0.6180339887498949) % 1.0
            saturation = (0.58, 0.68, 0.78)[(seed + index) % 3]
            value = (0.78, 0.84, 0.90)[((seed >> 8) + index) % 3]
            rgb = tuple(
                round(channel * 255)
                for channel in colorsys.hsv_to_rgb(hue, saturation, value)
            )
            if rgb in existing:
                continue
            distance = min(
                (_lab_distance(rgb, colour) for colour in existing), default=100.0
            )
            pool.append((distance, -index, rgb))
        if not pool:  # Defensive; the supported maximum cannot exhaust RGB space.
            raise RuntimeError("unable to derive enough unique fallback colours")
        pool.sort(reverse=True)
        chosen = pool[0][2]
        generated.append(chosen)
        existing.append(chosen)

    return generated


def validate_palette_report(
    report: Mapping[str, Any], *, expected_color_count: int | None = None
) -> Mapping[str, Any]:
    """Validate the stable public fields of a palette report and return it."""

    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported palette report schema")
    colors = report.get("colors")
    if not isinstance(colors, list) or not colors:
        raise ValueError("palette report colors must be a non-empty list")
    normalized = [normalize_hex_color(color) for color in colors]
    if normalized != colors or len(set(colors)) != len(colors):
        raise ValueError(
            "palette report colors must be unique uppercase #RRGGBB values"
        )
    if expected_color_count is not None and len(colors) != expected_color_count:
        raise ValueError("palette report has an unexpected color count")
    if report.get("primary") != colors[0]:
        raise ValueError("palette report primary must be its first color")
    expected_secondary = colors[1] if len(colors) > 1 else None
    if report.get("secondary") != expected_secondary:
        raise ValueError("palette report secondary must be its second color")
    for field in ("cover_sha256", "generator_sha256"):
        value = report.get(field)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(f"palette report {field} must be a SHA-256 hex digest")
    return report


def extract_cover_palette(cover_path: Path, *, color_count: int = 8) -> dict[str, Any]:
    """Extract an ordered, dark-background-safe palette from a local image."""

    if isinstance(color_count, bool) or not isinstance(color_count, int):
        raise TypeError("color_count must be an integer")
    if not 1 <= color_count <= _MAX_COLOR_COUNT:
        raise ValueError(f"color_count must be between 1 and {_MAX_COLOR_COUNT}")

    path = Path(cover_path)
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise ValueError(f"cover path is not a file: {path}")

    cover_sha256 = _sha256_file(path)
    with Image.open(path) as image:
        pixels, sample = _sample_pixels(image)
        image_format = image.format
    candidates = _build_candidates(pixels)
    selected, selected_candidates = _select_source_colours(candidates, color_count)
    fallback = _fallback_colours(selected, color_count, cover_sha256)
    colours = selected + fallback
    color_hex = [_rgb_to_hex(colour) for colour in colours]

    adjustments: list[dict[str, Any]] = []
    for candidate in selected_candidates:
        if candidate["readability_adjusted"]:
            adjustments.append(
                {
                    "type": "readability",
                    "source": candidate["source_color"],
                    "result": candidate["color"],
                }
            )
    if fallback:
        adjustments.append(
            {
                "type": "deterministic-hsv-fallback",
                "added": len(fallback),
                "reason": "insufficient distinct chromatic cover colours",
            }
        )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_path": str(path),
        "cover_sha256": cover_sha256,
        "generator_sha256": _sha256_file(Path(__file__).resolve()),
        "method": METHOD,
        "sample": {**sample, "image_format": image_format},
        "candidates": candidates,
        "colors": color_hex,
        "primary": color_hex[0],
        "secondary": color_hex[1] if len(color_hex) > 1 else None,
        "fallback_used": bool(fallback),
        "adjustments": adjustments,
    }
    validate_palette_report(report, expected_color_count=color_count)
    return report


__all__ = [
    "METHOD",
    "SCHEMA_VERSION",
    "extract_cover_palette",
    "normalize_hex_color",
    "validate_palette_report",
]
