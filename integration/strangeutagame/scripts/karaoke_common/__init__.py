"""Language-neutral karaoke rendering contracts."""

from .device import (
    DEFAULT_DEVICE,
    DEVICE_CHOICES,
    DeviceResolutionError,
    DeviceSelection,
    add_device_argument,
    normalize_device,
    resolve_device,
)
from .layout import STANDARD_LAYOUT, WIDE_BASE_LAYOUT, Lane, SubtitleLayout
from .pronunciation import (
    PRONUNCIATION_VALIDATION_MODES,
    PronunciationValidationResult,
    validate_pronunciation,
)

__all__ = [
    "DEFAULT_DEVICE",
    "DEVICE_CHOICES",
    "DeviceResolutionError",
    "DeviceSelection",
    "Lane",
    "PRONUNCIATION_VALIDATION_MODES",
    "PronunciationValidationResult",
    "STANDARD_LAYOUT",
    "SubtitleLayout",
    "WIDE_BASE_LAYOUT",
    "add_device_argument",
    "normalize_device",
    "resolve_device",
    "validate_pronunciation",
]
