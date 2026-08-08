#!/usr/bin/env python3
"""Finalize the two-layout karaoke release with reproducible validation data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .karaoke_album import load_album_manifest
    from .karaoke_common.ffmpeg_tools import resolve_ffmpeg
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_album import load_album_manifest  # type: ignore[no-redef]
    from karaoke_common.ffmpeg_tools import resolve_ffmpeg  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("standard", "wide")


def track_record(track: Any) -> dict[str, Any]:
    """Build finalizer metadata directly from one validated manifest track."""

    return {
        "song_id": track.song_id,
        "title": track.title,
        "artist": track.artist,
        "artifact_slug": track.artifact_slug,
        "timing_stem": track.timing_stem,
        "report_stem": track.report_stem,
        "numbered_video_filename": track.numbered_video_filename,
        "audio": track.audio_path,
        "expected_cues": track.expected_cues,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(path: Path, root: Path) -> str:
    return Path(os.path.relpath(path.resolve(), root.resolve())).as_posix()


def artifact(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": relative_path(path, root),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_if_present(path: Path) -> dict[str, Any]:
    """Load an optional source without turning a missing source into a crash."""

    if not path.is_file():
        return {}
    try:
        value = read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _validate_hashed_audit_path(
    document: dict[str, Any],
    *,
    label: str,
    path_key: str,
    hash_key: str,
    project_root: Path,
) -> dict[str, Any]:
    raw_path = document.get(path_key)
    expected_sha256 = document.get(hash_key)
    if not isinstance(raw_path, str) or not raw_path:
        return {"ok": False, "label": label, "error": f"missing {path_key}"}
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    within_project = (
        path == project_root.resolve() or project_root.resolve() in path.parents
    )
    exists = within_project and path.is_file()
    actual_sha256 = sha256_file(path) if exists else None
    hash_ok = (
        isinstance(expected_sha256, str)
        and len(expected_sha256) == 64
        and actual_sha256 == expected_sha256.lower()
    )
    return {
        "ok": within_project and exists and hash_ok,
        "label": label,
        "path": relative_path(path, project_root) if within_project else str(path),
        "within_project": within_project,
        "exists": exists,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual_sha256,
        "hash_ok": hash_ok,
    }


def validate_audit_source_provenance(
    audit: Any,
    *,
    project_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Verify every immutable input recorded by the dual-audio MMS audit."""

    if not isinstance(audit, dict):
        return {"ok": False, "checks": [], "songs": {}}
    checks = [
        _validate_hashed_audit_path(
            audit,
            label="manifest",
            path_key="manifest_path",
            hash_key="manifest_sha256",
            project_root=project_root,
        ),
        _validate_hashed_audit_path(
            audit,
            label="NetEase lyrics",
            path_key="netease_lyrics_path",
            hash_key="netease_lyrics_sha256",
            project_root=project_root,
        ),
        _validate_hashed_audit_path(
            audit,
            label="lyric corrections",
            path_key="lyric_corrections_path",
            hash_key="lyric_corrections_sha256",
            project_root=project_root,
        ),
        _validate_hashed_audit_path(
            audit,
            label="MMS model",
            path_key="model_path",
            hash_key="model_sha256",
            project_root=project_root,
        ),
    ]
    song_checks: dict[str, dict[str, Any]] = {}
    for song in audit.get("songs", []):
        if not isinstance(song, dict) or song.get("song_id") is None:
            continue
        song_id = str(song["song_id"])
        vocal = _validate_hashed_audit_path(
            song,
            label=f"{song_id} isolated vocals",
            path_key="vocals_path",
            hash_key="vocals_sha256",
            project_root=project_root,
        )
        mix = _validate_hashed_audit_path(
            song,
            label=f"{song_id} original mix",
            path_key="mix_path",
            hash_key="mix_sha256",
            project_root=project_root,
        )
        song_checks[song_id] = {
            "ok": vocal["ok"] and mix["ok"],
            "isolated_vocals": vocal,
            "original_mix": mix,
            "baseline_sug_sha256_recorded": isinstance(song.get("sug_sha256"), str),
        }
    return {
        "ok": bool(song_checks)
        and all(check["ok"] for check in checks)
        and all(check["ok"] for check in song_checks.values()),
        "checks": checks,
        "songs": song_checks,
    }


def _coerce_index(value: Any) -> int | None:
    """Return a JSON line/character index without silently truncating values."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _sug_lines(document: Any) -> list[dict[str, Any]]:
    """Extract the current SUG line and character axis from a SUG JSON document."""

    if not isinstance(document, dict) or not isinstance(
        document.get("sentences"), list
    ):
        return []
    result: list[dict[str, Any]] = []
    for position, sentence in enumerate(document["sentences"]):
        if not isinstance(sentence, dict):
            result.append({"line_index": None, "text": "", "characters": []})
            continue
        line_index = _coerce_index(
            sentence.get("line_index", sentence.get("index", position))
        )
        raw_characters = sentence.get("characters")
        characters = raw_characters if isinstance(raw_characters, list) else []
        text = "".join(
            str(character.get("char", character.get("character", "")))
            for character in characters
            if isinstance(character, dict)
        )
        if not text and isinstance(sentence.get("text"), str):
            text = sentence["text"]
        result.append(
            {
                "line_index": line_index,
                "text": text,
                "characters": characters,
            }
        )
    return result


def _iter_character_records(value: Any):
    """Yield character-shaped records from either a list or an indexed mapping."""

    if isinstance(value, list):
        for position, item in enumerate(value):
            if isinstance(item, dict):
                record = dict(item)
                record.setdefault("character_index", position)
                yield record
        return
    if isinstance(value, dict):
        for raw_index, item in value.items():
            if isinstance(item, dict):
                record = dict(item)
                record.setdefault("character_index", raw_index)
                yield record
            else:
                yield {"character_index": raw_index, "disposition": item}


def _record_index(record: dict[str, Any]) -> int | None:
    return _coerce_index(record.get("character_index", record.get("index")))


def _record_character(record: dict[str, Any]) -> str | None:
    for key in ("character", "char", "text"):
        value = record.get(key)
        if isinstance(value, str):
            return value
    return None


def _explicit_retained_disposition(value: Any) -> bool:
    """Recognize only affirmative inherited/retained language as a disposition."""

    if isinstance(value, bool):
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_explicit_retained_disposition(item) for item in value)
    if isinstance(value, dict):
        return any(
            _explicit_retained_disposition(item)
            for key, item in value.items()
            if "disposition" in str(key).lower()
            or str(key).lower() in {"status", "review_status"}
        )
    if not isinstance(value, str):
        return False
    text = value.strip().lower()
    if re.search(r"\b(?:not|no)\s+(?:inherited|retained|retain)\b", text):
        return False
    return (
        re.search(
            r"\b(?:inherited|inherit(?:ed|s|ing)?|retained|retain(?:ed|s|ing)?)\b",
            text,
        )
        is not None
    )


def _record_has_disposition(record: dict[str, Any]) -> bool:
    for key, value in record.items():
        key_text = str(key).lower()
        if (
            ("inherit" in key_text or "retain" in key_text)
            and value is not None
            and value is not False
        ):
            return True
        if (
            "disposition" in key_text
            or key_text in {"status", "review_status", "retained", "inherited"}
        ) and _explicit_retained_disposition(value):
            return True
    return False


def _has_character_disposition(
    line: dict[str, Any], override: dict[str, Any] | None, character_index: int
) -> bool:
    """Find a per-character or explicitly line-scoped retained disposition."""

    for source in (line, override or {}):
        for key in (
            "character_dispositions",
            "character_statuses",
            "dispositions",
            "characters",
            "comparisons",
            "dual_audio_comparisons",
            "disposition",
        ):
            for record in _iter_character_records(source.get(key)):
                if _record_index(record) == character_index and _record_has_disposition(
                    record
                ):
                    return True
        for key in ("retained_characters", "inherited_characters"):
            values = source.get(key)
            if isinstance(values, dict):
                for raw_index, value in values.items():
                    if _coerce_index(
                        raw_index
                    ) == character_index and _explicit_retained_disposition(value):
                        return True
            elif isinstance(values, list):
                for value in values:
                    if isinstance(value, dict):
                        if _record_index(
                            value
                        ) == character_index and _record_has_disposition(value):
                            return True
                    elif _coerce_index(value) == character_index:
                        return True
        for key, value in source.items():
            key_text = str(key).lower()
            if (
                (
                    "disposition" in key_text
                    or key_text in {"status", "review_status"}
                    or "inherit" in key_text
                    or "retain" in key_text
                )
                and not isinstance(value, (dict, list))
                and _explicit_retained_disposition(value)
            ):
                return True
    return False


def _override_line(override_song: Any, line_index: int) -> dict[str, Any] | None:
    if not isinstance(override_song, dict):
        return None
    lines = override_song.get("lines")
    if isinstance(lines, dict):
        value = lines.get(str(line_index), lines.get(line_index))
        return value if isinstance(value, dict) else None
    if isinstance(lines, list):
        for value in lines:
            if (
                isinstance(value, dict)
                and _coerce_index(value.get("line_index", value.get("index")))
                == line_index
            ):
                return value
    return None


def _validate_audit_song(
    song: Any,
    sug_document: Any,
    override_song: Any,
) -> dict[str, Any]:
    sug_lines = _sug_lines(sug_document)
    sug_by_index = {
        line["line_index"]: line
        for line in sug_lines
        if line.get("line_index") is not None
    }
    audit_lines = song.get("lines") if isinstance(song, dict) else None
    audit_lines = audit_lines if isinstance(audit_lines, list) else []
    audit_indices = [
        _coerce_index(line.get("line_index", line.get("index")))
        for line in audit_lines
        if isinstance(line, dict)
    ]
    line_index_ok = (
        len(audit_lines) == len(sug_lines)
        and all(index is not None for index in audit_indices)
        and len(set(audit_indices)) == len(audit_indices)
        and set(audit_indices) == set(sug_by_index)
    )

    text_and_character_ok = True
    timestamp_coverage_ok = True
    line_checks: dict[str, dict[str, Any]] = {}
    audit_by_index: dict[int, dict[str, Any]] = {}
    for line in audit_lines:
        if not isinstance(line, dict):
            text_and_character_ok = False
            timestamp_coverage_ok = False
            continue
        line_index = _coerce_index(line.get("line_index", line.get("index")))
        if line_index is None or line_index in audit_by_index:
            text_and_character_ok = False
            timestamp_coverage_ok = False
            continue
        audit_by_index[line_index] = line
        sug_line = sug_by_index.get(line_index)
        override = _override_line(override_song, line_index)
        text_ok = (
            sug_line is not None
            and isinstance(line.get("text"), str)
            and line["text"] == sug_line.get("text")
        )
        character_ok = True
        dual_indices: set[int] = set()
        for field in ("comparisons", "dual_audio_comparisons"):
            records = list(_iter_character_records(line.get(field)))
            for record in records:
                index = _record_index(record)
                character = _record_character(record)
                expected_character = (
                    sug_line.get("characters", [])[index].get(
                        "char",
                        sug_line["characters"][index].get("character"),
                    )
                    if sug_line is not None
                    and index is not None
                    and 0 <= index < len(sug_line.get("characters", []))
                    and isinstance(sug_line["characters"][index], dict)
                    else None
                )
                if (
                    index is None
                    or character is None
                    or character != expected_character
                ):
                    character_ok = False
                if (
                    field == "dual_audio_comparisons"
                    and index is not None
                    and character == expected_character
                ):
                    dual_indices.add(index)

        coverage_ok = True
        if sug_line is None:
            coverage_ok = False
        else:
            for index, character in enumerate(sug_line.get("characters", [])):
                if not isinstance(character, dict):
                    coverage_ok = False
                    continue
                timestamps = character.get("timestamps")
                if not isinstance(timestamps, list) or not timestamps:
                    continue
                if index not in dual_indices and not _has_character_disposition(
                    line, override, index
                ):
                    coverage_ok = False
        text_and_character_ok &= text_ok and character_ok
        timestamp_coverage_ok &= coverage_ok
        line_checks[str(line_index)] = {
            "text_ok": text_ok,
            "character_ok": character_ok,
            "timestamp_coverage_ok": coverage_ok,
            "display_timestamp_count": sum(
                1
                for character in (sug_line or {}).get("characters", [])
                if isinstance(character, dict)
                and isinstance(character.get("timestamps"), list)
                and character["timestamps"]
            ),
        }

    ok = line_index_ok and text_and_character_ok and timestamp_coverage_ok
    return {
        "ok": ok,
        "sug_present": bool(sug_lines),
        "expected_line_count": len(sug_lines),
        "reported_line_count": len(audit_lines),
        "line_index_ok": line_index_ok,
        "text_and_character_ok": text_and_character_ok,
        "timestamp_coverage_ok": timestamp_coverage_ok,
        "lines": line_checks,
    }


def validate_alignment_audit(
    audit: Any,
    timing_overrides: Any,
    sug_documents: dict[str, Any],
    tracks: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Validate audit provenance and every audited character against current SUG JSON."""

    expected_song_ids = {str(track["song_id"]) for track in tracks}
    audit_songs = audit.get("songs") if isinstance(audit, dict) else None
    audit_songs = audit_songs if isinstance(audit_songs, list) else []
    audit_by_song: dict[str, dict[str, Any]] = {}
    duplicate_song_ids: list[str] = []
    for song in audit_songs:
        if not isinstance(song, dict):
            continue
        song_id = str(song.get("song_id", ""))
        if song_id in audit_by_song:
            duplicate_song_ids.append(song_id)
        audit_by_song[song_id] = song
    reported_song_ids = set(audit_by_song)
    expected_songs_present = (
        len(audit_songs) == len(expected_song_ids)
        and not duplicate_song_ids
        and reported_song_ids == expected_song_ids
    )

    override_songs = (
        timing_overrides.get("songs") if isinstance(timing_overrides, dict) else None
    )
    override_songs = override_songs if isinstance(override_songs, dict) else {}
    song_checks: dict[str, dict[str, Any]] = {}
    for song_id in sorted(expected_song_ids):
        song = audit_by_song.get(song_id)
        if song is None:
            song_checks[song_id] = {
                "ok": False,
                "sug_present": song_id in sug_documents,
                "missing_audit_song": True,
            }
            continue
        song_check = _validate_audit_song(
            song,
            sug_documents.get(song_id),
            override_songs.get(song_id),
        )
        song_check["missing_audit_song"] = False
        song_checks[song_id] = song_check

    audit_schema_ok = (
        isinstance(audit, dict)
        and audit.get("schema_version") == "karaoke-mms-dual-audio-audit/v1"
    )
    overrides_schema_ok = (
        isinstance(timing_overrides, dict)
        and timing_overrides.get("schema_version") == "karaoke-timing-overrides/v2"
    )
    all_song_checks_ok = all(check.get("ok") is True for check in song_checks.values())
    return {
        "ok": audit_schema_ok
        and overrides_schema_ok
        and expected_songs_present
        and all_song_checks_ok,
        "audit_schema_ok": audit_schema_ok,
        "timing_overrides_schema_ok": overrides_schema_ok,
        "expected_songs_present": expected_songs_present,
        "expected_song_ids": sorted(expected_song_ids),
        "reported_song_ids": sorted(reported_song_ids),
        "missing_song_ids": sorted(expected_song_ids - reported_song_ids),
        "extra_song_ids": sorted(reported_song_ids - expected_song_ids),
        "duplicate_song_ids": duplicate_song_ids,
        "song_checks": song_checks,
    }


# Descriptive aliases keep the gate discoverable to callers and tests.
validate_mms_alignment_audit = validate_alignment_audit


def _hevc444_output_key(item: Any) -> tuple[str, str] | None:
    if not isinstance(item, dict):
        return None
    profile = item.get("profile")
    song_id = item.get("song_id")
    if not isinstance(profile, str) or song_id is None:
        return None
    return profile, str(song_id)


def _direct_report_exists(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return Path(value).is_file()


def _iter_text_values(value: Any):
    """Yield nested provenance strings without trusting their container shape."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _iter_text_values(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _iter_text_values(nested)


def _has_forbidden_intermediate_video_provenance(*items: Any) -> bool:
    """Reject H.264/AV1 masters anywhere in a recorded source chain."""

    provenance_keys = (
        "source",
        "source_chain",
        "provenance",
        "input_codec",
        "input_video",
        "intermediate_codec",
        "intermediate_video",
        "master_video",
        "video_master",
        "source_artifact",
        "source_paths",
        "sources",
    )
    for item in items:
        if not isinstance(item, dict):
            continue
        if any(
            item.get(key) is True
            for key in (
                "intermediate_h264",
                "intermediate_av1",
                "intermediate_video",
            )
        ):
            return True
        for key in provenance_keys:
            for value in _iter_text_values(item.get(key)):
                text = value.lower().replace("\\", "/")
                if re.search(r"\bh\.?264\b|\bh264\b|\bav1\b|\bav01\b", text):
                    return True
                if re.search(
                    r"(?:^|/)video/(?:standard|wide|h264|av1|av01)(?:/|$)",
                    text,
                ):
                    return True
    return False


def _source_matches_hevc444_contract(item: dict[str, Any], profile: str) -> bool:
    """Require original audio, artwork and the profile-specific ASS source."""

    values = list(_iter_text_values(item.get("source")))
    if not values:
        return False
    text = " ".join(values).lower().replace("\\", "/")
    if not re.search(r"\boriginal\s+(?:audio|mp3|mix)\b", text):
        return False
    if "artwork" not in text or "ass" not in text:
        return False
    profile_pattern = rf"\b{re.escape(profile.lower())}\b[^\s,>]*(?:\.ass\b|/ass\b)"
    return bool(
        re.search(profile_pattern, text)
        or re.search(rf"\b{re.escape(profile.lower())}\s+ass\b", text)
    )


def _hevc_rext_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalised = re.sub(r"[\s_-]+", "", value.lower())
    return normalised in {"rext", "hevcrext"}


def validate_hevc444_delivery(
    hevc444_report: Any,
    tracks: tuple[dict[str, Any], ...],
    profiles: tuple[str, ...] = PROFILES,
    direct_report_paths: dict[tuple[str, str], Any] | None = None,
) -> dict[str, Any]:
    """Validate the complete direct HEVC 4:4:4 delivery contract."""

    expected = {
        (profile, str(track["song_id"])): track
        for profile in profiles
        for track in tracks
    }
    direct_render = (
        hevc444_report.get("direct_render")
        if isinstance(hevc444_report, dict)
        else None
    )
    direct_render = direct_render if isinstance(direct_render, dict) else {}
    outputs = (
        hevc444_report.get("outputs") if isinstance(hevc444_report, dict) else None
    )
    outputs = outputs if isinstance(outputs, list) else []
    output_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_keys: list[str] = []
    malformed_output_count = 0
    for item in outputs:
        key = _hevc444_output_key(item)
        if key is None:
            malformed_output_count += 1
            continue
        if key in output_by_key:
            duplicate_keys.append(f"{key[0]}:{key[1]}")
        output_by_key[key] = item

    entry_checks: dict[str, dict[str, Any]] = {}
    for key, track in sorted(expected.items()):
        profile, song_id = key
        item = output_by_key.get(key)
        direct_report_value = (
            direct_report_paths.get(key) if direct_report_paths is not None else True
        )
        direct_report_ok = _direct_report_exists(direct_report_value)
        expected_output = f"video/hevc444/{profile}/{track['artifact_slug']}.mp4"
        output_path = (
            str(item.get("output", "")).replace("\\", "/") if item is not None else ""
        )
        intermediate_h264 = (
            item.get("intermediate_h264", direct_render.get("intermediate_h264"))
            if item is not None
            else None
        )
        intermediate_av1 = (
            item.get("intermediate_av1", direct_render.get("intermediate_av1"))
            if item is not None
            else None
        )
        source_ok = item is not None and _source_matches_hevc444_contract(item, profile)
        forbidden_provenance = _has_forbidden_intermediate_video_provenance(
            item, direct_render
        )
        checks = {
            "report_entry_present": item is not None,
            "render_mode_direct_hevc444": item is not None
            and item.get("render_mode") == "direct-hevc444",
            "intermediate_h264_false": intermediate_h264 is False,
            "intermediate_av1_false_or_absent": intermediate_av1 is not True,
            "no_forbidden_intermediate_video": item is not None
            and not forbidden_provenance,
            "source_original_audio_artwork_profile_ass": source_ok,
            "output_path_matches": output_path == expected_output,
            "direct_render_report_exists": direct_report_ok,
        }
        entry_checks[f"{profile}:{song_id}"] = {
            "profile": profile,
            "song_id": song_id,
            "output": item,
            "direct_render_report": {
                "exists": direct_report_ok,
                "path": (
                    str(direct_report_value)
                    if not isinstance(direct_report_value, bool)
                    and direct_report_value is not None
                    else None
                ),
            },
            "checks": checks,
            "ok": all(checks.values()),
        }

    expected_keys = set(expected)
    reported_keys = set(output_by_key)
    entries_exact = (
        len(outputs) == len(expected)
        and malformed_output_count == 0
        and not duplicate_keys
        and reported_keys == expected_keys
    )
    schema_ok = (
        isinstance(hevc444_report, dict)
        and hevc444_report.get("schema_version") == "karaoke-hevc444/v1"
    )
    status_ok = (
        isinstance(hevc444_report, dict) and hevc444_report.get("status") == "pass"
    )
    encoder_ok = (
        isinstance(hevc444_report, dict)
        and hevc444_report.get("encoder") == "hevc_nvenc"
        and _hevc_rext_value(
            hevc444_report.get("profile", hevc444_report.get("video_profile"))
        )
        and hevc444_report.get("codec_tag") == "hvc1"
        and hevc444_report.get("pixel_format") == "yuv444p"
        and hevc444_report.get("color_range") == "pc"
        and hevc444_report.get("container") == "mp4"
    )
    direct_render_section_ok = isinstance(hevc444_report, dict) and isinstance(
        hevc444_report.get("direct_render"), dict
    )
    direct_render_h264_ok = direct_render.get("intermediate_h264") is False
    direct_render_av1_ok = direct_render.get("intermediate_av1") is not True
    direct_render_no_forbidden_video = not _has_forbidden_intermediate_video_provenance(
        direct_render
    )
    return {
        "ok": schema_ok
        and status_ok
        and encoder_ok
        and direct_render_section_ok
        and direct_render_h264_ok
        and direct_render_av1_ok
        and direct_render_no_forbidden_video
        and entries_exact
        and all(entry["ok"] for entry in entry_checks.values()),
        "schema_ok": schema_ok,
        "status_ok": status_ok,
        "hevc444_encoder_ok": encoder_ok,
        "direct_render_intermediate_h264_false": direct_render_h264_ok,
        "direct_render_intermediate_av1_false_or_absent": direct_render_av1_ok,
        "direct_render_no_forbidden_intermediate_video": direct_render_no_forbidden_video,
        "entries_exact": entries_exact,
        "expected_entry_count": len(expected),
        "reported_entry_count": len(outputs),
        "malformed_output_count": malformed_output_count,
        "duplicate_keys": duplicate_keys,
        "entry_checks": entry_checks,
    }


validate_hevc444_report = validate_hevc444_delivery


def _has_video_master_source(item: dict[str, Any]) -> bool:
    for value in _iter_text_values(item.get("source_paths")):
        if re.search(r"\.(?:mp4|mkv|mov|webm|avi|m4v)(?:$|[?#])", value, re.IGNORECASE):
            return True
    return False


def validate_av1_420_delivery(
    av1_report: Any,
    tracks: tuple[dict[str, Any], ...],
    profiles: tuple[str, ...] = PROFILES,
    direct_report_paths: dict[tuple[str, str], Any] | None = None,
) -> dict[str, Any]:
    """Validate the complete direct AV1 Main YUV 4:2:0 delivery contract."""

    expected = {
        (profile, str(track["song_id"])): track
        for profile in profiles
        for track in tracks
    }
    direct_render = (
        av1_report.get("direct_render") if isinstance(av1_report, dict) else None
    )
    direct_render = direct_render if isinstance(direct_render, dict) else {}
    outputs = av1_report.get("outputs") if isinstance(av1_report, dict) else None
    outputs = outputs if isinstance(outputs, list) else []
    output_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_keys: list[str] = []
    malformed_output_count = 0
    for item in outputs:
        key = _hevc444_output_key(item)
        if key is None:
            malformed_output_count += 1
            continue
        if key in output_by_key:
            duplicate_keys.append(f"{key[0]}:{key[1]}")
        output_by_key[key] = item

    entry_checks: dict[str, dict[str, Any]] = {}
    for key, track in sorted(expected.items()):
        profile, song_id = key
        item = output_by_key.get(key)
        direct_report_value = (
            direct_report_paths.get(key) if direct_report_paths is not None else True
        )
        direct_report_ok = _direct_report_exists(direct_report_value)
        expected_output = (
            f"video/av1-420/{profile}/{track['numbered_video_filename']}"
        )
        expected_lossless_output = (
            "video/av1-420-lossless/"
            f"{profile}/{Path(track['numbered_video_filename']).with_suffix('.mkv')}"
        )
        output_path = (
            str(item.get("output", "")).replace("\\", "/")
            if item is not None
            else ""
        )
        media_checks = item.get("media_checks") if item is not None else None
        lossless_output_path = (
            str(item.get("lossless_output", "")).replace("\\", "/")
            if item is not None
            else ""
        )
        lossless_media_checks = (
            item.get("lossless_media_checks") if item is not None else None
        )
        checks = {
            "report_entry_present": item is not None,
            "render_mode_direct_av1_420": item is not None
            and item.get("render_mode") == "direct-av1-420",
            "intermediate_video_false": item is not None
            and item.get("intermediate_video") is False,
            "intermediate_h264_false": item is not None
            and item.get("intermediate_h264") is False,
            "intermediate_hevc_false": item is not None
            and item.get("intermediate_hevc") is False,
            "source_original_audio_artwork_profile_ass": item is not None
            and _source_matches_hevc444_contract(item, profile),
            "source_has_no_video_master": item is not None
            and not _has_video_master_source(item),
            "output_path_matches": output_path == expected_output,
            "lossless_output_path_matches": (
                lossless_output_path == expected_lossless_output
            ),
            "default_delivery_is_compatibility_mp4": item is not None
            and item.get("default_delivery") == "compatibility_mp4",
            "compatibility_audio_is_aac_lc_320k": item is not None
            and item.get("audio") == "aac-lc-320k",
            "lossless_audio_is_flac": item is not None
            and item.get("lossless_audio") == "flac",
            "direct_render_report_exists": direct_report_ok,
            "reported_media_checks_pass": isinstance(media_checks, dict)
            and media_checks.get("ok") is True,
            "reported_lossless_media_checks_pass": isinstance(
                lossless_media_checks, dict
            )
            and lossless_media_checks.get("ok") is True,
        }
        entry_checks[f"{profile}:{song_id}"] = {
            "profile": profile,
            "song_id": song_id,
            "output": item,
            "direct_render_report": {
                "exists": direct_report_ok,
                "path": (
                    str(direct_report_value)
                    if not isinstance(direct_report_value, bool)
                    and direct_report_value is not None
                    else None
                ),
            },
            "checks": checks,
            "ok": all(checks.values()),
        }

    expected_keys = set(expected)
    reported_keys = set(output_by_key)
    entries_exact = (
        len(outputs) == len(expected)
        and malformed_output_count == 0
        and not duplicate_keys
        and reported_keys == expected_keys
    )
    schema_ok = (
        isinstance(av1_report, dict)
        and av1_report.get("schema_version") == "karaoke-av1-420/v2"
    )
    status_ok = isinstance(av1_report, dict) and av1_report.get("status") == "pass"
    encoder_ok = (
        isinstance(av1_report, dict)
        and av1_report.get("encoder") == "av1_nvenc"
        and str(av1_report.get("profile", "")).casefold() == "main"
        and av1_report.get("codec_tag") == "av01"
        and av1_report.get("pixel_format") == "yuv420p"
        and av1_report.get("color_range") == "tv"
        and av1_report.get("default_delivery") == "compatibility_mp4"
        and av1_report.get("containers") == ["mp4", "matroska"]
    )
    direct_render_ok = (
        isinstance(av1_report, dict)
        and isinstance(av1_report.get("direct_render"), dict)
        and direct_render.get("intermediate_video") is False
    )
    full_decode_gate = (
        av1_report.get("full_decode_gate") if isinstance(av1_report, dict) else None
    )
    full_decode_ok = (
        isinstance(full_decode_gate, dict)
        and full_decode_gate.get("required") is False
        and (
            full_decode_gate.get("performed") is True
            or isinstance(full_decode_gate.get("reason"), str)
        )
    )
    return {
        "ok": schema_ok
        and status_ok
        and encoder_ok
        and direct_render_ok
        and full_decode_ok
        and entries_exact
        and all(entry["ok"] for entry in entry_checks.values()),
        "schema_ok": schema_ok,
        "status_ok": status_ok,
        "av1_420_encoder_ok": encoder_ok,
        "direct_render_intermediate_video_false": direct_render_ok,
        "reported_full_decode_ok": full_decode_ok,
        "entries_exact": entries_exact,
        "expected_entry_count": len(expected),
        "reported_entry_count": len(outputs),
        "malformed_output_count": malformed_output_count,
        "duplicate_keys": duplicate_keys,
        "entry_checks": entry_checks,
    }


validate_av1_delivery = validate_av1_420_delivery
validate_av1_report = validate_av1_420_delivery


def validate_timing_report(
    report: dict[str, Any], tracks: tuple[dict[str, Any], ...]
) -> dict[str, Any]:
    """Validate that the forced vocal/mix alignment gate reached the release."""
    expected_song_ids = {str(track["song_id"]) for track in tracks}
    songs = report.get("songs")
    song_checks: dict[str, dict[str, bool]] = {}
    reported_song_ids: list[str] = []
    if isinstance(songs, list):
        for song in songs:
            if not isinstance(song, dict):
                continue
            song_id = str(song.get("song_id", ""))
            reported_song_ids.append(song_id)
            alignment = song.get("alignment", {})
            project_validation = song.get("project_validation", {})
            mix_cross_check = (
                alignment.get("original_mix_cross_check", {})
                if isinstance(alignment, dict)
                else {}
            )
            song_checks[song_id] = {
                "forced_mode": alignment.get("requested_mode") == "forced",
                "alignment_ok": alignment.get("status") == "ok",
                "vocal_stem_used": (
                    alignment.get("audio_kind") == "msst-karaoke-vocals"
                ),
                "mix_cross_check_ok": mix_cross_check.get("status") == "ok",
                "gate_ok": alignment.get("gate_ok") is True,
                "project_validation_ok": project_validation.get("ok") is True,
            }

    schema_ok = report.get("schema_version") == "karaoke-timing-report/v1"
    report_ok = report.get("ok") is True
    expected_songs_present = (
        len(reported_song_ids) == len(expected_song_ids)
        and len(set(reported_song_ids)) == len(reported_song_ids)
        and set(reported_song_ids) == expected_song_ids
    )
    all_song_checks_ok = expected_songs_present and all(
        all(checks.values()) for checks in song_checks.values()
    )
    return {
        "ok": schema_ok and report_ok and all_song_checks_ok,
        "schema_ok": schema_ok,
        "report_ok": report_ok,
        "expected_songs_present": expected_songs_present,
        "expected_track_count": len(tracks),
        "reported_track_count": len(reported_song_ids),
        "song_checks": song_checks,
    }


def validate_expected_cue_counts(
    tracks: tuple[dict[str, Any], ...], cue_counts: dict[str, int]
) -> dict[str, Any]:
    """Require known manifest cue counts before a release can pass."""

    expected_cues_known = all(track["expected_cues"] is not None for track in tracks)
    checks = {
        track["song_id"]: (
            track["expected_cues"] is not None
            and cue_counts.get(track["song_id"]) == track["expected_cues"]
        )
        for track in tracks
    }
    return {
        "ok": expected_cues_known and all(checks.values()),
        "expected_cues_known": expected_cues_known,
        "checks": checks,
    }


def decode_media(ffmpeg: Path, video: Path, root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(video),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "path": relative_path(video, root),
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "stderr_tail": completed.stderr[-1000:],
    }


def inspect_hevc444_media(ffmpeg: Path, video: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(video)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    details = completed.stdout + "\n" + completed.stderr
    video_details = next(
        (line for line in details.splitlines() if "Video:" in line), details
    )
    return {
        "codec_hevc": re.search(r"Video:\s+hevc\b", video_details, re.IGNORECASE)
        is not None,
        "codec_tag_hvc1": re.search(r"\(\s*hvc1\s*/", video_details, re.IGNORECASE)
        is not None,
        "profile_rext": re.search(
            r"\bhevc\s+\(\s*rext\s*\)", video_details, re.IGNORECASE
        )
        is not None,
        "resolution_1920x1080": re.search(r"\b1920x1080\b", details) is not None,
        "pixel_format_yuv444p": re.search(r"\byuv444p\b", video_details, re.IGNORECASE)
        is not None,
        "yuv_full_range": re.search(
            r"\byuv444p\(\s*pc(?:[,)]|\s)", video_details, re.IGNORECASE
        )
        is not None,
        "cfr_30fps": re.search(
            r"\b30(?:\.0+)?\s+fps\b|\bfps\s*=\s*30(?:\.0+)?\b",
            details,
            re.IGNORECASE,
        )
        is not None,
        "aac_audio": re.search(r"Audio:\s+aac\b", details) is not None,
    }


def inspect_av1_420_media(ffmpeg: Path, video: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(video)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    details = completed.stdout + "\n" + completed.stderr
    video_details = next(
        (line for line in details.splitlines() if "Video:" in line), details
    )
    audio_details = next(
        (line for line in details.splitlines() if "Audio:" in line), details
    )
    bitrate_match = re.search(r"\b(\d+)\s+kb/s\b", audio_details)
    bitrate_kbps = int(bitrate_match.group(1)) if bitrate_match is not None else None
    return {
        "codec_av1": re.search(r"Video:\s+av1\b", video_details, re.IGNORECASE)
        is not None,
        "codec_tag_av01": re.search(r"\(\s*av01\s*/", video_details, re.IGNORECASE)
        is not None,
        "profile_main": re.search(
            r"\bav1\s+\([^)]*\)\s+\(\s*main\s*\)",
            video_details,
            re.IGNORECASE,
        )
        is not None,
        "resolution_1920x1080": re.search(r"\b1920x1080\b", details) is not None,
        "pixel_format_yuv420p": re.search(
            r"\byuv420p\b", video_details, re.IGNORECASE
        )
        is not None,
        "yuv_limited_range": re.search(
            r"\byuv420p\(\s*tv(?:[,)]|\s)", video_details, re.IGNORECASE
        )
        is not None,
        "cfr_30fps": re.search(
            r"\b30(?:\.0+)?\s+fps\b|\bfps\s*=\s*30(?:\.0+)?\b",
            details,
            re.IGNORECASE,
        )
        is not None,
        "aac_audio": re.search(r"Audio:\s+aac\b", details) is not None,
        "aac_lc_profile": re.search(
            r"Audio:\s+aac\s*\(LC\)", audio_details, re.IGNORECASE
        )
        is not None,
        "aac_bitrate_reasonable_for_320k_target": bitrate_kbps is not None
        and 240 <= bitrate_kbps <= 360,
    }


inspect_av1_media = inspect_av1_420_media


def inspect_av1_420_lossless_media(ffmpeg: Path, video: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(video)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    details = completed.stdout + "\n" + completed.stderr
    video_details = next(
        (line for line in details.splitlines() if "Video:" in line), details
    )
    return {
        "codec_av1": re.search(r"Video:\s+av1\b", video_details, re.IGNORECASE)
        is not None,
        "profile_main": re.search(
            r"\bav1\s+\([^)]*\)\s+\(\s*main\s*\)",
            video_details,
            re.IGNORECASE,
        )
        is not None,
        "resolution_1920x1080": re.search(r"\b1920x1080\b", details) is not None,
        "pixel_format_yuv420p": re.search(
            r"\byuv420p\b", video_details, re.IGNORECASE
        )
        is not None,
        "yuv_limited_range": re.search(
            r"\byuv420p\(\s*tv(?:[,)]|\s)", video_details, re.IGNORECASE
        )
        is not None,
        "cfr_30fps": re.search(
            r"\b30(?:\.0+)?\s+fps\b|\bfps\s*=\s*30(?:\.0+)?\b",
            details,
            re.IGNORECASE,
        )
        is not None,
        "flac_audio": re.search(r"Audio:\s+flac\b", details, re.IGNORECASE)
        is not None,
        "no_aac_audio": re.search(r"Audio:\s+aac\b", details, re.IGNORECASE)
        is None,
    }


def _ass_timestamp_ms(value: str) -> int | None:
    match = re.fullmatch(r"(\d+):(\d{1,2}):(\d{2})\.(\d{2})", value.strip())
    if match is None:
        return None
    hours, minutes, seconds, centiseconds = (int(part) for part in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + centiseconds * 10


def _ass_visible_text(value: str) -> str:
    value = re.sub(r"\{[^}]*\}", "", value)
    return value.replace("\\N", "").replace("\\n", "").replace("\\h", " ")


def _ass_character_records(path: Path) -> list[dict[str, Any]]:
    """Read Main ASS dialogue events and retain their full ``\\k``/``\\kf`` axis."""

    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.startswith("Dialogue:"):
            continue
        fields = raw_line[len("Dialogue:") :].split(",", 9)
        if len(fields) != 10 or fields[3].strip().lower() != "main":
            continue
        start_ms = _ass_timestamp_ms(fields[1])
        end_ms = _ass_timestamp_ms(fields[2])
        visible_text = _ass_visible_text(fields[9])
        if start_ms is None or end_ms is None or not visible_text:
            continue
        override_blocks = re.findall(r"\{([^}]*)\}", fields[9])
        tags = "".join(override_blocks)
        cumulative_match = re.search(r"\\k(?!f|o|t)(\d+)", tags)
        duration_match = re.search(r"\\kf(\d+)", tags)
        duration_cs = int(duration_match.group(1)) if duration_match else None
        if cumulative_match is not None:
            onset_cs = int(cumulative_match.group(1))
        elif duration_cs is not None:
            previous_cs = (
                records[-1].get("_axis_cs", 0)
                if records and records[-1].get("event_start_ms") == start_ms
                else 0
            )
            onset_cs = int(previous_cs) + duration_cs
        else:
            continue
        records.append(
            {
                "character": visible_text,
                "onset_ms": start_ms + onset_cs * 10,
                "duration_ms": duration_cs * 10 if duration_cs is not None else None,
                "event_start_ms": start_ms,
                "event_end_ms": end_ms,
                "_axis_cs": onset_cs,
            }
        )
    for record in records:
        record.pop("_axis_cs", None)
    return records


def _ass_character_axis(
    report: dict[str, Any], path: Path
) -> list[dict[str, Any]] | None:
    records = _ass_character_records(path)
    ass = report.get("ass", {})
    lines = ass.get("lines", []) if isinstance(ass, dict) else []
    if not records or not isinstance(lines, list):
        return None
    cursor = 0
    axis: list[dict[str, Any]] = []
    for line in lines:
        if not isinstance(line, dict) or not isinstance(line.get("text"), str):
            return None
        expected_text = line["text"]
        collected: list[dict[str, Any]] = []
        collected_text = ""
        while cursor < len(records) and len(collected_text) < len(expected_text):
            record = records[cursor]
            cursor += 1
            collected.append(record)
            collected_text += record["character"]
            if not expected_text.startswith(collected_text):
                return None
        if collected_text != expected_text:
            return None
        positions = list(range(len(line["text"])))
        if len(positions) != len(collected):
            return None
        for position, record in zip(positions, collected):
            axis.append(
                {
                    "line_index": line.get("line_index"),
                    "character_index": position,
                    "character": record["character"],
                    "onset_ms": record["onset_ms"],
                    "duration_ms": record["duration_ms"],
                    "event_start_ms": record["event_start_ms"],
                    "event_end_ms": record["event_end_ms"],
                }
            )
    if cursor != len(records):
        return None
    return axis


def _reported_character_axis(report: dict[str, Any]) -> list[dict[str, Any]] | None:
    ass = report.get("ass", {})
    lines = ass.get("lines", []) if isinstance(ass, dict) else []
    if not isinstance(lines, list):
        return None
    axis: list[dict[str, Any]] = []
    found = False
    for line in lines:
        if not isinstance(line, dict):
            return None
        raw_axis = None
        for key in (
            "character_axis",
            "characters",
            "character_timings",
            "char_timings",
            "character_timestamps",
            "timestamps",
        ):
            if isinstance(line.get(key), list):
                raw_axis = line[key]
                break
        if raw_axis is None:
            continue
        found = True
        for position, item in enumerate(raw_axis):
            if isinstance(item, dict):
                axis.append(
                    {
                        "line_index": line.get("line_index"),
                        "character_index": item.get(
                            "character_index", item.get("index", position)
                        ),
                        "character": item.get("character", item.get("char")),
                        "onset_ms": item.get(
                            "onset_ms", item.get("start_ms", item.get("timestamp_ms"))
                        ),
                        "duration_ms": item.get("duration_ms", item.get("duration")),
                        "release_ms": item.get("release_ms", item.get("end_ms")),
                    }
                )
            else:
                axis.append(
                    {
                        "line_index": line.get("line_index"),
                        "character_index": position,
                        "timestamp": item,
                    }
                )
    return axis if found else None


def timing_signature(
    report: dict[str, Any], ass_path: Path | None = None
) -> dict[str, Any]:
    """Build a profile signature including the complete character timing axis."""

    ass = report["ass"]
    signature: dict[str, Any] = {
        "lines": [
            {
                "line_index": line["line_index"],
                "text": line["text"],
                "first_onset_ms": line["first_onset_ms"],
                "release_ms": line["release_ms"],
                "event_start_ms": line["event_start_ms"],
                "event_end_ms": line["event_end_ms"],
                "ruby": line["ruby"],
            }
            for line in ass["lines"]
        ],
        "cue_config": ass["cue_config"],
        "cues": [
            {
                key: cue[key]
                for key in (
                    "after_line_index",
                    "before_line_index",
                    "gap_start_ms",
                    "gap_ms",
                    "cue_start_ms",
                    "vocal_onset_ms",
                    "dot_starts_ms",
                )
            }
            for cue in ass["cues"]
        ],
    }
    character_axis = (
        _ass_character_axis(report, ass_path) if ass_path is not None else None
    )
    if character_axis is None:
        character_axis = _reported_character_axis(report)
    if character_axis is not None:
        signature["character_axis"] = character_axis
    return signature


def run_tests(root: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/scripts",
        "-q",
        "--basetemp",
        str(REPO_ROOT / ".cache" / "pytest" / "release-manifest"),
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (completed.stdout + "\n" + completed.stderr).strip()
    summary = next(
        (line.strip() for line in reversed(output.splitlines()) if line.strip()),
        "",
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": "uv run --no-sync python -m pytest tests/unit/scripts -q",
        "summary": summary,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def write_checksums(root: Path) -> int:
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    lines = [
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}" for path in files
    ]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(files)


def numbered_package_paths(root: Path, *, album_title: str) -> dict[str, Path]:
    """Build expected numbered archive paths from selected manifest data."""

    title = str(album_title).strip()
    if not title:
        raise ValueError("album_title must not be empty")
    return {
        "hevc444": root / "video" / f"{title}_HEVC444.zip",
        "av1-420": root / "video" / f"{title}_AV1-420.zip",
    }


def _resolve_package_path(value: object, root: Path) -> Path | None:
    if value is None:
        return None
    path = Path(str(value))
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def validate_numbered_packages(
    report: Any,
    root: Path,
    *,
    expected_archives: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """Validate numbered package contents against explicit archive paths.

    When callers do not have manifest metadata, report paths are used only as
    a compatibility fallback. The release finalizer always supplies paths
    derived from the selected manifest title.
    """

    packages = report.get("packages") if isinstance(report, dict) else None
    packages = packages if isinstance(packages, list) else []
    by_lane = {
        str(item.get("lane")): item for item in packages if isinstance(item, dict)
    }
    if expected_archives is None:
        expected_archives = {
            lane: path
            for lane, item in by_lane.items()
            if (path := _resolve_package_path(item.get("path"), root)) is not None
        }
    expected = {
        lane: _resolve_package_path(path, root)
        for lane, path in expected_archives.items()
    }
    lane_checks: dict[str, dict[str, Any]] = {}
    required_lanes = ("hevc444", "av1-420")
    for lane in required_lanes:
        expected_path = expected.get(lane)
        item = by_lane.get(lane)
        exists = expected_path is not None and expected_path.is_file()
        entries = item.get("entries") if isinstance(item, dict) else None
        entries = entries if isinstance(entries, list) else []
        video_entries = [
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("kind") == "video"
        ]
        lossless_video_entries = [
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("kind") == "lossless-video"
        ]
        expected_track_keys = {
            (profile, track_number)
            for profile in PROFILES
            for track_number in range(1, 6)
        }
        reported_track_keys = {
            (str(entry.get("profile")), int(entry.get("track_number", -1)))
            for entry in video_entries
        }
        reported_lossless_track_keys = {
            (str(entry.get("profile")), int(entry.get("track_number", -1)))
            for entry in lossless_video_entries
        }
        expected_entry_count = 23 if lane == "av1-420" else 13
        expected_video_entry_count = 20 if lane == "av1-420" else 10
        expected_lossless_count = 10 if lane == "av1-420" else 0
        checks = {
            "report_entry_present": item is not None,
            "path_matches": item is not None
            and expected_path is not None
            and _resolve_package_path(item.get("path"), root) == expected_path,
            "archive_exists": exists,
            "size_matches": exists
            and item is not None
            and item.get("size_bytes") == expected_path.stat().st_size,
            "sha256_matches": exists
            and item is not None
            and item.get("sha256") == sha256_file(expected_path),
            "entry_count_matches_lane": item is not None
            and item.get("entry_count") == expected_entry_count,
            "video_entry_count_matches_lane": item is not None
            and item.get("video_entry_count") == expected_video_entry_count,
            "lossless_video_entry_count_matches_lane": item is not None
            and item.get("lossless_video_entry_count") == expected_lossless_count,
            "numbered_true": item is not None and item.get("numbered") is True,
            "track_numbers_exact": reported_track_keys == expected_track_keys,
            "lossless_track_numbers_exact": (
                reported_lossless_track_keys == expected_track_keys
                if lane == "av1-420"
                else not reported_lossless_track_keys
            ),
        }
        lane_checks[lane] = {"checks": checks, "ok": all(checks.values())}
    status_ok = isinstance(report, dict) and report.get("status") == "pass"
    schema_ok = (
        isinstance(report, dict)
        and report.get("schema_version") == "karaoke-numbered-packages/v2"
    )
    lanes_exact = set(by_lane) == set(required_lanes)
    return {
        "ok": status_ok
        and schema_ok
        and lanes_exact
        and all(item["ok"] for item in lane_checks.values()),
        "status_ok": status_ok,
        "schema_ok": schema_ok,
        "lanes_exact": lanes_exact,
        "lane_checks": lane_checks,
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--skip-decode", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    album = load_album_manifest(args.manifest)
    tracks = tuple(track_record(track) for track in album.tracks)
    root = (args.root or album.deliverable_dir).resolve()
    ffmpeg = resolve_ffmpeg(root=REPO_ROOT)
    tests = {"ok": True, "skipped": True} if args.skip_tests else run_tests(root)
    alignment_audit_path = root / "sources" / "mms_alignment_audit.json"
    timing_overrides_path = root / "sources" / "timing_overrides.json"
    alignment_audit = read_json_if_present(alignment_audit_path)
    timing_overrides = read_json_if_present(timing_overrides_path)
    sug_documents = {
        str(track["song_id"]): read_json_if_present(
            root / "timing" / f"{track['timing_stem']}.sug"
        )
        for track in tracks
    }
    alignment_validation = validate_alignment_audit(
        alignment_audit,
        timing_overrides,
        sug_documents,
        tracks=tracks,
    )
    audit_provenance_validation = validate_audit_source_provenance(
        alignment_audit,
        project_root=REPO_ROOT,
    )
    profiles: dict[str, Any] = {}
    signatures: dict[str, dict[str, Any]] = {}
    decode_results: list[dict[str, Any]] = []
    consistency = True
    cue_counts: dict[str, int] = {}
    cue_counts_ok = False
    expected_cues_known = False
    early_display_ok = True
    media_reports_ok = True

    for profile in PROFILES:
        media_path = root / "validation" / profile / "media_report.json"
        media_report = read_json(media_path)
        media_reports_ok &= media_report.get("status") == "pass"
        media_by_title = {
            track_report["track"]["title"]: track_report
            for track_report in media_report["tracks"]
        }
        profile_tracks: dict[str, Any] = {}
        for track in tracks:
            title = track["title"]
            render_path = (
                root
                / "validation"
                / profile
                / f"{track['report_stem']}_render_report.json"
            )
            render_report = read_json(render_path)
            ass = root / "timing" / profile / f"{track['timing_stem']}.ass"
            signature = timing_signature(render_report, ass)
            if profile == PROFILES[0]:
                signatures[track["song_id"]] = signature
            else:
                consistency &= signatures[track["song_id"]] == signature

            cues = render_report["ass"]["cues"]
            cue_counts[track["song_id"]] = len(cues)
            maximum_early = max(
                line["early_display_ms"] for line in render_report["ass"]["lines"]
            )
            early_display_ok &= maximum_early <= 20_000
            video = (
                root / "video" / "hevc444" / profile / f"{track['artifact_slug']}.mp4"
            )
            keyframes = (
                root
                / "validation"
                / profile
                / f"{track['artifact_slug']}_keyframes.png"
            )
            if not args.skip_decode:
                decode_results.append(decode_media(ffmpeg, video, root))
            media = media_by_title[title]
            profile_tracks[title] = {
                "artist": track["artist"],
                "layout": render_report["ass"]["layout"],
                "line_count": len(render_report["ass"]["lines"]),
                "maximum_early_display_ms": maximum_early,
                "cue_config": render_report["ass"]["cue_config"],
                "cues": cues,
                "duration": media["duration"],
                "media_checks": media["checks"],
                "artifacts": {
                    "video": artifact(video, root),
                    "ass": artifact(ass, root),
                    "render_report": artifact(render_path, root),
                    "keyframes": artifact(keyframes, root),
                },
            }
        profiles[profile] = {
            "media_report_status": media_report["status"],
            "media_report": artifact(media_path, root),
            "tracks": profile_tracks,
        }

    hevc444_report_path = root / "validation" / "hevc444_report.json"
    hevc444_report = read_json_if_present(hevc444_report_path)
    direct_report_paths = {
        (profile, str(track["song_id"])): root
        / "validation"
        / profile
        / f"{track['report_stem']}_direct_hevc444_render_report.json"
        for profile in PROFILES
        for track in tracks
    }
    hevc444_validation = validate_hevc444_delivery(
        hevc444_report,
        tracks=tracks,
        profiles=PROFILES,
        direct_report_paths=direct_report_paths,
    )
    hevc444_output_by_key = {
        key: item
        for item in hevc444_report.get("outputs", [])
        if (key := _hevc444_output_key(item)) is not None
    }
    hevc444_media_ok = True
    hevc444_profiles: dict[str, Any] = {}
    for profile in PROFILES:
        profile_tracks = {}
        for track in tracks:
            title = track["title"]
            video = (
                root / "video" / "hevc444" / profile / f"{track['artifact_slug']}.mp4"
            )
            exists = video.is_file()
            checks = (
                inspect_hevc444_media(ffmpeg, video)
                if exists
                else {
                    "codec_hevc": False,
                    "codec_tag_hvc1": False,
                    "profile_rext": False,
                    "resolution_1920x1080": False,
                    "pixel_format_yuv444p": False,
                    "yuv_full_range": False,
                    "cfr_30fps": False,
                    "aac_audio": False,
                }
            )
            key = (profile, str(track["song_id"]))
            hevc444_entry = hevc444_validation["entry_checks"].get(
                f"{profile}:{track['song_id']}", {}
            )
            track_ok = bool(hevc444_entry.get("ok")) and exists and all(checks.values())
            hevc444_media_ok &= track_ok
            direct_report_path = direct_report_paths[key]
            hevc444_output = hevc444_output_by_key.get(key, {})
            profile_tracks[title] = {
                "ok": track_ok,
                "checks": checks,
                "artifact": artifact(video, root) if exists else None,
                "direct_hevc444_render_report": (
                    artifact(direct_report_path, root)
                    if direct_report_path.is_file()
                    else None
                ),
                "reported_size_ratio": hevc444_output.get("size_ratio"),
            }
        hevc444_profiles[profile] = {"tracks": profile_tracks}
    hevc444_ok = hevc444_validation["ok"] and hevc444_media_ok

    av1_report_path = root / "validation" / "av1_420_report.json"
    av1_report = read_json_if_present(av1_report_path)
    av1_direct_report_paths = {
        (profile, str(track["song_id"])): root
        / "validation"
        / profile
        / f"{track['report_stem']}_direct_av1_420_render_report.json"
        for profile in PROFILES
        for track in tracks
    }
    av1_validation = validate_av1_420_delivery(
        av1_report,
        tracks=tracks,
        profiles=PROFILES,
        direct_report_paths=av1_direct_report_paths,
    )
    av1_output_by_key = {
        key: item
        for item in av1_report.get("outputs", [])
        if (key := _hevc444_output_key(item)) is not None
    }
    av1_media_ok = True
    av1_profiles: dict[str, Any] = {}
    for profile in PROFILES:
        profile_tracks = {}
        for track in tracks:
            title = track["title"]
            video = (
                root
                / "video"
                / "av1-420"
                / profile
                / track["numbered_video_filename"]
            )
            lossless_video = (
                root
                / "video"
                / "av1-420-lossless"
                / profile
                / Path(track["numbered_video_filename"]).with_suffix(".mkv")
            )
            exists = video.is_file()
            lossless_exists = lossless_video.is_file()
            checks = (
                inspect_av1_420_media(ffmpeg, video)
                if exists
                else {
                    "codec_av1": False,
                    "codec_tag_av01": False,
                    "profile_main": False,
                    "resolution_1920x1080": False,
                    "pixel_format_yuv420p": False,
                    "yuv_limited_range": False,
                    "cfr_30fps": False,
                    "aac_audio": False,
                    "aac_lc_profile": False,
                    "aac_bitrate_reasonable_for_320k_target": False,
                }
            )
            lossless_checks = (
                inspect_av1_420_lossless_media(ffmpeg, lossless_video)
                if lossless_exists
                else {
                    "codec_av1": False,
                    "profile_main": False,
                    "resolution_1920x1080": False,
                    "pixel_format_yuv420p": False,
                    "yuv_limited_range": False,
                    "cfr_30fps": False,
                    "flac_audio": False,
                    "no_aac_audio": False,
                }
            )
            if exists and not args.skip_decode:
                decode_results.append(decode_media(ffmpeg, video, root))
            key = (profile, str(track["song_id"]))
            av1_entry = av1_validation["entry_checks"].get(
                f"{profile}:{track['song_id']}", {}
            )
            reported = av1_output_by_key.get(key, {})
            actual_size = video.stat().st_size if exists else None
            actual_sha256 = sha256_file(video) if exists else None
            actual_lossless_size = (
                lossless_video.stat().st_size if lossless_exists else None
            )
            actual_lossless_sha256 = (
                sha256_file(lossless_video) if lossless_exists else None
            )
            size_matches = exists and reported.get("output_size_bytes") == actual_size
            sha256_matches = exists and reported.get("sha256") == actual_sha256
            lossless_size_matches = lossless_exists and reported.get(
                "lossless_output_size_bytes"
            ) == actual_lossless_size
            lossless_sha256_matches = lossless_exists and reported.get(
                "lossless_sha256"
            ) == actual_lossless_sha256
            track_ok = (
                bool(av1_entry.get("ok"))
                and exists
                and lossless_exists
                and all(checks.values())
                and all(lossless_checks.values())
                and size_matches
                and sha256_matches
                and lossless_size_matches
                and lossless_sha256_matches
            )
            av1_media_ok &= track_ok
            direct_report_path = av1_direct_report_paths[key]
            profile_tracks[title] = {
                "ok": track_ok,
                "checks": checks,
                "lossless_checks": lossless_checks,
                "reported_size_matches": size_matches,
                "reported_sha256_matches": sha256_matches,
                "reported_lossless_size_matches": lossless_size_matches,
                "reported_lossless_sha256_matches": lossless_sha256_matches,
                "artifact": artifact(video, root) if exists else None,
                "lossless_artifact": (
                    artifact(lossless_video, root) if lossless_exists else None
                ),
                "direct_av1_420_render_report": (
                    artifact(direct_report_path, root)
                    if direct_report_path.is_file()
                    else None
                ),
            }
        av1_profiles[profile] = {"tracks": profile_tracks}
    av1_ok = av1_validation["ok"] and av1_media_ok
    numbered_packages_path = root / "validation" / "numbered_packages_report.json"
    numbered_packages_report = read_json_if_present(numbered_packages_path)
    numbered_packages_validation = validate_numbered_packages(
        numbered_packages_report,
        root,
        expected_archives=numbered_package_paths(root, album_title=album.title),
    )
    numbered_packages_ok = numbered_packages_validation["ok"]

    decode_ok = args.skip_decode or all(item["ok"] for item in decode_results)
    cue_count_validation = validate_expected_cue_counts(tracks, cue_counts)
    cue_counts_ok = cue_count_validation["ok"]
    expected_cues_known = cue_count_validation["expected_cues_known"]
    local_venv = Path(sys.prefix).resolve() == (REPO_ROOT / ".venv").resolve()
    uv_cache = Path(os.environ.get("UV_CACHE_DIR", REPO_ROOT / ".uv-cache")).resolve()
    uv_cache_local = REPO_ROOT.resolve() in (uv_cache, *uv_cache.parents)
    timing_report_path = root / "validation" / "timing_report.json"
    timing_validation = validate_timing_report(
        read_json(timing_report_path), tracks=tracks
    )
    status_ok = all(
        (
            tests["ok"],
            decode_ok,
            consistency,
            cue_counts_ok,
            early_display_ok,
            media_reports_ok,
            hevc444_ok,
            av1_ok,
            numbered_packages_ok,
            alignment_validation["ok"],
            audit_provenance_validation["ok"],
            local_venv,
            uv_cache_local,
            timing_validation["ok"],
        )
    )
    manifest = {
        "schema_version": "karaoke-release/v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if status_ok else "fail",
        "release": album.title,
        "environment": {
            "python": sys.version.split()[0],
            "python_prefix": str(Path(sys.prefix).resolve()),
            "project_local_venv": local_venv,
            "uv_cache": str(uv_cache),
            "project_local_uv_cache": uv_cache_local,
            "ffmpeg": str(ffmpeg),
        },
        "render_invariants": {
            "font_family": "HarmonyOS Sans SC",
            "main_advance_scale": 0.78,
            "main_outline_px": 4,
            "ruby_outline_px": 2,
            "maximum_early_display_ms": 20_000,
            "standard_and_wide_timing_identical": consistency,
            "expected_cue_counts_met": cue_counts_ok,
            "expected_cue_counts_known": expected_cues_known,
            "early_display_cap_met": early_display_ok,
            "hevc444_delivery_ok": hevc444_ok,
            "hevc444_direct_render_gate_met": hevc444_validation["ok"],
            "av1_420_delivery_ok": av1_ok,
            "av1_420_direct_render_gate_met": av1_validation["ok"],
            "numbered_packages_ok": numbered_packages_ok,
            "alignment_audit_gate_met": alignment_validation["ok"],
            "alignment_audit_provenance_gate_met": audit_provenance_validation["ok"],
            "timing_alignment_gate_met": timing_validation["ok"],
        },
        "tests": tests,
        "full_decode": {
            "ok": decode_ok,
            "skipped": args.skip_decode,
            "results": decode_results,
        },
        "profiles": profiles,
        "hevc444_delivery": {
            "status": "pass" if hevc444_ok else "fail",
            "report": artifact(hevc444_report_path, root)
            if hevc444_report_path.is_file()
            else None,
            "validation": hevc444_validation,
            "profiles": hevc444_profiles,
        },
        "av1_420_delivery": {
            "status": "pass" if av1_ok else "fail",
            "report": artifact(av1_report_path, root)
            if av1_report_path.is_file()
            else None,
            "validation": av1_validation,
            "profiles": av1_profiles,
        },
        "numbered_packages": {
            "status": "pass" if numbered_packages_ok else "fail",
            "report": artifact(numbered_packages_path, root)
            if numbered_packages_path.is_file()
            else None,
            "validation": numbered_packages_validation,
        },
        "alignment_audit_validation": alignment_validation,
        "alignment_audit_provenance": audit_provenance_validation,
        "timing_sources": {
            "mms_alignment_audit": (
                artifact(alignment_audit_path, root)
                if alignment_audit_path.is_file()
                else None
            ),
            "timing_overrides": (
                artifact(timing_overrides_path, root)
                if timing_overrides_path.is_file()
                else None
            ),
        },
        "source_artifacts": {
            track["title"]: {
                "audio": artifact(track["audio"], root),
                "sug": artifact(
                    root / "timing" / f"{track['timing_stem']}.sug",
                    root,
                ),
            }
            for track in tracks
        },
        "timing_validation": timing_validation,
        "cue_count_validation": cue_count_validation,
        "timing_report": artifact(timing_report_path, root),
    }
    manifest_path = root / "validation" / "release_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    checksum_count = write_checksums(root)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "manifest": str(manifest_path),
                "checksum_entries": checksum_count,
                "tests": tests,
                "full_decode_ok": decode_ok,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if status_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
