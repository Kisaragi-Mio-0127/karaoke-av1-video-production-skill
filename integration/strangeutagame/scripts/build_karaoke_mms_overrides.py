"""Freeze dual-audio MMS character alignment into reviewed karaoke overrides.

The expensive MMS forced-alignment audit is retained as a source artifact.  This
script turns that audit into deterministic display-character timestamps while
retaining the earlier stable-ts value whenever an MMS unit is not trustworthy
enough to replace it.  Song selection is explicit so a reviewed subset can be
rebuilt without replacing unrelated dispositions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.karaoke_album import (
    DEFAULT_MANIFEST_PATH,
    load_album_manifest,
    sha256_file,
)
from scripts.karaoke_language import (
    DEFAULT_LANGUAGE,
    language_identity,
    normalize_language,
)
from scripts.karaoke_timing import (
    _MORA_JOINING_SMALL_KANA,
    apply_lyric_corrections,
    make_lyric_lines,
    parse_lrc,
)

MAX_DUAL_AUDIO_DELTA_MS = 180
MIN_VOCAL_SCORE = 0.15
MIN_MIX_SCORE = 0.05
MAX_RELEASE_END_DELTA_MS = 180
MIN_RELEASE_EXTENSION_MS = 40
RELEASE_PADDING_MS = 120
RELEASE_CROP_BOUNDARY_GUARD_MS = 40
MACHINE_REVIEW_STATUS = "dual-audio-machine-reviewed"
UNRESOLVED_REVIEW_STATUS = "unresolved"
REQUIRED_RECOGNITION_AUDIO_KINDS = ("stem", "mix")
RECOGNITION_DISPOSITIONS = frozenset({"support", "veto", "unresolved"})
MACHINE_ACCEPTED_DISPOSITIONS = frozenset(
    {"accepted-threshold", "inherited-accepted-threshold"}
)
# Album-specific review exceptions belong in private evidence, not in the
# reusable integration. Public builds therefore apply only the generic gates.
EXPLICIT_LOW_SCORE_ACCEPTS: set[tuple[str, int, int]] = set()
EXPLICIT_CANDIDATE_LANES: dict[tuple[str, int, int], str] = {}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _accepted(song_id: str, line_index: int, item: dict[str, Any]) -> bool:
    key = (song_id, line_index, int(item["character_index"]))
    if item.get("alignment_disposition") == "stable-ts-retained-ascii":
        return False
    if key in EXPLICIT_CANDIDATE_LANES:
        return True
    if abs(int(item["vocal_minus_mix_ms"])) > MAX_DUAL_AUDIO_DELTA_MS:
        return False
    if key in EXPLICIT_LOW_SCORE_ACCEPTS:
        return True
    return bool(
        float(item["vocal_score"]) >= MIN_VOCAL_SCORE
        and float(item["mix_score"]) >= MIN_MIX_SCORE
    )


def _candidate_threshold_reasons(item: Mapping[str, Any]) -> list[str]:
    """Explain why one MMS A/B candidate is not machine-reviewable.

    This deliberately does not apply the historical explicit exceptions.  Those
    exceptions may still select a useful display onset, but a low score or a
    large A/B delta must remain visible as unresolved evidence.
    """

    reasons: list[str] = []
    if item.get("alignment_disposition") == "stable-ts-retained-ascii":
        reasons.append("stable-ts-retained-ascii")
    required = ("vocal_mms_ms", "mix_mms_ms", "vocal_minus_mix_ms")
    missing = [key for key in required if item.get(key) is None]
    if missing:
        reasons.append("missing-dual-audio-fields:" + ",".join(missing))
    try:
        if abs(float(item["vocal_minus_mix_ms"])) > MAX_DUAL_AUDIO_DELTA_MS:
            reasons.append("large-vocal-mix-delta")
    except (KeyError, TypeError, ValueError):
        reasons.append("invalid-vocal-mix-delta")
    for key, threshold, label in (
        ("vocal_score", MIN_VOCAL_SCORE, "vocal"),
        ("mix_score", MIN_MIX_SCORE, "mix"),
    ):
        try:
            if float(item[key]) < threshold:
                reasons.append(f"low-{label}-confidence")
        except (KeyError, TypeError, ValueError):
            reasons.append(f"missing-{label}-confidence")
    return list(dict.fromkeys(reasons))


def _candidate_passes_machine_threshold(item: Mapping[str, Any]) -> bool:
    """Return true only for an actual, complete, normal-confidence A/B match."""

    return not _candidate_threshold_reasons(item)


def _recognition_sequence(value: Any, *, label: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (Mapping, str)):
        return [value]
    if not isinstance(value, Sequence):
        raise ValueError(f"{label} must be one value or a sequence")
    return list(value)


def _recognition_metadata(
    value: Any,
    *,
    count: int,
    label: str,
) -> list[str | None]:
    values = _recognition_sequence(value, label=label)
    if not values:
        return [None] * count
    if len(values) != count:
        raise ValueError(f"{label} count must match recognition audit count")
    return [str(item) if item is not None else None for item in values]


def _audit_song_audio(
    report: Mapping[str, Any],
    song_id: str,
    audio_kind: str,
) -> dict[str, Any]:
    entries = report.get("recognition_audits")
    if isinstance(entries, list):
        matches = [
            item
            for item in entries
            if isinstance(item, Mapping) and str(item.get("song_id")) == song_id
        ]
        if len(matches) != 1:
            raise ValueError(
                f"recognition audit {audio_kind} must contain one audio record for song {song_id}"
            )
        entry = matches[0]
        entry_kind = str(entry.get("audio_kind", audio_kind)).strip().lower()
        if entry_kind != audio_kind:
            raise ValueError(
                f"recognition audit audio kind mismatch for song {song_id}: "
                f"{entry_kind} != {audio_kind}"
            )
        return {
            "song_id": song_id,
            "audio_path": entry.get("audio_path"),
            "audio_sha256": entry.get("audio_sha256"),
            "model": entry.get("model", report.get("model")),
            "model_path": entry.get("model_path", report.get("model_path")),
            "model_sha256": entry.get("model_sha256", report.get("model_sha256")),
            "recognized_token_count": entry.get("recognized_token_count"),
            "transcription_cache_key": entry.get("transcription_cache_key"),
        }
    audio = report.get("audio")
    if not isinstance(audio, Mapping):
        raise ValueError(f"recognition audit {audio_kind} is missing audio provenance")
    report_song_id = report.get("song_id")
    if report_song_id is not None and str(report_song_id) != song_id:
        raise ValueError(
            f"recognition audit {audio_kind} belongs to song {report_song_id}, not {song_id}"
        )
    cache = report.get("cache")
    return {
        "song_id": song_id,
        "audio_path": audio.get("path"),
        "audio_sha256": audio.get("sha256"),
        "model": report.get("model"),
        "model_path": report.get("model_path"),
        "model_sha256": report.get("model_sha256"),
        "recognized_token_count": report.get("recognized_token_count"),
        "transcription_cache_key": (
            cache.get("key") if isinstance(cache, Mapping) else None
        ),
    }


def _aggregate_recognition_disposition(
    per_lane: Mapping[str, str],
) -> str:
    if any(value == "veto" for value in per_lane.values()):
        return "veto"
    if all(per_lane.get(kind) == "support" for kind in REQUIRED_RECOGNITION_AUDIO_KINDS):
        return "support"
    return "unresolved"


def _current_recognition_lyrics_sha256(
    song_id: str,
    language: str,
    line_windows: Mapping[tuple[str, int], tuple[int, int]],
    line_texts: Mapping[tuple[str, int], str],
) -> str:
    lines = [
        {
            "line_index": line_index,
            "text": line_texts[(song_id, line_index)],
            "start_ms": line_windows[(song_id, line_index)][0],
            "end_ms": line_windows[(song_id, line_index)][1],
        }
        for candidate_song_id, line_index in sorted(line_texts)
        if candidate_song_id == song_id
    ]
    payload = json.dumps(
        {"language": normalize_language(language), "lines": lines},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _prepare_recognition_evidence(
    recognition_audit: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
    *,
    recognition_audit_relative_path: str | Sequence[str] | None,
    recognition_audit_sha256: str | Sequence[str] | None,
    target_song_ids: tuple[str, ...],
    line_windows: Mapping[tuple[str, int], tuple[int, int]],
    line_texts: Mapping[tuple[str, int], str],
    allow_single_lane_review_only: bool,
) -> tuple[
    dict[tuple[str, int], dict[str, str]],
    list[dict[str, Any]],
    list[str],
]:
    reports = _recognition_sequence(recognition_audit, label="recognition_audit")
    if not reports:
        return {}, [], []
    paths = _recognition_metadata(
        recognition_audit_relative_path,
        count=len(reports),
        label="recognition_audit_relative_path",
    )
    hashes = _recognition_metadata(
        recognition_audit_sha256,
        count=len(reports),
        label="recognition_audit_sha256",
    )
    lanes: dict[str, Mapping[str, Any]] = {}
    provenance: list[dict[str, Any]] = []
    per_line: dict[tuple[str, int], dict[str, str]] = {
        key: {} for key in line_texts if key[0] in target_song_ids
    }
    for report_index, raw_report in enumerate(reports):
        if not isinstance(raw_report, Mapping):
            raise ValueError("each recognition audit must be a JSON object")
        audio_kind = str(raw_report.get("audio_kind", "")).strip().lower()
        if audio_kind not in REQUIRED_RECOGNITION_AUDIO_KINDS:
            raise ValueError("recognition audit audio_kind must be stem or mix")
        if audio_kind in lanes:
            raise ValueError(f"duplicate recognition audit audio_kind: {audio_kind}")
        lanes[audio_kind] = raw_report
        report_path = paths[report_index]
        report_hash = hashes[report_index]
        if not report_path or not report_hash:
            raise ValueError(
                f"recognition audit {audio_kind} requires report path and SHA-256"
            )
        songs = {
            str(song["song_id"]): song
            for song in raw_report.get("songs", [])
            if isinstance(song, Mapping) and song.get("song_id") is not None
        }
        if not set(target_song_ids) <= set(songs):
            raise ValueError(
                f"recognition audit {audio_kind} is missing a requested song"
            )
        audio_records: list[dict[str, Any]] = []
        for song_id in target_song_ids:
            song = songs[song_id]
            expected_keys = {
                key for key in line_texts if key[0] == song_id
            }
            audit_lines = {
                (song_id, int(line["line_index"])): line
                for line in song.get("lines", [])
                if isinstance(line, Mapping) and line.get("line_index") is not None
            }
            if set(audit_lines) != expected_keys:
                raise ValueError(
                    f"recognition audit {audio_kind} lines are stale for song {song_id}"
                )
            for key, line in audit_lines.items():
                expected_start_ms, expected_end_ms = line_windows[key]
                window_start_ms = line.get("window_start_ms", line.get("start_ms"))
                window_end_ms = line.get("window_end_ms", line.get("end_ms"))
                if str(line.get("text") or "") != line_texts[key]:
                    raise ValueError(
                        f"recognition audit {audio_kind} lyrics are stale for "
                        f"song {song_id} line {key[1]}"
                    )
                if (
                    int(window_start_ms) != expected_start_ms
                    or int(window_end_ms) != expected_end_ms
                ):
                    raise ValueError(
                        f"recognition audit {audio_kind} window is stale for "
                        f"song {song_id} line {key[1]}"
                    )
                value = str(
                    line.get("disposition", line.get("status", "unresolved"))
                ).strip().lower()
                if value not in RECOGNITION_DISPOSITIONS or line.get("gate_ok") is False:
                    value = "unresolved"
                per_line[key][audio_kind] = value
            reported_lyrics_hash = raw_report.get("lyrics_sha256")
            if reported_lyrics_hash is not None and len(target_song_ids) == 1:
                report_language = raw_report.get("language", song.get("language"))
                if not report_language or str(reported_lyrics_hash) != (
                    _current_recognition_lyrics_sha256(
                        song_id,
                        str(report_language),
                        line_windows,
                        line_texts,
                    )
                ):
                    raise ValueError(
                        f"recognition audit {audio_kind} lyrics hash is stale "
                        f"for song {song_id}"
                    )
            audio_record = _audit_song_audio(raw_report, song_id, audio_kind)
            if (
                not audio_record.get("audio_path")
                or not audio_record.get("audio_sha256")
                or not audio_record.get("model")
            ):
                raise ValueError(
                    f"recognition audit {audio_kind} lacks audio/model provenance "
                    f"for song {song_id}"
                )
            audio_records.append(audio_record)
        provenance.append(
            {
                "path": report_path,
                "sha256": report_hash,
                "audio_kind": audio_kind,
                "model": raw_report.get("model"),
                "model_path": raw_report.get("model_path"),
                "model_sha256": raw_report.get("model_sha256"),
                "lyrics_sha256": raw_report.get("lyrics_sha256"),
                "audio_records": audio_records,
            }
        )
    missing_lanes = [
        kind for kind in REQUIRED_RECOGNITION_AUDIO_KINDS if kind not in lanes
    ]
    if missing_lanes and not allow_single_lane_review_only:
        raise ValueError(
            "recognition audits require both stem and mix; missing: "
            + ", ".join(missing_lanes)
        )
    return per_line, provenance, missing_lanes


def _last_unit(units: Any) -> dict[str, Any] | None:
    if not isinstance(units, list):
        return None
    candidates = [
        item
        for item in units
        if isinstance(item, dict) and isinstance(item.get("end_ms"), (int, float))
    ]
    return max(candidates, key=lambda item: int(item["end_ms"]), default=None)


def recommended_release_override(line: dict[str, Any]) -> dict[str, Any] | None:
    """Recommend a later release only when both audio lanes support the tail."""

    disposition = release_tail_disposition(line)
    if disposition.get("status") != "accepted-dual-audio-tail":
        return None
    return disposition


def release_tail_disposition(line: dict[str, Any]) -> dict[str, Any]:
    """Record why a sentence tail was accepted or conservatively retained."""

    vocal = _last_unit(line.get("units"))
    mix = _last_unit(line.get("mix_units"))
    if vocal is None or mix is None:
        return {
            "status": "rejected-missing-dual-audio-tail",
            "policy": "dual-audio-last-unit-end-plus-padding",
        }
    release_ms = int(line["sug_release_ms"])
    crop_end_ms = int(line["crop_end_ms"])
    vocal_end_ms = int(vocal["end_ms"])
    mix_end_ms = int(mix["end_ms"])
    vocal_minus_mix_end_ms = vocal_end_ms - mix_end_ms
    evidence = {
        "previous_release_ms": release_ms,
        "vocal_last_unit": str(vocal.get("unit") or ""),
        "vocal_end_ms": vocal_end_ms,
        "vocal_score": float(vocal.get("score", 0.0)),
        "mix_last_unit": str(mix.get("unit") or ""),
        "mix_end_ms": mix_end_ms,
        "mix_score": float(mix.get("score", 0.0)),
        "vocal_minus_mix_end_ms": vocal_minus_mix_end_ms,
        "crop_end_ms": crop_end_ms,
        "policy": "dual-audio-last-unit-end-plus-padding",
    }
    if abs(vocal_minus_mix_end_ms) > MAX_RELEASE_END_DELTA_MS:
        return {**evidence, "status": "rejected-dual-audio-end-disagreement"}
    if (
        float(vocal.get("score", 0.0)) < MIN_VOCAL_SCORE
        or float(mix.get("score", 0.0)) < MIN_MIX_SCORE
    ):
        return {**evidence, "status": "rejected-low-confidence-tail"}
    if max(vocal_end_ms, mix_end_ms) > (crop_end_ms - RELEASE_CROP_BOUNDARY_GUARD_MS):
        return {**evidence, "status": "rejected-crop-boundary-tail"}
    if min(vocal_end_ms, mix_end_ms) - release_ms < MIN_RELEASE_EXTENSION_MS:
        return {**evidence, "status": "retained-sug-release-no-late-dual-tail"}
    override_ms = min(
        crop_end_ms,
        max(vocal_end_ms, mix_end_ms) + RELEASE_PADDING_MS,
    )
    return {
        **evidence,
        "status": "accepted-dual-audio-tail",
        "release_override_ms": override_ms,
        "extension_ms": override_ms - release_ms,
    }


def build_overrides(
    audit: dict[str, Any],
    existing: dict[str, Any],
    *,
    audit_relative_path: str,
    line_windows: dict[tuple[str, int], tuple[int, int]],
    line_texts: dict[tuple[str, int], str],
    target_song_ids: tuple[str, ...],
    release_target_song_ids: tuple[str, ...] = (),
    audit_sha256: str | None = None,
    recognition_audit: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    recognition_audit_relative_path: str | Sequence[str] | None = None,
    recognition_audit_sha256: str | Sequence[str] | None = None,
    allow_single_recognition_lane_review_only: bool = False,
) -> dict[str, Any]:
    if not target_song_ids or len(set(target_song_ids)) != len(target_song_ids):
        raise ValueError("target_song_ids must be non-empty and unique")
    songs = dict(existing.get("songs") or {})
    all_report_songs = {
        str(song["song_id"]): song
        for song in audit.get("songs", [])
        if isinstance(song, dict) and song.get("song_id") is not None
    }
    required_song_ids = set(target_song_ids) | set(release_target_song_ids)
    if not required_song_ids <= set(all_report_songs):
        raise ValueError("MMS audit does not contain the exact requested song set")
    report_songs = {song_id: all_report_songs[song_id] for song_id in target_song_ids}
    (
        recognition_lines,
        recognition_provenance,
        missing_recognition_lanes,
    ) = _prepare_recognition_evidence(
        recognition_audit,
        recognition_audit_relative_path=recognition_audit_relative_path,
        recognition_audit_sha256=recognition_audit_sha256,
        target_song_ids=target_song_ids,
        line_windows=line_windows,
        line_texts=line_texts,
        allow_single_lane_review_only=allow_single_recognition_lane_review_only,
    )
    recognition_required = bool(recognition_provenance)

    for song_id in target_song_ids:
        existing_song = dict(songs.get(song_id) or {})
        song_language = normalize_language(
            report_songs[song_id].get("language"),
            default=existing_song.get("language", DEFAULT_LANGUAGE),
        )
        existing_lines = dict(existing_song.get("lines") or {})
        line_overrides: dict[str, Any] = {}
        audit_line_indices = {
            int(line["line_index"]) for line in report_songs[song_id]["lines"]
        }
        expected_line_indices = {
            line_index
            for candidate_song_id, line_index in line_texts
            if candidate_song_id == song_id
        }
        if audit_line_indices != expected_line_indices:
            raise ValueError(
                f"song {song_id} audit lines do not match corrected lyric lines"
            )
        for line in report_songs[song_id]["lines"]:
            line_index = int(line["line_index"])
            try:
                line_start_ms, line_end_ms = line_windows[(song_id, line_index)]
            except KeyError as error:
                raise ValueError(
                    f"missing LRC window for song {song_id} line {line_index}"
                ) from error
            corrected_text = line_texts[(song_id, line_index)]
            if str(line.get("text") or "") != corrected_text:
                raise ValueError(
                    f"song {song_id} line {line_index} audit text is stale"
                )
            comparisons = list(line.get("dual_audio_comparisons") or [])
            release_recommendation = recommended_release_override(line)
            character_window_end_ms = (
                int(release_recommendation["release_override_ms"])
                if release_recommendation is not None
                else line_end_ms
            )
            comparison_indices = [int(item["character_index"]) for item in comparisons]
            if len(comparison_indices) != len(set(comparison_indices)):
                raise ValueError(
                    f"song {song_id} line {line_index} has duplicate character indices"
                )
            expected_indices = [
                int(index)
                for index in line.get(
                    "timed_character_indices",
                    [
                        item["character_index"]
                        for item in line.get("comparisons", comparisons)
                    ],
                )
            ]
            expected_set = set(expected_indices)
            comparison_set = set(comparison_indices)
            comparison_positions = [
                expected_indices.index(index) for index in comparison_indices
            ]
            if (
                len(expected_set) != len(expected_indices)
                or any(index not in expected_set for index in comparison_indices)
                or comparison_positions != sorted(comparison_positions)
            ):
                raise ValueError(
                    f"song {song_id} line {line_index} has stale dual-audio indices"
                )
            uncovered_indices = [
                index for index in expected_indices if index not in comparison_set
            ]
            final_values: dict[str, int] = {}
            character_dispositions: dict[str, str] = {}
            candidate_dispositions: dict[str, str] = {}
            candidate_failure_reasons: dict[str, list[str]] = {}
            unresolved_indices: set[int] = set(uncovered_indices)
            accepted_count = 0
            retained_count = 0
            clamped_count = 0
            window_clamped_count = 0
            previous_ms = -1
            movements: list[dict[str, Any]] = []
            recognition_per_lane = dict(
                recognition_lines.get((song_id, line_index), {})
            )
            recognition_disposition = (
                _aggregate_recognition_disposition(recognition_per_lane)
                if recognition_required
                else None
            )
            recognition_veto = recognition_disposition == "veto"
            recognition_gate_pass = (
                not recognition_required or recognition_disposition == "support"
            )
            for item in comparisons:
                character_index = int(item["character_index"])
                if not 0 <= character_index < len(corrected_text):
                    raise ValueError(
                        f"song {song_id} line {line_index} character index is out of range"
                    )
                if str(item["character"]) != corrected_text[character_index]:
                    raise ValueError(
                        f"song {song_id} line {line_index} character identity mismatch"
                    )
                current_ms = int(item["current_ms"])
                small_kana_source_index = character_index - 1
                while (
                    small_kana_source_index >= 0
                    and corrected_text[small_kana_source_index]
                    in _MORA_JOINING_SMALL_KANA
                ):
                    small_kana_source_index -= 1
                inherit_small_kana = (
                    corrected_text[character_index] in _MORA_JOINING_SMALL_KANA
                    and str(small_kana_source_index) in final_values
                )
                threshold_reasons = _candidate_threshold_reasons(item)
                threshold_pass = not threshold_reasons
                if inherit_small_kana:
                    source_disposition = candidate_dispositions.get(
                        str(small_kana_source_index), ""
                    )
                    inherited_pass = source_disposition in MACHINE_ACCEPTED_DISPOSITIONS
                    use_mms = True and not recognition_veto
                    candidate_ms = final_values[str(small_kana_source_index)]
                    character_dispositions[str(character_index)] = (
                        f"mora-joining-small-kana-inherits-{small_kana_source_index}"
                    )
                    if inherited_pass and not recognition_veto:
                        candidate_dispositions[str(character_index)] = (
                            "inherited-accepted-threshold"
                        )
                    else:
                        candidate_dispositions[str(character_index)] = (
                            "inherited-unresolved"
                        )
                        unresolved_indices.add(character_index)
                        candidate_failure_reasons[str(character_index)] = (
                            ["recognition-veto"] if recognition_veto else [
                                "inherited-from-unresolved-candidate"
                            ]
                        )
                else:
                    try:
                        use_mms = _accepted(song_id, line_index, item)
                    except (KeyError, TypeError, ValueError):
                        use_mms = False
                    if recognition_veto:
                        use_mms = False
                    candidate_lane = EXPLICIT_CANDIDATE_LANES.get(
                        (song_id, line_index, character_index), "vocal"
                    )
                    candidate_ms = (
                        int(item[f"{candidate_lane}_mms_ms"])
                        if use_mms
                        else current_ms
                    )
                    if threshold_pass and not recognition_veto:
                        candidate_dispositions[str(character_index)] = (
                            "accepted-threshold"
                        )
                    else:
                        if recognition_veto:
                            reasons = ["recognition-veto"]
                        else:
                            reasons = threshold_reasons or [
                                "candidate-not-machine-reviewable"
                            ]
                        candidate_dispositions[str(character_index)] = (
                            "selected-explicit-but-unresolved"
                            if use_mms
                            else "stable-ts-retained-unresolved"
                        )
                        candidate_failure_reasons[str(character_index)] = reasons
                        unresolved_indices.add(character_index)
                bounded_candidate_ms = min(
                    character_window_end_ms,
                    max(line_start_ms, candidate_ms),
                )
                if bounded_candidate_ms != candidate_ms:
                    window_clamped_count += 1
                candidate_ms = bounded_candidate_ms
                final_ms = max(previous_ms, candidate_ms)
                if final_ms != candidate_ms:
                    clamped_count += 1
                previous_ms = final_ms
                final_values[str(character_index)] = final_ms
                accepted_count += int(use_mms)
                retained_count += int(not use_mms)
                if (
                    abs(final_ms - current_ms) >= 250
                    and item.get("vocal_mms_ms") is not None
                    and item.get("mix_mms_ms") is not None
                ):
                    movements.append(
                        {
                            "character_index": character_index,
                            "character": item["character"],
                            "stable_ts_ms": current_ms,
                            "mms_vocal_ms": int(item["vocal_mms_ms"]),
                            "mms_mix_ms": int(item["mix_mms_ms"]),
                            "final_ms": final_ms,
                            "delta_ms": final_ms - current_ms,
                            "vocal_score": item["vocal_score"],
                            "mix_score": item["mix_score"],
                        }
                    )
            if uncovered_indices:
                for character_index in uncovered_indices:
                    candidate_dispositions[str(character_index)] = "uncovered"
                    candidate_failure_reasons[str(character_index)] = [
                        "uncovered-dual-audio-unit"
                    ]
            if (
                recognition_required
                and recognition_disposition != "support"
                and not unresolved_indices
            ):
                unresolved_indices.update(expected_indices)
            actual_dual_audio = (
                not uncovered_indices
                and bool(comparisons)
                and all(
                    item.get("alignment_disposition") != "stable-ts-retained-ascii"
                    and all(
                        item.get(key) is not None
                        for key in (
                            "vocal_mms_ms",
                            "mix_mms_ms",
                            "vocal_minus_mix_ms",
                            "vocal_score",
                            "mix_score",
                        )
                    )
                    for item in comparisons
                )
            )
            review_gate_reasons = [
                *(
                    ["uncovered-dual-audio-unit"]
                    if uncovered_indices
                    else []
                ),
                *[
                    f"character-{index}:{reason}"
                    for index, reasons in sorted(candidate_failure_reasons.items())
                    for reason in reasons
                ],
                *(
                    [f"recognition-{recognition_disposition}"]
                    if recognition_required and recognition_disposition != "support"
                    else []
                ),
                *[
                    f"missing-recognition-lane:{lane}"
                    for lane in missing_recognition_lanes
                ],
            ]
            review_gate_reasons = list(dict.fromkeys(review_gate_reasons))
            line_reviewed = (
                actual_dual_audio
                and not unresolved_indices
                and recognition_gate_pass
                and all(
                    disposition in MACHINE_ACCEPTED_DISPOSITIONS
                    for disposition in candidate_dispositions.values()
                )
            )
            previous_line = dict(existing_lines.get(str(line_index)) or {})
            previous_evidence = list(previous_line.get("evidence") or [])
            new_evidence = [
                f"audit: {audit_relative_path}",
                f"MMS accepted characters: {accepted_count}",
                f"stable-ts retained characters: {retained_count}",
                f"LRC window clamps: {window_clamped_count}",
                f"monotonic clamps: {clamped_count}",
                f"movements >=250ms: {len(movements)}",
                f"unresolved characters: {len(unresolved_indices)}",
            ]
            line_overrides[str(line_index)] = {
                **previous_line,
                "review_status": (
                    MACHINE_REVIEW_STATUS if line_reviewed else UNRESOLVED_REVIEW_STATUS
                ),
                "reason": (
                    "Dual-audio MMS forced-alignment evidence is mapped to display "
                    "units; it is not independent phoneme recognition."
                    if line_reviewed
                    else "MMS candidate evidence is incomplete or outside the "
                    "machine-review threshold; stable-ts remains the fallback."
                ),
                "character_overrides_ms": final_values,
                "character_dispositions": character_dispositions,
                "candidate_dispositions": candidate_dispositions,
                "candidate_failure_reasons": candidate_failure_reasons,
                "unresolved_character_indices": sorted(unresolved_indices),
                "actual_dual_audio": actual_dual_audio,
                "actual_ab_evidence": actual_dual_audio,
                "review_gate": {
                    "ok": line_reviewed,
                    "actual_dual_audio": actual_dual_audio,
                    "candidate_thresholds": line_reviewed,
                    "reasons": review_gate_reasons,
                    "recognition_disposition": recognition_disposition,
                    "recognition_dispositions": recognition_per_lane,
                    "recognition_required_lanes": list(
                        REQUIRED_RECOGNITION_AUDIO_KINDS
                    ),
                    "recognition_missing_lanes": missing_recognition_lanes,
                    "recognition_review_only": bool(missing_recognition_lanes),
                },
                "evidence": list(dict.fromkeys([*previous_evidence, *new_evidence])),
                "mms_notable_movements": movements,
            }
            if (
                previous_line.get("reason")
                and previous_line["reason"] != line_overrides[str(line_index)]["reason"]
            ):
                line_overrides[str(line_index)]["prior_review_reason"] = previous_line[
                    "reason"
                ]
        songs[song_id] = {
            **existing_song,
            "language": song_language,
            "language_identity": language_identity(song_language),
            "lines": line_overrides,
        }

    for song_id in release_target_song_ids:
        existing_song = dict(songs.get(song_id) or {})
        song_language = normalize_language(
            all_report_songs[song_id].get("language"),
            default=existing_song.get("language", DEFAULT_LANGUAGE),
        )
        existing_lines = dict(existing_song.get("lines") or {})
        for line in all_report_songs[song_id].get("lines", []):
            line_index = int(line["line_index"])
            disposition = release_tail_disposition(line)
            recommendation = recommended_release_override(line)
            line_override = dict(existing_lines.get(str(line_index)) or {})
            evidence = list(line_override.get("evidence") or [])
            previous_release_evidence = line_override.get("release_evidence")
            if (
                recommendation is None
                and line_override.get("release_override_ms") is not None
                and isinstance(previous_release_evidence, dict)
            ):
                disposition = {
                    **previous_release_evidence,
                    "status": "accepted-dual-audio-tail-carried-forward",
                    "current_audit_status": disposition["status"],
                }
            line_override["release_disposition"] = disposition
            if recommendation is not None:
                evidence.append(
                    "dual-audio release extension: "
                    f"{recommendation['previous_release_ms']} -> "
                    f"{recommendation['release_override_ms']} ms"
                )
                line_override["release_override_ms"] = recommendation[
                    "release_override_ms"
                ]
                line_override["release_evidence"] = recommendation
            else:
                evidence.append(
                    f"dual-audio release disposition: {disposition['status']}"
                )
            line_override["evidence"] = list(dict.fromkeys(evidence))
            existing_lines[str(line_index)] = line_override
        songs[song_id] = {
            **existing_song,
            "language": song_language,
            "language_identity": language_identity(song_language),
            "lines": existing_lines,
        }

    previous_targets = (
        existing.get("mms_provenance", {}).get("target_song_ids", [])
        if isinstance(existing.get("mms_provenance"), dict)
        else []
    )
    all_target_song_ids = sorted(
        {str(item) for item in previous_targets}
        | set(target_song_ids)
        | set(release_target_song_ids)
    )
    language_identities = {
        song_id: language_identity(
            normalize_language(
                all_report_songs.get(song_id, {}).get("language"),
                default=songs.get(song_id, {}).get("language", DEFAULT_LANGUAGE),
            )
        )
        for song_id in all_target_song_ids
    }

    return {
        "schema_version": "karaoke-timing-overrides/v2",
        "mms_provenance": {
            "audit": audit_relative_path,
            "audit_sha256": audit_sha256,
            "model": audit.get("model"),
            "model_path": audit.get("model_path"),
            "model_sha256": audit.get("model_sha256"),
            "lyric_source_path": audit.get(
                "lyric_source_path", audit.get("netease_lyrics_path")
            ),
            "lyric_source_sha256": audit.get(
                "lyric_source_sha256", audit.get("netease_lyrics_sha256")
            ),
            "recognition_audit": (
                recognition_provenance[0]["path"]
                if len(recognition_provenance) == 1
                else None
            ),
            "recognition_audit_sha256": (
                recognition_provenance[0]["sha256"]
                if len(recognition_provenance) == 1
                else None
            ),
            "recognition_audits": recognition_provenance,
            "target_song_ids": all_target_song_ids,
            "language_codes": {
                song_id: identity["code"]
                for song_id, identity in language_identities.items()
            },
            "language_identities": language_identities,
            "policy": {
                "max_vocal_mix_delta_ms": MAX_DUAL_AUDIO_DELTA_MS,
                "minimum_vocal_score": MIN_VOCAL_SCORE,
                "minimum_mix_score": MIN_MIX_SCORE,
                "maximum_release_end_delta_ms": MAX_RELEASE_END_DELTA_MS,
                "minimum_release_extension_ms": MIN_RELEASE_EXTENSION_MS,
                "release_padding_ms": RELEASE_PADDING_MS,
                "release_crop_boundary_guard_ms": RELEASE_CROP_BOUNDARY_GUARD_MS,
                "machine_review_status": MACHINE_REVIEW_STATUS,
                "unresolved_review_status": UNRESOLVED_REVIEW_STATUS,
                "requires_actual_dual_audio": True,
                "independent_asr_is_support_or_veto_only": True,
                "required_recognition_audio_kinds": list(
                    REQUIRED_RECOGNITION_AUDIO_KINDS
                ),
                "single_recognition_lane_is_review_only": True,
                "sokuon_requires_normal_confidence": True,
                "low_score_explicit_accepts": [
                    list(item) for item in sorted(EXPLICIT_LOW_SCORE_ACCEPTS)
                ],
                "explicit_candidate_lanes": [
                    [*key, lane]
                    for key, lane in sorted(EXPLICIT_CANDIDATE_LANES.items())
                ],
                "human_reviewed": False,
            },
        },
        "gate_ok": all(
            line.get("review_status") == MACHINE_REVIEW_STATUS
            and bool(line.get("review_gate", {}).get("ok"))
            for song in songs.values()
            for line in (song.get("lines", {}) or {}).values()
            if isinstance(line, Mapping)
        ),
        "unresolved": [
            {
                "song_id": song_id,
                "lines": [
                    line_index
                    for line_index, line in (song.get("lines", {}) or {}).items()
                    if isinstance(line, Mapping)
                    and line.get("review_status") == UNRESOLVED_REVIEW_STATUS
                ],
            }
            for song_id, song in songs.items()
            if any(
                isinstance(line, Mapping)
                and line.get("review_status") == UNRESOLVED_REVIEW_STATUS
                for line in (song.get("lines", {}) or {}).values()
            )
        ],
        "songs": songs,
    }


def build_line_windows(
    album: Any,
    source: dict[str, Any],
    corrections: dict[str, Any],
    target_song_ids: tuple[str, ...],
) -> tuple[
    dict[tuple[str, int], tuple[int, int]],
    dict[tuple[str, int], str],
]:
    """Recreate the exact coarse LRC windows enforced by the timing builder."""

    tracks = {str(track.song_id): track for track in album.tracks}
    windows: dict[tuple[str, int], tuple[int, int]] = {}
    texts: dict[tuple[str, int], str] = {}
    for song_id in target_song_ids:
        track = tracks[song_id]
        raw_lrc = str(source["songs"][song_id].get("lrc") or "")
        corrected_lrc, _ = apply_lyric_corrections(
            raw_lrc,
            corrections.get("songs", {}).get(song_id, []),
        )
        lyric_lines, _ = make_lyric_lines(
            parse_lrc(corrected_lrc),
            int(track.expected_duration_ms),
        )
        windows.update(
            {
                (song_id, line.line_index): (line.start_ms, line.end_ms)
                for line in lyric_lines
            }
        )
        texts.update({(song_id, line.line_index): line.text for line in lyric_lines})
    return windows, texts


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--source",
        type=Path,
        help="frozen local/net lyrics JSON used by the audit and line windows",
    )
    parser.add_argument(
        "--allow-partial-manifest",
        action="store_true",
        help="allow an explicitly supplied manifest with fewer than five tracks",
    )
    parser.add_argument("--audit", type=Path)
    parser.add_argument(
        "--recognition-audit",
        type=Path,
        action="append",
        dest="recognition_audits",
        help="optional independent ASR report used only as support/veto evidence",
    )
    parser.add_argument(
        "--allow-single-recognition-lane-review-only",
        action="store_true",
        help=(
            "allow one stem or mix ASR report for review only; release gate "
            "always remains unresolved"
        ),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--song-id",
        action="append",
        dest="song_ids",
        help="song ID to rebuild; repeat for multiple songs (default: every song in audit)",
    )
    parser.add_argument(
        "--release-song-id",
        action="append",
        dest="release_song_ids",
        help=(
            "song ID whose dual-audio tail may extend the visual release; "
            "repeat for multiple songs (default: same set as --song-id)"
        ),
    )
    return parser


def validate_audit_source_hashes(
    audit: dict[str, Any],
    album: Any,
    manifest_path: Path,
    song_ids: tuple[str, ...],
    source_path: Path | None = None,
) -> None:
    """Reject an audit made from stale lyrics, corrections, manifest, or SUG."""

    source_dir = album.deliverable_dir / "sources"
    resolved_source = (
        Path(source_path).expanduser().resolve()
        if source_path is not None
        else (source_dir / "netease_lyrics.json").resolve()
    )
    checks = (
        ("manifest", manifest_path.resolve(), audit.get("manifest_sha256")),
        (
            "lyric source",
            resolved_source,
            audit.get("lyric_source_sha256", audit.get("netease_lyrics_sha256")),
        ),
        (
            "lyric corrections",
            source_dir / "lyric_corrections.json",
            audit.get("lyric_corrections_sha256"),
        ),
    )
    for label, path, expected_hash in checks:
        if not isinstance(expected_hash, str) or sha256_file(path) != expected_hash:
            raise ValueError(f"MMS audit {label} hash is missing or stale")

    tracks = {str(track.song_id): track for track in album.tracks}
    audit_songs = {
        str(song["song_id"]): song
        for song in audit.get("songs", [])
        if isinstance(song, dict) and song.get("song_id") is not None
    }
    for song_id in song_ids:
        song = audit_songs.get(song_id)
        if song is None:
            raise ValueError(f"MMS audit is missing song {song_id}")
        track = tracks[song_id]
        sug_path = album.deliverable_dir / "timing" / f"{track.timing_stem}.sug"
        expected_hash = song.get("sug_sha256")
        if not isinstance(expected_hash, str) or sha256_file(sug_path) != expected_hash:
            raise ValueError(f"MMS audit SUG hash is stale for song {song_id}")


def validate_recognition_audio_sources(
    recognition_audits: Sequence[Mapping[str, Any]],
    album: Any,
    song_ids: tuple[str, ...],
) -> None:
    """Reject ASR reports whose selected mix/stem audio is missing or stale."""

    tracks = {str(track.song_id): track for track in album.tracks}
    for report in recognition_audits:
        audio_kind = str(report.get("audio_kind", "")).strip().lower()
        for song_id in song_ids:
            record = _audit_song_audio(report, song_id, audio_kind)
            audio_path = Path(str(record.get("audio_path") or ""))
            if not audio_path.is_absolute():
                audio_path = album.project_root / audio_path
            audio_path = audio_path.resolve()
            expected_hash = record.get("audio_sha256")
            if (
                not audio_path.is_file()
                or not isinstance(expected_hash, str)
                or sha256_file(audio_path) != expected_hash
            ):
                raise ValueError(
                    f"recognition audit {audio_kind} audio is missing or stale "
                    f"for song {song_id}"
                )
            if audio_kind == "mix" and sha256_file(tracks[song_id].audio_path) != expected_hash:
                raise ValueError(
                    f"recognition audit mix audio is not the current manifest audio "
                    f"for song {song_id}"
                )


def main() -> int:
    args = make_parser().parse_args()
    album = load_album_manifest(
        args.manifest,
        require_five_tracks=not args.allow_partial_manifest,
    )
    source_dir = album.deliverable_dir / "sources"
    source_path = (
        args.source.expanduser().resolve()
        if args.source is not None
        else (source_dir / "netease_lyrics.json").resolve()
    )
    audit_path = (args.audit or source_dir / "mms_alignment_audit.json").resolve()
    recognition_paths = tuple(
        path.resolve() for path in (args.recognition_audits or [])
    )
    output_path = (args.output or source_dir / "timing_overrides.json").resolve()
    audit = _load(audit_path)
    recognition_audits = tuple(_load(path) for path in recognition_paths)
    audit_song_ids = tuple(str(song["song_id"]) for song in audit.get("songs", []))
    target_song_ids = tuple(args.song_ids or audit_song_ids)
    release_target_song_ids = tuple(args.release_song_ids or target_song_ids)
    manifest_song_ids = {str(track.song_id) for track in album.tracks}
    if not (set(target_song_ids) | set(release_target_song_ids)) <= manifest_song_ids:
        raise ValueError("requested song ID is not present in the album manifest")
    validate_audit_source_hashes(
        audit,
        album,
        args.manifest,
        tuple(dict.fromkeys([*target_song_ids, *release_target_song_ids])),
        source_path,
    )
    validate_recognition_audio_sources(recognition_audits, album, target_song_ids)
    existing = _load(output_path) if output_path.is_file() else {"songs": {}}
    source = _load(source_path)
    corrections = _load(source_dir / "lyric_corrections.json")
    line_windows, line_texts = build_line_windows(
        album,
        source,
        corrections,
        target_song_ids,
    )
    relative_audit = audit_path.relative_to(album.project_root).as_posix()
    result = build_overrides(
        audit,
        existing,
        audit_relative_path=relative_audit,
        line_windows=line_windows,
        line_texts=line_texts,
        target_song_ids=target_song_ids,
        release_target_song_ids=release_target_song_ids,
        audit_sha256=sha256_file(audit_path),
        recognition_audit=recognition_audits,
        recognition_audit_relative_path=tuple(
            path.relative_to(album.project_root).as_posix()
            for path in recognition_paths
        ),
        recognition_audit_sha256=tuple(
            sha256_file(path) for path in recognition_paths
        ),
        allow_single_recognition_lane_review_only=(
            args.allow_single_recognition_lane_review_only
        ),
    )
    _dump(output_path, result)
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(output_path),
                "target_song_ids": list(target_song_ids),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
