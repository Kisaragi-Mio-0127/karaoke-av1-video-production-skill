#!/usr/bin/env python3
"""Render short review clips with contextual furigana and smooth karaoke."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from types import SimpleNamespace
from typing import Any

import imageio_ffmpeg
from PIL import ImageFont
from pykakasi import kakasi

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.karaoke_language import (  # noqa: E402
    DEFAULT_LANGUAGE,
    language_identity,
    normalize_language,
    uses_ruby,
)
from scripts.karaoke_timing import ms_to_ass_time, verify_font  # noqa: E402
from scripts.render_vinyl_karaoke import escape_filter_path  # noqa: E402
from strange_uta_game.backend.domain import Character, Sentence  # noqa: E402
from strange_uta_game.backend.infrastructure.persistence.sug_io import (  # noqa: E402
    SugProjectParser,
)

FONT_FAMILY = "HarmonyOS Sans SC"
CANVAS_WIDTH = 1920
MAIN_FONT_SIZE = 52
MIN_MAIN_FONT_SIZE = 38
RUBY_FONT_SIZE = 24
MAIN_ADVANCE_SCALE = 0.78
MAIN_OUTLINE_PX = 4
RUBY_OUTLINE_PX = 2
MAIN_GLOW_BLUR = 8
RUBY_GLOW_BLUR = 5
WIDE_SEMANTIC_GAP_EM = 0.14
WIDE_RUBY_TO_MAIN_ANCHOR_GAP_PX = 35
ENGLISH_WIDE_MAIN_FONT_SIZE = 96
ENGLISH_WIDE_MIN_MAIN_FONT_SIZE = 54
ENGLISH_WIDE_LETTER_SPACING_EM = 0.0
# Pillow's 96 px advances are wider than libass's visible HarmonyOS Sans SC
# word runs by roughly 1 / 0.735.  English words are emitted as intact ASS
# runs, so this scale changes only their block placement, never letter spacing.
ENGLISH_WIDE_RENDER_ADVANCE_SCALE = 0.735
# HarmonyOS Sans SC's natural ASCII space is about 0.27 em at this size,
# which reads too loose once every word is rendered as an outlined ASS run.
# Keep the font's kerning inside words, but use a calibrated total word gap.
ENGLISH_WIDE_WORD_GAP_EM = 0.25
ENGLISH_WIDE_MIN_SPLIT_WORDS = 3
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
STANDARD_RIGHT_START_X = 860
STANDARD_RIGHT_SAFE_EDGE_X = 1890
STANDARD_RIGHT_SAFE_MARGIN_PX = CANVAS_WIDTH - STANDARD_RIGHT_SAFE_EDGE_X
STANDARD_RIGHT_AVAILABLE_WIDTH = (
    CANVAS_WIDTH - STANDARD_RIGHT_START_X - STANDARD_RIGHT_SAFE_MARGIN_PX
)
# Backwards-compatible name for the default (standard) fit width.  It is
# derived from the two standard lane anchors rather than the former 810px
# estimate.
SLOT_WIDTH = STANDARD_RIGHT_AVAILABLE_WIDTH


def _load_external_json(name: str) -> object:
    """Load optional inline JSON or a UTF-8 JSON file named by an env var."""

    raw_value = os.environ.get(name, "").strip()
    if not raw_value:
        return {}
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        try:
            return json.loads(Path(raw_value).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                f"{name} must contain JSON or name a UTF-8 JSON file"
            ) from error


def _load_ruby_group_overrides() -> dict[str, Any]:
    """Load reviewed ruby rules without embedding song-specific mappings."""

    document = _load_external_json("KARAOKE_RUBY_GROUP_OVERRIDES")
    if not document:
        return {
            "reading_overrides": {},
            "span_splits": {},
            "multi_kanji_splits": {},
            "linked_spans": frozenset(),
        }
    if not isinstance(document, dict):
        raise ValueError("KARAOKE_RUBY_GROUP_OVERRIDES must be a JSON object")

    reading_overrides = document.get("reading_overrides", {})
    if not isinstance(reading_overrides, dict) or not all(
        isinstance(surface, str) and isinstance(reading, str)
        for surface, reading in reading_overrides.items()
    ):
        raise ValueError("ruby reading_overrides must map strings to strings")

    span_splits: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {}
    raw_span_splits = document.get("span_splits", {})
    if not isinstance(raw_span_splits, dict):
        raise ValueError("ruby span_splits must be an object")
    for surface, readings in raw_span_splits.items():
        if not isinstance(surface, str) or not isinstance(readings, dict):
            raise ValueError("ruby span_splits must be nested string objects")
        for reading, parts in readings.items():
            if not isinstance(reading, str) or not isinstance(parts, list):
                raise ValueError("ruby span split readings must be string lists")
            converted_parts: list[tuple[str, str]] = []
            for part in parts:
                if (
                    not isinstance(part, list)
                    or len(part) != 2
                    or not all(isinstance(value, str) for value in part)
                ):
                    raise ValueError(
                        "ruby span split parts must be [surface, reading] pairs"
                    )
                converted_parts.append((part[0], part[1]))
            if not converted_parts:
                raise ValueError("ruby span split parts must not be empty")
            span_splits[(surface, reading)] = tuple(converted_parts)

    multi_kanji_splits: dict[tuple[str, str], tuple[str, ...]] = {}
    raw_multi_kanji_splits = document.get("multi_kanji_splits", {})
    if not isinstance(raw_multi_kanji_splits, dict):
        raise ValueError("ruby multi_kanji_splits must be an object")
    for surface, readings in raw_multi_kanji_splits.items():
        if not isinstance(surface, str) or not isinstance(readings, dict):
            raise ValueError("ruby multi_kanji_splits must be nested string objects")
        for reading, parts in readings.items():
            if not isinstance(reading, str) or not isinstance(parts, list):
                raise ValueError("ruby multi-kanji split readings must be string lists")
            if not all(isinstance(part, str) for part in parts):
                raise ValueError("ruby multi-kanji split parts must be strings")
            multi_kanji_splits[(surface, reading)] = tuple(parts)

    linked_spans = document.get("linked_spans", [])
    if not isinstance(linked_spans, list) or not all(
        isinstance(surface, str) for surface in linked_spans
    ):
        raise ValueError("ruby linked_spans must be a string list")

    return {
        "reading_overrides": dict(reading_overrides),
        "span_splits": span_splits,
        "multi_kanji_splits": multi_kanji_splits,
        "linked_spans": frozenset(linked_spans),
    }


RUBY_GROUP_OVERRIDES = _load_ruby_group_overrides()
READING_OVERRIDES = RUBY_GROUP_OVERRIDES["reading_overrides"]
REVIEWED_RUBY_SPAN_SPLITS = RUBY_GROUP_OVERRIDES["span_splits"]


@dataclass(frozen=True)
class Lane:
    """One of two staggered slots inside the existing lower-right panel."""

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
    # ``fit_advance_scale`` and ``fit_outline_px`` are the exact metrics used
    # by the fit gate.  Japanese keeps the historical wide contract; English
    # wide selects a separate natural-advance layout below.
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
        Lane(
            x=STANDARD_RIGHT_SAFE_EDGE_X,
            main_y=960,
            ruby_y=932,
            alignment=9,
        ),
    ),
    advance_scale=MAIN_ADVANCE_SCALE,
    slot_width=SLOT_WIDTH,
    vinyl_x=1030,
    vinyl_y=110,
    vinyl_size=860,
    fit_advance_scale=MAIN_ADVANCE_SCALE,
    fit_outline_px=MAIN_OUTLINE_PX,
)
WIDE_LAYOUT = SubtitleLayout(
    name="wide-bottom",
    lanes=(
        Lane(
            x=32,
            main_y=660,
            ruby_y=660 - WIDE_RUBY_TO_MAIN_ANCHOR_GAP_PX,
            alignment=7,
        ),
        Lane(
            x=1888,
            main_y=870,
            ruby_y=870 - WIDE_RUBY_TO_MAIN_ANCHOR_GAP_PX,
            alignment=9,
        ),
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
    semantic_gap_em=WIDE_SEMANTIC_GAP_EM,
    main_outline_px=6,
    ruby_outline_px=3,
    main_glow_blur=12,
    ruby_glow_blur=8,
)
CHINESE_WIDE_LAYOUT = replace(
    WIDE_LAYOUT,
    name="wide-bottom-zh",
    max_phrase_chars=None,
    enforce_main_font_size=False,
)
ENGLISH_WIDE_LAYOUT = replace(
    WIDE_LAYOUT,
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
SUBTITLE_LAYOUTS = {
    "standard": STANDARD_LAYOUT,
    "wide": WIDE_LAYOUT,
    "wide-zh": CHINESE_WIDE_LAYOUT,
    "wide-en": ENGLISH_WIDE_LAYOUT,
}
LANES = STANDARD_LAYOUT.lanes


@dataclass(frozen=True)
class RubyToken:
    text: str
    reading: str
    start: int
    end: int


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


def _contains_kanji(text: str) -> bool:
    return any(
        0x3400 <= ord(char) <= 0x4DBF
        or 0x4E00 <= ord(char) <= 0x9FFF
        or 0xF900 <= ord(char) <= 0xFAFF
        or char == "々"
        for char in text
    )


def _is_kanji(char: str) -> bool:
    return _contains_kanji(char)


def _kana_to_hiragana(text: str) -> str:
    return "".join(
        chr(ord(char) - 0x60) if 0x30A1 <= ord(char) <= 0x30F6 else char
        for char in text
    )


def _kanji_ruby_spans(
    original: str,
    reading: str,
    *,
    start: int,
) -> list[RubyToken]:
    """Remove visible okurigana and return ruby only for the matching kanji spans."""

    runs: list[tuple[bool, int, int, str]] = []
    run_start = 0
    for index in range(1, len(original) + 1):
        if index == len(original) or _is_kanji(original[index]) != _is_kanji(
            original[run_start]
        ):
            runs.append(
                (
                    _is_kanji(original[run_start]),
                    run_start,
                    index,
                    original[run_start:index],
                )
            )
            run_start = index

    result: list[RubyToken] = []
    reading_cursor = 0
    for run_index, (is_kanji, local_start, local_end, run_text) in enumerate(runs):
        if not is_kanji:
            literal = _kana_to_hiragana(run_text)
            if reading.startswith(literal, reading_cursor):
                reading_cursor += len(literal)
                continue
            found = reading.find(literal, reading_cursor)
            if found < 0:
                return [RubyToken(original, reading, start, start + len(original))]
            reading_cursor = found + len(literal)
            continue

        next_literal = ""
        if run_index + 1 < len(runs):
            next_literal = _kana_to_hiragana(runs[run_index + 1][3])
        reading_end = (
            reading.find(next_literal, reading_cursor) if next_literal else len(reading)
        )
        if reading_end < reading_cursor:
            return [RubyToken(original, reading, start, start + len(original))]
        ruby = reading[reading_cursor:reading_end]
        if ruby:
            reviewed_split = REVIEWED_RUBY_SPAN_SPLITS.get((run_text, ruby))
            if reviewed_split is None:
                result.append(
                    RubyToken(
                        text=run_text,
                        reading=ruby,
                        start=start + local_start,
                        end=start + local_end,
                    )
                )
            else:
                split_cursor = start + local_start
                for split_text, split_reading in reviewed_split:
                    split_end = split_cursor + len(split_text)
                    result.append(
                        RubyToken(
                            text=split_text,
                            reading=split_reading,
                            start=split_cursor,
                            end=split_end,
                        )
                    )
                    split_cursor = split_end
                if split_cursor != start + local_end:
                    raise ValueError(
                        f"reviewed ruby split width mismatch for {run_text!r}"
                    )
        reading_cursor = reading_end
    return result


def contextual_ruby_tokens(
    text: str,
    language: str = DEFAULT_LANGUAGE,
) -> list[RubyToken]:
    """Return context-aware ruby attached only to the kanji it annotates."""

    if not uses_ruby(language):
        return []
    converted = kakasi().convert(text)
    result: list[RubyToken] = []
    cursor = 0
    for item in converted:
        original = str(item.get("orig") or "")
        reading = str(item.get("hira") or original)
        reading = READING_OVERRIDES.get(original, reading)
        start = cursor
        end = start + len(original)
        cursor = end
        if (
            original in READING_OVERRIDES
            and not _contains_kanji(original)
            and reading != original
        ):
            result.append(
                RubyToken(
                    original,
                    reading,
                    start,
                    end,
                )
            )
        elif _contains_kanji(original) and reading and reading != original:
            result.extend(_kanji_ruby_spans(original, reading, start=start))
    return result


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


_ENGLISH_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['\u2018\u2019][A-Za-z0-9]+)*")


def _english_letter_spacing_after_indices(text: str) -> frozenset[int]:
    """Return character positions that receive positive word-internal spacing."""

    positions: set[int] = set()
    for match in _ENGLISH_WORD_RE.finditer(text):
        positions.update(range(match.start(), match.end() - 1))
    return frozenset(positions)


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
    letter_spacing_count = len(_english_letter_spacing_after_indices(text))
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
    letter_spacing_after_indices = _english_letter_spacing_after_indices(text)
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


def expand_english_word_tokens_for_render(
    sentence: Sentence,
) -> tuple[Sentence, tuple[int, ...]]:
    """Expand editable English word tokens only in renderer memory.

    The SUG remains one checkpoint per word for human timing work.  This
    renderer-only view distributes strictly ordered visual onsets across the
    visible codepoints of each timed word and records the owning source-token
    index for every expanded character.
    """

    if all(len(character.char) == 1 for character in sentence.characters):
        return sentence, tuple(range(len(sentence.characters)))

    expanded: list[Character] = []
    source_token_indices: list[int] = []
    for token_index, token in enumerate(sentence.characters):
        token_text = token.char
        if len(token_text) == 1:
            expanded.append(token)
            source_token_indices.append(token_index)
            continue

        onset = int(token.timestamps[0]) if token.timestamps else None
        next_onset = next(
            (
                int(candidate.timestamps[0])
                for candidate in sentence.characters[token_index + 1 :]
                if candidate.timestamps
            ),
            None,
        )
        release = (
            int(token.sentence_end_ts)
            if token.is_sentence_end and token.sentence_end_ts is not None
            else next_onset
        )
        if onset is not None:
            release = max(
                onset + 10 * len(token_text),
                int(release) if release is not None else onset + 120 * len(token_text),
            )

        for character_index, visible_character in enumerate(token_text):
            timed = onset is not None and not visible_character.isspace()
            timestamp = (
                onset
                + round(
                    (int(release) - onset)
                    * character_index
                    / max(1, len(token_text))
                )
                if timed
                else None
            )
            is_last = character_index == len(token_text) - 1
            character = Character(
                char=visible_character,
                check_count=1 if timed else 0,
                timestamps=[] if timestamp is None else [timestamp],
                sentence_end_ts=(
                    token.sentence_end_ts
                    if is_last and token.is_sentence_end
                    else None
                ),
                linked_to_next=False,
                is_line_end=is_last and token.is_line_end,
                is_sentence_end=is_last and token.is_sentence_end,
                is_rest=token.is_rest,
                singer_id=token.singer_id,
                needs_guide=token.needs_guide,
                is_guide=token.is_guide,
                force_singer_tag=token.force_singer_tag and character_index == 0,
            )
            expanded.append(character)
            source_token_indices.append(token_index)

    rendered = Sentence(
        id=sentence.id,
        singer_id=sentence.singer_id,
        characters=expanded,
    )
    if rendered.text != sentence.text:
        raise ValueError("English renderer expansion changed the source text")
    return rendered, tuple(source_token_indices)


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


def english_word_karaoke_events(
    sentence: Sentence,
    *,
    event_start_ms: int,
    event_end_ms: int,
    release_ms: int,
    lane: Lane,
    font_size: int,
    geometry: TextGeometry,
    outline_px: int,
    glow_blur: int,
    offset_ms: int = 0,
    onset_overrides: dict[int, int] | None = None,
    release_overrides: dict[int, int] | None = None,
) -> list[str]:
    """Render each English word as one naturally kerned karaoke text run.

    Keeping all letters of a word in one ASS event lets the selected font own
    its normal kerning.  Per-letter ``\\kf`` tags still drive the colour sweep,
    while the fill, outline, and glow all share the exact same word geometry.
    """

    onsets = _character_onsets(
        sentence,
        offset_ms=offset_ms,
        onset_overrides=onset_overrides,
        release_ms=release_ms,
    )
    if not onsets:
        return []
    releases = _character_releases(
        onsets,
        release_ms=release_ms,
        offset_ms=offset_ms,
        release_overrides=release_overrides,
    )

    result: list[str] = []
    for match in re.finditer(r"\S+", sentence.text):
        start, end = match.span()
        if end <= start:
            continue
        x = (geometry.glyph_starts[start] + geometry.glyph_ends[end - 1]) / 2.0
        common = (
            f"{{\\an8\\pos({int(round(x))},{lane.main_y})"
            f"\\fs{font_size}\\bord{outline_px}\\fad(80,120)"
        )
        lead_in_cs = max(0, onsets[start] - event_start_ms) // 10
        timed_text = [f"\\k{lead_in_cs}}}"]
        for index in range(start, end):
            duration_cs = max(
                1,
                int(round((releases[index] - onsets[index]) / 10)),
            )
            timed_text.append(
                f"{{\\kf{duration_cs}}}"
                f"{_escape_ass_text(sentence.characters[index].char)}"
            )
        karaoke_run = "".join(timed_text)
        glow_override = common + f"\\blur{glow_blur}{karaoke_run}"
        main_override = common + karaoke_run
        result.append(
            f"Dialogue: 1,"
            f"{ms_to_ass_time(event_start_ms)},{ms_to_ass_time(event_end_ms)},"
            f"Glow,WordKaraoke,0,0,0,,{glow_override}"
        )
        result.append(
            f"Dialogue: 2,"
            f"{ms_to_ass_time(event_start_ms)},{ms_to_ass_time(event_end_ms)},"
            f"Main,WordKaraoke,0,0,0,,{main_override}"
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
    tokens = (
        contextual_ruby_tokens(sentence.text, language=language)
        if tokens is None
        else tokens
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


def _normalize_display_text(text: str) -> str:
    """Return the source-line text used for display-phrase override lookup."""

    return "".join(character for character in text if not character.isspace())


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
    if normalize_language(language, default=DEFAULT_LANGUAGE) == "en":
        last_visible_index: int | None = None
        for display_index, character in enumerate(display_sentence.characters):
            if character.char.isspace():
                if last_visible_index is not None:
                    result.add(last_visible_index)
            else:
                last_visible_index = display_index
    return frozenset(result)


_BAD_DISPLAY_BOUNDARY_START_CHARS = frozenset(
    "・ーぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ"
)
_BAD_DISPLAY_BOUNDARY_END_CHARS = frozenset("・ーっッ")
def _load_display_phrase_overrides() -> dict[str, tuple[str, ...]]:
    """Load source-text-to-phrase-list overrides from optional external JSON."""

    document = _load_external_json("KARAOKE_DISPLAY_OVERRIDES")
    if not document:
        return {}
    if (
        isinstance(document, dict)
        and set(document) == {"overrides"}
        and isinstance(document["overrides"], dict)
    ):
        document = document["overrides"]
    if not isinstance(document, dict):
        raise ValueError("KARAOKE_DISPLAY_OVERRIDES must be a JSON object")
    result: dict[str, tuple[str, ...]] = {}
    for source_text, phrases in document.items():
        if (
            not isinstance(source_text, str)
            or not isinstance(phrases, list)
            or not all(isinstance(phrase, str) for phrase in phrases)
        ):
            raise ValueError(
                "display overrides must map source strings to string lists"
            )
        result[source_text] = tuple(phrases)
    return result


_DISPLAY_PHRASE_OVERRIDES = _load_display_phrase_overrides()


def _validate_display_phrase_overrides() -> None:
    """Validate external display overrides before any source line can use them."""

    for source_text, phrases in _DISPLAY_PHRASE_OVERRIDES.items():
        normalized_source_text = _normalize_display_text(source_text)
        if source_text != normalized_source_text:
            raise ValueError(
                "display override keys must be normalized source text: "
                f"{source_text!r}"
            )
        if not phrases or "".join(phrases) != source_text:
            raise ValueError(
                "display override phrases must concatenate to their key: "
                f"{source_text!r}"
            )
        for phrase in phrases:
            if not MIN_DISPLAY_PHRASE_CHARS <= len(phrase) <= MAX_DISPLAY_PHRASE_CHARS:
                raise ValueError(
                    "display override phrase length is outside the supported range: "
                    f"{phrase!r}"
                )
        for left, right in zip(phrases, phrases[1:]):
            if (
                left[-1] in _BAD_DISPLAY_BOUNDARY_END_CHARS
                or right[0] in _BAD_DISPLAY_BOUNDARY_START_CHARS
            ):
                raise ValueError(
                    "display override has a bad Japanese phrase boundary: "
                    f"{left!r} | {right!r}"
                )


_validate_display_phrase_overrides()


def _split_sentence_by_display_override(
    sentence: Sentence,
    *,
    language: str = DEFAULT_LANGUAGE,
) -> list[list] | None:
    """Return original character slices for a full source-line override."""

    normalized_source_text = _normalize_display_text(sentence.text)
    override = _DISPLAY_PHRASE_OVERRIDES.get(normalized_source_text)
    if override is None:
        return None
    if "".join(override) != normalized_source_text:
        raise ValueError(
            "display override phrases must concatenate to their source key: "
            f"{normalized_source_text!r}"
        )

    visible_characters = [
        character
        for character in sentence.characters
        if not character.char.isspace()
    ]
    visible_text = "".join(character.char for character in visible_characters)
    if visible_text != normalized_source_text:
        raise ValueError(
            "display override source text does not match its character sequence: "
            f"{normalized_source_text!r} != {visible_text!r}"
        )

    result: list[list] = []
    cursor = 0
    for phrase in override:
        end = cursor + len(phrase)
        result.append(visible_characters[cursor:end])
        cursor = end
    if cursor != len(visible_characters):
        raise ValueError(
            "display override slices do not cover the complete source line: "
            f"{normalized_source_text!r}"
        )
    if normalize_language(language) == "en":
        visible_positions = [
            index
            for index, character in enumerate(sentence.characters)
            if not character.char.isspace()
        ]
        cursor = 0
        for phrase in override[:-1]:
            cursor += len(phrase)
            left_index = visible_positions[cursor - 1]
            right_index = visible_positions[cursor]
            between = sentence.characters[left_index + 1 : right_index]
            if not any(character.char.isspace() for character in between):
                raise ValueError(
                    "English display override splits inside a word: "
                    f"{normalized_source_text!r} at {cursor}"
                )
    return result


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


def _english_word_spans(sentence: Sentence) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for index, character in enumerate(sentence.characters):
        if character.char.isspace():
            if start is not None:
                spans.append((start, index))
                start = None
        elif start is None:
            start = index
    if start is not None:
        spans.append((start, len(sentence.characters)))
    return spans


def _english_word_slice(
    sentence: Sentence,
    spans: list[tuple[int, int]],
    start_word: int,
    end_word: int,
) -> Sentence:
    start = spans[start_word][0]
    end = spans[end_word - 1][1]
    return Sentence(
        singer_id=sentence.singer_id,
        characters=list(sentence.characters[start:end]),
    )


def _english_phrase_width_at_target(
    sentence: Sentence,
    *,
    font_file: Path,
    layout: SubtitleLayout,
) -> float:
    semantic_gaps = _semantic_gap_after_indices(
        sentence,
        sentence,
        language="en",
    )
    return _measured_text_span(
        font_file,
        sentence.text,
        font_size=layout.main_font_size,
        advance_scale=layout.fit_advance_scale,
        semantic_gap_count=len(semantic_gaps),
        semantic_gap_em=layout.semantic_gap_em,
        letter_spacing_em=layout.letter_spacing_em,
        word_gap_em=layout.word_gap_em,
    ) + 2 * layout.fit_outline_px


def _split_english_sentence_for_wide_fit(
    sentence: Sentence,
    *,
    font_file: Path,
    layout: SubtitleLayout,
) -> list[Sentence]:
    """Split an overflowing English line at balanced whole-word boundaries."""

    if _english_phrase_width_at_target(
        sentence,
        font_file=font_file,
        layout=layout,
    ) <= layout.slot_width:
        return [sentence]

    spans = _english_word_spans(sentence)
    if len(spans) < 2 * ENGLISH_WIDE_MIN_SPLIT_WORDS:
        return [sentence]

    candidates: list[tuple[float, int, Sentence, Sentence]] = []
    for split_at in range(
        ENGLISH_WIDE_MIN_SPLIT_WORDS,
        len(spans) - ENGLISH_WIDE_MIN_SPLIT_WORDS + 1,
    ):
        left = _english_word_slice(sentence, spans, 0, split_at)
        right = _english_word_slice(sentence, spans, split_at, len(spans))
        left_width = _english_phrase_width_at_target(
            left,
            font_file=font_file,
            layout=layout,
        )
        right_width = _english_phrase_width_at_target(
            right,
            font_file=font_file,
            layout=layout,
        )
        if left_width > layout.slot_width or right_width > layout.slot_width:
            continue
        punctuation_bonus = (
            layout.slot_width * 0.08
            if left.text.rstrip().endswith((",", ";", ":", ".", "!", "?"))
            else 0.0
        )
        candidates.append(
            (
                abs(left_width - right_width) - punctuation_bonus,
                split_at,
                left,
                right,
            )
        )
    if candidates:
        _, _, left, right = min(candidates, key=lambda item: (item[0], item[1]))
        return [left, right]
    return [sentence]


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
    if max_chars is None and language == "ja":
        return [sentence]
    override_runs = _split_sentence_by_display_override(
        sentence,
        language=language,
    )
    if override_runs is not None:
        return [
            Sentence(
                singer_id=sentence.singer_id,
                characters=list(run),
            )
            for run in override_runs
        ]
    if language == "en":
        if (
            font_file is not None
            and layout is not None
            and layout.name == ENGLISH_WIDE_LAYOUT.name
        ):
            return _split_english_sentence_for_wide_fit(
                sentence,
                font_file=font_file,
                layout=layout,
            )
        return [sentence]
    if language == "zh":
        return [sentence]
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
    """Select language-specific wide typography without changing Japanese."""

    language = normalize_language(language)
    if not layout.name.startswith("wide"):
        return layout
    if language == "en":
        return ENGLISH_WIDE_LAYOUT
    if language == "zh":
        return CHINESE_WIDE_LAYOUT
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
        # one editable source line.  Some short source lines (notably the
        # Some short intro lines produce only one display phrase, so the second
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
) -> dict:
    visual_release_overrides = visual_release_overrides or {}
    language = _project_language(project)
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
        if language == "en":
            sentence, source_token_indices = expand_english_word_tokens_for_render(
                source_sentence
            )
        else:
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
        if language == "en":
            events.extend(
                english_word_karaoke_events(
                    sentence,
                    event_start_ms=event_start_ms,
                    event_end_ms=event_end_ms,
                    release_ms=release_ms,
                    lane=lane,
                    font_size=font_size,
                    geometry=geometry,
                    outline_px=layout.main_outline_px,
                    glow_blur=layout.main_glow_blur,
                    offset_ms=offset_ms,
                    onset_overrides=item["visual_onset_overrides"],
                    release_overrides=item["visual_release_overrides"],
                )
            )
        else:
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
                "word_gap_px": round(
                    font_size * layout.word_gap_em
                    if layout.word_gap_em is not None
                    else ImageFont.truetype(str(font_file), font_size).getlength(" ")
                    + font_size * layout.semantic_gap_em,
                    2,
                )
                if language == "en"
                else 0.0,
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
                    {"text": token.text, "reading": token.reading}
                    for token in contextual_ruby_tokens(
                        sentence.text,
                        language=language,
                    )
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
        marker_text = {
            "ja": "終わり",
            "zh": "结束",
            "en": "The End",
        }[language]
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
        marker_ruby = "お" if language == "ja" else None
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
                    tokens=[RubyToken(text="終", reading=marker_ruby, start=0, end=1)],
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
            "scope": (
                "english-word-internal"
                if layout.letter_spacing_em > 0
                else "none"
            ),
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
                language == "en"
                and layout.semantic_gap_em == 0.0
                and layout.word_gap_em is None
            ),
            "narrowed_from_natural": (
                language == "en"
                and layout.word_gap_em is not None
                and layout.main_font_size * layout.word_gap_em
                < ImageFont.truetype(
                    str(font_file),
                    layout.main_font_size,
                ).getlength(" ")
            ),
            "strategy": (
                "fixed-em-renderer-geometry"
                if language == "en" and layout.word_gap_em is not None
                else "font-natural-plus-semantic-gap"
            ),
            "word_run_positioning_advance_scale": (
                layout.advance_scale if language == "en" else None
            ),
            "word_internal_spacing_affected": False,
            "native_frame_visible_white_gap_target_px": (
                {"minimum": 18, "maximum": 32}
                if language == "en"
                else None
            ),
            "native_frame_measurement_required": language == "en",
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
    return {
        "ass": str(output_path),
        "language": language,
        "language_identity": identity,
        "ruby_enabled": uses_ruby(language),
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
    av1_cq: int = 44,
    hevc_cq: int = 30,
    visual_style: str = "vinyl",
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
        filter_complex = (
            "[0:v]format=rgba[bg];"
            f"[1:v]scale={layout.vinyl_size}:{layout.vinyl_size}:flags=lanczos,"
            "format=rgba,rotate=2*PI*t/8:ow=iw:oh=ih:"
            "fillcolor=black@0:bilinear=1[vinyl];"
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
            "pad=1040:228:0:0:color=black,"
            "erosion=coordinates=90,erosion=coordinates=90,"
            "erosion=coordinates=90,dilation=coordinates=90,"
            "dilation=coordinates=90,dilation=coordinates=90,"
            "gblur=sigma=0.8:steps=1,"
            "split=5[coremask][specinner][specouter][specwide][specpeak];"
            "[specinner]pad=1168:284:64:0:color=black,"
            "gblur=sigma=4:steps=2,"
            "lut=y='val*2.0'[innermask];"
            "[specouter]pad=1168:284:64:0:color=black,"
            "gblur=sigma=14:steps=2,"
            "lut=y='val*2.4'[outermask];"
            "[specwide]pad=1168:284:64:0:color=black,"
            "gblur=sigma=28:steps=3,"
            "lut=y='val*2.8'[widemask];"
            "[specpeak]pad=1168:284:64:0:color=black,"
            "lagfun=decay=0.975,"
            "gblur=sigma=2.2:steps=2,lut=y='val*0.55'[peakmask];"
            f"color=c=0x{color}:s=1040x228:r=30:d={duration:.3f},"
            "format=rgba,colorchannelmixer=rr=1:rg=0.18:rb=0.18:"
            "gr=0.18:gg=1:gb=0.18:br=0.18:bg=0.18:bb=1[corecolor];"
            f"color=c=0x{color}:s=1168x284:r=30:d={duration:.3f},"
            "format=rgba[innercolor];"
            f"color=c=0x{color}:s=1168x284:r=30:d={duration:.3f},"
            "format=rgba[outercolor];"
            f"color=c=0x{color}:s=1168x284:r=30:d={duration:.3f},"
            "format=rgba[widecolor];"
            f"color=c=0x{color}:s=1168x284:r=30:d={duration:.3f},"
            "format=rgba[peakcolor];"
            "[corecolor][coremask]alphamerge[core];"
            "[innercolor][innermask]alphamerge[innerglow];"
            "[outercolor][outermask]alphamerge[outerglow];"
            "[widecolor][widemask]alphamerge[wideglow];"
            "[peakcolor][peakmask]alphamerge[peakhold];"
            "[bgclip][wideglow]overlay=736:290:format=auto[wide];"
            "[wide][outerglow]overlay=736:290:format=auto[outer];"
            "[outer][peakhold]overlay=736:290:format=auto[held];"
            "[held][innerglow]overlay=736:290:format=auto[inner];"
            "[inner][core]overlay=800:290:format=auto[spectrumbars];"
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
            "p7",
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
        "hevc_cq": hevc_cq if video_encoder == "hevc_nvenc_444" else None,
        "visual_style": visual_style,
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
        "spectrum_bar_bottom_clearance_px": 8 if visual_style == "spectrum" else None,
        "spectrum_glow_bottom_padding_px": 56 if visual_style == "spectrum" else None,
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
    parser.add_argument("--fonts-dir", type=Path, required=True)
    parser.add_argument("--font-file", type=Path, required=True)
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
        default=44,
        choices=range(0, 64),
        metavar="0..63",
        help="constant-quality value for av1_nvenc (default: 44)",
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
    project = SugProjectParser.load(str(args.sug.resolve()))
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
