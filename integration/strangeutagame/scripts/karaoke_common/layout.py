"""Shared subtitle geometry used by every karaoke language profile."""

from __future__ import annotations

from dataclasses import dataclass

CANVAS_WIDTH = 1920
MAIN_FONT_SIZE = 52
MIN_MAIN_FONT_SIZE = 38
RUBY_FONT_SIZE = 24
MAIN_ADVANCE_SCALE = 0.78
MAIN_OUTLINE_PX = 4
RUBY_OUTLINE_PX = 2
MAIN_GLOW_BLUR = 8
RUBY_GLOW_BLUR = 5
STANDARD_RIGHT_START_X = 860
STANDARD_RIGHT_SAFE_EDGE_X = 1890
STANDARD_RIGHT_SAFE_MARGIN_PX = CANVAS_WIDTH - STANDARD_RIGHT_SAFE_EDGE_X
STANDARD_RIGHT_AVAILABLE_WIDTH = (
    CANVAS_WIDTH - STANDARD_RIGHT_START_X - STANDARD_RIGHT_SAFE_MARGIN_PX
)
SLOT_WIDTH = STANDARD_RIGHT_AVAILABLE_WIDTH
WIDE_RUBY_TO_MAIN_ANCHOR_GAP_PX = 35


@dataclass(frozen=True)
class Lane:
    """One of two staggered subtitle slots."""

    x: int
    main_y: int
    ruby_y: int
    alignment: int


@dataclass(frozen=True)
class SubtitleLayout:
    """Subtitle geometry plus video-side vinyl placement for one edition."""

    name: str
    lanes: tuple[Lane, Lane]
    advance_scale: float
    slot_width: int
    vinyl_x: int
    vinyl_y: int
    vinyl_size: int
    main_font_size: int = MAIN_FONT_SIZE
    min_main_font_size: int = MIN_MAIN_FONT_SIZE
    ruby_font_size: int = RUBY_FONT_SIZE
    max_phrase_chars: int | None = None
    fit_advance_scale: float = 1.0
    fit_outline_px: int = 0
    semantic_gap_em: float = 0.0
    letter_spacing_em: float = 0.0
    word_gap_em: float | None = None
    enforce_main_font_size: bool = True
    main_outline_px: int = MAIN_OUTLINE_PX
    ruby_outline_px: int = RUBY_OUTLINE_PX
    main_glow_blur: int = MAIN_GLOW_BLUR
    ruby_glow_blur: int = RUBY_GLOW_BLUR


STANDARD_LAYOUT = SubtitleLayout(
    name="standard-v7",
    lanes=(
        Lane(x=STANDARD_RIGHT_START_X, main_y=790, ruby_y=762, alignment=7),
        Lane(x=STANDARD_RIGHT_SAFE_EDGE_X, main_y=960, ruby_y=932, alignment=9),
    ),
    advance_scale=MAIN_ADVANCE_SCALE,
    slot_width=SLOT_WIDTH,
    vinyl_x=1030,
    vinyl_y=110,
    vinyl_size=860,
    fit_advance_scale=MAIN_ADVANCE_SCALE,
    fit_outline_px=MAIN_OUTLINE_PX,
)

# Language-neutral wide geometry. Language packages derive their public
# layouts from this value without importing one another.
WIDE_BASE_LAYOUT = SubtitleLayout(
    name="wide-base",
    lanes=(
        Lane(x=32, main_y=660, ruby_y=625, alignment=7),
        Lane(x=1888, main_y=870, ruby_y=835, alignment=9),
    ),
    advance_scale=MAIN_ADVANCE_SCALE,
    slot_width=1856,
    vinyl_x=790,
    vinyl_y=-10,
    vinyl_size=1100,
    main_font_size=108,
    min_main_font_size=75,
    ruby_font_size=51,
    max_phrase_chars=12,
    fit_advance_scale=MAIN_ADVANCE_SCALE,
    fit_outline_px=6,
    main_outline_px=6,
    ruby_outline_px=3,
    main_glow_blur=12,
    ruby_glow_blur=8,
)
