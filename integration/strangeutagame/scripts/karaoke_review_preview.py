#!/usr/bin/env python3
"""Render short review clips from canonical SUG ruby facts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from types import SimpleNamespace
from typing import Any

import imageio_ffmpeg
from PIL import ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.karaoke_common.layout import (  # noqa: E402
    CANVAS_WIDTH,
    MAIN_ADVANCE_SCALE,
    MAIN_FONT_SIZE,
    MAIN_GLOW_BLUR,
    MAIN_OUTLINE_PX,
    MIN_MAIN_FONT_SIZE,
    RUBY_FONT_SIZE,
    RUBY_GLOW_BLUR,
    RUBY_OUTLINE_PX,
    SLOT_WIDTH,
    STANDARD_LAYOUT,
    STANDARD_RIGHT_AVAILABLE_WIDTH,
    STANDARD_RIGHT_SAFE_EDGE_X,
    STANDARD_RIGHT_SAFE_MARGIN_PX,
    STANDARD_RIGHT_START_X,
    WIDE_RUBY_TO_MAIN_ANCHOR_GAP_PX,
    Lane,
    SubtitleLayout,
)
from scripts.karaoke_common.pronunciation import (  # noqa: E402
    PRONUNCIATION_VALIDATION_MODES,
    validate_pronunciation,
)
from scripts.karaoke_japanese.layout import (  # noqa: E402
    WIDE_LAYOUT,
    WIDE_SEMANTIC_GAP_EM,
)
from scripts.karaoke_language import (  # noqa: E402
    DEFAULT_LANGUAGE,
    language_identity,
    normalize_language,
    uses_ruby,
)
from scripts.karaoke_timing import ms_to_ass_time, verify_font  # noqa: E402
from scripts.render_vinyl_karaoke import escape_filter_path  # noqa: E402
from scripts.sug_ruby import (  # noqa: E402
    RubyToken,
    RubyValidationError,
    iter_sug_ruby_spans,
    load_review_sidecar,
    sug_hash,
)

try:  # noqa: E402
    from scripts.sug_ruby import validate_review_sidecar
except ImportError:  # pragma: no cover - supplied by the ruby-review lane
    validate_review_sidecar = None
from strange_uta_game.backend.domain import Sentence  # noqa: E402
from strange_uta_game.backend.infrastructure.persistence.sug_io import (  # noqa: E402
    SugProjectParser,
)

__all__ = [
    "Lane",
    "STANDARD_LAYOUT",
    "STANDARD_RIGHT_AVAILABLE_WIDTH",
    "STANDARD_RIGHT_SAFE_EDGE_X",
    "STANDARD_RIGHT_SAFE_MARGIN_PX",
    "STANDARD_RIGHT_START_X",
    "SubtitleLayout",
    "WIDE_LAYOUT",
    "WIDE_RUBY_TO_MAIN_ANCHOR_GAP_PX",
    "WIDE_SEMANTIC_GAP_EM",
    "build_review_ass",
    "build_review_ass_required",
]


def _require_reviewed_canonical_ruby(
    project: Any,
    sidecar: Mapping[str, Any] | None,
    canonical_spans: list[Any],
) -> None:
    """Require the reviewed sidecar before rendering any canonical ruby."""

    if not canonical_spans:
        return
    if sidecar is None:
        raise RubyValidationError(
            "canonical ruby spans require a reviewed ruby sidecar"
        )
    if not callable(validate_review_sidecar):
        raise RubyValidationError(
            "canonical ruby review validator is unavailable"
        )
    try:
        errors = validate_review_sidecar(project, sidecar)
    except RubyValidationError:
        raise
    except Exception as error:
        raise RubyValidationError(
            f"canonical ruby review validation failed: {error}"
        ) from error
    if errors:
        raise RubyValidationError(
            "canonical ruby review sidecar rejected: "
            + "; ".join(str(error) for error in errors)
        )

FONT_FAMILY = "HarmonyOS Sans SC"
SHARED_FONT_DIR = REPO_ROOT / "assets" / "fonts" / "HarmonyOS-Sans"
SHARED_FONT_FILE = SHARED_FONT_DIR / "HarmonyOS_Sans_SC_Regular.ttf"
# Stable release profile shared by full renders and review reports.
DEFAULT_AV1_CQ = 38
DEFAULT_AV1_PRESET = "p7"
SECONDARY_FONT_SIZE = 51
SECONDARY_MIN_FONT_SIZE = 36
SECONDARY_OUTLINE_PX = 3
SECONDARY_GLOW_BLUR = 8
SECONDARY_TOP_Y = 72
SECONDARY_TOP_SAFE_TOP_PX = 24
SECONDARY_TOP_SAFE_BOTTOM_PX = 160
SECONDARY_SAFE_MARGIN_X = 160
PRE_ROLL_MS = 200
POST_ROLL_MS = 180
MAX_EARLY_DISPLAY_MS = 20_000
MIN_DISPLAY_PHRASE_CHARS = 6
MAX_DISPLAY_PHRASE_CHARS = 16
DISPLAY_PHRASE_SOFT_OVERRUN = 2
INTERLUDE_GAP_THRESHOLD_MS = 8_000
VOCAL_CUE_LEAD_MS = 3_000
VOCAL_CUE_DOT_COUNT = 4
VOCAL_CUE_FONT_SIZE = 39
VOCAL_CUE_DOT_SPACING = 51
VOCAL_CUE_ABOVE_RUBY_PX = 16
OUTRO_MAIN_Y = 765
OUTRO_RUBY_Y = OUTRO_MAIN_Y - WIDE_RUBY_TO_MAIN_ANCHOR_GAP_PX
DEFAULT_HIGHLIGHT_COLOR = "#FF0000"
COMPATIBILITY_AUDIO_BITRATE = "320k"
COMPATIBILITY_AUDIO_PROFILE = "aac_low"
LOSSLESS_AUDIO_CODEC = "flac"
SUBTITLE_LAYOUTS = {
    "standard": STANDARD_LAYOUT,
    "wide": WIDE_LAYOUT,
}
LANES = STANDARD_LAYOUT.lanes


@dataclass(frozen=True)
class TextGeometry:
    """Measured line boundaries shared by base glyphs and ruby."""

    left: float
    boundaries: tuple[float, ...]
    glyph_starts: tuple[float, ...]
    glyph_ends: tuple[float, ...]
    width: float = 0.0
    letter_spacing_px: float = 0.0
    semantic_gap_px: float = 0.0
    word_gap_px: float | None = None

    @property
    def right(self) -> float:
        return self.left + self.width


@dataclass(frozen=True)
class VocalCue:
    """A four-beat cue immediately before vocals resume after an interlude."""

    after_line_index: int
    before_line_index: int
    gap_start_ms: int
    vocal_onset_ms: int
    cue_start_ms: int

    @property
    def gap_ms(self) -> int:
        return self.vocal_onset_ms - self.gap_start_ms

    @property
    def dot_starts_ms(self) -> tuple[int, ...]:
        interval_ms = (self.vocal_onset_ms - self.cue_start_ms) // VOCAL_CUE_DOT_COUNT
        return tuple(
            self.cue_start_ms + dot_index * interval_ms
            for dot_index in range(VOCAL_CUE_DOT_COUNT)
        )


@dataclass(frozen=True)
class VocalCuePlacement:
    """Cue geometry derived from the exact line that will be sung next."""

    cue: VocalCue
    x: int
    y: int
    lane: Lane


def lane_for_line(line_index: int, lanes: tuple[Lane, Lane] = LANES) -> Lane:
    return lanes[line_index % len(lanes)]


def _ruby_record_for_span(
    span,
    sidecar: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if not isinstance(sidecar, Mapping):
        return None
    records = sidecar.get("records", [])
    if not isinstance(records, list):
        return None
    matching = [
        record
        for record in records
        if isinstance(record, Mapping)
        and str(record.get("sentence_id", "")) == span.sentence_id
        and int(record.get("start", -1)) >= span.start
        and int(record.get("end", -1)) <= span.end
    ]
    exact = [
        record
        for record in matching
        if int(record.get("start", -1)) == span.start
        and int(record.get("end", -1)) == span.end
    ]
    if exact:
        return exact[-1]
    if not matching:
        return None
    sources = {str(record.get("source", "")) for record in matching}
    statuses = {str(record.get("review_status", "")) for record in matching}
    evidence = [
        evidence_item
        for record in matching
        for evidence_item in (record.get("evidence", []) or [])
    ]
    return {
        "source": next(iter(sources)) if len(sources) == 1 else "mixed",
        "review_status": next(iter(statuses)) if len(statuses) == 1 else "mixed",
        "confidence": min(
            (float(record["confidence"]) for record in matching if record.get("confidence") is not None),
            default=None,
        ),
        "evidence": evidence,
        "model_prompt_version": next(
            (
                record.get("model_prompt_version")
                for record in reversed(matching)
                if record.get("model_prompt_version")
            ),
            None,
        ),
        "before_hash": None,
        "after_hash": None,
    }


def canonical_ruby_tokens(
    sentence: Sentence,
    *,
    sidecar: Mapping[str, Any] | None = None,
    start_offset: int = 0,
) -> list[RubyToken]:
    """Convert stored SUG ruby spans into renderer tokens without inference."""

    tokens: list[RubyToken] = []
    for span in iter_sug_ruby_spans(sentence):
        record = _ruby_record_for_span(span, sidecar)
        record = record or {}
        tokens.append(
            RubyToken(
                text=span.surface,
                reading=span.reading,
                start=span.start + start_offset,
                end=span.end + start_offset,
                sentence_id=span.sentence_id,
                source=str(record.get("source", span.source) or span.source),
                review_status=str(
                    record.get("review_status", span.review_status)
                    or span.review_status
                ),
                confidence=(
                    float(record["confidence"])
                    if record.get("confidence") is not None
                    else span.confidence
                ),
                evidence=tuple(record.get("evidence", span.evidence) or ()),
                model_prompt_version=record.get(
                    "model_prompt_version", span.model_prompt_version
                ),
                before_hash=record.get("before_hash"),
                after_hash=record.get("after_hash"),
            )
        )
    return tokens


def _canonical_tokens_for_phrase(
    source_sentence: Sentence,
    phrase: Sentence,
    *,
    sidecar: Mapping[str, Any] | None = None,
) -> list[RubyToken]:
    """Project canonical source spans onto a display-only sentence slice."""

    source_indices = {id(character): index for index, character in enumerate(source_sentence.characters)}
    indices = [source_indices.get(id(character)) for character in phrase.characters]
    if not indices:
        return []
    if any(index is None for index in indices):
        raise RubyValidationError("display phrase contains a character outside canonical SUG")
    concrete_indices = [int(index) for index in indices]
    source_to_phrase = {
        source_index: phrase_index
        for phrase_index, source_index in enumerate(concrete_indices)
    }
    source_tokens = canonical_ruby_tokens(source_sentence, sidecar=sidecar)
    projected: list[RubyToken] = []
    for token in source_tokens:
        token_indices = list(range(token.start, token.end))
        phrase_indices = [source_to_phrase.get(index) for index in token_indices]
        if all(index is None for index in phrase_indices):
            continue
        if any(index is None for index in phrase_indices):
            raise RubyValidationError(
                f"display phrase splits canonical ruby span {token.text!r}"
            )
        start = int(phrase_indices[0])
        end = int(phrase_indices[-1]) + 1
        if phrase_indices != list(range(start, end)):
            raise RubyValidationError(
                f"display phrase reorders canonical ruby span {token.text!r}"
            )
        projected.append(replace(token, start=start, end=end))
    return projected


def _escape_ass_text(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )


def _first_timestamp(sentence: Sentence) -> int | None:
    values = [
        int(character.global_timestamps[0])
        for character in sentence.characters
        if character.global_timestamps
    ]
    return min(values) if values else None


def _last_timestamp(sentence: Sentence) -> int | None:
    values = [
        int(character.global_timestamps[0])
        for character in sentence.characters
        if character.global_timestamps
    ]
    return max(values) if values else None


def _visible_chunks(sentence: Sentence) -> list[tuple[int, str]]:
    anchors = [
        index
        for index, character in enumerate(sentence.characters)
        if character.global_timestamps
    ]
    chunks: list[tuple[int, str]] = []
    for position, char_index in enumerate(anchors):
        next_index = (
            anchors[position + 1]
            if position + 1 < len(anchors)
            else len(sentence.characters)
        )
        text = "".join(
            character.char for character in sentence.characters[char_index:next_index]
        )
        chunks.append((int(sentence.characters[char_index].global_timestamps[0]), text))
    return chunks


def karaoke_text(
    sentence: Sentence,
    *,
    event_start_ms: int,
    release_ms: int,
    lane: Lane,
    font_size: int,
    offset_ms: int = 0,
) -> str:
    """Build a white-to-red smooth sweep with an explicit pre-roll delay."""

    onsets = _character_onsets(
        sentence,
        offset_ms=offset_ms,
        release_ms=release_ms,
    )
    if not onsets:
        return _escape_ass_text(sentence.text)
    shifted_release = max(release_ms + offset_ms, onsets[-1] + 10)
    first_onset = onsets[0]
    lead_in_cs = max(0, first_onset - event_start_ms) // 10
    tags = (
        f"{{\\an{lane.alignment}\\pos({lane.x},{lane.main_y})"
        f"\\fs{font_size}\\fad(80,120)\\k{lead_in_cs}}}"
    )
    parts = [tags]
    for index, (character, timestamp) in enumerate(
        zip(sentence.characters, onsets, strict=True)
    ):
        next_timestamp = (
            onsets[index + 1] if index + 1 < len(onsets) else shifted_release
        )
        duration_cs = max(1, int(round((next_timestamp - timestamp) / 10)))
        parts.append(
            f"{{\\kf{duration_cs}}}{_escape_ass_text(character.char)}"
        )
    return "".join(parts)


def _text_width(font_file: Path, size: int, text: str) -> float:
    font = ImageFont.truetype(str(font_file), size)
    return float(font.getlength(text))


def _letter_spacing_after_indices(text: str) -> frozenset[int]:
    """Return adjacent visible glyph positions eligible for configured spacing."""

    return frozenset(
        index
        for index, (left, right) in enumerate(zip(text, text[1:], strict=False))
        if not left.isspace() and not right.isspace()
    )


def _measured_text_span(
    font_file: Path,
    text: str,
    *,
    font_size: int,
    advance_scale: float,
    semantic_gap_count: int = 0,
    semantic_gap_em: float = 0.0,
    letter_spacing_em: float = 0.0,
    word_gap_em: float | None = None,
) -> float:
    """Measure the exact span consumed by the geometry and fit gate."""

    font = ImageFont.truetype(str(font_file), font_size)
    natural_width = float(font.getlength(text)) * advance_scale
    word_gap_adjustment = 0.0
    if word_gap_em is not None:
        target_word_gap_px = font_size * word_gap_em
        word_gap_adjustment = sum(
            target_word_gap_px - float(font.getlength(character)) * advance_scale
            for character in text
            if character.isspace()
        )
    letter_spacing_count = len(_letter_spacing_after_indices(text))
    return float(
        natural_width
        + word_gap_adjustment
        + semantic_gap_count * font_size * semantic_gap_em
        + letter_spacing_count * font_size * letter_spacing_em
    )


def fit_main_font_size(
    font_file: Path,
    text: str,
    *,
    slot_width: int = SLOT_WIDTH,
    advance_scale: float = MAIN_ADVANCE_SCALE,
    outline_px: int = MAIN_OUTLINE_PX,
    max_size: int = MAIN_FONT_SIZE,
    min_size: int = MIN_MAIN_FONT_SIZE,
    semantic_gap_count: int = 0,
    semantic_gap_em: float = 0.0,
    letter_spacing_em: float = 0.0,
    word_gap_em: float | None = None,
) -> int:
    """Choose the largest size whose measured rendered span fits the slot.

    ``advance_scale`` and ``outline_px`` are explicit so callers can preserve
    a layout's existing fit policy.  The defaults match standard; wide passes
    its historical natural-width values through the layout-specific wrapper.
    """

    for size in range(max_size, min_size - 1, -1):
        measured_width = _measured_text_span(
            font_file,
            text,
            font_size=size,
            advance_scale=advance_scale,
            semantic_gap_count=semantic_gap_count,
            semantic_gap_em=semantic_gap_em,
            letter_spacing_em=letter_spacing_em,
            word_gap_em=word_gap_em,
        ) + 2 * outline_px
        if measured_width <= slot_width:
            return size
    return min_size


def fit_main_font_size_for_layout(
    font_file: Path,
    text: str,
    *,
    layout: SubtitleLayout,
    semantic_gap_count: int = 0,
    letter_spacing_em: float | None = None,
) -> int:
    """Apply the layout-specific main-text fit policy."""

    if letter_spacing_em is None:
        letter_spacing_em = layout.letter_spacing_em
    return fit_main_font_size(
        font_file,
        text,
        slot_width=layout.slot_width,
        advance_scale=layout.fit_advance_scale,
        outline_px=layout.fit_outline_px,
        max_size=layout.main_font_size,
        min_size=layout.min_main_font_size,
        semantic_gap_count=semantic_gap_count,
        semantic_gap_em=layout.semantic_gap_em,
        letter_spacing_em=letter_spacing_em,
        word_gap_em=layout.word_gap_em,
    )


def text_geometry(
    font_file: Path,
    text: str,
    *,
    font_size: int,
    lane: Lane,
    advance_scale: float = MAIN_ADVANCE_SCALE,
    semantic_gap_after_indices: frozenset[int] = frozenset(),
    semantic_gap_em: float = 0.0,
    letter_spacing_em: float = 0.0,
    word_gap_em: float | None = None,
) -> TextGeometry:
    """Measure one line once so its glyphs and ruby use identical coordinates."""

    font = ImageFont.truetype(str(font_file), font_size)
    natural_boundaries = tuple(
        float(font.getlength(text[:index])) * advance_scale
        for index in range(len(text) + 1)
    )
    gap_px = font_size * semantic_gap_em
    letter_spacing_px = font_size * letter_spacing_em
    target_word_gap_px = font_size * word_gap_em if word_gap_em is not None else None
    letter_spacing_after_indices = _letter_spacing_after_indices(text)
    gap_before = tuple(
        sum(1 for gap_index in semantic_gap_after_indices if gap_index < index)
        * gap_px
        for index in range(len(text) + 1)
    )
    spacing_before = tuple(
        sum(
            1
            for spacing_index in letter_spacing_after_indices
            if spacing_index < index
        )
        * letter_spacing_px
        for index in range(len(text) + 1)
    )
    word_gap_adjustment_before = tuple(
        sum(
            target_word_gap_px - float(font.getlength(character)) * advance_scale
            for character in text[:index]
            if character.isspace()
        )
        if target_word_gap_px is not None
        else 0.0
        for index in range(len(text) + 1)
    )
    glyph_starts_relative = tuple(
        natural_boundaries[index]
        + gap_before[index]
        + spacing_before[index]
        + word_gap_adjustment_before[index]
        for index in range(len(text))
    )
    glyph_ends_relative = tuple(
        natural_boundaries[index + 1]
        + gap_before[index]
        + spacing_before[index]
        + word_gap_adjustment_before[index + 1]
        for index in range(len(text))
    )
    line_width = (
        glyph_ends_relative[-1]
        if glyph_ends_relative
        else 0.0
    )
    left = float(lane.x) if lane.alignment == 7 else float(lane.x) - line_width
    glyph_starts = tuple(left + value for value in glyph_starts_relative)
    glyph_ends = tuple(left + value for value in glyph_ends_relative)
    # ``boundaries`` remains the compact no-gap-compatible public span used by
    # older callers. Glyph and ruby placement use the explicit starts/ends so
    # a semantic gap shifts only the following text, never the preceding glyph.
    boundaries = (
        (*glyph_starts, glyph_ends[-1])
        if glyph_ends
        else (left,)
    )
    return TextGeometry(
        left=left,
        boundaries=boundaries,
        glyph_starts=glyph_starts,
        glyph_ends=glyph_ends,
        width=line_width,
        letter_spacing_px=letter_spacing_px,
        semantic_gap_px=gap_px,
        word_gap_px=target_word_gap_px,
    )


def centered_lane_for_text(
    font_file: Path,
    text: str,
    *,
    font_size: int,
    advance_scale: float = MAIN_ADVANCE_SCALE,
    main_y: int = OUTRO_MAIN_Y,
    ruby_y: int = OUTRO_RUBY_Y,
    semantic_gap_after_indices: frozenset[int] = frozenset(),
    semantic_gap_em: float = 0.0,
    letter_spacing_em: float = 0.0,
    word_gap_em: float | None = None,
) -> Lane:
    """Create a left-anchored lane whose rendered text is screen-centered."""

    width = _measured_text_span(
        font_file,
        text,
        font_size=font_size,
        advance_scale=advance_scale,
        semantic_gap_count=len(semantic_gap_after_indices),
        semantic_gap_em=semantic_gap_em,
        letter_spacing_em=letter_spacing_em,
        word_gap_em=word_gap_em,
    )
    return Lane(
        x=int(round((CANVAS_WIDTH - width) / 2.0)),
        main_y=main_y,
        ruby_y=ruby_y,
        alignment=7,
    )


def _character_onsets(
    sentence: Sentence,
    *,
    offset_ms: int = 0,
    onset_overrides: dict[int, int] | None = None,
    release_ms: int | None = None,
) -> list[int]:
    """Resolve a strictly ordered visual onset for every displayed character.

    Acoustic timestamps remain untouched. Untimed punctuation/spaces and
    characters sharing one source timestamp are spread inside the following
    acoustic interval. The final interval uses ``release_ms``. A final
    centisecond pass guarantees that libass never starts two visible glyphs on
    the same karaoke tick.
    """

    raw: list[int | None] = [
        int(character.global_timestamps[0]) + offset_ms
        if character.global_timestamps
        else None
        for character in sentence.characters
    ]
    for character_index, onset_ms in (onset_overrides or {}).items():
        if not 0 <= character_index < len(raw):
            raise IndexError(f"visual onset index out of range: {character_index}")
        raw[character_index] = int(onset_ms) + offset_ms
    anchors = [value for value in raw if value is not None]
    if not anchors:
        return []

    first_anchor_index = next(
        index for index, value in enumerate(raw) if value is not None
    )
    first_anchor = int(raw[first_anchor_index])
    result: list[int] = [first_anchor] * len(raw)
    current = first_anchor
    for index, value in enumerate(raw):
        if value is not None:
            current = int(value)
        result[index] = current

    for index in range(first_anchor_index - 1, -1, -1):
        result[index] = max(0, result[index + 1] - 10)

    terminal = (
        int(release_ms) + offset_ms
        if release_ms is not None
        else max(anchors) + max(300, 10 * len(raw))
    )
    terminal = max(terminal, result[-1] + 10)

    index = first_anchor_index
    while index < len(result):
        end = index + 1
        while end < len(result) and result[end] == result[index]:
            end += 1
        if end - index > 1 or any(raw[pos] is None for pos in range(index, end)):
            boundary = result[end] if end < len(result) else terminal
            boundary = max(boundary, result[index] + 10 * (end - index))
            start = result[index]
            span = boundary - start
            count = end - index
            for position in range(index, end):
                result[position] = start + round(
                    span * (position - index) / count
                )
        index = end

    for index in range(1, len(result)):
        if result[index] // 10 <= result[index - 1] // 10:
            result[index] = (result[index - 1] // 10 + 1) * 10
    if any(right <= left for left, right in zip(result, result[1:], strict=False)):
        raise ValueError("visual onset overrides must preserve monotonic order")
    return result


def _character_releases(
    onsets: list[int],
    *,
    release_ms: int,
    offset_ms: int = 0,
    release_overrides: dict[int, int] | None = None,
) -> list[int]:
    """Resolve each glyph sweep end without assigning silence to the glyph.

    Ordinary glyphs sweep until the next visible onset.  Reviewed overrides
    may finish a glyph earlier while the fully highlighted glyph remains
    visible through the following breath or instrumental gap.
    """

    if not onsets:
        return []
    shifted_release = max(release_ms + offset_ms, onsets[-1] + 10)
    result: list[int] = []
    for index, onset in enumerate(onsets):
        natural_release = next(
            (candidate for candidate in onsets[index + 1 :] if candidate > onset),
            shifted_release,
        )
        override = (release_overrides or {}).get(index)
        if override is None:
            result.append(natural_release)
            continue
        reviewed_release = int(override) + offset_ms
        if not onset + 10 <= reviewed_release <= natural_release:
            raise ValueError(
                "visual release override for glyph "
                f"{index} is outside {onset + 10}..{natural_release}ms: "
                f"{reviewed_release}ms"
            )
        result.append(reviewed_release)
    return result


def _trailing_repeated_token_visual_onsets(sentence: Sentence) -> dict[int, int]:
    """Repair a repeated refrain whose last morae were aligned to the tail.

    MMS can mistake the end of a held vowel for the onset of the final mora.
    For a lyric ending in at least three adjacent copies of the same token, use
    the earlier copies as the visual rhythm reference and move only implausibly
    late mora onsets in the final copy. Source timing remains untouched.
    """

    separated = re.search(r"(.{2,}?)([^\w\s]+)\1\2\1$", sentence.text)
    contiguous = re.search(r"(.{2,}?)\1\1$", sentence.text) if separated is None else None
    repeated = separated or contiguous
    if repeated is None:
        return {}
    token = repeated.group(1)
    separator = repeated.group(2) if separated is not None else ""
    starts = [
        repeated.start(1) + index * (len(token) + len(separator))
        for index in range(3)
    ]
    onsets = _character_onsets(sentence)
    if not onsets:
        return {}
    target_start = starts[-1]
    overrides: dict[int, int] = {}
    effective_previous = onsets[target_start]
    for token_offset in range(1, len(token)):
        reference_steps = [
            onsets[start + token_offset] - onsets[start + token_offset - 1]
            for start in starts[:-1]
        ]
        expected_step = int(round(median(reference_steps)))
        target_index = target_start + token_offset
        expected_onset = effective_previous + expected_step
        current_onset = onsets[target_index]
        if current_onset - expected_onset >= 500:
            overrides[target_index] = expected_onset
            effective_previous = expected_onset
        else:
            effective_previous = current_onset
    return overrides


def main_glyph_events(
    sentence: Sentence,
    *,
    event_start_ms: int,
    event_end_ms: int,
    release_ms: int,
    lane: Lane,
    font_file: Path,
    font_size: int,
    advance_scale: float = MAIN_ADVANCE_SCALE,
    semantic_gap_after_indices: frozenset[int] = frozenset(),
    semantic_gap_em: float = 0.0,
    offset_ms: int = 0,
    onset_overrides: dict[int, int] | None = None,
    release_overrides: dict[int, int] | None = None,
    outline_px: int = MAIN_OUTLINE_PX,
    glow_blur: int = MAIN_GLOW_BLUR,
    letter_spacing_em: float = 0.0,
    word_gap_em: float | None = None,
    glow_style: str = "Glow",
    main_style: str = "Main",
    glow_layer: int = 1,
    main_layer: int = 2,
    geometry: TextGeometry | None = None,
) -> list[str]:
    """Render every base character at an explicit center shared with its ruby span."""

    onsets = _character_onsets(
        sentence,
        offset_ms=offset_ms,
        onset_overrides=onset_overrides,
        release_ms=release_ms,
    )
    if not onsets:
        return []
    if geometry is None:
        geometry = text_geometry(
            font_file,
            sentence.text,
            font_size=font_size,
            lane=lane,
            advance_scale=advance_scale,
            semantic_gap_after_indices=semantic_gap_after_indices,
            semantic_gap_em=semantic_gap_em,
            letter_spacing_em=letter_spacing_em,
            word_gap_em=word_gap_em,
        )
    releases = _character_releases(
        onsets,
        release_ms=release_ms,
        offset_ms=offset_ms,
        release_overrides=release_overrides,
    )
    result: list[str] = []
    for index, (character, onset) in enumerate(
        zip(sentence.characters, onsets, strict=True)
    ):
        duration_cs = max(1, int(round((releases[index] - onset) / 10)))
        lead_in_cs = max(0, onset - event_start_ms) // 10
        x = (geometry.glyph_starts[index] + geometry.glyph_ends[index]) / 2.0
        common_override = (
            f"{{\\an8\\pos({int(round(x))},{lane.main_y})"
            f"\\fs{font_size}\\bord{outline_px}\\fad(80,120)"
            f"\\k{lead_in_cs}\\kf{duration_cs}}}"
        )
        if letter_spacing_em > 0:
            letter_spacing_px = font_size * letter_spacing_em
            formatted_spacing = f"{letter_spacing_px:.3f}".rstrip("0").rstrip(".")
            common_override = common_override[:-1] + f"\\fsp{formatted_spacing}}}"
        glow_override = common_override[:-1] + f"\\blur{glow_blur}}}"
        escaped = _escape_ass_text(character.char)
        result.append(
            f"Dialogue: {glow_layer},"
            f"{ms_to_ass_time(event_start_ms)},{ms_to_ass_time(event_end_ms)},"
            f"{glow_style},,0,0,0,,{glow_override}{escaped}"
        )
        result.append(
            f"Dialogue: {main_layer},"
            f"{ms_to_ass_time(event_start_ms)},{ms_to_ass_time(event_end_ms)},"
            f"{main_style},,0,0,0,,{common_override}{escaped}"
        )
    return result


def ruby_events(
    sentence: Sentence,
    *,
    event_start_ms: int,
    event_end_ms: int,
    lane: Lane,
    font_file: Path,
    main_font_size: int,
    ruby_font_size: int = RUBY_FONT_SIZE,
    advance_scale: float = MAIN_ADVANCE_SCALE,
    semantic_gap_after_indices: frozenset[int] = frozenset(),
    semantic_gap_em: float = 0.0,
    letter_spacing_em: float = 0.0,
    word_gap_em: float | None = None,
    tokens: list[RubyToken] | None = None,
    language: str = DEFAULT_LANGUAGE,
    outline_px: int = RUBY_OUTLINE_PX,
    glow_blur: int = RUBY_GLOW_BLUR,
    geometry: TextGeometry | None = None,
) -> list[str]:
    if not uses_ruby(language):
        return []
    if tokens is None:
        raise RubyValidationError(
            "renderer requires canonical SUG ruby tokens; inference is disabled"
        )
    if not tokens:
        return []
    if geometry is None:
        geometry = text_geometry(
            font_file,
            sentence.text,
            font_size=main_font_size,
            lane=lane,
            advance_scale=advance_scale,
            semantic_gap_after_indices=semantic_gap_after_indices,
            semantic_gap_em=semantic_gap_em,
            letter_spacing_em=letter_spacing_em,
            word_gap_em=word_gap_em,
        )
    result: list[str] = []
    for token in tokens:
        x = (
            geometry.glyph_starts[token.start]
            + geometry.glyph_ends[token.end - 1]
        ) / 2.0
        common_override = (
            f"{{\\an8\\pos({int(round(x))},{lane.ruby_y})"
            f"\\fs{ruby_font_size}\\bord{outline_px}\\fad(80,120)}}"
        )
        glow_override = common_override[:-1] + f"\\blur{glow_blur}}}"
        escaped = _escape_ass_text(token.reading)
        result.append(
            "Dialogue: 3,"
            f"{ms_to_ass_time(event_start_ms)},{ms_to_ass_time(event_end_ms)},"
            f"RubyGlow,,0,0,0,,{glow_override}{escaped}"
        )
        result.append(
            "Dialogue: 4,"
            f"{ms_to_ass_time(event_start_ms)},{ms_to_ass_time(event_end_ms)},"
            f"Ruby,,0,0,0,,{common_override}{escaped}"
        )
    return result


def _release_for_sentence(
    sentence: Sentence,
    line_index: int,
    release_overrides: dict[int, int],
) -> int:
    last_onset = _last_timestamp(sentence)
    if last_onset is None:
        return 0
    if line_index in release_overrides:
        return max(last_onset, int(release_overrides[line_index]))
    for character in reversed(sentence.characters):
        if character.sentence_end_ts is not None:
            return max(last_onset, int(character.sentence_end_ts))
    return last_onset + 300


def _last_character_onset(
    sentence: Sentence,
    *,
    onset_overrides: dict[int, int] | None = None,
) -> int | None:
    """Return the effective onset of the phrase's final displayed character."""

    onsets = _character_onsets(sentence, onset_overrides=onset_overrides)
    return onsets[-1] if onsets else None


def _apply_visual_release_caps(prepared: list[dict], *, offset_ms: int = 0) -> None:
    """Cap safe visual releases at the next prepared phrase's first onset.

    This is a render-only gate.  It does not alter the source ``Sentence`` or
    its acoustic event end.  A cap is applied only after a reviewed visual
    onset repair and only when the final displayed character has already
    started by the next phrase's first onset.  The old line may remain fully
    red through its real tail, but two lines no longer sweep simultaneously.
    """

    for current, following in zip(prepared, prepared[1:], strict=False):
        next_first_onset = int(following["first_onset"])
        current_release = int(current["release_ms_raw"])
        onset_overrides = current.get("visual_onset_overrides") or {}
        if not onset_overrides:
            continue
        last_character_onset = _last_character_onset(
            current["sentence"],
            onset_overrides=onset_overrides,
        )
        if (
            last_character_onset is None
            or current_release <= next_first_onset
            or last_character_onset > next_first_onset
        ):
            continue

        current["visual_release_capped_from_ms"] = current_release
        current["release_ms_raw"] = next_first_onset


_PREFERRED_PHRASE_ENDINGS = (
    "けど",
    "から",
    "なら",
    "のに",
    "たら",
    "れば",
    "ても",
    "もの",
    "こと",
    "は",
    "が",
    "を",
    "に",
    "へ",
    "で",
    "と",
    "も",
)

def _semantic_gap_after_indices(
    source_sentence: Sentence,
    display_sentence: Sentence,
    *,
    language: str | None = None,
) -> frozenset[int]:
    """Locate source whitespace retained as subtle gaps inside one display phrase.

    Display phrases intentionally omit whitespace characters so they do not
    receive karaoke timing or a full-width Japanese space. The original
    character objects are reused, which lets us recover only the whitespace
    boundaries that remain inside the sliced phrase.
    """

    if len(display_sentence.characters) < 2:
        return frozenset()
    source_positions = {
        id(character): index
        for index, character in enumerate(source_sentence.characters)
    }
    result: set[int] = set()
    for display_index, (left, right) in enumerate(
        zip(
            display_sentence.characters,
            display_sentence.characters[1:],
            strict=False,
        )
    ):
        left_index = source_positions.get(id(left))
        right_index = source_positions.get(id(right))
        if left_index is None or right_index is None or right_index <= left_index:
            continue
        if any(
            character.char.isspace()
            for character in source_sentence.characters[left_index + 1 : right_index]
        ):
            result.add(display_index)
    normalize_language(language, default=DEFAULT_LANGUAGE)
    return frozenset(result)


def _character_onset(character) -> int | None:
    if not character.global_timestamps:
        return None
    return int(character.global_timestamps[0])


def _split_character_run(
    characters: list,
    max_chars: int,
    *,
    min_chars: int = MIN_DISPLAY_PHRASE_CHARS,
) -> list[list]:
    """Split one no-space run near a sung or grammatical phrase boundary."""

    run_text = "".join(character.char for character in characters)
    soft_max = max_chars + DISPLAY_PHRASE_SOFT_OVERRUN
    if len(characters) <= soft_max:
        return [characters]
    target = max(min_chars, int(round(max_chars * 0.75)))
    minimum = min(min_chars, max(1, len(characters) - 1))
    # Permit a small semantic overrun.  A 13- or 14-character grammatical
    # phrase is preferable to cutting a conjugation such as ``しま｜う``.
    maximum = min(soft_max, len(characters) - min_chars)
    if maximum < minimum:
        maximum = min(max_chars, len(characters) - 1)

    best_position = maximum
    best_score = float("-inf")
    for position in range(minimum, maximum + 1):
        left_onset = _character_onset(characters[position - 1])
        right_onset = _character_onset(characters[position])
        acoustic_gap = (
            max(0, right_onset - left_onset)
            if left_onset is not None and right_onset is not None
            else 0
        )
        ending_bonus = 0
        prefix = run_text[:position]
        if any(prefix.endswith(ending) for ending in _PREFERRED_PHRASE_ENDINGS):
            ending_bonus = 320
        if prefix.endswith(("、", "。", "？", "！", "…")):
            ending_bonus += 600
        score = min(acoustic_gap, 1_500) * 0.35 + ending_bonus
        score -= abs(position - target) * 70
        if score > best_score:
            best_score = score
            best_position = position

    return [
        characters[:best_position],
        *_split_character_run(
            characters[best_position:],
            max_chars,
            min_chars=min_chars,
        ),
    ]


def _join_short_display_runs(
    runs: list[list],
    *,
    max_chars: int,
    min_chars: int = MIN_DISPLAY_PHRASE_CHARS,
) -> list[list]:
    """Join or rebalance short phrases until every avoidable phrase is >=6."""

    result = [list(run) for run in runs if run]
    if not result:
        return []
    if sum(len(run) for run in result) < min_chars:
        return [[character for run in result for character in run]]

    soft_max = max_chars + DISPLAY_PHRASE_SOFT_OVERRUN
    index = 0
    while index < len(result):
        current = result[index]
        if len(current) >= min_chars:
            index += 1
            continue

        if index + 1 < len(result) and len(current) + len(result[index + 1]) <= soft_max:
            result[index] = current + result.pop(index + 1)
            continue
        if index > 0 and len(result[index - 1]) + len(current) <= soft_max:
            result[index - 1].extend(current)
            result.pop(index)
            index = max(0, index - 1)
            continue

        needed = min_chars - len(current)
        if index + 1 < len(result) and len(result[index + 1]) - needed >= min_chars:
            donor = result[index + 1]
            result[index].extend(donor[:needed])
            result[index + 1] = donor[needed:]
            index += 1
            continue
        if index > 0 and len(result[index - 1]) - needed >= min_chars:
            donor = result[index - 1]
            result[index] = donor[-needed:] + current
            result[index - 1] = donor[:-needed]
            index += 1
            continue

        # Minimum length is the hard requirement. If neither neighbour can
        # donate cleanly, merge even when this creates a rare soft-max overrun.
        if index + 1 < len(result):
            result[index] = current + result.pop(index + 1)
        else:
            result[index - 1].extend(current)
            result.pop(index)
            index -= 1

    return result


def _coalesce_display_runs_that_fit(
    runs: list[list],
    *,
    max_chars: int,
) -> list[list]:
    """Keep adjacent breathing segments on one line when they still fit.

    The minimum-length pass has already protected short fragments. This pass
    only removes an avoidable line break; source whitespace remains available
    to the renderer as a subtle semantic gap inside the merged phrase.
    """

    soft_max = max_chars + DISPLAY_PHRASE_SOFT_OVERRUN
    result: list[list] = []
    for run in runs:
        if result and len(result[-1]) + len(run) <= soft_max:
            result[-1].extend(run)
        else:
            result.append(list(run))
    return result


def split_sentence_for_display(
    sentence: Sentence,
    *,
    max_chars: int | None = None,
    language: str = DEFAULT_LANGUAGE,
    font_file: Path | None = None,
    layout: SubtitleLayout | None = None,
) -> list[Sentence]:
    """Create short display-only phrases without changing the editable SUG."""

    language = normalize_language(language)
    if max_chars is not None and max_chars < MIN_DISPLAY_PHRASE_CHARS:
        raise ValueError(
            f"max_chars must be at least {MIN_DISPLAY_PHRASE_CHARS}, got {max_chars}"
        )
    if max_chars is None:
        return [sentence]
    runs: list[list] = []
    current: list = []
    for character in sentence.characters:
        if character.char.isspace():
            if current:
                runs.extend(_split_character_run(current, max_chars))
                current = []
            continue
        current.append(character)
    if current:
        runs.extend(_split_character_run(current, max_chars))
    runs = _join_short_display_runs(runs, max_chars=max_chars)
    runs = _coalesce_display_runs_that_fit(runs, max_chars=max_chars)
    return [
        Sentence(
            singer_id=sentence.singer_id,
            characters=list(run),
        )
        for run in runs
        if run
    ] or [sentence]


def layout_for_language(
    layout: SubtitleLayout,
    language: str,
) -> SubtitleLayout:
    """Validate the language and retain the bundled generic/Japanese layout."""

    normalize_language(language)
    return layout


_SECONDARY_VOICE_ROLES = frozenset({"opera", "harmony", "secondary"})


def _normalise_voice_role(value: object) -> str | None:
    role = str(value or "").strip().casefold()
    return role if role in _SECONDARY_VOICE_ROLES else None


def _sentence_singer(sentence: Sentence, project: object):
    explicit_singer = getattr(sentence, "singer", None)
    if explicit_singer is not None:
        return explicit_singer
    singers = getattr(project, "singers", None) or []
    singer_id = getattr(sentence, "singer_id", "")
    for singer in singers:
        if getattr(singer, "id", None) == singer_id:
            return singer
    if len(singers) == 1:
        return singers[0]
    return None


def sentence_voice_metadata(
    sentence: Sentence,
    project: object,
) -> tuple[str | None, str | None]:
    """Resolve an explicit voice role or singer group without text heuristics."""

    singer = _sentence_singer(sentence, project)
    group = str(getattr(singer, "group", "") or "").strip()
    explicit_values = [
        getattr(sentence, "voice_role", None),
        getattr(getattr(sentence, "metadata", None), "voice_role", None),
    ]
    explicit_values.extend(
        getattr(character, "voice_role", None)
        for character in getattr(sentence, "characters", ())
    )
    for value in explicit_values:
        role = _normalise_voice_role(value)
        if role is not None:
            return role, group or None

    singer_role = _normalise_voice_role(getattr(singer, "voice_role", None))
    if singer_role is not None:
        return singer_role, group or None
    if group:
        return _normalise_voice_role(group) or "secondary", group
    return None, None


def _is_secondary_sentence(sentence: Sentence, project: object) -> bool:
    role, _ = sentence_voice_metadata(sentence, project)
    return role is not None


def fit_secondary_font_size(
    font_file: Path,
    text: str,
    *,
    semantic_gap_count: int = 0,
    semantic_gap_em: float = 0.0,
) -> int:
    """Fit a secondary line inside the top-center safe area."""

    slot_width = CANVAS_WIDTH - 2 * SECONDARY_SAFE_MARGIN_X
    for size in range(SECONDARY_FONT_SIZE, SECONDARY_MIN_FONT_SIZE - 1, -1):
        if (
            _measured_text_span(
                font_file,
                text,
                font_size=size,
                advance_scale=1.0,
                semantic_gap_count=semantic_gap_count,
                semantic_gap_em=semantic_gap_em,
            )
            + 2 * SECONDARY_OUTLINE_PX
            <= slot_width
        ):
            return size
    return SECONDARY_MIN_FONT_SIZE


def find_vocal_cues(
    project,
    release_overrides: dict[int, int],
    *,
    offset_ms: int = 0,
    gap_threshold_ms: int = INTERLUDE_GAP_THRESHOLD_MS,
    lead_ms: int = VOCAL_CUE_LEAD_MS,
) -> list[VocalCue]:
    """Locate the intro count-in and genuine interludes.

    The intro is treated like an interlude whose previous release is time zero,
    so a sufficiently long instrumental opening receives the same four-beat
    cue immediately before the first vocal.
    """

    timed_lines: list[tuple[int, int, int]] = []
    for line_index, sentence in enumerate(project.sentences):
        if _is_secondary_sentence(sentence, project):
            continue
        first_onset = _first_timestamp(sentence)
        if first_onset is None:
            continue
        timed_lines.append(
            (
                line_index,
                first_onset + offset_ms,
                _release_for_sentence(sentence, line_index, release_overrides)
                + offset_ms,
            )
        )

    cues: list[VocalCue] = []
    if timed_lines:
        first_index, first_onset, _ = timed_lines[0]
        if first_onset >= gap_threshold_ms:
            cues.append(
                VocalCue(
                    after_line_index=-1,
                    before_line_index=first_index,
                    gap_start_ms=0,
                    vocal_onset_ms=first_onset,
                    cue_start_ms=max(0, first_onset - lead_ms),
                )
            )
    for previous, following in zip(timed_lines, timed_lines[1:], strict=False):
        previous_index, _, previous_release = previous
        following_index, following_onset, _ = following
        gap_ms = following_onset - previous_release
        if gap_ms < gap_threshold_ms:
            continue
        cues.append(
            VocalCue(
                after_line_index=previous_index,
                before_line_index=following_index,
                gap_start_ms=previous_release,
                vocal_onset_ms=following_onset,
                cue_start_ms=max(previous_release, following_onset - lead_ms),
            )
        )
    return cues


def build_interlude_prompt_events(
    sentences: Iterable[Sentence],
    *,
    gap_threshold_ms: int = INTERLUDE_GAP_THRESHOLD_MS,
    lead_ms: int = VOCAL_CUE_LEAD_MS,
    offset_ms: int = 0,
) -> list[dict]:
    """Expose the timing contract for tests, diagnostics, and other renderers."""

    project = SimpleNamespace(sentences=list(sentences))
    cues = find_vocal_cues(
        project,
        {},
        offset_ms=offset_ms,
        gap_threshold_ms=gap_threshold_ms,
        lead_ms=lead_ms,
    )
    return [
        {
            "kind": "dot",
            "color": "#ff0000",
            "next_line_index": cue.before_line_index,
            "start_ms": dot_start_ms,
            "end_ms": cue.vocal_onset_ms,
            "dot_count": dot_index + 1,
            "text": "●" * (dot_index + 1),
        }
        for cue in cues
        for dot_index, dot_start_ms in enumerate(cue.dot_starts_ms)
    ]


def vocal_cue_anchor(
    sentence: Sentence,
    *,
    line_index: int,
    layout: SubtitleLayout,
    font_file: Path,
    semantic_gap_after_indices: frozenset[int] = frozenset(),
    lane: Lane | None = None,
) -> tuple[int, int]:
    """Place a cue above the leading characters of the upcoming lyric."""

    lane = lane or lane_for_line(line_index, layout.lanes)
    font_size = fit_main_font_size_for_layout(
        font_file,
        sentence.text,
        layout=layout,
        semantic_gap_count=len(semantic_gap_after_indices),
    )
    geometry = text_geometry(
        font_file,
        sentence.text,
        font_size=font_size,
        lane=lane,
        advance_scale=layout.advance_scale,
        semantic_gap_after_indices=semantic_gap_after_indices,
        semantic_gap_em=layout.semantic_gap_em,
        letter_spacing_em=layout.letter_spacing_em,
        word_gap_em=layout.word_gap_em,
    )
    group_half_width = (
        VOCAL_CUE_DOT_COUNT - 1
    ) * VOCAL_CUE_DOT_SPACING / 2 + VOCAL_CUE_FONT_SIZE / 2
    center_x = geometry.boundaries[0] + group_half_width
    safe_x = min(
        1920 - group_half_width - 8,
        max(group_half_width + 8, center_x),
    )
    return int(round(safe_x)), lane.ruby_y - VOCAL_CUE_ABOVE_RUBY_PX


def vocal_cue_events(
    cue: VocalCue,
    *,
    cue_x: int,
    cue_y: int,
) -> list[str]:
    """Render four dim dots that turn red one-by-one before the next vocal."""

    cue_start = ms_to_ass_time(cue.cue_start_ms)
    cue_end = ms_to_ass_time(cue.vocal_onset_ms)
    events: list[str] = []
    group_left = cue_x - ((VOCAL_CUE_DOT_COUNT - 1) * VOCAL_CUE_DOT_SPACING / 2)
    dot_positions = tuple(
        int(round(group_left + dot_index * VOCAL_CUE_DOT_SPACING))
        for dot_index in range(VOCAL_CUE_DOT_COUNT)
    )
    for dot_index, dot_x in enumerate(dot_positions):
        base_override = f"{{\\an5\\pos({dot_x},{cue_y})\\fad(100,80)}}"
        events.append(
            "Dialogue: 5,"
            f"{cue_start},{cue_end},CueDim,CueBase{dot_index + 1},"
            f"0,0,0,,{base_override}●"
        )
    for dot_index, (dot_start_ms, dot_x) in enumerate(
        zip(cue.dot_starts_ms, dot_positions, strict=True)
    ):
        hot_override = f"{{\\an5\\pos({dot_x},{cue_y})\\fad(70,80)}}"
        events.append(
            "Dialogue: 6,"
            f"{ms_to_ass_time(dot_start_ms)},{cue_end},CueHot,"
            f"CueDot{dot_index + 1},0,0,0,,{hot_override}●"
        )
    return events


def _cue_lyric_preload_starts(
    cues: Iterable[VocalCue],
    prepared: list[dict],
) -> dict[int, int]:
    """Return the shared visible start for the two phrases attached to each cue."""

    preload_starts: dict[int, int] = {}
    for cue in cues:
        target_index = cue.before_line_index
        if not 0 <= target_index < len(prepared):
            continue
        # A cue fills the two display lanes, not necessarily two phrases from
        # one editable source line. Some short intro lines produce only one
        # display phrase, so the second
        # lane must preload the following source line without merging either
        # source or changing its timing data.
        target_phrase_indices = range(
            target_index,
            min(target_index + 2, len(prepared)),
        )

        if cue.after_line_index < 0:
            # The established album contract keeps the first two lyric lanes
            # visible throughout the title/intro, independently of the later
            # countdown-dot window and acoustic lyric onset.
            preload_start_ms = 0
        else:
            previous_indices = range(max(0, target_index - 2), target_index)
            previous_visible_end_ms = max(
                (
                    int(prepared[index]["event_end_ms"])
                    for index in previous_indices
                ),
                default=0,
            )
            after_line_end_ms = (
                int(prepared[cue.after_line_index]["event_end_ms"])
                if 0 <= cue.after_line_index < len(prepared)
                else 0
            )
            # During an instrumental break, preload the next pair as soon as
            # the preceding visible lyrics finish.  The countdown itself is a
            # separate event and must not shorten lyric visibility.
            preload_start_ms = max(previous_visible_end_ms, after_line_end_ms)

        for line_index in target_phrase_indices:
            preload_starts[line_index] = max(
                preload_starts.get(line_index, preload_start_ms),
                preload_start_ms,
            )
    return preload_starts


def _normalise_highlight_color(value: object) -> str:
    color = str(value or "").strip().upper()
    if not re.fullmatch(r"#[0-9A-F]{6}", color):
        return DEFAULT_HIGHLIGHT_COLOR
    return color


def _project_highlight_color(project: object) -> str:
    singers = getattr(project, "singers", None)
    if singers:
        return _normalise_highlight_color(getattr(singers[0], "color", None))
    return DEFAULT_HIGHLIGHT_COLOR


def _project_secondary_highlight_color(
    project: object,
    *,
    fallback: str,
) -> tuple[str, str]:
    """Use an explicitly assigned secondary singer colour when available."""

    for sentence in getattr(project, "sentences", ()):
        role, _ = sentence_voice_metadata(sentence, project)
        if role is None:
            continue
        singer = _sentence_singer(sentence, project)
        value = str(getattr(singer, "color", "") or "").strip().upper()
        if re.fullmatch(r"#[0-9A-F]{6}", value):
            return value, "secondary-singer"
    return fallback, "project-default-singer"


def _project_language(project: object) -> str:
    metadata = getattr(project, "metadata", None)
    value = getattr(metadata, "language", None) if metadata is not None else None
    return normalize_language(value, default=DEFAULT_LANGUAGE)


def _ass_bgr(color: str, *, alpha: str = "00") -> str:
    normalized = _normalise_highlight_color(color)
    red, green, blue = normalized[1:3], normalized[3:5], normalized[5:7]
    return f"&H{alpha}{blue}{green}{red}"


def build_review_ass(
    project,
    output_path: Path,
    *,
    font_file: Path,
    release_overrides: dict[int, int],
    visual_release_overrides: dict[tuple[int, int], int] | None = None,
    layout: SubtitleLayout = STANDARD_LAYOUT,
    offset_ms: int = 0,
    ruby_sidecar: Mapping[str, Any] | None = None,
    pronunciation_validation: str = "optional",
) -> dict:
    visual_release_overrides = visual_release_overrides or {}
    language = _project_language(project)
    canonical_spans = iter_sug_ruby_spans(project)
    pronunciation_result = validate_pronunciation(
        project,
        mode=pronunciation_validation,
        sidecar=ruby_sidecar,
        sidecar_validator=(
            validate_review_sidecar if callable(validate_review_sidecar) else None
        ),
        rendered_ruby_count=len(canonical_spans),
    )
    canonical_sug_hash = sug_hash(project)
    canonical_project_ruby_tokens = [
        token
        for source_sentence in project.sentences
        for token in canonical_ruby_tokens(source_sentence, sidecar=ruby_sidecar)
    ]
    layout = layout_for_language(layout, language)
    identity = language_identity(language)
    visual_release_override_hits: set[tuple[int, int]] = set()
    highlight_color = _project_highlight_color(project)
    main_hot = _ass_bgr(highlight_color)
    glow_hot = _ass_bgr(highlight_color, alpha="50")
    secondary_highlight_color, secondary_highlight_color_source = (
        _project_secondary_highlight_color(
            project,
            fallback=highlight_color,
        )
    )
    secondary_hot = _ass_bgr(secondary_highlight_color)
    header = [
        "[Script Info]",
        "; Generator: StrangeUtaGame karaoke renderer",
        f"; Layout: {layout.name}",
        f"; Language: {identity['code']} ({identity['name']})",
        f"; Ruby policy: {identity['ruby_policy']}",
        f"; Pronunciation validation mode: {pronunciation_result.mode}",
        f"; Pronunciation validation status: {pronunciation_result.status}",
        f"; Pronunciation validation reason: {pronunciation_result.reason}",
        "; Ruby source: canonical-sug",
        f"; SUG Ruby hash: {canonical_sug_hash}",
        *[
            "; Ruby span: "
            f"{token.sentence_id}:{token.start}:{token.end} "
            f"source={token.source}; review_status={token.review_status}; "
            f"confidence={token.confidence}"
            for token in canonical_project_ruby_tokens
        ],
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Glow,{FONT_FAMILY},{layout.main_font_size},{glow_hot},&H70FFFFFF,&H90FFFFFF,&HFF000000,1,0,0,0,100,100,0,0,1,{layout.main_outline_px},0,7,0,0,0,1",
        f"Style: Main,{FONT_FAMILY},{layout.main_font_size},{main_hot},&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,{layout.main_outline_px},0,7,0,0,0,1",
        f"Style: RubyGlow,{FONT_FAMILY},{layout.ruby_font_size},&H70F3F3F3,&H70F3F3F3,&HA0FFFFFF,&HFF000000,1,0,0,0,100,100,0,0,1,{layout.ruby_outline_px},0,8,0,0,0,1",
        f"Style: Ruby,{FONT_FAMILY},{layout.ruby_font_size},&H00F3F3F3,&H00F3F3F3,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,{layout.ruby_outline_px},0,8,0,0,0,1",
        f"Style: CueDim,{FONT_FAMILY},{VOCAL_CUE_FONT_SIZE},&H68FFFFFF,&H68FFFFFF,&H80000000,&HFF000000,1,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1",
        f"Style: CueHot,{FONT_FAMILY},{VOCAL_CUE_FONT_SIZE},{main_hot},{main_hot},&H50000000,&HFF000000,1,0,0,0,100,100,0,0,1,4,0,5,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events: list[str] = []
    diagnostics: list[dict] = []
    secondary_diagnostics: list[dict] = []
    prepared: list[dict] = []
    secondary_prepared: list[dict] = []
    source_to_first_display: dict[int, int] = {}
    source_to_last_display: dict[int, int] = {}
    source_sentence_to_display: dict[int, dict] = {}
    latest_secondary_block_release_ms: int | None = None
    for source_line_index, source_sentence in enumerate(project.sentences):
        voice_role, singer_group = sentence_voice_metadata(source_sentence, project)
        sentence = source_sentence
        source_token_indices = tuple(range(len(sentence.characters)))
        source_character_indices = {
            id(character): source_token_indices[index]
            for index, character in enumerate(sentence.characters)
        }
        phrases = split_sentence_for_display(
            sentence,
            max_chars=layout.max_phrase_chars,
            language=language,
            font_file=font_file,
            layout=layout,
        )
        source_sentence_to_display[source_line_index] = {
            "source_sentence": source_sentence.text,
            "display_phrases": [phrase.text for phrase in phrases],
            "voice_role": voice_role,
            "singer_group": singer_group,
        }
        source_release_ms = _release_for_sentence(
            sentence,
            source_line_index,
            release_overrides,
        )
        if voice_role is not None:
            for phrase_index, phrase in enumerate(phrases):
                phrase_ruby_tokens = _canonical_tokens_for_phrase(
                    source_sentence,
                    phrase,
                    sidecar=ruby_sidecar,
                )
                first_onset = _first_timestamp(phrase)
                if first_onset is None:
                    continue
                semantic_gap_after_indices = _semantic_gap_after_indices(
                    sentence,
                    phrase,
                    language=language,
                )
                visual_onset_overrides = _trailing_repeated_token_visual_onsets(
                    phrase
                )
                glyph_release_overrides: dict[int, int] = {}
                source_glyph_indices: dict[int, int] = {}
                for display_index, character in enumerate(phrase.characters):
                    source_character_index = source_character_indices[id(character)]
                    source_glyph_indices[display_index] = source_character_index
                    override_key = (source_line_index, source_character_index)
                    if override_key in visual_release_overrides:
                        glyph_release_overrides[display_index] = int(
                            visual_release_overrides[override_key]
                        )
                        visual_release_override_hits.add(override_key)
                if phrase_index + 1 < len(phrases):
                    next_onset = _first_timestamp(phrases[phrase_index + 1])
                    release_ms = max(
                        _last_timestamp(phrase) or first_onset,
                        next_onset if next_onset is not None else first_onset,
                    )
                else:
                    release_ms = source_release_ms
                secondary_prepared.append(
                    {
                        "source_line_index": source_line_index,
                        "phrase_index": phrase_index,
                        "sentence": phrase,
                        "first_onset": first_onset,
                        "release_ms_raw": release_ms,
                        "semantic_gap_after_indices": semantic_gap_after_indices,
                        "visual_onset_overrides": visual_onset_overrides,
                        "visual_release_overrides": glyph_release_overrides,
                        "source_glyph_indices": source_glyph_indices,
                        "voice_role": voice_role,
                        "singer_group": singer_group,
                        "natural_start_ms": max(
                            0,
                            first_onset + offset_ms - PRE_ROLL_MS,
                        ),
                        "event_end_ms": release_ms + offset_ms + POST_ROLL_MS,
                        "ruby_tokens": phrase_ruby_tokens,
                    }
                )
                shifted_release_ms = release_ms + offset_ms
                latest_secondary_block_release_ms = max(
                    latest_secondary_block_release_ms or shifted_release_ms,
                    shifted_release_ms,
                )
            continue
        source_to_first_display[source_line_index] = len(prepared)
        for phrase_index, phrase in enumerate(phrases):
            phrase_ruby_tokens = _canonical_tokens_for_phrase(
                source_sentence,
                phrase,
                sidecar=ruby_sidecar,
            )
            first_onset = _first_timestamp(phrase)
            if first_onset is None:
                continue
            semantic_gap_after_indices = _semantic_gap_after_indices(
                sentence,
                phrase,
                language=language,
            )
            visual_onset_overrides = _trailing_repeated_token_visual_onsets(phrase)
            glyph_release_overrides: dict[int, int] = {}
            source_glyph_indices: dict[int, int] = {}
            for display_index, character in enumerate(phrase.characters):
                source_character_index = source_character_indices[id(character)]
                source_glyph_indices[display_index] = source_character_index
                override_key = (source_line_index, source_character_index)
                if override_key in visual_release_overrides:
                    glyph_release_overrides[display_index] = int(
                        visual_release_overrides[override_key]
                    )
                    visual_release_override_hits.add(override_key)
            if phrase_index + 1 < len(phrases):
                next_onset = _first_timestamp(phrases[phrase_index + 1])
                release_ms = max(
                    _last_timestamp(phrase) or first_onset,
                    next_onset if next_onset is not None else first_onset,
                )
            else:
                release_ms = source_release_ms
            display_index = len(prepared)
            prepared.append(
                {
                    "line_index": display_index,
                    "source_line_index": source_line_index,
                    "phrase_index": phrase_index,
                    "sentence": phrase,
                    "first_onset": first_onset,
                    "release_ms_raw": release_ms,
                    "semantic_gap_after_indices": semantic_gap_after_indices,
                    "visual_onset_overrides": visual_onset_overrides,
                    "visual_release_overrides": glyph_release_overrides,
                    "source_glyph_indices": source_glyph_indices,
                    "voice_role": None,
                    "singer_group": None,
                    "preceding_secondary_block_release_ms": (
                        latest_secondary_block_release_ms
                    ),
                    "visual_release_capped_from_ms": None,
                    "natural_start_ms": max(
                        0,
                        first_onset + offset_ms - PRE_ROLL_MS,
                    ),
                    "event_end_ms": release_ms + offset_ms + POST_ROLL_MS,
                    "ruby_tokens": phrase_ruby_tokens,
                }
            )
        source_to_last_display[source_line_index] = len(prepared) - 1

    missing_visual_release_overrides = (
        set(visual_release_overrides) - visual_release_override_hits
    )
    if missing_visual_release_overrides:
        raise ValueError(
            "unreachable visual release overrides: "
            f"{sorted(missing_visual_release_overrides)}"
        )

    _apply_visual_release_caps(prepared, offset_ms=offset_ms)

    source_cues = find_vocal_cues(
        project,
        release_overrides,
        offset_ms=offset_ms,
    )
    cues = [
        VocalCue(
            after_line_index=(
                source_to_last_display.get(cue.after_line_index, -1)
                if cue.after_line_index >= 0
                else -1
            ),
            before_line_index=source_to_first_display[cue.before_line_index],
            gap_start_ms=cue.gap_start_ms,
            vocal_onset_ms=cue.vocal_onset_ms,
            cue_start_ms=cue.cue_start_ms,
        )
        for cue in source_cues
        if cue.before_line_index in source_to_first_display
    ]
    # Lane phase is local to each vocal section.  Every intro/interlude starts
    # again in the upper-left lane, then alternates to the lower-right lane.
    # A global line-index parity would make odd-indexed interludes start in the
    # wrong lane.
    cue_start_indices = {cue.before_line_index for cue in cues}
    lane_phase = 0
    section_index = 0
    for prepared_index, item in enumerate(prepared):
        if prepared_index in cue_start_indices:
            lane_phase = 0
            if prepared_index > 0:
                section_index += 1
        item["lane_index"] = lane_phase
        item["section_index"] = section_index
        lane_phase = (lane_phase + 1) % len(layout.lanes)

    # Each lane is a rolling buffer within one vocal section.  Do not cap the
    # preceding section against the next section's natural pre-roll: interlude
    # preload timing below waits for the preceding visible lyrics to finish.
    previous_index_by_lane: dict[int, int] = {}
    for prepared_index, item in enumerate(prepared):
        lane_index = int(item["lane_index"])
        previous_index = previous_index_by_lane.get(lane_index)
        if (
            previous_index is not None
            and prepared[previous_index]["section_index"] == item["section_index"]
        ):
            prepared[previous_index]["event_end_ms"] = min(
                prepared[previous_index]["event_end_ms"],
                item["natural_start_ms"],
            )
        previous_index_by_lane[lane_index] = prepared_index

    # Keep both lyric lanes populated from one explicit cue preload start. The
    # intro begins at zero; an interlude waits until the previous two visible
    # phrases have fully ended. Cue dots still use cue.cue_start_ms below.
    cue_lyric_preload_start_by_line = _cue_lyric_preload_starts(cues, prepared)
    cue_placements: list[VocalCuePlacement] = []
    for cue in cues:
        target_sentence = prepared[cue.before_line_index]["sentence"]
        target_lane = layout.lanes[
            int(prepared[cue.before_line_index]["lane_index"])
        ]
        cue_x, cue_y = vocal_cue_anchor(
            target_sentence,
            line_index=cue.before_line_index,
            layout=layout,
            font_file=font_file,
            semantic_gap_after_indices=prepared[cue.before_line_index][
                "semantic_gap_after_indices"
            ],
            lane=target_lane,
        )
        placement = VocalCuePlacement(
            cue=cue,
            x=cue_x,
            y=cue_y,
            lane=target_lane,
        )
        cue_placements.append(placement)
        events.extend(
            vocal_cue_events(
                cue,
                cue_x=cue_x,
                cue_y=cue_y,
            )
        )

    initial_start_ms = min(
        (item["natural_start_ms"] for item in prepared[:2]),
        default=0,
    )
    previous_index_by_lane = {}
    for prepared_index, item in enumerate(prepared):
        line_index = item["line_index"]
        sentence = item["sentence"]
        first_onset = item["first_onset"]
        release_ms = item["release_ms_raw"]
        event_end_ms = item["event_end_ms"]
        earliest_allowed_ms = max(
            0,
            first_onset + offset_ms - MAX_EARLY_DISPLAY_MS,
        )
        lane_index = int(item["lane_index"])
        previous_index = previous_index_by_lane.get(lane_index)
        if previous_index is None:
            event_start_ms = item["natural_start_ms"]
        else:
            event_start_ms = max(
                prepared[previous_index]["event_end_ms"],
                earliest_allowed_ms,
            )
        if prepared_index == 1:
            event_start_ms = max(initial_start_ms, earliest_allowed_ms)
        cue_preload_start_ms = cue_lyric_preload_start_by_line.get(prepared_index)
        if cue_preload_start_ms is not None:
            event_start_ms = cue_preload_start_ms
        else:
            event_start_ms = min(event_start_ms, item["natural_start_ms"])
        # Secondary vocals occupy an independent top overlay. They must not
        # suppress the normal two-lane preload of upcoming main lyrics below.
        # Keep the legacy diagnostic fields for report compatibility, but the
        # barrier is intentionally disabled and acoustic checkpoints stay put.
        item["event_start_before_secondary_barrier_ms"] = event_start_ms
        item["secondary_release_barrier_applied"] = False
        item["event_start_ms"] = event_start_ms
        previous_index_by_lane[lane_index] = prepared_index
        lane = layout.lanes[lane_index]
        font_size = fit_main_font_size_for_layout(
            font_file,
            sentence.text,
            layout=layout,
            semantic_gap_count=len(item["semantic_gap_after_indices"]),
        )
        geometry = text_geometry(
            font_file,
            sentence.text,
            font_size=font_size,
            lane=lane,
            advance_scale=layout.advance_scale,
            semantic_gap_after_indices=item["semantic_gap_after_indices"],
            semantic_gap_em=layout.semantic_gap_em,
            letter_spacing_em=layout.letter_spacing_em,
            word_gap_em=layout.word_gap_em,
        )
        events.extend(
            main_glyph_events(
                sentence,
                event_start_ms=event_start_ms,
                event_end_ms=event_end_ms,
                release_ms=release_ms,
                lane=lane,
                font_file=font_file,
                font_size=font_size,
                advance_scale=layout.advance_scale,
                semantic_gap_after_indices=item["semantic_gap_after_indices"],
                semantic_gap_em=layout.semantic_gap_em,
                letter_spacing_em=layout.letter_spacing_em,
                word_gap_em=layout.word_gap_em,
                offset_ms=offset_ms,
                onset_overrides=item["visual_onset_overrides"],
                release_overrides=item["visual_release_overrides"],
                outline_px=layout.main_outline_px,
                glow_blur=layout.main_glow_blur,
                geometry=geometry,
            )
        )
        events.extend(
            ruby_events(
                sentence,
                event_start_ms=event_start_ms,
                event_end_ms=event_end_ms,
                lane=lane,
                font_file=font_file,
                main_font_size=font_size,
                ruby_font_size=layout.ruby_font_size,
                advance_scale=layout.advance_scale,
                semantic_gap_after_indices=item["semantic_gap_after_indices"],
                semantic_gap_em=layout.semantic_gap_em,
                letter_spacing_em=layout.letter_spacing_em,
                word_gap_em=layout.word_gap_em,
                tokens=item["ruby_tokens"],
                language=language,
                outline_px=layout.ruby_outline_px,
                glow_blur=layout.ruby_glow_blur,
                geometry=geometry,
            )
        )
        visual_character_onsets = _character_onsets(
            sentence,
            offset_ms=offset_ms,
            onset_overrides=item["visual_onset_overrides"],
            release_ms=release_ms,
        )
        visual_character_releases = _character_releases(
            visual_character_onsets,
            release_ms=release_ms,
            offset_ms=offset_ms,
            release_overrides=item["visual_release_overrides"],
        )
        diagnostics.append(
            {
                "line_index": line_index,
                "source_line_index": item["source_line_index"],
                "phrase_index": item["phrase_index"],
                "text": sentence.text,
                "source_sentence": project.sentences[item["source_line_index"]].text,
                "display_phrase": sentence.text,
                "voice_role": item["voice_role"],
                "singer_group": item["singer_group"],
                "event_start_ms": event_start_ms,
                "first_onset_ms": first_onset + offset_ms,
                "early_display_ms": first_onset + offset_ms - event_start_ms,
                "preceding_secondary_block_release_ms": item[
                    "preceding_secondary_block_release_ms"
                ],
                "event_start_before_secondary_barrier_ms": item[
                    "event_start_before_secondary_barrier_ms"
                ],
                "secondary_release_barrier_applied": item[
                    "secondary_release_barrier_applied"
                ],
                "release_ms": release_ms + offset_ms,
                "acoustic_release_ms": (
                    item["visual_release_capped_from_ms"] + offset_ms
                    if item["visual_release_capped_from_ms"] is not None
                    else release_ms + offset_ms
                ),
                "visual_release_capped": (
                    item["visual_release_capped_from_ms"] is not None
                ),
                "visual_onset_overrides": {
                    str(index): onset + offset_ms
                    for index, onset in item["visual_onset_overrides"].items()
                },
                "visual_release_overrides": {
                    str(index): release + offset_ms
                    for index, release in item["visual_release_overrides"].items()
                },
                "source_glyph_indices": {
                    str(index): source_index
                    for index, source_index in item["source_glyph_indices"].items()
                },
                "visual_character_onsets_ms": visual_character_onsets,
                "visual_character_releases_ms": visual_character_releases,
                "visual_onsets_strict_ass_order": all(
                    right // 10 > left // 10
                    for left, right in zip(
                        visual_character_onsets,
                        visual_character_onsets[1:],
                        strict=False,
                    )
                ),
                "event_end_ms": event_end_ms,
                "simultaneous_with_previous": prepared_index > 0,
                "lane": lane.__dict__,
                "font_size": font_size,
                "letter_spacing_em": layout.letter_spacing_em,
                "letter_spacing_px": round(geometry.letter_spacing_px, 3),
                "semantic_gap_after_indices": sorted(
                    item["semantic_gap_after_indices"]
                ),
                "semantic_gap_px": round(font_size * layout.semantic_gap_em, 2),
                "word_gap_px": round(geometry.word_gap_px or 0.0, 2),
                "geometry": {
                    "left": round(geometry.left, 3),
                    "right": round(geometry.right, 3),
                    "width": round(geometry.width, 3),
                    "clipped": (
                        geometry.left - layout.main_outline_px < 0
                        or geometry.right + layout.main_outline_px > CANVAS_WIDTH
                    ),
                    "overlap_count": sum(
                        1
                        for left, right in zip(
                            geometry.glyph_ends,
                            geometry.glyph_starts[1:],
                            strict=False,
                        )
                        if right < left
                    ),
                },
                "ruby": [
                    token.to_dict() for token in item["ruby_tokens"]
                ],
                "language": language,
                "language_identity": identity,
            }
        )
    for secondary_index, item in enumerate(secondary_prepared):
        sentence = item["sentence"]
        font_size = fit_secondary_font_size(
            font_file,
            sentence.text,
            semantic_gap_count=len(item["semantic_gap_after_indices"]),
            semantic_gap_em=layout.semantic_gap_em,
        )
        lane = centered_lane_for_text(
            font_file,
            sentence.text,
            font_size=font_size,
            advance_scale=layout.advance_scale,
            main_y=SECONDARY_TOP_Y,
            ruby_y=SECONDARY_TOP_Y,
            semantic_gap_after_indices=item["semantic_gap_after_indices"],
            semantic_gap_em=layout.semantic_gap_em,
        )
        event_start_ms = item["natural_start_ms"]
        event_end_ms = item["event_end_ms"]
        release_ms = item["release_ms_raw"]
        geometry = text_geometry(
            font_file,
            sentence.text,
            font_size=font_size,
            lane=lane,
            advance_scale=layout.advance_scale,
            semantic_gap_after_indices=item["semantic_gap_after_indices"],
            semantic_gap_em=layout.semantic_gap_em,
        )
        events.extend(
            main_glyph_events(
                sentence,
                event_start_ms=event_start_ms,
                event_end_ms=event_end_ms,
                release_ms=release_ms,
                lane=lane,
                font_file=font_file,
                font_size=font_size,
                advance_scale=layout.advance_scale,
                semantic_gap_after_indices=item["semantic_gap_after_indices"],
                semantic_gap_em=layout.semantic_gap_em,
                offset_ms=offset_ms,
                onset_overrides=item["visual_onset_overrides"],
                release_overrides=item["visual_release_overrides"],
                outline_px=SECONDARY_OUTLINE_PX,
                glow_blur=SECONDARY_GLOW_BLUR,
                glow_style="SecondaryGlow",
                main_style="Secondary",
                glow_layer=7,
                main_layer=8,
                geometry=geometry,
            )
        )
        secondary_diagnostics.append(
            {
                "secondary_line_index": secondary_index,
                "source_line_index": item["source_line_index"],
                "phrase_index": item["phrase_index"],
                "text": sentence.text,
                "source_sentence": project.sentences[
                    item["source_line_index"]
                ].text,
                "display_phrase": sentence.text,
                "voice_role": item["voice_role"],
                "singer_group": item["singer_group"],
                "event_start_ms": event_start_ms,
                "first_onset_ms": item["first_onset"] + offset_ms,
                "release_ms": release_ms + offset_ms,
                "event_end_ms": event_end_ms,
                "font_size": font_size,
                "ruby": [],
                "lane": lane.__dict__,
                "top_safe_area": {
                    "top_px": SECONDARY_TOP_SAFE_TOP_PX,
                    "bottom_px": SECONDARY_TOP_SAFE_BOTTOM_PX,
                    "center_x": CANVAS_WIDTH // 2,
                },
                "letter_spacing_em": 0.0,
                "letter_spacing_px": 0.0,
                "highlight_color": secondary_highlight_color,
                "highlight_color_source": secondary_highlight_color_source,
                "hot_primary_ass": secondary_hot,
                "unhighlighted_secondary_ass": "&H00FFFFFF",
                "colors": {
                    "highlight_color": secondary_highlight_color,
                    "highlight_color_source": secondary_highlight_color_source,
                    "hot_primary_ass": secondary_hot,
                    "glow_hot_primary_ass": secondary_hot,
                    "unhighlighted_ass": "&H00FFFFFF",
                    "glow_unhighlighted_ass": "&H70FFFFFF",
                },
                "semantic_gap_after_indices": sorted(
                    item["semantic_gap_after_indices"]
                ),
                "geometry": {
                    "left": round(geometry.left, 3),
                    "right": round(geometry.right, 3),
                    "width": round(geometry.width, 3),
                    "clipped": (
                        geometry.left - SECONDARY_OUTLINE_PX < 0
                        or geometry.right + SECONDARY_OUTLINE_PX > CANVAS_WIDTH
                    ),
                    "overlap_count": sum(
                        1
                        for left, right in zip(
                            geometry.glyph_ends,
                            geometry.glyph_starts[1:],
                            strict=False,
                        )
                        if right < left
                    ),
                },
            }
        )
    outro: dict | None = None
    project_duration_ms = int(getattr(project, "audio_duration_ms", 0) or 0)
    if prepared and project_duration_ms > int(prepared[-1]["event_end_ms"]):
        marker_start_ms = int(prepared[-1]["event_end_ms"])
        marker_end_ms = project_duration_ms
        marker_text = "終わり"
        marker_sentence = Sentence.from_text(marker_text, "outro")
        marker_fill_duration_ms = marker_end_ms - marker_start_ms
        marker_character_onsets_ms = [
            marker_start_ms
            + round(
                marker_fill_duration_ms * index / len(marker_sentence.characters)
            )
            for index in range(len(marker_sentence.characters))
        ]
        for character, onset_ms in zip(
            marker_sentence.characters,
            marker_character_onsets_ms,
            strict=True,
        ):
            character.add_timestamp(onset_ms)
        marker_font_size = layout.main_font_size
        marker_lane = centered_lane_for_text(
            font_file,
            marker_text,
            font_size=marker_font_size,
            advance_scale=layout.advance_scale,
            letter_spacing_em=layout.letter_spacing_em,
            word_gap_em=layout.word_gap_em,
        )
        events.extend(
            main_glyph_events(
                marker_sentence,
                event_start_ms=marker_start_ms,
                event_end_ms=marker_end_ms,
                release_ms=marker_end_ms,
                lane=marker_lane,
                font_file=font_file,
                font_size=marker_font_size,
                advance_scale=layout.advance_scale,
                letter_spacing_em=layout.letter_spacing_em,
                word_gap_em=layout.word_gap_em,
                outline_px=layout.main_outline_px,
                glow_blur=layout.main_glow_blur,
            )
        )
        marker_ruby = "お"
        if marker_ruby:
            events.extend(
                ruby_events(
                    marker_sentence,
                    event_start_ms=marker_start_ms,
                    event_end_ms=marker_end_ms,
                    lane=marker_lane,
                    font_file=font_file,
                    main_font_size=marker_font_size,
                    ruby_font_size=layout.ruby_font_size,
                    advance_scale=layout.advance_scale,
                    letter_spacing_em=layout.letter_spacing_em,
                    word_gap_em=layout.word_gap_em,
                    tokens=[
                        RubyToken(
                            text="終",
                            reading=marker_ruby,
                            start=0,
                            end=1,
                            source="renderer-marker",
                            review_status="system-generated",
                        )
                    ],
                    language=language,
                    outline_px=layout.ruby_outline_px,
                    glow_blur=layout.ruby_glow_blur,
                )
            )
        outro = {
            "text": marker_text,
            "ruby": marker_ruby,
            "language": language,
            "language_identity": identity,
            "event_start_ms": marker_start_ms,
            "event_end_ms": marker_end_ms,
            "fill_duration_ms": marker_fill_duration_ms,
            "character_onsets_ms": marker_character_onsets_ms,
            "placement": "subtitle-region-center",
            "center_x": CANVAS_WIDTH // 2,
            "lane": marker_lane.__dict__,
        }

    if secondary_diagnostics:
        secondary_styles = [
            f"Style: SecondaryGlow,{FONT_FAMILY},{SECONDARY_FONT_SIZE},{secondary_hot},&H70FFFFFF,&HA0FFFFFF,&HFF000000,1,0,0,0,100,100,0,0,1,{SECONDARY_OUTLINE_PX},0,8,0,0,0,1",
            f"Style: Secondary,{FONT_FAMILY},{SECONDARY_FONT_SIZE},{secondary_hot},&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,{SECONDARY_OUTLINE_PX},0,8,0,0,0,1",
        ]
        events_marker = header.index("[Events]")
        header[events_marker:events_marker] = secondary_styles

    is_wide = layout.name.startswith("wide")
    shrunk_lines = [
        {"line_index": line["line_index"], "text": line["text"], "font_size": line["font_size"]}
        for line in diagnostics
        if line["font_size"] != layout.main_font_size
    ]
    if is_wide and layout.enforce_main_font_size and shrunk_lines:
        raise ValueError(
            "wide layout requires the requested main font size without silent "
            f"shrink: {shrunk_lines!r}"
        )
    lane_anchor_gaps = [lane.main_y - lane.ruby_y for lane in layout.lanes]
    outro_anchor_gap = (
        outro["lane"]["main_y"] - outro["lane"]["ruby_y"] if outro else None
    )
    cue_anchor_gaps = [
        placement.lane.ruby_y - placement.y for placement in cue_placements
    ]
    layout_contract = {
        "status": "pass" if is_wide else "not-applicable",
        "main_font_size": layout.main_font_size,
        "ruby_font_size": layout.ruby_font_size,
        "cue_font_size": VOCAL_CUE_FONT_SIZE,
        "ruby_to_main_anchor_gap_px": WIDE_RUBY_TO_MAIN_ANCHOR_GAP_PX,
        "cue_above_ruby_px": VOCAL_CUE_ABOVE_RUBY_PX,
        "lane_anchor_gaps_px": lane_anchor_gaps,
        "outro_anchor_gap_px": outro_anchor_gap,
        "cue_anchor_gaps_px": cue_anchor_gaps,
        "shrunk_line_count": len(shrunk_lines),
        "shrunk_lines": shrunk_lines,
        "letter_spacing": {
            "em": layout.letter_spacing_em,
            "px_at_main_font_size": round(
                layout.main_font_size * layout.letter_spacing_em,
                3,
            ),
            "scope": "adjacent-visible-glyphs" if layout.letter_spacing_em > 0 else "none",
            "positive": layout.letter_spacing_em > 0,
        },
        "word_gap": {
            "target_em": layout.word_gap_em,
            "target_px_at_main_font_size": (
                round(layout.main_font_size * layout.word_gap_em, 3)
                if layout.word_gap_em is not None
                else None
            ),
            "semantic_gap_em": layout.semantic_gap_em,
            "semantic_gap_px_at_main_font_size": round(
                layout.main_font_size * layout.semantic_gap_em,
                3,
            ),
            "natural_space_px_at_main_font_size": round(
                ImageFont.truetype(
                    str(font_file),
                    layout.main_font_size,
                ).getlength(" "),
                3,
            ),
            "natural_space_only": (
                layout.semantic_gap_em == 0.0 and layout.word_gap_em is None
            ),
            "narrowed_from_natural": (
                layout.word_gap_em is not None
                and layout.main_font_size * layout.word_gap_em
                < ImageFont.truetype(
                    str(font_file),
                    layout.main_font_size,
                ).getlength(" ")
            ),
            "strategy": (
                "fixed-em-renderer-geometry"
                if layout.word_gap_em is not None
                else "font-natural-plus-semantic-gap"
            ),
            "word_run_positioning_advance_scale": (
                layout.advance_scale if layout.word_gap_em is not None else None
            ),
            "word_internal_spacing_affected": False,
            "greater_than_letter_spacing": (
                (
                    layout.main_font_size * layout.word_gap_em
                    if layout.word_gap_em is not None
                    else ImageFont.truetype(
                        str(font_file),
                        layout.main_font_size,
                    ).getlength(" ")
                    + layout.main_font_size * layout.semantic_gap_em
                )
                > layout.main_font_size * layout.letter_spacing_em
            ),
        },
        "top_safe_area": {
            "top_px": SECONDARY_TOP_SAFE_TOP_PX,
            "bottom_px": SECONDARY_TOP_SAFE_BOTTOM_PX,
            "center_x": CANVAS_WIDTH // 2,
            "secondary_y": SECONDARY_TOP_Y,
        },
        "secondary": {
            "font_size": SECONDARY_FONT_SIZE,
            "min_font_size": SECONDARY_MIN_FONT_SIZE,
            "style": "Secondary",
            "glow_style": "SecondaryGlow",
            "line_count": len(secondary_diagnostics),
            "excluded_from_main_lane_phase": True,
            "excluded_from_main_cue_pairing": True,
            "blocks_main_preload": False,
            "main_preload_coexists": True,
            "ruby": False,
            "highlight_color": secondary_highlight_color,
            "highlight_color_source": secondary_highlight_color_source,
            "hot_primary_ass": secondary_hot,
            "unhighlighted_secondary_ass": "&H00FFFFFF",
            "glow_unhighlighted_secondary_ass": "&H70FFFFFF",
        },
        "highlight_color": highlight_color,
        "highlight_color_source": (
            "project-default-singer" if getattr(project, "singers", None) else "fallback"
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(header + events) + "\n", encoding="utf-8")
    lyric_texts = [sentence.text for sentence in project.sentences]
    if outro is not None:
        lyric_texts.append(str(outro["text"]))
    canonical_ruby_spans = [token.to_dict() for token in canonical_project_ruby_tokens]
    return {
        "ass": str(output_path),
        "language": language,
        "language_identity": identity,
        "ruby_enabled": uses_ruby(language),
        "sug_hash": canonical_sug_hash,
        "ruby_source": "canonical-sug",
        "ruby_spans": canonical_ruby_spans,
        "ruby_review": {
            "schema": ruby_sidecar.get("schema") if ruby_sidecar else None,
            "generation_id": ruby_sidecar.get("generation_id") if ruby_sidecar else None,
            "record_count": len(ruby_sidecar.get("records", []))
            if ruby_sidecar
            and isinstance(ruby_sidecar.get("records"), list)
            else 0,
        },
        "pronunciation_validation": pronunciation_result.to_dict(),
        "ruby_consistency_gate": {
            "sug": "canonical-sug",
            "ass": "canonical-sug",
            "report": "canonical-sug",
            "sug_hash": canonical_sug_hash,
            "status": "pass",
        },
        "font_verification": verify_font(
            FONT_FAMILY,
            font_file,
            lyric_texts=lyric_texts,
        ),
        "layout": layout.name,
        "layout_contract": layout_contract,
        "lines": diagnostics,
        "secondary_lines": secondary_diagnostics,
        "source_sentence_to_display_phrases": [
            {
                "source_line_index": source_line_index,
                **mapping,
            }
            for source_line_index, mapping in source_sentence_to_display.items()
        ],
        "source_to_display": {
            str(source_line_index): mapping
            for source_line_index, mapping in source_sentence_to_display.items()
        },
        "letter_spacing": layout_contract["letter_spacing"],
        "top_safe_area": layout_contract["top_safe_area"],
        "cue_config": {
            "gap_threshold_ms": INTERLUDE_GAP_THRESHOLD_MS,
            "lead_ms": VOCAL_CUE_LEAD_MS,
            "dot_count": VOCAL_CUE_DOT_COUNT,
        },
        "outro": outro,
        "cues": [
            {
                "after_line_index": placement.cue.after_line_index,
                "before_line_index": placement.cue.before_line_index,
                "gap_start_ms": placement.cue.gap_start_ms,
                "gap_ms": placement.cue.gap_ms,
                "cue_start_ms": placement.cue.cue_start_ms,
                "vocal_onset_ms": placement.cue.vocal_onset_ms,
                "dot_starts_ms": list(placement.cue.dot_starts_ms),
                "anchor": {
                    "x": placement.x,
                    "y": placement.y,
                    "relation": "above-next-lyric-start",
                    "lane": placement.lane.__dict__,
                },
            }
            for placement in cue_placements
        ],
    }


def build_review_ass_required(*args: Any, **kwargs: Any) -> dict:
    """Compatibility entry point for callers that explicitly require sidecars."""

    kwargs["pronunciation_validation"] = "required"
    return build_review_ass(*args, **kwargs)


def ensure_lossless_output_is_new(output_path: Path) -> None:
    if output_path.exists():
        raise FileExistsError(
            "lossless preview refuses to overwrite existing output: "
            f"{output_path}"
        )


def probe_lossless_audio_codec(audio_path: Path) -> str:
    """Probe the source audio and return its first FFmpeg-reported codec."""

    if audio_path.suffix.lower() not in {".flac", ".wav"}:
        raise ValueError(
            "--lossless-output requires --audio with a .flac or .wav extension: "
            f"{audio_path}"
        )

    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    completed = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(audio_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    diagnostic = completed.stdout + "\n" + completed.stderr
    codecs = re.findall(
        r"Stream #.*?:\s*Audio:\s*([^\s,]+)",
        diagnostic,
        flags=re.IGNORECASE,
    )
    if not codecs:
        raise ValueError(
            "FFmpeg could not probe a lossless audio stream in "
            f"{audio_path}"
        )
    normalized = [codec.lower() for codec in codecs]
    unsupported = [
        codec
        for codec in normalized
        if codec != "flac" and not codec.startswith("pcm_")
    ]
    if unsupported:
        raise ValueError(
            "--lossless-output requires a lossless --audio source; FFmpeg "
            f"reported codec(s) {', '.join(normalized)} for {audio_path}"
        )
    return normalized[0]


def build_lossless_review_command(
    *,
    ffmpeg: Path,
    mp4_path: Path,
    audio_path: Path,
    output_path: Path,
    start_seconds: float,
    duration_seconds: float,
) -> list[str]:
    """Build the MKV command using MP4 video and original lossless audio."""

    start = max(0.0, float(start_seconds))
    duration = max(0.1, float(duration_seconds))
    end = start + duration
    return [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-i",
        str(mp4_path),
        "-i",
        str(audio_path),
        "-filter_complex",
        (
            f"[1:a]atrim=start={start:.3f}:end={end:.3f},"
            "asetpts=PTS-STARTPTS[lossless_audio]"
        ),
        "-map",
        "0:v:0",
        "-map",
        "[lossless_audio]",
        "-c:v",
        "copy",
        "-c:a",
        LOSSLESS_AUDIO_CODEC,
        str(output_path),
    ]


def render_lossless_review_clip(
    *,
    ffmpeg: Path,
    mp4_path: Path,
    audio_path: Path,
    output_path: Path,
    start_seconds: float,
    duration_seconds: float,
    source_codec: str | None = None,
) -> dict:
    """Mux copied MP4 video with a trimmed FLAC stream from the source audio."""

    ensure_lossless_output_is_new(output_path)
    if not mp4_path.exists():
        raise FileNotFoundError(f"MP4 preview was not generated: {mp4_path}")
    if source_codec is None:
        source_codec = probe_lossless_audio_codec(audio_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_lossless_review_command(
        ffmpeg=ffmpeg,
        mp4_path=mp4_path,
        audio_path=audio_path,
        output_path=output_path,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
    )
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0 or not output_path.exists():
        raise RuntimeError(completed.stderr[-2000:])
    start = max(0.0, float(start_seconds))
    duration = max(0.1, float(duration_seconds))
    end = start + duration
    return {
        "path": str(output_path),
        "container": "mkv",
        "codec": LOSSLESS_AUDIO_CODEC,
        "audio_codec": LOSSLESS_AUDIO_CODEC,
        "source": str(audio_path),
        "audio_source": str(audio_path),
        "source_codec": source_codec,
        "video_source": str(mp4_path),
        "video_codec": "copy",
        "audio_filter": (
            f"[1:a]atrim=start={start:.3f}:end={end:.3f},"
            "asetpts=PTS-STARTPTS"
        ),
        "start_seconds": start,
        "duration_seconds": duration,
        "bytes": output_path.stat().st_size,
    }


def render_review_clip(
    *,
    ass_path: Path,
    audio_path: Path,
    composition_path: Path,
    vinyl_path: Path | None,
    fonts_dir: Path,
    output_path: Path,
    start_seconds: float,
    duration_seconds: float,
    layout: SubtitleLayout = STANDARD_LAYOUT,
    video_encoder: str = "libx264",
    av1_cq: int = DEFAULT_AV1_CQ,
    hevc_cq: int = 30,
    visual_style: str = "vinyl",
    vinyl_motion: str = "rotate",
    spectrum_color: str = "#E19E84",
    progress_color: str | None = None,
    program_duration_seconds: float | None = None,
    lossless_output: Path | None = None,
    lossless_audio_codec: str | None = None,
) -> dict:
    if lossless_output is not None:
        ensure_lossless_output_is_new(lossless_output)
        if lossless_output.resolve() == output_path.resolve():
            raise ValueError("lossless output must be different from MP4 output")
        if lossless_audio_codec is None:
            lossless_audio_codec = probe_lossless_audio_codec(audio_path)
    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    subtitle = (
        f"ass=filename='{escape_filter_path(ass_path)}'"
        f":fontsdir='{escape_filter_path(fonts_dir)}'"
    )
    start = max(0.0, float(start_seconds))
    duration = max(0.1, float(duration_seconds))
    end = start + duration
    if visual_style not in {"vinyl", "spectrum"}:
        raise ValueError(f"unsupported visual style: {visual_style}")
    if vinyl_motion not in {"static", "rotate"}:
        raise ValueError(f"unsupported vinyl motion: {vinyl_motion}")
    if visual_style == "vinyl" and vinyl_path is None:
        raise ValueError("vinyl visual style requires a vinyl image")
    if visual_style == "spectrum" and output_path.exists():
        raise FileExistsError(f"spectrum preview already exists: {output_path}")
    color = spectrum_color.strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
        raise ValueError(f"invalid spectrum color: {spectrum_color!r}")
    color = color.upper()
    progress = (progress_color or f"#{color}").strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", progress):
        raise ValueError(f"invalid progress color: {progress_color!r}")
    progress = progress.upper()
    program_duration = max(
        end,
        float(program_duration_seconds)
        if program_duration_seconds is not None
        else end,
    )
    if visual_style == "vinyl":
        vinyl_filter = (
            "format=rgba,rotate=2*PI*t/8:ow=iw:oh=ih:"
            "fillcolor=black@0:bilinear=1"
            if vinyl_motion == "rotate"
            else "format=rgba"
        )
        filter_complex = (
            "[0:v]format=rgba[bg];"
            f"[1:v]scale={layout.vinyl_size}:{layout.vinyl_size}:flags=lanczos,"
            f"{vinyl_filter}[vinyl];"
            f"[bg][vinyl]overlay={layout.vinyl_x}:{layout.vinyl_y}:format=auto[scene];"
            f"[scene]{subtitle},trim=start={start:.3f}:end={end:.3f},"
            "setpts=PTS-STARTPTS[v];"
            f"[2:a]atrim=start={start:.3f}:end={end:.3f},"
            "asetpts=PTS-STARTPTS[a]"
        )
    else:
        filter_complex = (
            f"[0:v]format=rgba,trim=start={start:.3f}:end={end:.3f},"
            "setpts=PTS-STARTPTS[bgclip];"
            f"[1:a]atrim=start={start:.3f}:end={end:.3f},"
            "asetpts=PTS-STARTPTS,asplit=2[a][specaudio];"
            "[specaudio]aformat=channel_layouts=mono,"
            "showfreqs=s=80x220:r=30:mode=bar:ascale=log:fscale=log:"
            f"win_size=4096:overlap=0.80:averaging=4:colors=0x{color},"
            "scale=1040:220:flags=neighbor,"
            "drawgrid=width=13:height=220:thickness=5:color=black@1,"
            "format=rgba,colorkey=0x000000:0.06:0.08,alphaextract,"
            # Preserve the 220 px bars at y=290 while giving their mask 8 px
            # of clearance above and below for rounded edges.
            "pad=1040:236:0:8:color=black,"
            "erosion=coordinates=90,erosion=coordinates=90,"
            "erosion=coordinates=90,dilation=coordinates=90,"
            "dilation=coordinates=90,dilation=coordinates=90,"
            "gblur=sigma=0.8:steps=1,"
            "split=5[coremask][specinner][specouter][specwide][specpeak];"
            "[specinner]pad=1168:348:64:56:color=black,"
            "gblur=sigma=4:steps=2,"
            "lut=y='val*2.0'[innermask];"
            "[specouter]pad=1168:348:64:56:color=black,"
            "gblur=sigma=14:steps=2,"
            "lut=y='val*2.4'[outermask];"
            "[specwide]pad=1168:348:64:56:color=black,"
            "gblur=sigma=28:steps=3,"
            "lut=y='val*2.8'[widemask];"
            "[specpeak]pad=1168:348:64:56:color=black,"
            "lagfun=decay=0.975,"
            "gblur=sigma=2.2:steps=2,lut=y='val*0.55'[peakmask];"
            f"color=c=0x{color}:s=1040x236:r=30:d={duration:.3f},"
            "format=rgba,colorchannelmixer=rr=1:rg=0.18:rb=0.18:"
            "gr=0.18:gg=1:gb=0.18:br=0.18:bg=0.18:bb=1[corecolor];"
            f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
            "format=rgba[innercolor];"
            f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
            "format=rgba[outercolor];"
            f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
            "format=rgba[widecolor];"
            f"color=c=0x{color}:s=1168x348:r=30:d={duration:.3f},"
            "format=rgba[peakcolor];"
            "[corecolor][coremask]alphamerge[core];"
            "[innercolor][innermask]alphamerge[innerglow];"
            "[outercolor][outermask]alphamerge[outerglow];"
            "[widecolor][widemask]alphamerge[wideglow];"
            "[peakcolor][peakmask]alphamerge[peakhold];"
            # Moving the padded layers upward exactly offsets their new top
            # padding: bars stay at y=290 and the glow gains a clip-safe top.
            "[bgclip][wideglow]overlay=736:226:format=auto[wide];"
            "[wide][outerglow]overlay=736:226:format=auto[outer];"
            "[outer][peakhold]overlay=736:226:format=auto[held];"
            "[held][innerglow]overlay=736:226:format=auto[inner];"
            "[inner][core]overlay=800:282:format=auto[spectrumbars];"
            f"[spectrumbars]drawbox=x=800:y=516:w=1040:h=3:"
            f"color=0x{color}@0.85:t=fill[spectrumscene];"
            f"color=c=black@0.0:s=1040x28:r=30:d={duration:.3f},"
            "format=rgba[progressbase];"
            f"color=c=0x{progress}@0.98:s=1040x6:r=30:d={duration:.3f},"
            "format=rgba[progressfill];"
            "[progressbase][progressfill]overlay="
            f"x='-1040+1040*(t+{start:.3f})/{program_duration:.3f}':"
            "y=11:eval=frame:format=auto[progress];"
            "[progress]split=2[progresscore][progressglowsrc];"
            "[progressglowsrc]gblur=sigma=8:steps=2,"
            "colorchannelmixer=aa=2.0[progressglow];"
            f"color=c=0x{progress}:s=40x40:r=30:d={duration:.3f},"
            "format=rgba,"
            "geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)':"
            "a='255*lte((X-19.5)*(X-19.5)+(Y-19.5)*(Y-19.5)\\,100)'"
            "[knobsource];"
            "[knobsource]split=2[knobcore][knobglowsrc];"
            "[knobglowsrc]gblur=sigma=7:steps=2,"
            "colorchannelmixer=aa=1.8[knobglow];"
            f"[spectrumscene]drawbox=x=800:y=548:w=1040:h=6:"
            f"color=0x{progress}@0.34:t=fill[track];"
            "[track][progressglow]overlay=800:537:format=auto[trackglow];"
            "[trackglow][progresscore]overlay=800:537:format=auto[progressscene];"
            "[progressscene][knobglow]overlay="
            f"x='800+1040*(t+{start:.3f})/{program_duration:.3f}-20':"
            "y=531:eval=frame:format=auto[knobhalo];"
            "[knobhalo][knobcore]overlay="
            f"x='800+1040*(t+{start:.3f})/{program_duration:.3f}-20':"
            "y=531:eval=frame:format=auto[visual];"
            f"[visual]setpts=PTS+{start:.3f}/TB,{subtitle},"
            "setpts=PTS-STARTPTS[v]"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pixel_format = "yuv420p"
    if video_encoder == "av1_nvenc":
        video_codec_args = [
            "-c:v",
            "av1_nvenc",
            "-preset",
            DEFAULT_AV1_PRESET,
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            str(av1_cq),
            "-b:v",
            "0",
            "-multipass",
            "fullres",
            "-rc-lookahead",
            "32",
            "-spatial-aq",
            "1",
            "-temporal-aq",
            "1",
            "-aq-strength",
            "8",
            "-g",
            "240",
            "-tag:v",
            "av01",
            "-bsf:v",
            (
                "av1_metadata=color_primaries=1:transfer_characteristics=1:"
                "matrix_coefficients=1:color_range=tv"
            ),
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
        ]
    elif video_encoder == "libaom-av1":
        pixel_format = "gbrp"
        video_codec_args = [
            "-c:v",
            "libaom-av1",
            "-usage",
            "realtime",
            "-cpu-used",
            "8",
            "-row-mt",
            "1",
            "-tiles",
            "2x2",
            "-crf",
            "34",
            "-b:v",
            "0",
            "-g",
            "240",
            "-tag:v",
            "av01",
            "-color_range",
            "pc",
            "-colorspace",
            "rgb",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "iec61966-2-1",
        ]
    elif video_encoder == "hevc_nvenc_444":
        pixel_format = "yuv444p"
        video_codec_args = [
            "-c:v",
            "hevc_nvenc",
            "-profile:v",
            "rext",
            "-preset",
            "p7",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            str(hevc_cq),
            "-b:v",
            "0",
            "-multipass",
            "fullres",
            "-rc-lookahead",
            "32",
            "-spatial-aq",
            "1",
            "-temporal-aq",
            "1",
            "-aq-strength",
            "8",
            "-g",
            "240",
            "-tag:v",
            "hvc1",
            "-color_range",
            "pc",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
        ]
    elif video_encoder == "h264_nvenc":
        video_codec_args = [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p7",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            "20",
            "-b:v",
            "0",
            "-multipass",
            "fullres",
            "-rc-lookahead",
            "32",
            "-spatial-aq",
            "1",
            "-temporal-aq",
            "1",
            "-aq-strength",
            "8",
            "-g",
            "240",
        ]
    else:
        video_codec_args = [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
        ]
    input_args = [
        "-loop",
        "1",
        "-framerate",
        "30",
        "-i",
        str(composition_path),
    ]
    if visual_style == "vinyl":
        input_args.extend(
            [
                "-loop",
                "1",
                "-framerate",
                "30",
                "-i",
                str(vinyl_path),
            ]
        )
    input_args.extend(["-i", str(audio_path)])
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y" if visual_style == "vinyl" else "-n",
        *input_args,
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-t",
        f"{duration:.3f}",
        "-r",
        "30",
        "-fps_mode",
        "cfr",
        *video_codec_args,
        "-pix_fmt",
        pixel_format,
        "-c:a",
        "aac",
        "-profile:a",
        COMPATIBILITY_AUDIO_PROFILE,
        "-b:a",
        COMPATIBILITY_AUDIO_BITRATE,
        "-ar",
        "44100",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0 or not output_path.exists():
        raise RuntimeError(completed.stderr[-2000:])
    lossless_report = None
    if lossless_output is not None:
        lossless_report = render_lossless_review_clip(
            ffmpeg=ffmpeg,
            mp4_path=output_path,
            audio_path=audio_path,
            output_path=lossless_output,
            start_seconds=start,
            duration_seconds=duration,
            source_codec=lossless_audio_codec,
        )
    vinyl_asset = None
    if visual_style == "vinyl" and vinyl_path is not None:
        provenance_path = vinyl_path.parent / "artwork.json"
        provenance = None
        if provenance_path.is_file():
            try:
                candidate = json.loads(provenance_path.read_text(encoding="utf-8"))
                provenance = candidate if isinstance(candidate, dict) else None
            except (OSError, ValueError):
                provenance = None
        vinyl_asset = {
            "path": str(vinyl_path),
            "sha256": (
                hashlib.sha256(vinyl_path.read_bytes()).hexdigest()
                if vinyl_path.is_file()
                else None
            ),
            "provenance_path": (
                str(provenance_path) if provenance_path.is_file() else None
            ),
            "provenance_sha256": (
                hashlib.sha256(provenance_path.read_bytes()).hexdigest()
                if provenance_path.is_file()
                else None
            ),
            "generated_at_utc": (
                provenance.get("generated_at_utc") if provenance else None
            ),
            "source_sha256": provenance.get("source_sha256") if provenance else None,
            "vinyl_style_version": (
                provenance.get("vinyl_style_version") if provenance else None
            ),
            "metadata_vinyl_sha256": (
                provenance.get("vinyl_sha256") if provenance else None
            ),
            "vinyl_generator_sha256": (
                (
                    provenance.get("vinyl_generator_sha256")
                    or provenance.get("render_vinyl_karaoke_sha256")
                )
                if provenance
                else None
            ),
            "vinyl_generator_sha256_field": (
                "vinyl_generator_sha256"
                if provenance and provenance.get("vinyl_generator_sha256")
                else "render_vinyl_karaoke_sha256"
                if provenance and provenance.get("render_vinyl_karaoke_sha256")
                else None
            ),
            "generator": "scripts/render_vinyl_karaoke.py",
        }
    return {
        "video": str(output_path),
        "bytes": output_path.stat().st_size,
        "start_seconds": start,
        "duration_seconds": duration,
        "ffmpeg": str(ffmpeg),
        "layout": layout.name,
        "video_encoder": video_encoder,
        "pixel_format": pixel_format,
        "preferred_output": "compatibility-mp4",
        "default_output": "compatibility-mp4",
        "output_policy": {
            "preferred": "compatibility-mp4",
            "default": "compatibility-mp4",
        },
        "compatibility_mp4": {
            "path": str(output_path),
            "container": "mp4",
            "audio_codec": "aac",
            "audio_profile": COMPATIBILITY_AUDIO_PROFILE,
            "audio_codec_label": "AAC-LC",
            "audio_bitrate": COMPATIBILITY_AUDIO_BITRATE,
        },
        "audio_codec": "aac",
        "audio_profile": COMPATIBILITY_AUDIO_PROFILE,
        "audio_bitrate": COMPATIBILITY_AUDIO_BITRATE,
        "lossless": lossless_report,
        "av1_cq": av1_cq if video_encoder == "av1_nvenc" else None,
        "av1_preset": (
            DEFAULT_AV1_PRESET if video_encoder == "av1_nvenc" else None
        ),
        "hevc_cq": hevc_cq if video_encoder == "hevc_nvenc_444" else None,
        "visual_style": visual_style,
        "vinyl_motion": vinyl_motion,
        "vinyl_asset": vinyl_asset,
        "spectrum_color": f"#{color}" if visual_style == "spectrum" else None,
        "spectrum_geometry": (
            {"x": 800, "y": 290, "width": 1040, "height": 220}
            if visual_style == "spectrum"
            else None
        ),
        "spectrum_mode": "glowing-bars" if visual_style == "spectrum" else None,
        "spectrum_bar_count": 80 if visual_style == "spectrum" else None,
        "spectrum_bar_corner_radius_px": 3 if visual_style == "spectrum" else None,
        "spectrum_bar_soft_edge_sigma": 0.8 if visual_style == "spectrum" else None,
        "spectrum_glow_horizontal_padding_px": (
            64 if visual_style == "spectrum" else None
        ),
        "spectrum_bar_top_clearance_px": 8 if visual_style == "spectrum" else None,
        "spectrum_bar_bottom_clearance_px": 8 if visual_style == "spectrum" else None,
        "spectrum_glow_top_padding_px": 56 if visual_style == "spectrum" else None,
        "spectrum_glow_bottom_padding_px": 56 if visual_style == "spectrum" else None,
        "spectrum_clip_safe_geometry": (
            {"x": 736, "y": 226, "width": 1168, "height": 348}
            if visual_style == "spectrum"
            else None
        ),
        "spectrum_baseline_y": 516 if visual_style == "spectrum" else None,
        "peak_hold": (
            {"enabled": True, "decay": 0.975, "half_life_seconds": 0.91}
            if visual_style == "spectrum"
            else None
        ),
        "program_duration_seconds": (
            program_duration if visual_style == "spectrum" else None
        ),
        "progress_bar": (
            {
                "x": 800,
                "y": 548,
                "width": 1040,
                "height": 6,
                "color": f"#{progress}",
                "color_source": (
                    "explicit-secondary" if progress_color is not None else "spectrum-fallback"
                ),
                "show_time": False,
                "indicator": {"shape": "circle", "diameter": 20},
            }
            if visual_style == "spectrum"
            else None
        ),
    }


def parse_release_overrides(values: Iterable[str]) -> dict[int, int]:
    result: dict[int, int] = {}
    for value in values:
        match = re.fullmatch(r"(\d+)=(\d+)", value.strip())
        if not match:
            raise argparse.ArgumentTypeError(
                f"invalid --release value {value!r}; expected LINE_INDEX=MILLISECONDS"
            )
        result[int(match.group(1))] = int(match.group(2))
    return result


def load_visual_release_overrides(
    path: Path,
    *,
    song_id: str,
) -> dict[tuple[int, int], int]:
    """Load source-line/source-character visual sweep releases."""

    document = json.loads(path.read_text(encoding="utf-8"))
    songs = document.get("songs") if isinstance(document, dict) else None
    song = songs.get(song_id) if isinstance(songs, dict) else None
    lines = song.get("lines") if isinstance(song, dict) else None
    if not isinstance(lines, dict):
        raise ValueError(f"timing overrides have no song {song_id}: {path}")
    result: dict[tuple[int, int], int] = {}
    for line_index_text, line in lines.items():
        if not isinstance(line, dict):
            continue
        values = line.get("visual_release_overrides_ms")
        if values is None:
            continue
        if not isinstance(values, dict):
            raise ValueError(
                f"visual releases for song {song_id} line {line_index_text} "
                "must be an object"
            )
        line_index = int(line_index_text)
        for character_index_text, release_ms in values.items():
            key = (line_index, int(character_index_text))
            if key in result:
                raise ValueError(f"duplicate visual release override: {key}")
            result[key] = int(release_ms)
    return result


def ensure_spectrum_targets_are_new(
    *,
    output_path: Path,
    ass_path: Path,
    report_path: Path | None,
    ass_only: bool,
) -> None:
    targets = [ass_path]
    if not ass_only:
        targets.append(output_path)
    if report_path is not None:
        targets.append(report_path)
    existing = [str(path) for path in targets if path.exists()]
    if existing:
        raise FileExistsError(
            "spectrum preview refuses to overwrite existing outputs: "
            + ", ".join(existing)
        )


def probe_audio_duration_seconds(audio_path: Path) -> float:
    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    completed = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(audio_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    diagnostic = completed.stdout + "\n" + completed.stderr
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", diagnostic)
    if match is None:
        raise RuntimeError(f"could not determine audio duration: {audio_path}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sug", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--composition", type=Path, required=True)
    parser.add_argument("--vinyl", type=Path)
    parser.add_argument(
        "--fonts-dir",
        type=Path,
        default=SHARED_FONT_DIR,
        help="HarmonyOS Sans SC directory (defaults to the project shared font directory)",
    )
    parser.add_argument(
        "--font-file",
        type=Path,
        default=SHARED_FONT_FILE,
        help="HarmonyOS Sans SC regular face used for geometry and glyph checks",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--lossless-output",
        type=Path,
        metavar="MKV",
        help="optional MKV output with FLAC audio from the original lossless source",
    )
    parser.add_argument("--ass-output", type=Path)
    parser.add_argument("--report-output", type=Path)
    parser.add_argument("--ass-only", action="store_true")
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument(
        "--video-encoder",
        choices=(
            "libx264",
            "h264_nvenc",
            "av1_nvenc",
            "libaom-av1",
            "hevc_nvenc_444",
        ),
        default="libx264",
        help=(
            "video encoder; hevc_nvenc_444 emits full-range YUV 4:4:4 HEVC, "
            "while libaom-av1 emits true RGB gbrp AV1"
        ),
    )
    parser.add_argument("--offset-ms", type=int, default=0)
    parser.add_argument(
        "--av1-cq",
        type=int,
        default=DEFAULT_AV1_CQ,
        choices=range(0, 64),
        metavar="0..63",
        help=(
            "constant-quality value for av1_nvenc "
            f"(default: {DEFAULT_AV1_CQ}; preset fixed to {DEFAULT_AV1_PRESET})"
        ),
    )
    parser.add_argument(
        "--hevc-cq",
        type=int,
        default=30,
        choices=range(0, 52),
        metavar="0..51",
        help="constant-quality value for hevc_nvenc_444 (default: 30)",
    )
    parser.add_argument(
        "--layout",
        choices=sorted(SUBTITLE_LAYOUTS),
        default="standard",
    )
    parser.add_argument(
        "--release",
        action="append",
        default=[],
        metavar="LINE_INDEX=MILLISECONDS",
    )
    parser.add_argument("--timing-overrides", type=Path)
    parser.add_argument("--song-id")
    parser.add_argument(
        "--visual-style",
        choices=("vinyl", "spectrum"),
        default="vinyl",
        help="mutually exclusive right-side visual effect",
    )
    parser.add_argument(
        "--vinyl-motion",
        choices=("static", "rotate"),
        default="rotate",
        help="vinyl animation mode (default: rotate for CLI compatibility)",
    )
    parser.add_argument(
        "--pronunciation-validation",
        choices=PRONUNCIATION_VALIDATION_MODES,
        default="optional",
        help="pronunciation sidecar policy; structure is always validated",
    )
    parser.add_argument(
        "--spectrum-color",
        help="RGB hex color for the spectrum; defaults to the project singer color",
    )
    parser.add_argument(
        "--progress-color",
        help="RGB hex secondary color for the spectrum progress track",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    paths = [
        args.sug,
        args.audio,
        args.composition,
        args.fonts_dir,
        args.font_file,
    ]
    if args.visual_style == "vinyl":
        if args.vinyl is None:
            raise ValueError("--vinyl is required when --visual-style=vinyl")
        paths.append(args.vinyl)
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"missing review inputs: {missing}")
    output = args.output.resolve()
    ass_path = (
        args.ass_output.resolve()
        if args.ass_output is not None
        else output.with_suffix(".ass")
    )
    report_path = (
        args.report_output.resolve() if args.report_output is not None else None
    )
    lossless_output = (
        args.lossless_output.resolve()
        if args.lossless_output is not None
        else None
    )
    lossless_audio_codec = None
    if not args.ass_only and lossless_output is not None:
        ensure_lossless_output_is_new(lossless_output)
        if lossless_output == output:
            raise ValueError("lossless output must be different from MP4 output")
        lossless_audio_codec = probe_lossless_audio_codec(args.audio.resolve())
    if args.visual_style == "spectrum":
        ensure_spectrum_targets_are_new(
            output_path=output,
            ass_path=ass_path,
            report_path=report_path,
            ass_only=args.ass_only,
        )
    sug_path = args.sug.resolve()
    project = SugProjectParser.load(str(sug_path))
    ruby_sidecar_path = sug_path.with_suffix(".ruby-review.json")
    ruby_sidecar = (
        load_review_sidecar(ruby_sidecar_path)
        if ruby_sidecar_path.exists()
        else None
    )
    releases = parse_release_overrides(args.release)
    if (args.timing_overrides is None) != (args.song_id is None):
        raise ValueError("--timing-overrides and --song-id must be provided together")
    visual_releases = (
        load_visual_release_overrides(
            args.timing_overrides.resolve(),
            song_id=str(args.song_id),
        )
        if args.timing_overrides is not None
        else {}
    )
    layout = SUBTITLE_LAYOUTS[args.layout]
    ass_report = build_review_ass(
        project,
        ass_path,
        font_file=args.font_file.resolve(),
        release_overrides=releases,
        visual_release_overrides=visual_releases,
        layout=layout,
        offset_ms=args.offset_ms,
        ruby_sidecar=ruby_sidecar,
        pronunciation_validation=args.pronunciation_validation,
    )
    if args.ass_only:
        payload = {"status": "ass-ready", "ass": ass_report}
        if args.report_output is not None:
            args.report_output.resolve().parent.mkdir(parents=True, exist_ok=True)
            args.report_output.resolve().write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(
            json.dumps(
                {
                    "status": "ass-ready",
                    "ass": str(ass_path),
                    "layout": layout.name,
                    "line_count": len(ass_report["lines"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    lossless_render_options = (
        {
            "lossless_output": lossless_output,
            "lossless_audio_codec": lossless_audio_codec,
        }
        if lossless_output is not None
        else {}
    )
    video_report = render_review_clip(
        ass_path=ass_path,
        audio_path=args.audio.resolve(),
        composition_path=args.composition.resolve(),
        vinyl_path=args.vinyl.resolve() if args.vinyl is not None else None,
        fonts_dir=args.fonts_dir.resolve(),
        output_path=output,
        start_seconds=args.start,
        duration_seconds=args.duration,
        layout=layout,
        video_encoder=args.video_encoder,
        av1_cq=args.av1_cq,
        hevc_cq=args.hevc_cq,
        visual_style=args.visual_style,
        vinyl_motion=args.vinyl_motion,
        spectrum_color=args.spectrum_color or _project_highlight_color(project),
        progress_color=args.progress_color,
        program_duration_seconds=(
            probe_audio_duration_seconds(args.audio.resolve())
            if args.visual_style == "spectrum"
            else None
        ),
        **lossless_render_options,
    )
    payload = {"status": "ok", "ass": ass_report, "video": video_report}
    if args.report_output is not None:
        args.report_output.resolve().parent.mkdir(parents=True, exist_ok=True)
        args.report_output.resolve().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": "ok",
                "ass": str(ass_path),
                "layout": layout.name,
                "line_count": len(ass_report["lines"]),
                "video": video_report,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
