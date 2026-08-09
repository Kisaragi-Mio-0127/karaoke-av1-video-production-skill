#!/usr/bin/env python3
"""Create a private editable SUG companion from reviewed MMS timing evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    from scripts.sug_ruby import (
        load_review_sidecar,
        sentence_id,
        span_hash,
        sug_hash,
    )
    from strange_uta_game.backend.infrastructure.persistence.sug_io import (
        SugProjectParser,
    )
except ImportError:  # pragma: no cover - direct script execution
    from sug_ruby import (  # type: ignore[no-redef]
        load_review_sidecar,
        sentence_id,
        span_hash,
        sug_hash,
    )
    from strange_uta_game.backend.infrastructure.persistence.sug_io import (  # type: ignore[no-redef]
        SugProjectParser,
    )


class MmsEditableError(ValueError):
    """Raised when an MMS companion cannot be created without corrupting SUG data."""


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MmsEditableError(f"{label} is not valid UTF-8 JSON: {path}: {error}") from error
    if not isinstance(value, dict):
        raise MmsEditableError(f"{label} root must be an object: {path}")
    return value


def _integer_ms(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise MmsEditableError(f"{label} must be a non-negative integer millisecond value")
    try:
        converted = int(value)
    except (TypeError, ValueError) as error:
        raise MmsEditableError(
            f"{label} must be a non-negative integer millisecond value"
        ) from error
    if converted < 0 or converted != value:
        raise MmsEditableError(f"{label} must be a non-negative integer millisecond value")
    return converted


def _line_overrides(
    overrides: Mapping[str, Any], song_id: str
) -> Mapping[str, Any]:
    songs = overrides.get("songs")
    if not isinstance(songs, Mapping) or set(songs) != {song_id}:
        raise MmsEditableError("timing overrides must contain exactly the selected song")
    song = songs.get(song_id)
    lines = song.get("lines") if isinstance(song, Mapping) else None
    if not isinstance(lines, Mapping):
        raise MmsEditableError("timing overrides selected-song lines must be an object")
    return lines


def _apply_line(
    sentence: dict[str, Any], line: Mapping[str, Any], line_index: int
) -> tuple[int, bool]:
    characters = sentence.get("characters")
    if not isinstance(characters, list) or not characters:
        raise MmsEditableError(f"SUG line {line_index} has no character tokens")

    applied = 0
    character_overrides = line.get("character_overrides_ms", {})
    if not isinstance(character_overrides, Mapping):
        raise MmsEditableError(
            f"line {line_index} character_overrides_ms must be an object"
        )
    for raw_index, raw_ms in character_overrides.items():
        try:
            token_index = int(raw_index)
        except (TypeError, ValueError) as error:
            raise MmsEditableError(
                f"line {line_index} has an invalid source token index: {raw_index!r}"
            ) from error
        if str(token_index) != str(raw_index) or not 0 <= token_index < len(characters):
            raise MmsEditableError(
                f"line {line_index} source token index is out of range: {raw_index!r}"
            )
        character = characters[token_index]
        if not isinstance(character, dict) or not isinstance(character.get("char"), str):
            raise MmsEditableError(
                f"line {line_index} source token {token_index} is not a SUG character"
            )
        timestamps = character.get("timestamps")
        if not isinstance(timestamps, list) or not timestamps:
            raise MmsEditableError(
                f"line {line_index} source token {token_index} has no first checkpoint"
            )
        timestamps[0] = _integer_ms(
            raw_ms, f"line {line_index} source token {token_index} checkpoint"
        )
        applied += 1

    release_applied = False
    if line.get("release_override_ms") is not None:
        release_ms = _integer_ms(
            line["release_override_ms"], f"line {line_index} release"
        )
        carriers = [
            character
            for character in characters
            if isinstance(character, dict) and character.get("sentence_end_ts") is not None
        ]
        if len(carriers) > 1:
            raise MmsEditableError(
                f"SUG line {line_index} has multiple sentence_end_ts carriers"
            )
        carrier = carriers[0] if carriers else characters[-1]
        if not isinstance(carrier, dict):
            raise MmsEditableError(f"SUG line {line_index} has an invalid release carrier")
        carrier["sentence_end_ts"] = release_ms
        release_applied = True

    timeline: list[int] = []
    for token_index, character in enumerate(characters):
        if not isinstance(character, Mapping):
            raise MmsEditableError(
                f"SUG line {line_index} source token {token_index} is not an object"
            )
        timestamps = character.get("timestamps", [])
        if not isinstance(timestamps, list):
            raise MmsEditableError(
                f"SUG line {line_index} source token {token_index} timestamps is not a list"
            )
        timeline.extend(
            _integer_ms(value, f"line {line_index} source token {token_index} timestamp")
            for value in timestamps
        )
    if any(current < previous for previous, current in zip(timeline, timeline[1:])):
        raise MmsEditableError(f"line {line_index} checkpoint timeline is not non-decreasing")
    releases = [
        _integer_ms(character["sentence_end_ts"], f"line {line_index} release")
        for character in characters
        if isinstance(character, Mapping) and character.get("sentence_end_ts") is not None
    ]
    if releases and timeline and releases[-1] < timeline[-1]:
        raise MmsEditableError(f"line {line_index} release precedes its last checkpoint")
    return applied, release_applied


def _assert_only_timing_and_media_changed(
    canonical: Mapping[str, Any], companion: Mapping[str, Any]
) -> None:
    """Reject any companion drift outside the explicitly editable fields."""

    normalized = copy.deepcopy(companion)
    if "media_path" in canonical:
        normalized["media_path"] = copy.deepcopy(canonical["media_path"])
    else:
        normalized.pop("media_path", None)

    canonical_sentences = canonical.get("sentences")
    companion_sentences = normalized.get("sentences")
    if not isinstance(canonical_sentences, list) or not isinstance(
        companion_sentences, list
    ):
        raise MmsEditableError("canonical and companion SUG sentences must be lists")
    if len(canonical_sentences) != len(companion_sentences):
        raise MmsEditableError("editable MMS companion changed the sentence count")

    for line_index, (canonical_sentence, companion_sentence) in enumerate(
        zip(canonical_sentences, companion_sentences, strict=True)
    ):
        if not isinstance(canonical_sentence, Mapping) or not isinstance(
            companion_sentence, dict
        ):
            raise MmsEditableError(f"SUG line {line_index} must remain an object")
        canonical_characters = canonical_sentence.get("characters")
        companion_characters = companion_sentence.get("characters")
        if not isinstance(canonical_characters, list) or not isinstance(
            companion_characters, list
        ):
            raise MmsEditableError(
                f"SUG line {line_index} characters must remain a list"
            )
        if len(canonical_characters) != len(companion_characters):
            raise MmsEditableError(
                f"editable MMS companion changed line {line_index} token count"
            )
        for token_index, (canonical_character, companion_character) in enumerate(
            zip(canonical_characters, companion_characters, strict=True)
        ):
            if not isinstance(canonical_character, Mapping) or not isinstance(
                companion_character, dict
            ):
                raise MmsEditableError(
                    f"SUG line {line_index} token {token_index} must remain an object"
                )
            canonical_timestamps = canonical_character.get("timestamps")
            companion_timestamps = companion_character.get("timestamps")
            if isinstance(canonical_timestamps, list) and canonical_timestamps:
                if (
                    not isinstance(companion_timestamps, list)
                    or not companion_timestamps
                ):
                    raise MmsEditableError(
                        f"SUG line {line_index} token {token_index} lost its timestamps"
                    )
                companion_timestamps[0] = copy.deepcopy(canonical_timestamps[0])
            if "sentence_end_ts" in canonical_character:
                companion_character["sentence_end_ts"] = copy.deepcopy(
                    canonical_character["sentence_end_ts"]
                )
            else:
                companion_character.pop("sentence_end_ts", None)

    if normalized != canonical:
        raise MmsEditableError(
            "editable MMS companion changed fields outside timestamp[0], "
            "sentence_end_ts, or media_path"
        )


def _companion_review_sidecar(
    canonical_sidecar: Mapping[str, Any],
    canonical: Mapping[str, Any],
    companion: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebind canonical ruby provenance to the timing-adjusted companion."""

    sidecar = copy.deepcopy(dict(canonical_sidecar))
    records = sidecar.get("records")
    if not isinstance(records, list):
        raise MmsEditableError("canonical ruby review sidecar records must be a list")
    canonical_hash = sug_hash(canonical)
    if sidecar.get("sug_hash_after") != canonical_hash:
        raise MmsEditableError(
            "canonical ruby review sidecar sug_hash_after is stale for the canonical SUG"
        )

    sentences = canonical.get("sentences", [])
    sentence_indices = {
        sentence_id(sentence, f"sentence:{sentence_index}"): sentence_index
        for sentence_index, sentence in enumerate(sentences)
    }
    for record_index, record in enumerate(records):
        if not isinstance(record, dict):
            raise MmsEditableError(
                f"canonical ruby review sidecar record {record_index} must be an object"
            )
        sid = str(record.get("sentence_id", ""))
        try:
            start = int(record.get("start", -1))
            end = int(record.get("end", -1))
        except (TypeError, ValueError) as error:
            raise MmsEditableError(
                f"canonical ruby review sidecar record {record_index} has invalid span coordinates"
            ) from error
        sentence_index = sentence_indices.get(sid)
        characters = (
            sentences[sentence_index].get("characters", [])
            if sentence_index is not None and isinstance(sentences[sentence_index], Mapping)
            else []
        )
        if sentence_index is None or not 0 <= start < end <= len(characters):
            raise MmsEditableError(
                f"canonical ruby review sidecar record {record_index} does not match a canonical SUG span"
            )
        canonical_span_hash = span_hash(canonical, sentence_index, start, end)
        if record.get("after_hash") != canonical_span_hash:
            raise MmsEditableError(
                f"canonical ruby review sidecar record {record_index} after_hash is stale"
            )
        record["after_hash"] = span_hash(companion, sentence_index, start, end)

    sidecar["sug_hash_after"] = sug_hash(companion)
    return sidecar


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _recover_interrupted_publish(
    *,
    state_path: Path,
    destination: Path,
    destination_sidecar: Path,
    companion_bytes: bytes,
    sidecar_bytes: bytes | None,
) -> bool:
    """Recover a journaled two-file publish; return true for an already complete pair."""

    if not state_path.exists():
        destination_exists = destination.exists()
        sidecar_exists = destination_sidecar.exists()
        if destination_exists and sidecar_exists:
            raise FileExistsError(f"editable MMS companion already exists: {destination}")
        orphan_path = destination if destination_exists else destination_sidecar
        orphan_bytes = companion_bytes if destination_exists else sidecar_bytes
        if destination_exists or sidecar_exists:
            if orphan_bytes is None or _sha256(orphan_path.read_bytes()) != _sha256(
                orphan_bytes
            ):
                raise MmsEditableError(
                    f"untracked editable MMS publish orphan has unexpected content: {orphan_path}"
                )
            orphan_path.unlink()
        return False

    expected_state = {
        "schema": 1,
        "destination": destination.name,
        "destination_sha256": _sha256(companion_bytes),
        "sidecar": destination_sidecar.name if sidecar_bytes is not None else None,
        "sidecar_sha256": _sha256(sidecar_bytes) if sidecar_bytes is not None else None,
    }
    state = _load_object(state_path, "editable MMS publish state")
    if state != expected_state:
        raise MmsEditableError(
            "interrupted editable MMS publish state does not match the current inputs"
        )

    for path, expected_bytes in (
        (destination, companion_bytes),
        (destination_sidecar, sidecar_bytes),
    ):
        if expected_bytes is None:
            if path.exists():
                raise MmsEditableError(
                    f"interrupted editable MMS publish has an unexpected file: {path}"
                )
        elif path.exists() and _sha256(path.read_bytes()) != _sha256(expected_bytes):
            raise MmsEditableError(
                f"interrupted editable MMS publish file content changed: {path}"
            )

    pair_complete = destination.exists() and (
        sidecar_bytes is None or destination_sidecar.exists()
    )
    if pair_complete:
        state_path.unlink()
        return True

    destination.unlink(missing_ok=True)
    destination_sidecar.unlink(missing_ok=True)
    state_path.unlink()
    return False


def create_mms_editable_companion(
    *,
    canonical_sug: Path,
    audio: Path,
    build_dir: Path,
    song_id: str,
    overrides: Mapping[str, Any],
) -> Path:
    """Recoverably create a SUG/sidecar pair without overwriting a complete pair.

    A fsynced publish record precedes both hard links. A retry validates recorded
    hashes before completing or removing an interrupted partial publication.
    """

    canonical = canonical_sug.expanduser().resolve()
    selected_audio = audio.expanduser().resolve()
    destination_dir = build_dir.expanduser().resolve()
    destination = destination_dir / f"{canonical.stem}.mms-editable.sug"
    canonical_sidecar = canonical.with_suffix(".ruby-review.json")
    destination_sidecar = destination.with_suffix(".ruby-review.json")
    if not selected_audio.is_file():
        raise MmsEditableError(f"selected audio is missing: {selected_audio}")

    original_bytes = canonical.read_bytes()
    canonical_document = _load_object(canonical, "canonical SUG")
    companion = copy.deepcopy(canonical_document)
    sentences = companion.get("sentences")
    if not isinstance(sentences, list):
        raise MmsEditableError("canonical SUG sentences must be a list")
    lines = _line_overrides(overrides, song_id)
    for raw_index, raw_line in lines.items():
        try:
            line_index = int(raw_index)
        except (TypeError, ValueError) as error:
            raise MmsEditableError(f"invalid override line index: {raw_index!r}") from error
        if str(line_index) != str(raw_index) or not 0 <= line_index < len(sentences):
            raise MmsEditableError(f"override line index is out of range: {raw_index!r}")
        if not isinstance(raw_line, Mapping):
            raise MmsEditableError(f"override line {line_index} must be an object")
        sentence = sentences[line_index]
        if not isinstance(sentence, dict):
            raise MmsEditableError(f"SUG line {line_index} must be an object")
        _apply_line(sentence, raw_line, line_index)

    relative_audio = os.path.relpath(selected_audio, start=destination.parent)
    companion["media_path"] = Path(relative_audio).as_posix()
    _assert_only_timing_and_media_changed(canonical_document, companion)
    canonical_sidecar_bytes = (
        canonical_sidecar.read_bytes() if canonical_sidecar.is_file() else None
    )
    companion_sidecar = (
        _companion_review_sidecar(
            load_review_sidecar(canonical_sidecar), canonical_document, companion
        )
        if canonical_sidecar_bytes is not None
        else None
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    companion_bytes = _json_bytes(companion)
    sidecar_bytes = _json_bytes(companion_sidecar) if companion_sidecar is not None else None
    publish_state = destination.with_name(f".{destination.name}.publish.json")
    if _recover_interrupted_publish(
        state_path=publish_state,
        destination=destination,
        destination_sidecar=destination_sidecar,
        companion_bytes=companion_bytes,
        sidecar_bytes=sidecar_bytes,
    ):
        return destination

    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    sidecar_temporary = destination_sidecar.with_name(
        f".{destination_sidecar.name}.{uuid.uuid4().hex}.tmp"
    )
    state_temporary = publish_state.with_name(
        f".{publish_state.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("xb") as handle:
            handle.write(companion_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        SugProjectParser.load(str(temporary))
        extras = SugProjectParser.load_extras(str(temporary))
        if extras.get("media_path") != companion["media_path"]:
            raise MmsEditableError("editable MMS companion media_path did not round-trip")
        if companion_sidecar is not None:
            with sidecar_temporary.open("xb") as handle:
                handle.write(sidecar_bytes or b"")
                handle.flush()
                os.fsync(handle.fileno())
            load_review_sidecar(sidecar_temporary)

        publish_payload = {
            "schema": 1,
            "destination": destination.name,
            "destination_sha256": _sha256(companion_bytes),
            "sidecar": destination_sidecar.name if sidecar_bytes is not None else None,
            "sidecar_sha256": _sha256(sidecar_bytes) if sidecar_bytes is not None else None,
        }
        with state_temporary.open("xb") as handle:
            handle.write(_json_bytes(publish_payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(state_temporary, publish_state)
        if companion_sidecar is not None:
            os.link(sidecar_temporary, destination_sidecar)
        os.link(temporary, destination)
        publish_state.unlink()
    finally:
        temporary.unlink(missing_ok=True)
        sidecar_temporary.unlink(missing_ok=True)
        state_temporary.unlink(missing_ok=True)

    if canonical.read_bytes() != original_bytes:
        destination.unlink(missing_ok=True)
        destination_sidecar.unlink(missing_ok=True)
        publish_state.unlink(missing_ok=True)
        raise MmsEditableError("canonical SUG bytes changed while creating companion")
    if (
        canonical_sidecar_bytes is not None
        and canonical_sidecar.read_bytes() != canonical_sidecar_bytes
    ):
        destination.unlink(missing_ok=True)
        destination_sidecar.unlink(missing_ok=True)
        publish_state.unlink(missing_ok=True)
        raise MmsEditableError(
            "canonical ruby review sidecar bytes changed while creating companion"
        )
    return destination
