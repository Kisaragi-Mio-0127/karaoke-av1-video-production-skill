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
from .ffmpeg_tools import (
    FFmpegToolError,
    prepend_ffmpeg_to_path,
    resolve_ffmpeg,
    resolve_ffprobe,
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
    "FFmpegToolError",
    "Lane",
    "PRONUNCIATION_VALIDATION_MODES",
    "PronunciationValidationResult",
    "STANDARD_LAYOUT",
    "SubtitleLayout",
    "WIDE_BASE_LAYOUT",
    "add_device_argument",
    "normalize_device",
    "prepend_ffmpeg_to_path",
    "resolve_ffmpeg",
    "resolve_ffprobe",
    "resolve_device",
    "validate_pronunciation",
]
