"""Canonical Japanese ruby facts plus an optional candidate generator.

The editable SUG character fields are the source of truth.  Canonical reads
are import-safe without pykakasi; the optional candidate helper lazy-loads it
for candidate-only tests and generation, never for preview rendering.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping, MutableMapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

DEFAULT_AUTO_APPROVE_CONFIDENCE = 0.90
RUBY_REVIEW_SCHEMA = "strange-utagame-ruby-review/v1"
RUBY_PLACEHOLDER_TEXTS = frozenset({"", "^", "^pause^"})
BLOCKED_REVIEW_STATES = frozenset({"low-confidence", "conflict", "unresolved"})
APPROVED_REVIEW_STATES = frozenset(
    {"ai-approved", "ai-reviewed", "human-reviewed", "human-locked"}
)
MACHINE_SOURCES = frozenset({"pykakasi", "dictionary", "machine-fill"})
_KATAKANA_MARKS = frozenset({"ー", "・", "･", "ｰ", "゛", "゜", "゙", "゚"})


class RubyValidationError(ValueError):
    """Raised when canonical SUG ruby facts cannot be trusted."""


@dataclass(frozen=True)
class RubyToken:
    """A candidate or canonical span-shaped ruby token."""

    text: str
    reading: str
    start: int
    end: int
    sentence_id: str = ""
    source: str = "candidate-generator"
    review_status: str = "human-locked"
    confidence: float | None = None
    evidence: tuple[Any, ...] = ()
    model_prompt_version: str | None = None
    before_hash: str | None = None
    after_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "surface": self.text,
            "reading": self.reading,
            "start": self.start,
            "end": self.end,
            "sentence_id": self.sentence_id,
            "source": self.source,
            "review_status": self.review_status,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "model_prompt_version": self.model_prompt_version,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
        }


def _candidate_language_enabled(language: str) -> bool:
    return str(language or "ja").strip().lower().replace("_", "-") in {
        "ja",
        "jp",
        "jpn",
        "japanese",
        "ja-jp",
    }


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


def is_pure_katakana(text: str) -> bool:
    """Return whether a surface contains only katakana and katakana marks."""

    surface = str(text)
    if not surface:
        return False
    for char in surface:
        codepoint = ord(char)
        name = unicodedata.name(char, "")
        is_letter = (
            0x30A1 <= codepoint <= 0x30FA
            or 0x31F0 <= codepoint <= 0x31FF
            or 0xFF66 <= codepoint <= 0xFF9D
            or "KATAKANA LETTER" in name
        )
        if is_letter:
            continue
        if char in _KATAKANA_MARKS:
            continue
        return False
    return True


def _kanji_ruby_spans(
    original: str,
    reading: str,
    *,
    start: int,
) -> list[RubyToken]:
    """Remove visible okurigana and keep ruby on matching kanji spans."""

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
            result.append(
                RubyToken(
                    text=run_text,
                    reading=ruby,
                    start=start + local_start,
                    end=start + local_end,
                )
            )
        reading_cursor = reading_end
    return result


def candidate_ruby_tokens(
    text: str,
    language: str = "ja",
) -> list[RubyToken]:
    """Generate contextual candidates outside the renderer's import path."""

    if not _candidate_language_enabled(language):
        return []
    try:
        from pykakasi import kakasi

        converted = kakasi().convert(text)
    except Exception:
        return []
    result: list[RubyToken] = []
    cursor = 0
    for item in converted:
        original = str(item.get("orig") or "")
        start = cursor
        end = start + len(original)
        reading = str(item.get("hira") or original)
        cursor = end
        if _contains_kanji(original) and reading and reading != original:
            result.extend(_kanji_ruby_spans(original, reading, start=start))
    return result


@dataclass(frozen=True)
class CanonicalRubySpan:
    """One SUG-owned lexical ruby span in sentence-local character indices."""

    sentence_id: str
    start: int
    end: int
    surface: str
    reading: str
    part_readings: tuple[str, ...]
    linked_to_next: tuple[bool, ...]
    source: str = "legacy-existing"
    review_status: str = "human-locked"
    confidence: float | None = None
    evidence: tuple[Any, ...] = ()
    model_prompt_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sentence_id": self.sentence_id,
            "start": self.start,
            "end": self.end,
            "surface": self.surface,
            "reading": self.reading,
            "part_readings": list(self.part_readings),
            "linked_to_next": list(self.linked_to_next),
            "source": self.source,
            "review_status": self.review_status,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "model_prompt_version": self.model_prompt_version,
        }


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _set_value(item: Any, key: str, value: Any) -> None:
    if isinstance(item, MutableMapping):
        item[key] = value
    else:
        setattr(item, key, value)


def _characters(sentence: Any) -> list[Any]:
    result = _value(sentence, "characters", [])
    return list(result or [])


def sentence_id(sentence: Any, fallback: str = "") -> str:
    return str(_value(sentence, "id", fallback) or fallback)


def _ruby_parts(character: Any) -> list[Any]:
    ruby = _value(character, "ruby")
    if ruby is None:
        return []
    parts = _value(ruby, "parts", [])
    return list(parts or [])


def _part_text(part: Any) -> str:
    return str(_value(part, "text", "") or "")


def _visible_part_text(part: Any) -> str:
    text = _part_text(part)
    return "" if text in RUBY_PLACEHOLDER_TEXTS else text


def _character_reading(character: Any) -> str:
    return "".join(_visible_part_text(part) for part in _ruby_parts(character))


def _character_has_ruby(character: Any) -> bool:
    return bool(_ruby_parts(character))


def _linked_to_next(character: Any) -> bool:
    return bool(_value(character, "linked_to_next", False))


def _is_space(character: Any) -> bool:
    return str(_value(character, "char", "")).isspace()


def _sug_language(document_or_project: Any) -> str:
    metadata = _value(document_or_project, "metadata", {})
    language = _value(metadata, "language", "ja")
    return str(language or "ja").strip().lower().replace("_", "-")


def is_ruby_language(document_or_project: Any) -> bool:
    """Return whether the Japanese-only ruby adapter may operate."""

    return _sug_language(document_or_project) in {"ja", "jp", "jpn", "japanese", "ja-jp"}


def _sentence_spans(sentence: Any, *, fallback_id: str = "") -> list[CanonicalRubySpan]:
    chars = _characters(sentence)
    sid = sentence_id(sentence, fallback_id)
    spans: list[CanonicalRubySpan] = []
    index = 0
    while index < len(chars):
        start = index
        while index < len(chars) - 1 and _linked_to_next(chars[index]):
            index += 1
        end = index + 1
        chain = chars[start:end]
        readings = tuple(
            reading
            for character in chain
            for reading in (_character_reading(character),)
            if reading
        )
        reading = "".join(readings)
        if reading:
            surface = "".join(
                str(_value(character, "char", "")) for character in chain
            )
            if is_pure_katakana(surface):
                index = end
                continue
            if any(_is_space(character) for character in chain):
                raise RubyValidationError(
                    f"ruby link crosses whitespace in sentence {sid!r}: {start}:{end}"
                )
            spans.append(
                CanonicalRubySpan(
                    sentence_id=sid,
                    start=start,
                    end=end,
                    surface=surface,
                    reading=reading,
                    part_readings=readings,
                    linked_to_next=tuple(_linked_to_next(character) for character in chain),
                )
            )
        index = end
    return spans


def iter_sug_ruby_spans(source: Any) -> list[CanonicalRubySpan]:
    """Read ruby spans from a Project/Sentence or a raw SUG document.

    The function only follows stored ``linked_to_next`` and ``ruby.parts``;
    it never calls a tokenizer, dictionary, or language model.
    """

    if isinstance(source, Mapping):
        sentences = source.get("sentences", [])
        return [
            span
            for index, sentence in enumerate(sentences or [])
            for span in _sentence_spans(sentence, fallback_id=f"sentence:{index}")
        ]
    sentences = _value(source, "sentences", None)
    if sentences is not None:
        return [
            span
            for index, sentence in enumerate(sentences or [])
            for span in _sentence_spans(sentence, fallback_id=f"sentence:{index}")
        ]
    return _sentence_spans(source)


def validate_sug_ruby(source: Any, *, require_ruby: bool = False) -> list[str]:
    """Return structural errors without attempting to repair the source."""

    errors: list[str] = []
    try:
        spans = iter_sug_ruby_spans(source)
    except RubyValidationError as error:
        return [str(error)]
    if not is_ruby_language(source):
        if spans:
            errors.append(f"ruby is disabled for language {_sug_language(source)!r}")
        return errors
    if require_ruby and not spans:
        errors.append("canonical SUG contains no ruby spans")
    for span in spans:
        if not span.surface or not span.reading:
            errors.append(f"empty ruby span {span.sentence_id}:{span.start}:{span.end}")
        if len(span.linked_to_next) != span.end - span.start:
            errors.append(f"link width mismatch for {span.surface!r}")
        if span.linked_to_next[-1]:
            errors.append(f"ruby chain does not terminate: {span.surface!r}")
    return errors


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _character_payload(character: Any) -> dict[str, Any]:
    ruby = _ruby_parts(character)
    return {
        "char": str(_value(character, "char", "") or ""),
        "check_count": int(_value(character, "check_count", 0) or 0),
        "timestamps": [int(value) for value in (_value(character, "timestamps", []) or [])],
        "sentence_end_ts": _value(character, "sentence_end_ts"),
        "linked_to_next": _linked_to_next(character),
        "is_line_end": bool(_value(character, "is_line_end", False)),
        "is_sentence_end": bool(_value(character, "is_sentence_end", False)),
        "singer_id": str(_value(character, "singer_id", "") or ""),
        "ruby": [
            {
                "text": _part_text(part),
                "offset_ms": int(_value(part, "offset_ms", 0) or 0),
            }
            for part in ruby
        ],
    }


def timing_fingerprint(source: Any) -> str:
    """Hash only timing/line-boundary facts; Ruby edits must not change it."""

    if isinstance(source, Mapping):
        sentences = source.get("sentences", []) or []
    else:
        sentences = _value(source, "sentences", []) or []
    payload = [
        {
            "id": sentence_id(sentence, f"sentence:{index}"),
            "characters": [
                {
                    key: _character_payload(character)[key]
                    for key in (
                        "char",
                        "check_count",
                        "timestamps",
                        "sentence_end_ts",
                        "is_line_end",
                        "is_sentence_end",
                        "singer_id",
                    )
                }
                for character in _characters(sentence)
            ],
        }
        for index, sentence in enumerate(sentences)
    ]
    return _digest(payload)


def sug_hash(source: Any) -> str:
    """Return a stable logical SUG hash including ruby and timing facts."""

    if isinstance(source, Mapping):
        sentences = source.get("sentences", []) or []
        raw_metadata = source.get("metadata", {})
        metadata = {
            key: _value(raw_metadata, key, "")
            for key in ("title", "artist", "album", "language")
        }
        payload = {
            "id": source.get("id", ""),
            "metadata": metadata,
            "audio_duration_ms": source.get("audio_duration_ms", 0),
            "sentences": [
                {
                    "id": sentence_id(sentence, f"sentence:{index}"),
                    "singer_id": sentence.get("singer_id", ""),
                    "characters": [_character_payload(character) for character in _characters(sentence)],
                }
                for index, sentence in enumerate(sentences)
            ],
        }
    else:
        sentences = _value(source, "sentences", []) or []
        metadata = _value(source, "metadata", None)
        payload = {
            "id": str(_value(source, "id", "") or ""),
            "metadata": {
                key: _value(metadata, key, "")
                for key in ("title", "artist", "album", "language")
            },
            "audio_duration_ms": int(_value(source, "audio_duration_ms", 0) or 0),
            "sentences": [
                {
                    "id": sentence_id(sentence, f"sentence:{index}"),
                    "singer_id": str(_value(sentence, "singer_id", "") or ""),
                    "characters": [_character_payload(character) for character in _characters(sentence)],
                }
                for index, sentence in enumerate(sentences)
            ],
        }
    return _digest(payload)


def span_hash(source: Any, sentence_index: int, start: int, end: int) -> str:
    if isinstance(source, Mapping):
        sentences = source.get("sentences", []) or []
    else:
        sentences = _value(source, "sentences", []) or []
    sentence = sentences[sentence_index]
    chars = _characters(sentence)
    return _digest(
        {
            "sentence_id": sentence_id(sentence, f"sentence:{sentence_index}"),
            "start": start,
            "end": end,
            "characters": [_character_payload(character) for character in chars[start:end]],
        }
    )


def _mora_units(reading: str) -> list[str]:
    small = set("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ")
    units: list[str] = []
    for character in reading:
        if character in small and units:
            units[-1] += character
        else:
            units.append(character)
    return units


def split_reading_for_parts(reading: str, count: int) -> list[str]:
    """Fit a reading to existing checkpoints without changing their count."""

    if count <= 0:
        return [reading]
    if count == 1:
        return [reading]
    units = _mora_units(reading)
    if not units:
        return [""] * count
    if len(units) <= count:
        return [*units, *([""] * (count - len(units)))]
    result: list[str] = []
    base, remainder = divmod(len(units), count)
    cursor = 0
    for index in range(count):
        width = base + (1 if index < remainder else 0)
        result.append("".join(units[cursor : cursor + width]))
        cursor += width
    return result


def _offsets(character: Any, count: int) -> list[int]:
    timestamps = [int(value) for value in (_value(character, "timestamps", []) or [])]
    base = timestamps[0] if timestamps else 0
    return [timestamps[index] - base if index < len(timestamps) else 0 for index in range(count)]


def _make_ruby(character: Any, parts: Sequence[str]) -> Any:
    old_ruby = _value(character, "ruby")
    offsets = _offsets(character, len(parts))
    if isinstance(character, MutableMapping):
        old_fields = dict(old_ruby) if isinstance(old_ruby, Mapping) else {}
        old_fields["parts"] = [
            {"text": text, "offset_ms": offsets[index]}
            for index, text in enumerate(parts)
        ]
        return old_fields
    if old_ruby is not None and getattr(old_ruby, "parts", None):
        part_type = type(old_ruby.parts[0])
        ruby_type = type(old_ruby)
        return ruby_type(parts=[part_type(text=text, offset_ms=offsets[index]) for index, text in enumerate(parts)])
    from strange_uta_game.backend.domain import Ruby, RubyPart

    return Ruby(parts=[RubyPart(text=text, offset_ms=offsets[index]) for index, text in enumerate(parts)])


def _set_character_reading(character: Any, reading: str, parts: Sequence[str] | None = None) -> None:
    current_parts = _ruby_parts(character)
    target_count = len(current_parts) or int(_value(character, "check_count", 0) or 0) or 1
    new_parts = list(parts) if parts is not None else split_reading_for_parts(reading, target_count)
    if parts is not None and target_count > 0 and len(new_parts) != target_count:
        raise RubyValidationError(
            f"ruby parts count {len(new_parts)} != checkpoint count {target_count}"
        )
    if not any(new_parts):
        _set_value(character, "ruby", None)
        return
    new_ruby = _make_ruby(character, new_parts)
    setter = getattr(character, "set_ruby", None)
    if callable(setter):
        setter(new_ruby)
    else:
        _set_value(character, "ruby", new_ruby)


def _sentence_index_map(source: Any) -> dict[str, int]:
    sentences = source.get("sentences", []) if isinstance(source, Mapping) else _value(source, "sentences", [])
    return {sentence_id(sentence, f"sentence:{index}"): index for index, sentence in enumerate(sentences or [])}


def _patch_parts(patch: Mapping[str, Any], width: int) -> list[list[str]]:
    raw_parts = patch.get("parts", patch.get("readings"))
    if raw_parts is None:
        reading = str(patch.get("reading") or "")
        return [[reading] if index == 0 else [] for index in range(width)]
    if not isinstance(raw_parts, Sequence) or isinstance(raw_parts, (str, bytes)):
        raise RubyValidationError("Agent ruby patch parts must be a list")
    if len(raw_parts) != width:
        raise RubyValidationError(
            f"Agent ruby patch parts width {len(raw_parts)} != span width {width}"
        )
    result: list[list[str]] = []
    for item in raw_parts:
        if isinstance(item, str):
            result.append([item])
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            result.append([str(value or "") for value in item])
        else:
            raise RubyValidationError("Agent ruby patch part must be string or list")
    return result


def _patch_links(patch: Mapping[str, Any], width: int) -> list[bool]:
    raw = patch.get("linked_to_next")
    if raw is None:
        return [True] * (width - 1) + [False]
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != width:
        raise RubyValidationError("Agent ruby patch linked_to_next width mismatch")
    return [bool(value) for value in raw]


def _review_key(sentence_id_value: str, start: int, end: int) -> str:
    return f"{sentence_id_value}:{start}:{end}"


def review_records_by_key(sidecar: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not isinstance(sidecar, Mapping):
        return {}
    records = sidecar.get("records", [])
    if not isinstance(records, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        key = _review_key(
            str(record.get("sentence_id", "")),
            int(record.get("start", 0)),
            int(record.get("end", 0)),
        )
        result[key] = record
    return result


def validate_review_sidecar(
    source: Any, sidecar: Mapping[str, Any] | None
) -> list[str]:
    """Return errors when a ruby review sidecar is not current and approved."""

    if not isinstance(sidecar, Mapping):
        return ["ruby review sidecar is missing or not an object"]

    errors: list[str] = []
    if sidecar.get("schema") != RUBY_REVIEW_SCHEMA:
        errors.append(
            f"ruby review sidecar schema must be {RUBY_REVIEW_SCHEMA!r}"
        )

    records = sidecar.get("records")
    if not isinstance(records, list):
        errors.append("ruby review sidecar records must be a list")
        return errors

    try:
        current_sug_hash = sug_hash(source)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        errors.append(f"cannot hash current SUG for ruby review: {error}")
        return errors
    sidecar_sug_hash = sidecar.get("sug_hash_after")
    if not isinstance(sidecar_sug_hash, str) or not sidecar_sug_hash:
        errors.append("ruby review sidecar is missing a valid sug_hash_after")
    elif sidecar_sug_hash != current_sug_hash:
        errors.append(
            "ruby review sidecar sug_hash_after does not match current sug_hash"
        )

    if isinstance(source, Mapping):
        sentences = source.get("sentences", []) or []
    else:
        sentences = _value(source, "sentences", None)
    if sentences is None:
        errors.append("current SUG has no sentence collection")
        return errors

    try:
        current_spans = [
            (sentence_index, span)
            for sentence_index, sentence in enumerate(sentences)
            for span in _sentence_spans(
                sentence, fallback_id=f"sentence:{sentence_index}"
            )
        ]
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, RubyValidationError) as error:
        errors.append(f"cannot read current canonical ruby spans: {error}")
        return errors

    latest_records: dict[tuple[str, int, int], Mapping[str, Any]] = {}
    for raw_record in records:
        if not isinstance(raw_record, Mapping):
            continue
        sentence_value = raw_record.get("sentence_id")
        start_value = raw_record.get("start")
        end_value = raw_record.get("end")
        if not (
            isinstance(sentence_value, str)
            and isinstance(start_value, int)
            and not isinstance(start_value, bool)
            and isinstance(end_value, int)
            and not isinstance(end_value, bool)
            and start_value < end_value
        ):
            continue
        latest_records[(sentence_value, start_value, end_value)] = raw_record

    for sentence_index, span in current_spans:
        identity = (span.sentence_id, span.start, span.end)
        record = latest_records.get(identity)
        if record is None:
            errors.append(
                "missing latest ruby review record for "
                f"{_review_key(*identity)}:{span.surface!r}"
            )
            continue

        record_surface = record.get("surface")
        if not isinstance(record_surface, str):
            errors.append(
                f"ruby review record has invalid surface for {_review_key(*identity)}"
            )
        elif record_surface != span.surface:
            errors.append(
                f"ruby review record surface mismatch for {_review_key(*identity)}"
            )

        status = record.get("review_status")
        if not isinstance(status, str):
            errors.append(
                f"ruby review record has invalid review_status for {_review_key(*identity)}"
            )
        elif status not in APPROVED_REVIEW_STATES:
            if status in BLOCKED_REVIEW_STATES:
                errors.append(
                    f"ruby review record {_review_key(*identity)} is blocked: {status}"
                )
            else:
                errors.append(
                    f"ruby review record {_review_key(*identity)} is not approved: {status}"
                )

        record_source = record.get("source")
        if not isinstance(record_source, str) or not record_source.strip():
            errors.append(
                f"ruby review record has missing or invalid source for {_review_key(*identity)}"
            )
        elif record_source.strip().casefold() in MACHINE_SOURCES:
            errors.append(
                f"ruby review record {_review_key(*identity)} is machine-only"
            )

        confidence = record.get("confidence")
        confidence_value: float | None = None
        if confidence is None:
            errors.append(
                f"ruby review record has missing confidence for {_review_key(*identity)}"
            )
        elif isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            errors.append(
                f"ruby review record has invalid confidence for {_review_key(*identity)}"
            )
        else:
            try:
                confidence_value = float(confidence)
            except (OverflowError, TypeError, ValueError):
                errors.append(
                    f"ruby review record has invalid confidence for {_review_key(*identity)}"
                )
            else:
                if not math.isfinite(confidence_value) or not 0 <= confidence_value <= 1:
                    errors.append(
                        f"ruby review record has invalid confidence for {_review_key(*identity)}"
                    )
                    confidence_value = None
        if confidence_value is not None and confidence_value < DEFAULT_AUTO_APPROVE_CONFIDENCE:
            errors.append(
                f"ruby review record {_review_key(*identity)} is low-confidence"
            )

        record_after_hash = record.get("after_hash")
        if not isinstance(record_after_hash, str) or not record_after_hash:
            errors.append(
                f"ruby review record has invalid after_hash for {_review_key(*identity)}"
            )
        try:
            current_span_hash = span_hash(
                source, sentence_index, span.start, span.end
            )
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
            errors.append(
                f"cannot hash current ruby span {_review_key(*identity)}: {error}"
            )
            continue
        if record_after_hash != current_span_hash:
            errors.append(
                f"ruby review record after_hash mismatch for {_review_key(*identity)}"
            )
    return errors


def _machine_record_covers(
    prior_records: Sequence[Mapping[str, Any]],
    sentence_id_value: str,
    start: int,
    end: int,
) -> bool:
    """Return whether every existing ruby character has machine provenance."""

    relevant = [
        record
        for record in prior_records
        if str(record.get("sentence_id", "")) == sentence_id_value
        and str(record.get("source", "")) in MACHINE_SOURCES
    ]
    return all(
        any(
            int(record.get("start", 0)) <= index < int(record.get("end", 0))
            for record in relevant
        )
        for index in range(start, end)
    )


def apply_review_patches(
    document: MutableMapping[str, Any],
    patches: Sequence[Mapping[str, Any]],
    *,
    sidecar: Mapping[str, Any] | None = None,
    auto_approve_confidence: float = DEFAULT_AUTO_APPROVE_CONFIDENCE,
) -> dict[str, Any]:
    """Apply an atomic, timing-preserving set of Agent ruby patches."""

    before_timing = timing_fingerprint(document)
    before_sug = sug_hash(document)
    if not is_ruby_language(document):
        return {
            "changes": [],
            "unresolved": [
                {"reason": "ruby-disabled-language", "language": _sug_language(document)}
            ],
            "records": [],
            "before_sug_hash": before_sug,
            "after_sug_hash": before_sug,
            "timing_unchanged": True,
        }
    sentence_indices = _sentence_index_map(document)
    prior_records = review_records_by_key(sidecar)
    prior_record_values = list(prior_records.values())
    prepared: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for raw_patch in patches:
        patch = dict(raw_patch)
        sid = str(patch.get("sentence_id", ""))
        if sid not in sentence_indices:
            blocked.append({"reason": "unknown-sentence", "sentence_id": sid})
            continue
        status = str(patch.get("review_status", "unresolved") or "unresolved")
        confidence_raw = patch.get("confidence")
        if status in BLOCKED_REVIEW_STATES:
            blocked.append({"reason": status, "patch": patch})
            continue
        if confidence_raw is None:
            blocked.append({"reason": "missing-confidence", "patch": patch})
            continue
        if isinstance(confidence_raw, bool):
            blocked.append({"reason": "invalid-confidence", "patch": patch})
            continue
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError, OverflowError):
            blocked.append({"reason": "invalid-confidence", "patch": patch})
            continue
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            blocked.append({"reason": "invalid-confidence", "patch": patch})
            continue
        if confidence < auto_approve_confidence:
            blocked.append({"reason": "low-confidence", "patch": patch})
            continue
        review_source_raw = patch.get("source")
        if not isinstance(review_source_raw, str):
            blocked.append({"reason": "invalid-review-source", "patch": patch})
            continue
        review_source = review_source_raw.strip()
        if not review_source or review_source.casefold() in MACHINE_SOURCES:
            blocked.append({"reason": "invalid-review-source", "patch": patch})
            continue
        if status not in APPROVED_REVIEW_STATES and not (
            status == "agent-reviewed" and confidence >= auto_approve_confidence
        ):
            blocked.append({"reason": "unapproved-review-status", "patch": patch})
            continue
        index = sentence_indices[sid]
        sentence = (document.get("sentences") or [])[index]
        chars = _characters(sentence)
        start = int(patch.get("start", -1))
        end = int(patch.get("end", -1))
        if not (0 <= start < end <= len(chars)):
            blocked.append({"reason": "invalid-span", "patch": patch})
            continue
        surface = "".join(str(_value(character, "char", "") or "") for character in chars[start:end])
        if patch.get("surface") not in (None, surface):
            blocked.append({"reason": "surface-mismatch", "patch": patch, "actual": surface})
            continue
        key = _review_key(sid, start, end)
        existing_ruby = any(_character_has_ruby(character) for character in chars[start:end])
        prior = prior_records.get(key, {})
        prior_source = str(prior.get("source", "") or "")
        allow_existing = bool(patch.get("override_existing", False))
        machine_covered = _machine_record_covers(
            prior_record_values,
            sid,
            start,
            end,
        )
        if (
            existing_ruby
            and not allow_existing
            and prior_source not in MACHINE_SOURCES
            and not machine_covered
        ):
            blocked.append({"reason": "human-locked", "patch": patch})
            continue
        current_hash = span_hash(document, index, start, end)
        expected_before = patch.get("before_hash")
        if expected_before and expected_before != current_hash:
            blocked.append({"reason": "before-hash-mismatch", "patch": patch, "actual": current_hash})
            continue
        try:
            parts = _patch_parts(patch, end - start)
            links = _patch_links(patch, end - start)
        except (TypeError, ValueError, RubyValidationError) as error:
            blocked.append({"reason": "invalid-patch", "patch": patch, "error": str(error)})
            continue
        prepared.append(
            {
                "patch": patch,
                "sentence_index": index,
                "sentence": sentence,
                "characters": chars,
                "start": start,
                "end": end,
                "surface": surface,
                "before_hash": current_hash,
                "parts": parts,
                "links": links,
                "confidence": confidence,
                "review_source": review_source,
            }
        )
    occupied: dict[str, list[tuple[int, int]]] = {}
    for item in prepared:
        sid = sentence_id(item["sentence"], f"sentence:{item['sentence_index']}")
        previous = occupied.setdefault(sid, [])
        if any(
            item["start"] < end and start < item["end"]
            for start, end in previous
        ):
            blocked.append({"reason": "overlapping-patch", "sentence_id": sid})
        previous.append((item["start"], item["end"]))
        for offset, character in enumerate(item["characters"][item["start"] : item["end"]]):
            supplied_parts = item["parts"][offset]
            target_count = len(_ruby_parts(character)) or int(
                _value(character, "check_count", 0) or 0
            )
            if supplied_parts and target_count > 0 and len(supplied_parts) != target_count:
                blocked.append(
                    {
                        "reason": "ruby-part-count-mismatch",
                        "sentence_id": sid,
                        "char_index": item["start"] + offset,
                        "expected": target_count,
                        "actual": len(supplied_parts),
                    }
                )
    if blocked:
        return {
            "changes": [],
            "unresolved": blocked,
            "records": [],
            "before_sug_hash": before_sug,
            "after_sug_hash": before_sug,
            "timing_unchanged": True,
        }

    changes: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for item in prepared:
        patch = item["patch"]
        chars = item["characters"]
        start = item["start"]
        end = item["end"]
        for offset, character in enumerate(chars[start:end]):
            old_reading = _character_reading(character)
            new_parts = item["parts"][offset]
            new_reading = "".join(new_parts)
            if old_reading != new_reading:
                _set_character_reading(character, new_reading, new_parts or None)
                changes.append(
                    {
                        "sentence_id": sentence_id(item["sentence"], f"sentence:{item['sentence_index']}"),
                        "char_index": start + offset,
                        "kind": "reading",
                        "before": old_reading,
                        "after": new_reading,
                    }
                )
            old_link = _linked_to_next(character)
            new_link = item["links"][offset]
            if old_link != new_link:
                _set_value(character, "linked_to_next", new_link)
                changes.append(
                    {
                        "sentence_id": sentence_id(item["sentence"], f"sentence:{item['sentence_index']}"),
                        "char_index": start + offset,
                        "kind": "linked_to_next",
                        "before": old_link,
                        "after": new_link,
                    }
                )
        after_hash = span_hash(document, item["sentence_index"], start, end)
        review_status = str(patch.get("review_status", "ai-reviewed"))
        if review_status in {"agent-reviewed", "ai-reviewed"} and item["confidence"] is not None and item["confidence"] >= auto_approve_confidence:
            review_status = "ai-approved"
        records.append(
            {
                "sentence_id": sentence_id(item["sentence"], f"sentence:{item['sentence_index']}"),
                "start": start,
                "end": end,
                "surface": item["surface"],
                "source": item["review_source"],
                "review_status": review_status,
                "confidence": item["confidence"],
                "evidence": list(patch.get("evidence", []) or []),
                "model_prompt_version": patch.get("model_prompt_version"),
                "generation_id": str(patch.get("generation_id") or uuid4()),
                "before_hash": item["before_hash"],
                "after_hash": after_hash,
            }
        )
    after_timing = timing_fingerprint(document)
    if after_timing != before_timing:
        raise RubyValidationError("ruby patch changed canonical timing fields")
    after_sug = sug_hash(document)
    return {
        "changes": changes,
        "unresolved": [],
        "records": records,
        "before_sug_hash": before_sug,
        "after_sug_hash": after_sug,
        "timing_unchanged": True,
    }


def fill_missing_project_ruby(project: Any, helper: Any) -> list[dict[str, Any]]:
    """Use a generator only for missing Japanese Ruby on a Project object."""

    if not is_ruby_language(project):
        return []
    before_timing = timing_fingerprint(project)
    records: list[dict[str, Any]] = []
    sentences = _value(project, "sentences", []) or []

    if hasattr(helper, "apply_to_sentence"):
        for sentence_index, sentence in enumerate(sentences):
            original_chars = _characters(sentence)
            analyzed_sentence = deepcopy(sentence)
            helper.apply_to_sentence(
                analyzed_sentence,
                keep_existing_timetags=True,
                only_noruby=True,
                apply_user_dict=True,
            )
            analyzed_chars = _characters(analyzed_sentence)
            if len(analyzed_chars) != len(original_chars):
                raise RubyValidationError(
                    "whole-sentence ruby analysis changed the character axis"
                )

            start = 0
            while start < len(analyzed_chars):
                end = start + 1
                while end < len(analyzed_chars) and _linked_to_next(
                    analyzed_chars[end - 1]
                ):
                    end += 1
                source_chain = original_chars[start:end]
                analyzed_chain = analyzed_chars[start:end]
                surface = "".join(
                    str(_value(character, "char", "") or "")
                    for character in source_chain
                )
                if (
                    any(_character_has_ruby(character) for character in source_chain)
                    or is_pure_katakana(surface)
                    or not any(
                        _character_has_ruby(character)
                        for character in analyzed_chain
                    )
                ):
                    start = end
                    continue

                before_hash = span_hash(project, sentence_index, start, end)
                for offset, (target, analyzed) in enumerate(
                    zip(source_chain, analyzed_chain, strict=True)
                ):
                    reading = _character_reading(analyzed)
                    if reading:
                        _set_character_reading(target, reading)
                    _set_value(
                        target,
                        "linked_to_next",
                        offset < len(source_chain) - 1
                        and _linked_to_next(analyzed),
                    )
                after_hash = span_hash(project, sentence_index, start, end)
                records.append(
                    {
                        "sentence_id": sentence_id(
                            sentence, f"sentence:{sentence_index}"
                        ),
                        "start": start,
                        "end": end,
                        "surface": surface,
                        "source": "project-auto-check",
                        "review_status": "machine-fill",
                        "confidence": None,
                        "evidence": [
                            "whole-sentence-tokenizer",
                            "project-dictionary",
                        ],
                        "model_prompt_version": None,
                        "generation_id": str(uuid4()),
                        "before_hash": before_hash,
                        "after_hash": after_hash,
                    }
                )
                start = end
        if timing_fingerprint(project) != before_timing:
            raise RubyValidationError("machine ruby fill changed canonical timing fields")
        return records

    for sentence_index, sentence in enumerate(sentences):
        chars = _characters(sentence)
        for char_index, character in enumerate(chars):
            if _character_has_ruby(character):
                continue
            surface = str(_value(character, "char", ""))
            if is_pure_katakana(surface):
                continue
            candidate = helper.ruby(surface, language="ja")
            if candidate is None:
                continue
            before_hash = span_hash(project, sentence_index, char_index, char_index + 1)
            candidate_reading = "".join(
                _visible_part_text(part)
                for part in (_value(candidate, "parts", []) or [])
            )
            _set_character_reading(character, candidate_reading)
            after_hash = span_hash(project, sentence_index, char_index, char_index + 1)
            records.append(
                {
                    "sentence_id": sentence_id(sentence, f"sentence:{sentence_index}"),
                    "start": char_index,
                    "end": char_index + 1,
                    "surface": str(_value(character, "char", "") or ""),
                    "source": "pykakasi",
                    "review_status": "machine-fill",
                    "confidence": None,
                    "evidence": ["default-generator"],
                    "model_prompt_version": None,
                    "generation_id": str(uuid4()),
                    "before_hash": before_hash,
                    "after_hash": after_hash,
                }
            )
    if timing_fingerprint(project) != before_timing:
        raise RubyValidationError("machine ruby fill changed canonical timing fields")
    return records


def write_review_sidecar(
    path: str | Path,
    *,
    sug_hash_before: str,
    sug_hash_after: str,
    records: Sequence[Mapping[str, Any]],
    generation_id: str | None = None,
    model_prompt_version: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": RUBY_REVIEW_SCHEMA,
        "generation_id": generation_id or str(uuid4()),
        "sug_hash_before": sug_hash_before,
        "sug_hash_after": sug_hash_after,
        "model_prompt_version": model_prompt_version,
        "records": [dict(record) for record in records],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def load_review_sidecar(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != RUBY_REVIEW_SCHEMA:
        raise RubyValidationError(f"unsupported ruby review sidecar: {path}")
    return value
