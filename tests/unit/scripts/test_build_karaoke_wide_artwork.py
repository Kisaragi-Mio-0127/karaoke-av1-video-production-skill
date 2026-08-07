"""Tests for the alternate small-sleeve karaoke composition."""

import os
from pathlib import Path

from PIL import Image

from scripts.build_karaoke_wide_artwork import (
    CANVAS_SIZE,
    WIDE_LAYOUT_VERSION,
    build_wide_composition,
)

FONT = Path(os.environ["WINDIR"]) / "Fonts" / "arial.ttf"


def _artwork(tmp_path: Path) -> tuple[Path, Path]:
    background = tmp_path / "background.png"
    cover = tmp_path / "cover.png"
    Image.new("RGB", CANVAS_SIZE, (20, 24, 32)).save(background)
    Image.new("RGB", (640, 640), (180, 120, 80)).save(cover)
    return background, cover


def test_build_wide_composition(tmp_path: Path):
    background, cover = _artwork(tmp_path)
    output = tmp_path / "composition_wide.png"

    report = build_wide_composition(
        background_path=background,
        cover_path=cover,
        regular_font=FONT,
        bold_font=FONT,
        title="Example Track",
        artist="Example Artist",
        album_title="Example Album",
        album_artist="Example Artist",
        output_path=output,
    )

    with Image.open(output) as image:
        assert image.size == CANVAS_SIZE
    assert output.with_suffix(".json").is_file()
    assert report["layout_version"] == WIDE_LAYOUT_VERSION
    assert len(report["layout_generator_sha256"]) == 64
    assert len(report["composition_sha256"]) == 64
    assert report["sleeve"]["width"] < 690
    assert report["sleeve"]["width"] == 340
    assert report["sleeve"]["x"] == 40
    assert report["sleeve"]["y"] == 30
    assert report["sleeve"]["y"] + report["sleeve"]["height"] <= 576
    assert report["sleeve_footer"]["height"] == 70
    assert report["sleeve_footer"]["bottom_padding"] == 12
    assert report["right_panel"] is None
    assert report["right_panel_visible"] is False
    assert report["outer_right_panel"] is None
    assert report["outer_right_panel_visible"] is False
    assert report["vinyl_backplate"] is None
    assert report["vinyl_backplate_present"] is False
    assert report["vinyl_backplate_preserved"] is False
    assert report["bottom_panel"] == (20, 576, 1900, 1050)
    assert report["title_block_y"] == {"label": 120, "title": 155, "artist": 220}
    assert report["secondary_overlay_contract"] == {
        "anchor_y": 12,
        "font_size_px": 60,
        "safe_bounds": (0, 0, 1920, 96),
        "outline_px": 3,
        "glow_px": 8,
        "reserved_bounds": (0, 0, 1920, 107),
    }
    assert report["secondary_reserved_bounds"] == (0, 0, 1920, 107)
    assert report["title_secondary_clearance_px"] >= 16
    assert report["title_secondary_collision"] is False
    assert (
        report["title_bounds"][1] - report["secondary_reserved_bounds"][3]
        == report["title_secondary_clearance_px"]
    )
    assert report["bottom_panel_fill"][3] >= 90
    assert report["album_title"] == "Example Album"
    assert 10 <= report["album_title_font_size"] <= 18
    assert report["album_artist"] == "Example Artist"


def test_spectrum_composition_moves_title_and_expands_sleeve(tmp_path: Path):
    background, cover = _artwork(tmp_path)
    output = tmp_path / "composition_spectrum.png"

    report = build_wide_composition(
        background_path=background,
        cover_path=cover,
        regular_font=FONT,
        bold_font=FONT,
        title="Example Track",
        artist="Example Artist",
        album_title="Example Album",
        album_artist="Example Artist",
        output_path=output,
        visual_style="spectrum",
    )

    assert report["visual_style"] == "spectrum"
    assert report["sleeve"] == {"x": 40, "y": 30, "width": 460, "height": 522}
    assert report["title_block_x"] == 800
    alignment = report["title_block_ink_alignment"]
    assert alignment["label"]["visual_left"] == 800
    assert alignment["title"]["visual_left"] == 800
    assert alignment["artist"]["visual_left"] == 800
    assert alignment["title"]["draw_x"] <= 800
    assert report["title_block_y"] == {"label": 120, "title": 155, "artist": 220}
    assert alignment["label"]["ink_bounds"][1] >= 120
    assert alignment["title"]["ink_bounds"][1] >= 155
    assert alignment["artist"]["ink_bounds"][1] >= 220
    assert report["title_bounds"][1] > report["secondary_reserved_bounds"][3]
    assert report["sleeve"]["y"] + report["sleeve"]["height"] < 576
