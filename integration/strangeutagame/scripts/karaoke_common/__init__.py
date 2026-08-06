"""Language-neutral karaoke rendering contracts."""

from .layout import STANDARD_LAYOUT, WIDE_BASE_LAYOUT, Lane, SubtitleLayout
from .pronunciation import (
    PRONUNCIATION_VALIDATION_MODES,
    PronunciationValidationResult,
    validate_pronunciation,
)

__all__ = [
    "Lane",
    "PRONUNCIATION_VALIDATION_MODES",
    "PronunciationValidationResult",
    "STANDARD_LAYOUT",
    "SubtitleLayout",
    "WIDE_BASE_LAYOUT",
    "validate_pronunciation",
]
