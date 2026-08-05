#!/usr/bin/env python3
"""Build the small-sleeve, large-vinyl composition for the wide karaoke cut."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

CANVAS_SIZE = (1920, 1080)
SLEEVE_BOX = (60, 40, 280, 365)
RIGHT_PANEL = (640, 30, 1900, 970)
BOTTOM_PANEL = (20, 450, 1900, 1050)
BOTTOM_PANEL_FILL = (3, 5, 10, 92)


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    *,
    max_width: int,
    start_size: int,
    min_size: int = 19,
) -> ImageFont.FreeTypeFont:
    for size in range(start_size, min_size - 1, -1):
        font = _font(font_path, size)
        if draw.textlength(text, font=font) <= max_width:
            return font
    return _font(font_path, min_size)


def build_wide_composition(
    *,
    background_path: Path,
    cover_path: Path,
    regular_font: Path,
    bold_font: Path,
    title: str,
    artist: str,
    album_title: str,
    album_artist: str,
    output_path: Path,
) -> dict:
    canvas = Image.open(background_path).convert("RGBA")
    if canvas.size != CANVAS_SIZE:
        canvas = ImageOps.fit(canvas, CANVAS_SIZE, method=Image.Resampling.LANCZOS)
    cover = Image.open(cover_path).convert("RGB")

    panels = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panels)
    panel_draw.rounded_rectangle(RIGHT_PANEL, radius=46, fill=(4, 7, 14, 66))
    panel_draw.rounded_rectangle(BOTTOM_PANEL, radius=34, fill=BOTTOM_PANEL_FILL)
    canvas.alpha_composite(panels)

    sleeve_x, sleeve_y, sleeve_width, sleeve_height = SLEEVE_BOX
    shadow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (
            sleeve_x + 12,
            sleeve_y + 15,
            sleeve_x + sleeve_width + 12,
            sleeve_y + sleeve_height + 15,
        ),
        radius=26,
        fill=(0, 0, 0, 175),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=18)))

    sleeve = Image.new("RGBA", (sleeve_width, sleeve_height), (0, 0, 0, 0))
    sleeve_draw = ImageDraw.Draw(sleeve)
    sleeve_draw.rounded_rectangle(
        (0, 0, sleeve_width - 1, sleeve_height - 1),
        radius=26,
        fill=(243, 241, 235, 255),
        outline=(255, 255, 255, 150),
        width=3,
    )
    cover_size = sleeve_width - 40
    fitted_cover = ImageOps.fit(
        cover,
        (cover_size, cover_size),
        method=Image.Resampling.LANCZOS,
    )
    sleeve.paste(fitted_cover, (20, 20))
    footer_top = 20 + cover_size
    sleeve_draw.rectangle(
        (20, footer_top, sleeve_width - 20, sleeve_height - 20),
        fill=(238, 235, 228, 255),
    )
    album_title_font = _fit_font(
        sleeve_draw,
        album_title,
        bold_font,
        max_width=sleeve_width - 60,
        start_size=18,
        min_size=10,
    )
    sleeve_draw.text(
        (30, footer_top + 7),
        album_title,
        font=album_title_font,
        fill=(39, 42, 48, 240),
    )
    sleeve_draw.text(
        (30, footer_top + 32),
        album_artist,
        font=_font(regular_font, 15),
        fill=(82, 84, 89, 230),
    )
    canvas.alpha_composite(sleeve, (sleeve_x, sleeve_y))

    draw = ImageDraw.Draw(canvas)
    draw.text(
        (380, 70),
        "STUDIO KARAOKE / WIDE CUT",
        font=_font(bold_font, 18),
        fill=(234, 232, 227, 195),
    )
    title_font = _fit_font(
        draw,
        title,
        bold_font,
        max_width=430,
        start_size=48,
    )
    draw.text((380, 105), title, font=title_font, fill=(255, 255, 255, 255))
    artist_font = _fit_font(
        draw,
        artist,
        regular_font,
        max_width=430,
        start_size=28,
    )
    draw.text((380, 170), artist, font=artist_font, fill=(204, 207, 215, 238))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
    report = {
        "schema_version": 1,
        "output": str(output_path),
        "canvas": {"width": CANVAS_SIZE[0], "height": CANVAS_SIZE[1]},
        "sleeve": {
            "x": sleeve_x,
            "y": sleeve_y,
            "width": sleeve_width,
            "height": sleeve_height,
        },
        "right_panel": RIGHT_PANEL,
        "bottom_panel": BOTTOM_PANEL,
        "bottom_panel_fill": BOTTOM_PANEL_FILL,
        "title": title,
        "artist": artist,
        "album_title": album_title,
        "album_title_font_size": album_title_font.size,
        "album_title_max_width": sleeve_width - 60,
        "album_artist": album_artist,
    }
    output_path.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background", type=Path, required=True)
    parser.add_argument("--cover", type=Path, required=True)
    parser.add_argument("--font-regular", type=Path, required=True)
    parser.add_argument("--font-bold", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--artist", required=True)
    parser.add_argument("--album-title", required=True)
    parser.add_argument("--album-artist", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    paths = [args.background, args.cover, args.font_regular, args.font_bold]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing wide-artwork inputs: {missing}")
    report = build_wide_composition(
        background_path=args.background.resolve(),
        cover_path=args.cover.resolve(),
        regular_font=args.font_regular.resolve(),
        bold_font=args.font_bold.resolve(),
        title=args.title,
        artist=args.artist,
        album_title=args.album_title,
        album_artist=args.album_artist,
        output_path=args.output.resolve(),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
