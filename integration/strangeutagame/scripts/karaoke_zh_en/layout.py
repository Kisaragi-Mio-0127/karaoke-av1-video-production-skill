"""Wide subtitle layouts for the no-ruby Chinese and English profiles."""

from dataclasses import replace

from scripts.karaoke_common.layout import WIDE_BASE_LAYOUT

ENGLISH_WIDE_MAIN_FONT_SIZE = 96
ENGLISH_WIDE_MIN_MAIN_FONT_SIZE = 54
ENGLISH_WIDE_LETTER_SPACING_EM = 0.0
ENGLISH_WIDE_RENDER_ADVANCE_SCALE = 0.85
ENGLISH_WIDE_WORD_GAP_EM = 0.18
ENGLISH_WIDE_MIN_SPLIT_WORDS = 3

CHINESE_WIDE_LAYOUT = replace(
    WIDE_BASE_LAYOUT,
    name="wide-bottom-zh",
    max_phrase_chars=None,
    semantic_gap_em=0.14,
    enforce_main_font_size=False,
)
ENGLISH_WIDE_LAYOUT = replace(
    WIDE_BASE_LAYOUT,
    name="wide-bottom-en",
    max_phrase_chars=None,
    main_font_size=ENGLISH_WIDE_MAIN_FONT_SIZE,
    min_main_font_size=ENGLISH_WIDE_MIN_MAIN_FONT_SIZE,
    advance_scale=ENGLISH_WIDE_RENDER_ADVANCE_SCALE,
    fit_advance_scale=ENGLISH_WIDE_RENDER_ADVANCE_SCALE,
    letter_spacing_em=ENGLISH_WIDE_LETTER_SPACING_EM,
    semantic_gap_em=0.0,
    word_gap_em=ENGLISH_WIDE_WORD_GAP_EM,
    enforce_main_font_size=False,
)
