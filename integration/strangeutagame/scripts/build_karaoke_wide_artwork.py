#!/usr/bin/env python3
"""Build the small-sleeve, large-vinyl composition for the wide karaoke cut."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

try:
    from scripts.karaoke_cover_palette import extract_cover_palette
except ImportError:  # pragma: no cover - direct script entry point
    from karaoke_cover_palette import extract_cover_palette  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED_FONT_DIR = REPO_ROOT / "assets" / "fonts" / "HarmonyOS-Sans"
SHARED_REGULAR_FONT = SHARED_FONT_DIR / "HarmonyOS_Sans_SC_Regular.ttf"
SHARED_BOLD_FONT = SHARED_FONT_DIR / "HarmonyOS_Sans_SC_Bold.ttf"
CANVAS_SIZE = (1920, 1080)
WIDE_LAYOUT_VERSION = "wide-layout-v7/cover-palette"
SLEEVE_BOXES = {
    "vinyl": (40, 30, 340, 402),
    "spectrum": (40, 30, 460, 522),
    "spectrum-line": (40, 30, 460, 522),
    "spectrum-mirror": (40, 30, 460, 522),
    "spectrum-dots": (40, 30, 460, 522),
    "spectrum-ribbon": (40, 30, 460, 522),
}
BOTTOM_PANEL = (20, 576, 1900, 1050)
BOTTOM_PANEL_FILL = (3, 5, 10, 92)
SLEEVE_MARGIN = 20
SLEEVE_FOOTER_HEIGHT = 70
SLEEVE_BOTTOM_PADDING = 12
TITLE_BLOCK_X = {
    "vinyl": 430,
    "spectrum": 800,
    "spectrum-line": 800,
    "spectrum-mirror": 800,
    "spectrum-dots": 800,
    "spectrum-ribbon": 800,
}
TITLE_BLOCK_Y = {"label": 120, "title": 155, "artist": 220}
SECONDARY_OVERLAY_SAFE_BOUNDS = (0, 0, 1920, 96)
SECONDARY_OVERLAY_OUTLINE_PX = 3
SECONDARY_OVERLAY_GLOW_PX = 8
MIN_TITLE_SECONDARY_CLEARANCE_PX = 16
SECONDARY_RESERVED_BOUNDS = (
    0,
    0,
    1920,
    SECONDARY_OVERLAY_SAFE_BOUNDS[3]
    + SECONDARY_OVERLAY_OUTLINE_PX
    + SECONDARY_OVERLAY_GLOW_PX,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _draw_text_with_visual_left(
    draw: ImageDraw.ImageDraw,
    *,
    visual_left: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
) -> dict[str, int | tuple[int, int, int, int]]:
    mask_bbox = font.getmask(text).getbbox()
    ink_left_offset = mask_bbox[0] if mask_bbox is not None else 0
    draw_x = visual_left - ink_left_offset
    ink_bounds = tuple(int(value) for value in draw.textbbox((draw_x, y), text, font=font))
    draw.text((draw_x, y), text, font=font, fill=fill)
    return {
        "visual_left": visual_left,
        "draw_x": draw_x,
        "ink_left_offset": ink_left_offset,
        "ink_bounds": ink_bounds,
    }


def _union_bounds(*bounds: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return (
        min(value[0] for value in bounds),
        min(value[1] for value in bounds),
        max(value[2] for value in bounds),
        max(value[3] for value in bounds),
    )


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
    visual_style: str = "vinyl",
) -> dict:
    if visual_style not in SLEEVE_BOXES:
        raise ValueError(f"unsupported visual style: {visual_style}")
    canvas = Image.open(background_path).convert("RGBA")
    if canvas.size != CANVAS_SIZE:
        canvas = ImageOps.fit(canvas, CANVAS_SIZE, method=Image.Resampling.LANCZOS)
    cover = Image.open(cover_path).convert("RGB")
    cover_palette = extract_cover_palette(cover_path, color_count=8)

    panels = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panels)
    panel_draw.rounded_rectangle(BOTTOM_PANEL, radius=34, fill=BOTTOM_PANEL_FILL)
    canvas.alpha_composite(panels)

    sleeve_x, sleeve_y, sleeve_width, sleeve_height = SLEEVE_BOXES[visual_style]
    title_block_x = TITLE_BLOCK_X[visual_style]
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
    cover_size = sleeve_width - 2 * SLEEVE_MARGIN
    fitted_cover = ImageOps.fit(
        cover,
        (cover_size, cover_size),
        method=Image.Resampling.LANCZOS,
    )
    sleeve.paste(fitted_cover, (SLEEVE_MARGIN, SLEEVE_MARGIN))
    footer_top = SLEEVE_MARGIN + cover_size
    footer_bottom = min(
        sleeve_height - SLEEVE_BOTTOM_PADDING,
        footer_top + SLEEVE_FOOTER_HEIGHT,
    )
    sleeve_draw.rectangle(
        (
            SLEEVE_MARGIN,
            footer_top,
            sleeve_width - SLEEVE_MARGIN,
            footer_bottom,
        ),
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
        (30, footer_top + 36),
        album_artist,
        font=_font(regular_font, 15),
        fill=(82, 84, 89, 230),
    )
    canvas.alpha_composite(sleeve, (sleeve_x, sleeve_y))

    draw = ImageDraw.Draw(canvas)
    label_alignment = _draw_text_with_visual_left(
        draw,
        visual_left=title_block_x,
        y=TITLE_BLOCK_Y["label"],
        text="STUDIO KARAOKE / WIDE CUT",
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
    title_alignment = _draw_text_with_visual_left(
        draw,
        visual_left=title_block_x,
        y=TITLE_BLOCK_Y["title"],
        text=title,
        font=title_font,
        fill=(255, 255, 255, 255),
    )
    artist_font = _fit_font(
        draw,
        artist,
        regular_font,
        max_width=430,
        start_size=28,
    )
    artist_alignment = _draw_text_with_visual_left(
        draw,
        visual_left=title_block_x,
        y=TITLE_BLOCK_Y["artist"],
        text=artist,
        font=artist_font,
        fill=(204, 207, 215, 238),
    )

    title_bounds = _union_bounds(
        tuple(label_alignment["ink_bounds"]),
        tuple(title_alignment["ink_bounds"]),
        tuple(artist_alignment["ink_bounds"]),
    )
    title_secondary_clearance_px = title_bounds[1] - SECONDARY_RESERVED_BOUNDS[3]
    if title_secondary_clearance_px < MIN_TITLE_SECONDARY_CLEARANCE_PX:
        raise ValueError(
            "wide artwork title does not clear the top secondary overlay reserve: "
            f"title_bounds={title_bounds} "
            f"secondary_reserved_bounds={SECONDARY_RESERVED_BOUNDS} "
            f"clearance_px={title_secondary_clearance_px} "
            f"required_px={MIN_TITLE_SECONDARY_CLEARANCE_PX}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
    report = {
        "schema_version": 1,
        "layout_version": WIDE_LAYOUT_VERSION,
        "layout_generator": "scripts/build_karaoke_wide_artwork.py",
        "layout_generator_sha256": _sha256_file(Path(__file__).resolve()),
        "output": str(output_path),
        "composition_sha256": _sha256_file(output_path),
        "cover": {
            "path": str(cover_path),
            "sha256": _sha256_file(cover_path),
        },
        "cover_palette": cover_palette,
        "canvas": {"width": CANVAS_SIZE[0], "height": CANVAS_SIZE[1]},
        "sleeve": {
            "x": sleeve_x,
            "y": sleeve_y,
            "width": sleeve_width,
            "height": sleeve_height,
        },
        "right_panel": None,
        "right_panel_visible": False,
        "outer_right_panel": None,
        "outer_right_panel_visible": False,
        "vinyl_backplate": None,
        "vinyl_backplate_present": False,
        # Compatibility field for consumers of the former preservation flag.
        "vinyl_backplate_preserved": False,
        "bottom_panel": BOTTOM_PANEL,
        "bottom_panel_fill": BOTTOM_PANEL_FILL,
        "title": title,
        "artist": artist,
        "album_title": album_title,
        "album_title_font_size": album_title_font.size,
        "album_title_max_width": sleeve_width - 60,
        "album_artist": album_artist,
        "sleeve_footer": {
            "top": footer_top,
            "bottom": footer_bottom,
            "height": footer_bottom - footer_top,
            "bottom_padding": sleeve_height - footer_bottom,
        },
        "title_block_x": title_block_x,
        "title_block_y": TITLE_BLOCK_Y,
        "title_bounds": title_bounds,
        "secondary_overlay_contract": {
            "anchor_y": 12,
            "font_size_px": 60,
            "safe_bounds": SECONDARY_OVERLAY_SAFE_BOUNDS,
            "outline_px": SECONDARY_OVERLAY_OUTLINE_PX,
            "glow_px": SECONDARY_OVERLAY_GLOW_PX,
            "reserved_bounds": SECONDARY_RESERVED_BOUNDS,
        },
        "secondary_reserved_bounds": SECONDARY_RESERVED_BOUNDS,
        "title_secondary_clearance_px": title_secondary_clearance_px,
        "title_secondary_collision": False,
        "title_block_ink_alignment": {
            "label": label_alignment,
            "title": title_alignment,
            "artist": artist_alignment,
        },
        "visual_style": visual_style,
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
    parser.add_argument("--font-regular", type=Path, default=SHARED_REGULAR_FONT)
    parser.add_argument("--font-bold", type=Path, default=SHARED_BOLD_FONT)
    parser.add_argument("--title", required=True)
    parser.add_argument("--artist", required=True)
    parser.add_argument("--album-title", required=True)
    parser.add_argument("--album-artist", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--visual-style",
        choices=sorted(SLEEVE_BOXES),
        default="vinyl",
    )
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
        visual_style=args.visual_style,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
