#!/usr/bin/env python3
"""Run a reproducible dual-audio MMS alignment audit for karaoke timings.

The command audits tracks selected from an explicit manifest. Expensive MMS
imports and model construction stay behind :func:`load_mms_runtime`, so pure
mapping and crop-window helpers remain unit-testable without loading the model.
With no ``--song-id`` the complete explicit manifest is audited, and the
default report location is derived from that manifest.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts import karaoke_timing
    from scripts.karaoke_album import (
        AlbumManifest,
        AlbumTrack,
        load_album_manifest,
        project_relative,
        sha256_file,
    )
    from scripts.karaoke_language import (
        DEFAULT_LANGUAGE,
        contextual_pinyin_for_text,
        english_word_spans,
        is_chinese_character,
        language_identity,
        mms_granularity,
        normalize_language,
        pinyin_for_character,
    )
    from scripts.karaoke_model_paths import resolve_mms_model_path
    from scripts.sug_ruby import iter_sug_ruby_spans
except ImportError:  # pragma: no cover - direct execution fallback
    import karaoke_timing  # type: ignore[no-redef]
    from karaoke_album import (  # type: ignore[no-redef]
        AlbumManifest,
        AlbumTrack,
        load_album_manifest,
        project_relative,
        sha256_file,
    )
    from karaoke_language import (  # type: ignore[no-redef]
        DEFAULT_LANGUAGE,
        contextual_pinyin_for_text,
        english_word_spans,
        is_chinese_character,
        language_identity,
        mms_granularity,
        normalize_language,
        pinyin_for_character,
    )
    from karaoke_model_paths import resolve_mms_model_path  # type: ignore[no-redef]
    from sug_ruby import iter_sug_ruby_spans  # type: ignore[no-redef]


SCHEMA_VERSION = "karaoke-mms-dual-audio-audit/v1"
SCHEMA_VERSION_V2 = "karaoke-mms-dual-audio-audit/v2"
UNIT_OVERRIDES_SCHEMA_VERSION = "karaoke-mms-unit-overrides/v1"
MODEL_NAME = "torchaudio.pipelines.MMS_FA"
DEFAULT_VOCALS_ROOT = ROOT / ".cache" / "msst-vocals"
_MORA_JOINING_SMALL_KANA = karaoke_timing._MORA_JOINING_SMALL_KANA
_DEFAULT_ALLOWED_UNITS = frozenset("abcdefghijklmnopqrstuvwxyz'")
_ASCII_MMS_UNIT_RE = re.compile(r"[a-z]+(?:'[a-z]+)*\Z")
ALIGNMENT_EVIDENCE_CONTRACT = karaoke_timing.ALIGNMENT_EVIDENCE_CONTRACT

Character = Mapping[str, Any]
Unit = Mapping[str, Any]
OverrideKey = tuple[str, int, int]


def _coerce_ms(value: Any) -> int | None:
    """Return a finite non-negative millisecond value, or ``None``."""

    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(round(number))


def _first_timestamp_ms(character: Character) -> int | None:
    timestamps = character.get("timestamps")
    if not isinstance(timestamps, Sequence) or isinstance(timestamps, (str, bytes)):
        return None
    for value in timestamps:
        result = _coerce_ms(value)
        if result is not None:
            return result
    return None


def _timed_characters(
    characters: Sequence[Character],
) -> list[tuple[int, Character, int]]:
    return [
        (index, character, timestamp)
        for index, character in enumerate(characters)
        if (timestamp := _first_timestamp_ms(character)) is not None
    ]


def sentence_release_ms(
    characters: Sequence[Character],
    *,
    fallback_after_last_onset_ms: int = 0,
) -> int:
    """Find a sentence release using every character's release metadata.

    SUG commonly stores ``sentence_end_ts`` on a non-timed punctuation
    character.  Looking only at the last timed character therefore truncates
    the crop.  A valid release on any character wins; when no release exists,
    the last onset is used and ``crop_window_ms`` supplies the trailing pad.
    """

    timed = _timed_characters(characters)
    if not timed:
        raise ValueError("a sentence must contain at least one timed character")

    last_onset = timed[-1][2]
    releases = [
        release
        for character in characters
        if (release := _coerce_ms(character.get("sentence_end_ts"))) is not None
    ]
    if releases:
        return max(last_onset, max(releases))
    return last_onset + max(0, int(fallback_after_last_onset_ms))


def crop_window_ms(
    characters: Sequence[Character],
    audio_duration_ms: int,
    *,
    lead_ms: int = 500,
    tail_ms: int = 1_000,
    fallback_after_last_onset_ms: int = 0,
) -> tuple[int, int]:
    """Return a bounded MMS crop window for one SUG sentence."""

    timed = _timed_characters(characters)
    if not timed:
        raise ValueError("a sentence must contain at least one timed character")

    duration = _coerce_ms(audio_duration_ms)
    if duration is None:
        raise ValueError(f"audio duration is invalid: {audio_duration_ms!r}")

    first_onset = timed[0][2]
    release = sentence_release_ms(
        characters,
        fallback_after_last_onset_ms=fallback_after_last_onset_ms,
    )
    crop_start = max(0, first_onset - max(0, int(lead_ms)))
    crop_end = min(duration, release + max(0, int(tail_ms)))
    if crop_end <= crop_start:
        raise ValueError(f"sentence crop is empty: start={crop_start} end={crop_end}")
    return crop_start, crop_end


@lru_cache(maxsize=1)
def _romanizer() -> Any:
    from pykakasi import kakasi

    return kakasi()


def romanize_mora(
    mora: str,
    *,
    particle: bool = False,
    previous: str = "",
    allowed_units: Iterable[str] | None = None,
) -> str:
    """Convert one mora to the MMS tokenizer's lower-case alphabet."""

    if mora == "っ":
        return "q"
    if mora == "ん":
        return "n"
    if mora == "を":
        return "o"
    if mora == "は" and particle:
        return "wa"
    if mora == "へ" and particle:
        return "e"
    if mora == "ー":
        vowels = [char for char in previous if char in "aeiou"]
        return vowels[-1] if vowels else "u"

    converted = _romanizer().convert(mora)
    value = "".join(str(item.get("hepburn") or "") for item in converted).lower()
    allowed = set(allowed_units or _DEFAULT_ALLOWED_UNITS)
    value = "".join(char for char in value if char in allowed)
    return value or "x"


def allocate_chunk(
    text: str,
    start: int,
    original: str,
    reading: str,
    helper: Any,
    *,
    allowed_units: Iterable[str] | None = None,
    language: str = DEFAULT_LANGUAGE,
) -> list[tuple[str, int]]:
    """Assign converted morae to source character indices."""

    positions = [
        start + offset
        for offset, char in enumerate(original)
        if karaoke_timing.is_timed_character(char)
    ]
    if not positions:
        return []
    moras = [
        mora
        for mora in karaoke_timing.split_moras(reading)
        if any(karaoke_timing.is_timed_character(character) for character in mora)
    ]
    if not moras:
        return []

    weights = karaoke_timing.contextual_mora_weights(text, helper, language)
    char_weights = [max(0.0, weights.get(index, 1.0)) for index in positions]
    if sum(char_weights) <= 0:
        char_weights = [1.0] * len(positions)

    cumulative: list[float] = []
    running = 0.0
    total = sum(char_weights)
    for weight in char_weights:
        running += weight
        cumulative.append(running / total)

    assignments: list[tuple[str, int]] = []
    previous_roman = ""
    final_particle = original[-1:] in {"は", "へ"} and (
        len(original) == 1
        or any(not ("ぁ" <= character <= "ヺ") for character in original[:-1])
    )
    for mora_index, mora in enumerate(moras):
        midpoint = (mora_index + 0.5) / len(moras)
        character_slot = next(
            (slot for slot, boundary in enumerate(cumulative) if midpoint <= boundary),
            len(positions) - 1,
        )
        particle_mora = final_particle and mora_index == len(moras) - 1
        roman = romanize_mora(
            mora,
            particle=particle_mora,
            previous=previous_roman,
            allowed_units=allowed_units,
        )
        assignments.append((roman, positions[character_slot]))
        previous_roman = roman
    return assignments


def _filter_mms_unit(
    value: str,
    allowed_units: Iterable[str] | None = None,
) -> str:
    """Normalize a pinyin/word token to the MMS alphabet."""

    allowed = set(allowed_units or _DEFAULT_ALLOWED_UNITS)
    unit = "".join(character for character in str(value).lower() if character in allowed)
    return unit or "x"


def _validated_ascii_mms_unit(
    value: Any,
    *,
    allowed_units: Iterable[str] | None = None,
    label: str,
) -> str:
    """Validate one explicit MMS lexical unit without silently filtering it."""

    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    unit = value.lower()
    if value != unit or _ASCII_MMS_UNIT_RE.fullmatch(unit) is None:
        raise ValueError(
            f"{label} must use the lowercase ASCII MMS alphabet with only "
            "internal apostrophes"
        )
    allowed = set(allowed_units or _DEFAULT_ALLOWED_UNITS)
    unsupported = sorted(set(unit) - allowed)
    if unsupported:
        raise ValueError(f"{label} contains unsupported MMS symbols: {unsupported}")
    return unit


def normalize_unit_overrides(document: Mapping[str, Any] | None) -> dict[OverrideKey, str]:
    """Validate structured song/line/token pronunciation overrides."""

    if document is None:
        return {}
    if not isinstance(document, Mapping):
        raise ValueError("unit_overrides must be an object")
    schema = document.get("schema_version", document.get("schema"))
    if schema != UNIT_OVERRIDES_SCHEMA_VERSION:
        raise ValueError(
            "unit_overrides schema_version must be "
            f"{UNIT_OVERRIDES_SCHEMA_VERSION!r}"
        )
    raw_records = document.get("overrides", document.get("unit_overrides"))
    if not isinstance(raw_records, list):
        raise ValueError("unit_overrides overrides must be an array")
    result: dict[OverrideKey, str] = {}
    for position, record in enumerate(raw_records):
        label = f"unit_overrides[{position}]"
        if not isinstance(record, Mapping):
            raise ValueError(f"{label} must be an object")
        required = {"song_id", "line_index", "token_index"}
        missing = sorted(required - set(record))
        if missing:
            raise ValueError(f"{label} is missing fields: {missing}")
        song_id = record["song_id"]
        line_index = record["line_index"]
        token_index = record["token_index"]
        if not isinstance(song_id, str) or not song_id:
            raise ValueError(f"{label}.song_id must be a non-empty string")
        if isinstance(line_index, bool) or not isinstance(line_index, int) or line_index < 0:
            raise ValueError(f"{label}.line_index must be a non-negative integer")
        if isinstance(token_index, bool) or not isinstance(token_index, int) or token_index < 0:
            raise ValueError(f"{label}.token_index must be a non-negative integer")
        has_unit = "unit" in record
        has_alignment_text = "alignment_text" in record
        if has_unit == has_alignment_text:
            raise ValueError(
                f"{label} must contain exactly one of unit or alignment_text"
            )
        value = record["unit"] if has_unit else record["alignment_text"]
        unit = _validated_ascii_mms_unit(value, label=f"{label}.unit")
        key = (song_id, line_index, token_index)
        if key in result:
            raise ValueError(f"duplicate unit override target: {key}")
        result[key] = unit
    return result


def build_alignment_input_units(
    characters: Sequence[Character],
    *,
    language: str,
    song_id: str,
    line_index: int,
    unit_overrides: Mapping[OverrideKey, str] | None = None,
    matched_override_keys: set[OverrideKey] | None = None,
    allowed_units: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Build v2 MMS input on the structured SUG ``characters`` token axis."""

    language = normalize_language(language)
    if language not in {"zh", "en"}:
        raise ValueError("structured SUG token alignment supports only zh/en")
    overrides = unit_overrides or {}
    texts = [str(character.get("char") or "") for character in characters]
    contextual_pinyin = (
        contextual_pinyin_for_text("".join(texts)) if language == "zh" else ()
    )
    text_offsets: list[int] = []
    offset = 0
    for text in texts:
        text_offsets.append(offset)
        offset += len(text)

    result: list[dict[str, Any]] = []
    previous_english_word_index: int | None = None
    for token_index, (character, source_text) in enumerate(zip(characters, texts)):
        timed = _first_timestamp_ms(character) is not None
        key = (song_id, line_index, token_index)
        explicit = overrides.get(key)
        if explicit is not None:
            if not timed:
                raise ValueError(f"unit override targets untimed SUG token {key}")
            alignment_text = _validated_ascii_mms_unit(
                explicit,
                allowed_units=allowed_units,
                label=f"unit override {key}",
            )
            provenance = "explicit-unit-override"
            if matched_override_keys is not None:
                matched_override_keys.add(key)
        elif language == "zh" and len(source_text) == 1 and is_chinese_character(source_text):
            if not timed:
                raise ValueError(
                    f"Chinese lexical SUG token {token_index} is untimed"
                )
            alignment_text = _validated_ascii_mms_unit(
                contextual_pinyin[text_offsets[token_index]],
                allowed_units=allowed_units,
                label=f"contextual pypinyin for token {token_index}",
            )
            provenance = "contextual-pypinyin"
        elif language == "en" or (source_text.isascii() and source_text):
            spans = english_word_spans(source_text)
            if not spans:
                if timed:
                    raise ValueError(
                        f"punctuation/space SUG token {token_index} must be untimed"
                    )
                alignment_text = ""
                provenance = "non-acoustic-display-token"
            else:
                if len(spans) != 1:
                    raise ValueError(
                        f"SUG token {token_index} contains multiple English words"
                    )
                start, end, word = spans[0]
                outside = source_text[:start] + source_text[end:]
                if any(character.isspace() or character.isalnum() for character in outside):
                    raise ValueError(
                        f"SUG token {token_index} mixes a word with whitespace/text"
                    )
                if not timed:
                    raise ValueError(f"English word SUG token {token_index} is untimed")
                if language == "en" and previous_english_word_index is not None:
                    raise ValueError(
                        "letter-level or multi-token English word axis is not allowed: "
                        f"tokens {previous_english_word_index} and {token_index} have no "
                        "untimed separator"
                    )
                alignment_text = _validated_ascii_mms_unit(
                    word.replace("’", "'").lower(),
                    allowed_units=allowed_units,
                    label=f"English word token {token_index}",
                )
                provenance = "sug-word-token"
                previous_english_word_index = token_index
        else:
            if timed:
                raise ValueError(
                    f"unsupported timed zh SUG token {token_index}: {source_text!r}"
                )
            alignment_text = ""
            provenance = "non-acoustic-display-token"

        if language == "en" and not alignment_text:
            previous_english_word_index = None
        result.append(
            {
                "source_token_index": token_index,
                "source_text": source_text,
                "timed": timed,
                "alignment_text": alignment_text,
                "provenance": provenance,
            }
        )
    return result


def build_source_token_display_mapping(
    characters: Sequence[Character],
) -> list[dict[str, Any]]:
    """Map every structured SUG token directly to its complete display text."""

    mapping: list[dict[str, Any]] = []
    for source_token_index, character in enumerate(characters):
        source_token_display = str(character.get("char") or "")
        if not source_token_display:
            raise ValueError(
                f"SUG token {source_token_index} has empty display text"
            )
        mapping.append(
            {
                "source_token_index": source_token_index,
                "source_token_display": source_token_display,
            }
        )
    return mapping


def line_units(
    text: str,
    helper: Any,
    *,
    allowed_units: Iterable[str] | None = None,
    language: str = DEFAULT_LANGUAGE,
) -> list[tuple[str, int]]:
    """Convert display text to generic MMS units for the selected language."""

    language = normalize_language(language)
    if language == "zh":
        units: list[tuple[str, int]] = []
        covered_word_characters: set[int] = set()
        for start, end, word in english_word_spans(text):
            unit = _filter_mms_unit(word, allowed_units)
            if unit == "x":
                for index in range(start, end):
                    if karaoke_timing.is_timed_character(text[index]):
                        units.append((unit, index))
            else:
                units.append((unit, start))
            covered_word_characters.update(range(start, end))
        for index, character in enumerate(text):
            if index in covered_word_characters or not is_chinese_character(character):
                continue
            unit = _filter_mms_unit(pinyin_for_character(character), allowed_units)
            if unit == "x":
                raise ValueError(
                    f"pypinyin produced no MMS unit for Chinese character "
                    f"{character!r} at index {index}"
                )
            units.append((unit, index))
        units.sort(key=lambda item: item[1])
        return units
    if language == "en":
        units = [
            (_filter_mms_unit(word, allowed_units), start)
            for start, _end, word in english_word_spans(text)
        ]
        return units

    cursor = 0
    units: list[tuple[str, int]] = []
    for item in _romanizer().convert(text):
        original = str(item.get("orig") or "")
        if not original:
            continue
        start = text.find(original, cursor)
        if start < 0:
            continue
        cursor = start + len(original)
        reading = str(item.get("hira") or original)
        units.extend(
            allocate_chunk(
                text,
                start,
                original,
                reading,
                helper,
                allowed_units=allowed_units,
                language=language,
            )
        )
    return units


def japanese_line_units(
    sentence: Mapping[str, Any],
    helper: Any,
    *,
    allowed_units: Iterable[str] | None = None,
) -> list[tuple[str, int]]:
    """Build Japanese MMS units, preferring canonical SUG ruby spans."""

    characters = sentence.get("characters")
    if not isinstance(characters, list):
        raise ValueError("Japanese SUG sentence has no characters array")
    text = "".join(str(character.get("char") or "") for character in characters)
    fallback = line_units(
        text,
        helper,
        allowed_units=allowed_units,
        language="ja",
    )
    spans = iter_sug_ruby_spans(
        {"metadata": {"language": "ja"}, "sentences": [sentence]}
    )
    if not spans:
        return fallback

    covered = {
        index
        for span in spans
        for index in range(span.start, span.end)
    }
    canonical: list[tuple[str, int]] = []
    for span in spans:
        canonical.extend(
            allocate_chunk(
                text,
                span.start,
                span.surface,
                span.reading,
                helper,
                allowed_units=allowed_units,
                language="ja",
            )
        )

    result: list[tuple[str, int]] = []
    for index in range(len(characters)):
        source = canonical if index in covered else fallback
        result.extend(item for item in source if item[1] == index)
    return result


def validate_mms_units(
    text: str,
    units: Sequence[tuple[str, int]],
) -> tuple[list[tuple[str, int]], frozenset[int]]:
    """Remove explicit ASCII stable-ts dispositions and reject other fallbacks."""

    supported: list[tuple[str, int]] = []
    retained_indices: set[int] = set()
    for unit, character_index in units:
        if unit != "x":
            supported.append((unit, character_index))
            continue
        character = text[character_index]
        if character.isascii() and character.isalnum():
            retained_indices.add(character_index)
            continue
        raise ValueError(
            "unsupported MMS fallback unit for character "
            f"{character_index} ({character!r}) in {text!r}"
        )
    return supported, frozenset(retained_indices)


def char_candidates(
    units: Sequence[Unit],
    *,
    index_field: str = "character_index",
) -> dict[int, dict[str, Any]]:
    """Group MMS units by source character, preserving first onset semantics."""

    grouped: dict[int, list[Unit]] = defaultdict(list)
    for item in units:
        grouped[int(item[index_field])].append(item)
    return {
        index: {
            "start_ms": int(items[0]["start_ms"]),
            "end_ms": max(int(item["end_ms"]) for item in items),
            "score": round(
                sum(float(item["score"]) for item in items) / len(items),
                6,
            ),
        }
        for index, items in grouped.items()
    }


def inherit_small_kana_candidates(
    text: str,
    units: Sequence[Unit],
) -> dict[int, dict[str, Any]]:
    """Fill MMS candidates for mora-joining small kana.

    MMS receives one token for a mora such as ``ねぇ``.  Depending on the
    weighted allocation, that token can be attached only to ``ね`` or only to
    ``ぇ``.  The karaoke character axis needs both characters to share the
    onset, while ``っ`` remains a real independent mora and is intentionally
    excluded by ``_MORA_JOINING_SMALL_KANA``.
    """

    candidates = char_candidates(units)
    for index, character in enumerate(text):
        if character not in _MORA_JOINING_SMALL_KANA:
            continue

        source_index = index - 1
        while source_index >= 0 and text[source_index] in _MORA_JOINING_SMALL_KANA:
            source_index -= 1
        if source_index < 0 or source_index not in candidates:
            continue

        source = candidates[source_index]
        candidates[index] = {
            "start_ms": source["start_ms"],
            "end_ms": source["end_ms"],
            "score": source["score"],
            "inherited_from_character_index": source_index,
        }
    return candidates


def inherit_display_group_candidates(
    text: str,
    units: Sequence[Unit],
    *,
    language: str = DEFAULT_LANGUAGE,
) -> dict[int, dict[str, Any]]:
    """Share one acoustic onset across glyphs forming one spoken unit.

    Small kana already inherit their base mora. Consecutive digits similarly
    share an onset when a general romanizer emits a spoken unit spanning
    multiple display glyphs.
    """

    language = normalize_language(language)
    candidates = inherit_small_kana_candidates(text, units)
    if language == "en":
        for start, end, _word in english_word_spans(text):
            group = [
                (candidate_index, candidates[candidate_index])
                for candidate_index in range(start, end)
                if candidate_index in candidates
            ]
            if not group:
                continue
            source_index, source = min(
                group,
                key=lambda item: (int(item[1]["start_ms"]), item[0]),
            )
            grouped = {
                "start_ms": int(source["start_ms"]),
                "end_ms": max(int(item[1]["end_ms"]) for item in group),
                "score": round(
                    sum(float(item[1]["score"]) for item in group) / len(group),
                    6,
                ),
            }
            for word_index in range(start, end):
                candidates[word_index] = {
                    **grouped,
                    **(
                        {"inherited_from_character_index": source_index}
                        if word_index != source_index
                        else {}
                    ),
                }
    index = 0
    while index < len(text):
        if not text[index].isdigit():
            index += 1
            continue
        end = index + 1
        while end < len(text) and text[end].isdigit():
            end += 1
        group = [
            (candidate_index, candidates[candidate_index])
            for candidate_index in range(index, end)
            if candidate_index in candidates
        ]
        if group:
            source_index, source = min(
                group,
                key=lambda item: (int(item[1]["start_ms"]), item[0]),
            )
            grouped = {
                "start_ms": int(source["start_ms"]),
                "end_ms": max(int(item[1]["end_ms"]) for item in group),
                "score": round(
                    sum(float(item[1]["score"]) for item in group) / len(group),
                    6,
                ),
            }
            for digit_index in range(index, end):
                candidates[digit_index] = {
                    **grouped,
                    **(
                        {"inherited_from_character_index": source_index}
                        if digit_index != source_index
                        else {}
                    ),
                }
        index = end
    return candidates


def build_comparisons(
    characters: Sequence[Character],
    units: Sequence[Unit],
    *,
    retained_character_indices: Iterable[int] = (),
    language: str = DEFAULT_LANGUAGE,
    index_field: str = "character_index",
) -> list[dict[str, Any]]:
    """Build current-vs-MMS comparisons, including inherited small kana."""

    text = "".join(str(character.get("char") or "") for character in characters)
    candidates = (
        inherit_display_group_candidates(text, units, language=language)
        if index_field == "character_index"
        else char_candidates(units, index_field=index_field)
    )
    retained = frozenset(int(index) for index in retained_character_indices)
    timed = _timed_characters(characters)
    comparisons: list[dict[str, Any]] = []
    for character_index, character, current_ms in timed:
        candidate = candidates.get(character_index)
        if candidate is None:
            if character_index in retained:
                comparisons.append(
                    {
                        index_field: character_index,
                        "character": str(character.get("char") or ""),
                        "current_ms": current_ms,
                        "mms_ms": current_ms,
                        "mms_end_ms": current_ms,
                        "delta_ms": 0,
                        "score": 0.0,
                        "alignment_disposition": "stable-ts-retained-ascii",
                    }
                )
                continue
            raise ValueError(
                "isolated-vocal MMS lacks candidate for timed character "
                f"{character_index} ({character.get('char')!r})"
            )
        mms_ms = int(candidate["start_ms"])
        comparison: dict[str, Any] = {
            index_field: character_index,
            "character": str(character.get("char") or ""),
            "current_ms": current_ms,
            "mms_ms": mms_ms,
            "mms_end_ms": int(candidate["end_ms"]),
            "delta_ms": mms_ms - current_ms,
            "score": float(candidate["score"]),
        }
        source_index = candidate.get("inherited_from_character_index")
        if source_index is not None:
            comparison["mms_inherited_from_character_index"] = int(source_index)
        comparisons.append(comparison)
    return comparisons


def build_dual_audio_comparisons(
    characters: Sequence[Character],
    comparisons: Sequence[Mapping[str, Any]],
    vocal_units: Sequence[Unit],
    mix_units: Sequence[Unit],
    *,
    language: str = DEFAULT_LANGUAGE,
    index_field: str = "character_index",
) -> list[dict[str, Any]]:
    """Pair isolated-vocal and original-mix MMS candidates by character index."""

    text = "".join(str(character.get("char") or "") for character in characters)
    if index_field == "character_index":
        vocal = inherit_display_group_candidates(text, vocal_units, language=language)
        mix = inherit_display_group_candidates(text, mix_units, language=language)
    else:
        vocal = char_candidates(vocal_units, index_field=index_field)
        mix = char_candidates(mix_units, index_field=index_field)
    paired: list[dict[str, Any]] = []
    for comparison in comparisons:
        index = int(comparison[index_field])
        if comparison.get("alignment_disposition") == "stable-ts-retained-ascii":
            current_ms = int(comparison["current_ms"])
            paired.append(
                {
                    **dict(comparison),
                    "vocal_mms_ms": current_ms,
                    "vocal_mms_end_ms": current_ms,
                    "vocal_score": 0.0,
                    "mix_mms_ms": current_ms,
                    "mix_mms_end_ms": current_ms,
                    "mix_score": 0.0,
                    "vocal_minus_mix_ms": 0,
                }
            )
            continue
        if index not in vocal or index not in mix:
            missing = []
            if index not in vocal:
                missing.append("isolated vocal")
            if index not in mix:
                missing.append("original mix")
            raise ValueError(
                "dual-audio MMS lacks candidate for timed character "
                f"{index} ({comparison.get('character')!r}) in " + " and ".join(missing)
            )
        vocal_candidate = vocal[index]
        mix_candidate = mix[index]
        item: dict[str, Any] = {
            **dict(comparison),
            "vocal_mms_ms": int(vocal_candidate["start_ms"]),
            "vocal_mms_end_ms": int(vocal_candidate["end_ms"]),
            "vocal_score": float(vocal_candidate["score"]),
            "mix_mms_ms": int(mix_candidate["start_ms"]),
            "mix_mms_end_ms": int(mix_candidate["end_ms"]),
            "mix_score": float(mix_candidate["score"]),
            "vocal_minus_mix_ms": int(vocal_candidate["start_ms"])
            - int(mix_candidate["start_ms"]),
        }
        mix_source = mix_candidate.get("inherited_from_character_index")
        if mix_source is not None:
            item["mix_mms_inherited_from_character_index"] = int(mix_source)
        paired.append(item)
    return paired


def normalize_song_ids(values: Sequence[str] | None) -> tuple[str, ...] | None:
    """Flatten repeated argparse values and comma-separated IDs."""

    if values is None:
        return None
    result: list[str] = []
    for value in values:
        for song_id in str(value).split(","):
            song_id = song_id.strip()
            if song_id and song_id not in result:
                result.append(song_id)
    return tuple(result)


def select_tracks(
    tracks: Sequence[AlbumTrack],
    song_ids: Sequence[str] | None = None,
) -> tuple[AlbumTrack, ...]:
    """Select manifest tracks in manifest order, validating every requested ID."""

    collection = tuple(tracks)
    requested = normalize_song_ids(song_ids)
    if not requested:
        return collection
    available = {track.song_id for track in collection}
    unknown = [song_id for song_id in requested if song_id not in available]
    if unknown:
        raise ValueError(
            "unknown song-id(s): "
            + ", ".join(unknown)
            + "; available: "
            + ", ".join(track.song_id for track in collection)
        )
    wanted = set(requested)
    return tuple(track for track in collection if track.song_id in wanted)


@dataclass(frozen=True)
class MmsRuntime:
    """Loaded MMS components; construction is intentionally not import-time."""

    torch: Any
    torchaudio: Any
    model: Any
    tokenizer: Any
    aligner: Any
    allowed_units: frozenset[str]
    sample_rate: int
    model_path: Path


def _validate_mms_model_access(
    model_path: Path | None,
    *,
    allow_network: bool,
) -> Path:
    """Resolve only a local MMS checkpoint; network fallback is unsupported."""

    del allow_network
    return resolve_mms_model_path(model_path)


def load_mms_runtime(
    project_root: Path = ROOT,
    *,
    model_path: Path | None = None,
    allow_network: bool = False,
) -> MmsRuntime:
    """Load one explicitly authorized or canonical repository-local MMS model."""

    local_model_path = _validate_mms_model_access(
        model_path,
        allow_network=allow_network,
    )

    del project_root
    import torch
    import torchaudio
    from torchaudio.pipelines import MMS_FA

    download_options = {
        "model_dir": str(local_model_path.parent),
        "file_name": local_model_path.name,
    }
    model = MMS_FA.get_model(dl_kwargs=download_options).eval()
    return MmsRuntime(
        torch=torch,
        torchaudio=torchaudio,
        model=model,
        tokenizer=MMS_FA.get_tokenizer(),
        aligner=MMS_FA.get_aligner(),
        allowed_units=frozenset(str(unit) for unit in MMS_FA.get_dict()),
        sample_rate=int(MMS_FA.sample_rate),
        model_path=local_model_path,
    )


def align_audio_units(
    audio: Any,
    crop_start_ms: int,
    crop_end_ms: int,
    units: Sequence[Unit],
    runtime: MmsRuntime,
    *,
    index_field: str = "character_index",
) -> list[dict[str, Any]]:
    """Run MMS on one crop and retain the source character index per unit."""

    if not units:
        return []
    sample_rate = int(audio.samplerate)
    frame_offset = max(0, int(round(crop_start_ms * sample_rate / 1000)))
    frame_count = max(
        1,
        int(round((crop_end_ms - crop_start_ms) * sample_rate / 1000)),
    )
    audio.seek(frame_offset)
    waveform = audio.read(
        frame_count,
        dtype="float32",
        always_2d=True,
    ).mean(axis=1)
    if len(waveform) == 0:
        raise RuntimeError(f"audio crop is empty: {crop_start_ms}..{crop_end_ms} ms")

    tensor = runtime.torch.from_numpy(waveform).unsqueeze(0)
    tensor = runtime.torchaudio.functional.resample(
        tensor,
        sample_rate,
        runtime.sample_rate,
    )
    with runtime.torch.inference_mode():
        emission = runtime.model(tensor)[0][0]
    frame_total = int(emission.size(0))
    if frame_total <= 0:
        raise RuntimeError("MMS returned an empty emission")

    spans = runtime.aligner(
        emission,
        runtime.tokenizer([str(item["unit"]) for item in units]),
    )
    if len(spans) != len(units):
        raise RuntimeError(
            f"MMS returned {len(spans)} token spans for {len(units)} units"
        )
    ratio_ms = (crop_end_ms - crop_start_ms) / frame_total
    results: list[dict[str, Any]] = []
    for source, unit_spans in zip(units, spans):
        if not unit_spans:
            raise RuntimeError(
                "MMS returned an empty token span for "
                f"{source['unit']!r} at source index {source[index_field]}"
            )
        start_frame = min(span.start for span in unit_spans)
        end_frame = max(span.end for span in unit_spans)
        duration_frames = sum(span.end - span.start for span in unit_spans)
        score = sum(
            float(span.score) * (span.end - span.start) for span in unit_spans
        ) / max(1, duration_frames)
        results.append(
            {
                "unit": str(source["unit"]),
                index_field: int(source[index_field]),
                "start_ms": round(crop_start_ms + start_frame * ratio_ms),
                "end_ms": round(crop_start_ms + end_frame * ratio_ms),
                "score": round(score, 6),
            }
        )
    return results


def _report_path(path: Path, project_root: Path) -> str:
    return project_relative(path, project_root)


def _vocal_path(track: AlbumTrack, vocals_root: Path) -> Path:
    return (vocals_root / track.audio_path.stem / "Vocals.wav").resolve()


def _load_sug(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(
        document.get("sentences"), list
    ):
        raise ValueError(f"SUG project has no sentences array: {path}")
    return document


def audit_track(
    track: AlbumTrack,
    album: AlbumManifest,
    runtime: MmsRuntime,
    vocals_root: Path,
    *,
    schema_version: str = SCHEMA_VERSION,
    unit_overrides: Mapping[OverrideKey, str] | None = None,
    matched_override_keys: set[OverrideKey] | None = None,
) -> dict[str, Any]:
    """Audit one manifest track against its MSST vocal and original MP3."""

    import soundfile as sf

    project_root = album.project_root
    sug_path = album.deliverable_dir / "timing" / f"{track.timing_stem}.sug"
    vocals_path = _vocal_path(track, vocals_root)
    mix_path = track.audio_path
    for path, label in (
        (sug_path, "SUG timing project"),
        (vocals_path, "MSST vocals"),
        (mix_path, "original MP3"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"missing {label}: {path}")

    project = _load_sug(sug_path)
    metadata = project.get("metadata")
    metadata_language = metadata.get("language") if isinstance(metadata, dict) else None
    language = normalize_language(metadata_language, default=track.language)
    structured_axis = schema_version == SCHEMA_VERSION_V2
    if structured_axis and language not in {"zh", "en"}:
        raise ValueError("audit v2 supports only zh/en tracks")
    song: dict[str, Any] = {
        "song_id": track.song_id,
        "title": track.title,
        "language": language,
        "language_identity": language_identity(language),
        "sug_path": _report_path(sug_path, project_root),
        "sug_sha256": sha256_file(sug_path),
        "vocals_path": _report_path(vocals_path, project_root),
        "vocals_sha256": sha256_file(vocals_path),
        "lines": [],
        "mix_path": _report_path(mix_path, project_root),
        "mix_sha256": sha256_file(mix_path),
    }

    with (
        sf.SoundFile(str(vocals_path)) as vocals_audio,
        sf.SoundFile(str(mix_path)) as mix_audio,
    ):
        vocal_duration_ms = int(
            round(len(vocals_audio) * 1000 / vocals_audio.samplerate)
        )
        helper = karaoke_timing.ReadingHelper()
        for line_index, sentence in enumerate(project["sentences"]):
            characters = (
                sentence.get("characters") if isinstance(sentence, dict) else None
            )
            if not isinstance(characters, list):
                raise ValueError(
                    f"sentence {line_index} has no characters array: {sug_path}"
                )
            text = "".join(str(character.get("char") or "") for character in characters)
            if not _timed_characters(characters):
                continue
            crop_start_ms, crop_end_ms = crop_window_ms(
                characters,
                vocal_duration_ms,
            )
            alignment_input_units: list[dict[str, Any]] = []
            timed_character_indices = [
                character_index
                for character_index, _character, _timestamp in _timed_characters(
                    characters
                )
            ]
            alignment_error: str | None = None
            units: list[tuple[str, int]] = []
            retained_character_indices: frozenset[int] = frozenset()
            vocal_units: list[dict[str, Any]] = []
            mix_units: list[dict[str, Any]] = []
            comparisons: list[dict[str, Any]] = []
            dual: list[dict[str, Any]] = []
            try:
                if structured_axis:
                    alignment_input_units = build_alignment_input_units(
                        characters,
                        language=language,
                        song_id=track.song_id,
                        line_index=line_index,
                        unit_overrides=unit_overrides,
                        matched_override_keys=matched_override_keys,
                        allowed_units=runtime.allowed_units,
                    )
                    units = [
                        (str(item["alignment_text"]), int(item["source_token_index"]))
                        for item in alignment_input_units
                        if item["alignment_text"]
                    ]
                else:
                    raw_units = (
                        japanese_line_units(
                            sentence,
                            helper,
                            allowed_units=runtime.allowed_units,
                        )
                        if language == "ja"
                        else line_units(
                            text,
                            helper,
                            allowed_units=runtime.allowed_units,
                            language=language,
                        )
                    )
                    units, retained_character_indices = validate_mms_units(
                        text, raw_units
                    )
                index_field = (
                    "source_token_index" if structured_axis else "character_index"
                )
                vocal_units = align_audio_units(
                    vocals_audio,
                    crop_start_ms,
                    crop_end_ms,
                    [
                        {"unit": unit, index_field: index}
                        for unit, index in units
                    ],
                    runtime,
                    index_field=index_field,
                )
                mix_units = align_audio_units(
                    mix_audio,
                    crop_start_ms,
                    crop_end_ms,
                    [
                        {"unit": unit, index_field: index}
                        for unit, index in units
                    ],
                    runtime,
                    index_field=index_field,
                )
                comparisons = build_comparisons(
                    characters,
                    vocal_units,
                    retained_character_indices=retained_character_indices,
                    language=language,
                    index_field=index_field,
                )
                dual = build_dual_audio_comparisons(
                    characters,
                    comparisons,
                    vocal_units,
                    mix_units,
                    language=language,
                    index_field=index_field,
                )
            except (RuntimeError, ValueError) as exc:
                alignment_error = f"{type(exc).__name__}: {exc}"
            index_field = "source_token_index" if structured_axis else "character_index"
            comparison_indices = [int(item[index_field]) for item in comparisons]
            dual_indices = [int(item[index_field]) for item in dual]
            coverage_complete = (
                alignment_error is None
                and comparison_indices == timed_character_indices
                and dual_indices == timed_character_indices
            )
            unresolved_reasons: list[str] = []
            if alignment_error:
                unresolved_reasons.append("mms-alignment-error")
            if comparison_indices != timed_character_indices:
                unresolved_reasons.append("isolated-vocal-coverage-incomplete")
            if dual_indices != timed_character_indices:
                unresolved_reasons.append("dual-audio-coverage-incomplete")
            if retained_character_indices:
                unresolved_reasons.append("stable-ts-retained-unit")
            release_ms = sentence_release_ms(characters)
            vocal_last_unit_end_ms = (
                max(int(item["end_ms"]) for item in vocal_units)
                if vocal_units
                else None
            )
            mix_last_unit_end_ms = (
                max(int(item["end_ms"]) for item in mix_units)
                if mix_units
                else None
            )
            line = {
                "line_index": line_index,
                "text": text,
                "language": language,
                "language_identity": language_identity(language),
                "mms_granularity": mms_granularity(language),
                "crop_start_ms": crop_start_ms,
                "crop_end_ms": crop_end_ms,
                (
                    "timed_source_token_indices"
                    if structured_axis
                    else "timed_character_indices"
                ): timed_character_indices,
                (
                    "timed_source_token_count"
                    if structured_axis
                    else "timed_character_count"
                ): len(timed_character_indices),
                "sug_release_ms": release_ms,
                "vocal_last_unit_end_ms": vocal_last_unit_end_ms,
                "mix_last_unit_end_ms": mix_last_unit_end_ms,
                "sug_release_minus_vocal_end_ms": (
                    release_ms - vocal_last_unit_end_ms
                    if vocal_last_unit_end_ms is not None
                    else None
                ),
                "sug_release_minus_mix_end_ms": (
                    release_ms - mix_last_unit_end_ms
                    if mix_last_unit_end_ms is not None
                    else None
                ),
                "units": vocal_units,
                "comparisons": comparisons,
                "mix_units": mix_units,
                "dual_audio_comparisons": dual,
                "actual_dual_audio": coverage_complete and not retained_character_indices,
                "coverage_complete": coverage_complete,
                "unresolved": bool(unresolved_reasons),
                "unresolved_reasons": unresolved_reasons,
                "alignment_error": alignment_error,
                "evidence_contract": {
                    "stable_ts": ALIGNMENT_EVIDENCE_CONTRACT["stable_ts"],
                    "mms_fa": ALIGNMENT_EVIDENCE_CONTRACT["mms_fa"],
                    "visual_interpolation": ALIGNMENT_EVIDENCE_CONTRACT[
                        "visual_interpolation"
                    ],
                },
            }
            if structured_axis:
                line.update(
                    alignment_input_units=alignment_input_units,
                    source_token_display_mapping=build_source_token_display_mapping(
                        characters
                    ),
                    unit_axis="structured-sug-token",
                    phoneme_alignment=False,
                )
            else:
                line["stable_ts_retained_character_indices"] = sorted(
                    retained_character_indices
                )
            song["lines"].append(line)
            notable = [
                item
                for item in dual
                if abs(int(item["vocal_mms_ms"]) - int(item["current_ms"])) >= 250
                and abs(int(item["vocal_minus_mix_ms"])) <= 180
            ]
            if notable:
                print(f"{track.title} L{line_index:02} {text}")
                for item in notable:
                    inherited = item.get("mms_inherited_from_character_index")
                    marker = (
                        f" inherited-from={inherited}" if inherited is not None else ""
                    )
                    print(
                        f"  i{int(item[index_field]):02} {item['character']} "
                        f"old={item['current_ms']} vocal={item['vocal_mms_ms']} "
                        f"mix={item['mix_mms_ms']} agree={item['vocal_minus_mix_ms']:+} "
                        f"scores={float(item['vocal_score']):.3f}/"
                        f"{float(item['mix_score']):.3f}{marker}"
                    )
    song["line_count"] = len(song["lines"])
    count_field = (
        "timed_source_token_count" if structured_axis else "timed_character_count"
    )
    song[count_field] = sum(int(line[count_field]) for line in song["lines"])
    song["unresolved"] = [
        {
            "line_index": line["line_index"],
            "reasons": line.get("unresolved_reasons", []),
        }
        for line in song["lines"]
        if line.get("unresolved")
    ]
    song["unresolved_count"] = len(song["unresolved"])
    song["gate_ok"] = (
        song["line_count"] > 0
        and song[count_field] > 0
        and song["unresolved_count"] == 0
    )
    return song


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _report_gate_ok(songs: Sequence[Mapping[str, Any]], unresolved_count: int) -> bool:
    """Reject vacuous audits as well as any song-level failure."""

    return (
        bool(songs)
        and all(bool(song.get("gate_ok")) for song in songs)
        and unresolved_count == 0
    )


def run_audit(
    *,
    song_ids: Sequence[str] | None = None,
    manifest_path: Path,
    source_path: Path | None = None,
    output_path: Path | None = None,
    vocals_root: Path | None = None,
    schema_version: str = SCHEMA_VERSION,
    version: str | None = None,
    unit_overrides: Mapping[str, Any] | None = None,
    allow_partial_manifest: bool = False,
    model_path: Path | None = None,
    allow_network: bool = False,
) -> dict[str, Any]:
    """Load the manifest, audit selected tracks, and write the report."""

    requested_schema = version or schema_version
    if (
        version is not None
        and schema_version != SCHEMA_VERSION
        and version != schema_version
    ):
        raise ValueError("conflicting audit schema_version and version")
    if requested_schema not in {SCHEMA_VERSION, SCHEMA_VERSION_V2}:
        raise ValueError(f"unsupported MMS audit schema version: {requested_schema!r}")
    normalized_unit_overrides = normalize_unit_overrides(unit_overrides)
    if normalized_unit_overrides and requested_schema != SCHEMA_VERSION_V2:
        raise ValueError("structured unit_overrides require MMS audit v2")
    matched_override_keys: set[OverrideKey] = set()

    album = load_album_manifest(
        manifest_path,
        require_five_tracks=not allow_partial_manifest,
    )
    project_root = album.project_root
    tracks = select_tracks(album.tracks, song_ids)
    runtime = load_mms_runtime(
        project_root,
        model_path=model_path,
        allow_network=allow_network,
    )
    resolved_vocals_root = (
        Path(vocals_root).expanduser().resolve()
        if vocals_root is not None
        else (project_root / ".cache" / "msst-vocals").resolve()
    )
    resolved_output = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else album.deliverable_dir / "sources" / "mms_alignment_audit.json"
    )
    resolved_source = (
        Path(source_path).expanduser().resolve()
        if source_path is not None
        else (album.deliverable_dir / "sources" / "netease_lyrics.json").resolve()
    )
    report: dict[str, Any] = {
        "schema_version": requested_schema,
        "evidence_contract": ALIGNMENT_EVIDENCE_CONTRACT,
        "manifest_path": _report_path(Path(manifest_path).resolve(), project_root),
        "manifest_sha256": sha256_file(Path(manifest_path).resolve()),
        "lyric_source_path": _report_path(resolved_source, project_root),
        "lyric_source_sha256": sha256_file(resolved_source),
        **(
            {
                "netease_lyrics_path": _report_path(resolved_source, project_root),
                "netease_lyrics_sha256": sha256_file(resolved_source),
            }
            if requested_schema == SCHEMA_VERSION
            else {}
        ),
        "lyric_corrections_path": _report_path(
            album.deliverable_dir / "sources" / "lyric_corrections.json",
            project_root,
        ),
        "lyric_corrections_sha256": sha256_file(
            album.deliverable_dir / "sources" / "lyric_corrections.json"
        ),
        "model": MODEL_NAME,
        "model_path": _report_path(runtime.model_path, project_root),
        "model_sha256": sha256_file(runtime.model_path),
        "model_network_allowed": bool(allow_network),
        "language_codes": {track.song_id: track.language for track in tracks},
        "language_identities": {
            track.song_id: language_identity(track.language) for track in tracks
        },
        "songs": [
            audit_track(
                track,
                album,
                runtime,
                resolved_vocals_root,
                schema_version=requested_schema,
                unit_overrides=normalized_unit_overrides,
                matched_override_keys=matched_override_keys,
            )
            for track in tracks
        ],
    }
    if requested_schema == SCHEMA_VERSION_V2:
        report["alignment_contract"] = {
            "alignment_type": "supplied-known-token-forced-alignment",
            "supplied_tokens": True,
            "known_tokens": True,
            "forced_alignment": True,
            "independent_recognition": False,
            "phoneme_alignment": False,
        }
        unmatched = sorted(set(normalized_unit_overrides) - matched_override_keys)
        if unmatched:
            raise ValueError(f"unit overrides did not match SUG tokens: {unmatched}")
    report["language_codes"] = {
        song["song_id"]: song["language"] for song in report["songs"]
    }
    report["language_identities"] = {
        song["song_id"]: song["language_identity"] for song in report["songs"]
    }
    report["unresolved"] = [
        {
            "song_id": song["song_id"],
            "items": song.get("unresolved", []),
        }
        for song in report["songs"]
        if song.get("unresolved")
    ]
    report["unresolved_count"] = sum(
        int(song.get("unresolved_count", 0)) for song in report["songs"]
    )
    report["gate_ok"] = _report_gate_ok(
        report["songs"],
        report["unresolved_count"],
    )
    _write_json(resolved_output, report)
    print(f"REPORT {resolved_output}")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit SUG character timing against project-local MSST vocals and "
            "the original MP3 using torchaudio MMS_FA."
        )
    )
    parser.add_argument(
        "--song-id",
        dest="song_ids",
        action="append",
        nargs="+",
        metavar="SONG_ID",
        help=(
            "audit one or more manifest song IDs; repeat the option or use "
            "comma-separated IDs (default: all tracks)"
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="album.json manifest owning the SUG and original MP3 inputs",
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="frozen local/net lyrics JSON bound into the audit provenance",
    )
    parser.add_argument(
        "--allow-partial-manifest",
        action="store_true",
        help="allow an explicitly supplied manifest with fewer than five tracks",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="report path (default: deliverables/.../sources/mms_alignment_audit.json)",
    )
    parser.add_argument(
        "--vocals-root",
        type=Path,
        help="MSST vocal cache root (default: .cache/msst-vocals)",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        help="existing local MMS checkpoint; validated before model loading",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    flattened_song_ids = (
        [song_id for group in args.song_ids for song_id in group]
        if args.song_ids
        else None
    )
    output_path = args.output
    if output_path is not None and not output_path.is_absolute():
        output_path = ROOT / output_path
    vocals_root = args.vocals_root
    if vocals_root is not None and not vocals_root.is_absolute():
        vocals_root = ROOT / vocals_root

    run_audit(
        song_ids=flattened_song_ids,
        manifest_path=args.manifest,
        source_path=args.source,
        output_path=output_path,
        vocals_root=vocals_root,
        allow_partial_manifest=args.allow_partial_manifest,
        model_path=args.model_path,
        allow_network=args.allow_network,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
