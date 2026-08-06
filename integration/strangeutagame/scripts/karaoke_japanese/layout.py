"""Japanese wide subtitle layout."""

from dataclasses import replace

from scripts.karaoke_common.layout import WIDE_BASE_LAYOUT

WIDE_SEMANTIC_GAP_EM = 0.14
WIDE_LAYOUT = replace(
    WIDE_BASE_LAYOUT,
    name="wide-bottom",
    semantic_gap_em=WIDE_SEMANTIC_GAP_EM,
)
