from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.finalize_karaoke_release import (
    PROFILES,
    validate_alignment_audit,
    validate_audit_source_provenance,
    validate_av1_420_delivery,
    validate_hevc444_delivery,
    validate_timing_report,
)


def _sha256(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_audit_source_provenance_requires_current_project_local_hashes(tmp_path):
    paths = {}
    for name in (
        "manifest.json",
        "lyrics.json",
        "corrections.json",
        "model.pt",
        "vocals.wav",
        "mix.mp3",
    ):
        path = tmp_path / name
        path.write_bytes(name.encode("utf-8"))
        paths[name] = path
    audit = {
        "manifest_path": "manifest.json",
        "manifest_sha256": _sha256(paths["manifest.json"]),
        "netease_lyrics_path": "lyrics.json",
        "netease_lyrics_sha256": _sha256(paths["lyrics.json"]),
        "lyric_corrections_path": "corrections.json",
        "lyric_corrections_sha256": _sha256(paths["corrections.json"]),
        "model_path": "model.pt",
        "model_sha256": _sha256(paths["model.pt"]),
        "songs": [
            {
                "song_id": "1",
                "vocals_path": "vocals.wav",
                "vocals_sha256": _sha256(paths["vocals.wav"]),
                "mix_path": "mix.mp3",
                "mix_sha256": _sha256(paths["mix.mp3"]),
                "sug_sha256": "a" * 64,
            }
        ],
    }

    assert validate_audit_source_provenance(audit, project_root=tmp_path)["ok"] is True
    paths["mix.mp3"].write_bytes(b"changed")
    result = validate_audit_source_provenance(audit, project_root=tmp_path)
    assert result["ok"] is False
    assert result["songs"]["1"]["original_mix"]["hash_ok"] is False


FIXTURE_TRACKS = tuple(
    {
        "song_id": str(9000 + index),
        "artifact_slug": f"song-{index}",
        "track_number": index + 1,
        "title": f"Song {index + 1}",
        "numbered_video_filename": f"{index + 1:02d} Song {index + 1}.mp4",
    }
    for index in range(5)
)


def _json_fixture(value: object) -> object:
    """Keep these gates pure-JSON: no media or project files are needed."""

    return json.loads(json.dumps(value))


def valid_alignment_fixtures() -> tuple[dict, dict, dict[str, dict]]:
    sug_documents: dict[str, dict] = {}
    audit_songs: list[dict] = []
    override_songs: dict[str, dict] = {}
    for track_index, track in enumerate(FIXTURE_TRACKS):
        song_id = track["song_id"]
        texts = [f"甲{track_index} 乙", "丙丁"]
        sentences = []
        audit_lines = []
        override_lines = {}
        for line_index, text in enumerate(texts):
            characters = [
                {
                    "char": character,
                    "timestamps": [1000 + line_index * 100 + index]
                    if not character.isspace()
                    else [],
                }
                for index, character in enumerate(text)
            ]
            sentences.append({"characters": characters})
            comparisons = [
                {"character_index": index, "character": character}
                for index, character in enumerate(text)
                if not character.isspace()
            ]
            audit_lines.append(
                {
                    "line_index": line_index,
                    "text": text,
                    "comparisons": comparisons,
                    "dual_audio_comparisons": list(comparisons),
                }
            )
            override_lines[str(line_index)] = {}
        sug_documents[song_id] = {"sentences": sentences}
        audit_songs.append({"song_id": song_id, "lines": audit_lines})
        override_songs[song_id] = {"lines": override_lines}
    audit = _json_fixture(
        {
            "schema_version": "karaoke-mms-dual-audio-audit/v1",
            "songs": audit_songs,
        }
    )
    overrides = _json_fixture(
        {
            "schema_version": "karaoke-timing-overrides/v2",
            "songs": override_songs,
        }
    )
    return audit, overrides, sug_documents


def valid_hevc444_fixtures() -> tuple[dict, dict[tuple[str, str], bool]]:
    outputs = []
    report_paths = {}
    for profile in PROFILES:
        for track in FIXTURE_TRACKS:
            song_id = track["song_id"]
            outputs.append(
                {
                    "profile": profile,
                    "song_id": song_id,
                    "artifact_slug": track["artifact_slug"],
                    "output": (f"video/hevc444/{profile}/{track['artifact_slug']}.mp4"),
                    "render_mode": "direct-hevc444",
                    "source": f"original audio + artwork + timing/{profile}/ASS",
                    "intermediate_h264": False,
                    "intermediate_av1": False,
                    "direct_hevc444_render_report": (
                        f"validation/{profile}/{track['artifact_slug']}"
                        "_direct_hevc444_render_report.json"
                    ),
                }
            )
            report_paths[(profile, song_id)] = True
    report = _json_fixture(
        {
            "schema_version": "karaoke-hevc444/v1",
            "status": "pass",
            "encoder": "hevc_nvenc",
            "container": "mp4",
            "codec_tag": "hvc1",
            "profile": "Rext",
            "pixel_format": "yuv444p",
            "color_range": "pc",
            "direct_render": {
                "intermediate_h264": False,
                "intermediate_av1": False,
            },
            "outputs": outputs,
        }
    )
    return report, report_paths


def valid_av1_420_fixtures() -> tuple[dict, dict[tuple[str, str], bool]]:
    outputs = []
    report_paths = {}
    for profile in PROFILES:
        for track in FIXTURE_TRACKS:
            song_id = track["song_id"]
            outputs.append(
                {
                    "profile": profile,
                    "song_id": song_id,
                    "artifact_slug": track["artifact_slug"],
                    "output": (
                        f"video/av1-420/{profile}/"
                        f"{track['numbered_video_filename']}"
                    ),
                    "lossless_output": (
                        f"video/av1-420-lossless/{profile}/"
                        f"{Path(track['numbered_video_filename']).with_suffix('.mkv')}"
                    ),
                    "default_delivery": "compatibility_mp4",
                    "audio": "aac-lc-320k",
                    "lossless_audio": "flac",
                    "render_mode": "direct-av1-420",
                    "source": f"original audio + artwork + timing/{profile}/ASS",
                    "source_paths": {
                        "audio": "audio/source.flac",
                        "composition": "artwork/composition.png",
                        "vinyl": "artwork/vinyl.png",
                        "sug": "timing/source.sug",
                        "latest_ass": f"timing/{profile}/source.ass",
                    },
                    "intermediate_video": False,
                    "intermediate_h264": False,
                    "intermediate_hevc": False,
                    "media_checks": {"ok": True},
                    "lossless_media_checks": {"ok": True},
                }
            )
            report_paths[(profile, song_id)] = True
    report = _json_fixture(
        {
            "schema_version": "karaoke-av1-420/v2",
            "status": "pass",
            "encoder": "av1_nvenc",
            "default_delivery": "compatibility_mp4",
            "containers": ["mp4", "matroska"],
            "codec_tag": "av01",
            "profile": "Main",
            "pixel_format": "yuv420p",
            "color_range": "tv",
            "full_decode": True,
            "full_decode_gate": {
                "performed": True,
                "required": False,
                "reason": None,
            },
            "direct_render": {"intermediate_video": False},
            "outputs": outputs,
        }
    )
    return report, report_paths


def test_accepts_complete_json_alignment_fixture() -> None:
    audit, overrides, sug_documents = valid_alignment_fixtures()

    result = validate_alignment_audit(
        audit,
        overrides,
        sug_documents,
        tracks=FIXTURE_TRACKS,
    )

    assert result["ok"] is True
    assert result["expected_songs_present"] is True


def test_rejects_missing_song_from_mms_audit() -> None:
    audit, overrides, sug_documents = valid_alignment_fixtures()
    audit["songs"].pop()

    result = validate_alignment_audit(
        audit,
        overrides,
        sug_documents,
        tracks=FIXTURE_TRACKS,
    )

    assert result["ok"] is False
    assert FIXTURE_TRACKS[-1]["song_id"] in result["missing_song_ids"]


def test_rejects_audit_character_misalignment_against_current_sug() -> None:
    audit, overrides, sug_documents = valid_alignment_fixtures()
    audit["songs"][0]["lines"][0]["dual_audio_comparisons"][0]["character"] = "错"

    result = validate_alignment_audit(
        audit,
        overrides,
        sug_documents,
        tracks=FIXTURE_TRACKS,
    )

    assert result["ok"] is False
    assert (
        result["song_checks"][FIXTURE_TRACKS[0]["song_id"]]["text_and_character_ok"]
        is False
    )


def test_accepts_explicit_inherited_disposition_for_timestamped_character() -> None:
    audit, overrides, sug_documents = valid_alignment_fixtures()
    line = audit["songs"][0]["lines"][0]
    line["dual_audio_comparisons"] = line["dual_audio_comparisons"][1:]
    overrides["songs"][FIXTURE_TRACKS[0]["song_id"]]["lines"]["0"] = {
        "character_dispositions": {"0": "inherited-stable-ts"},
    }

    result = validate_alignment_audit(
        audit,
        overrides,
        sug_documents,
        tracks=FIXTURE_TRACKS,
    )

    assert result["ok"] is True


def test_accepts_ten_direct_hevc444_entries_without_h264_or_av1_master() -> None:
    report, report_paths = valid_hevc444_fixtures()

    result = validate_hevc444_delivery(
        report,
        tracks=FIXTURE_TRACKS,
        profiles=PROFILES,
        direct_report_paths=report_paths,
    )

    assert result["ok"] is True
    assert result["expected_entry_count"] == 10


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("intermediate_h264", True),
        ("intermediate_av1", True),
        ("source", "video/standard/song-0.mp4"),
        ("source_chain", "original audio + artwork -> av1 master -> HEVC"),
    ],
)
def test_rejects_forbidden_intermediate_video_provenance(
    field: str, value: object
) -> None:
    report, report_paths = valid_hevc444_fixtures()
    report["outputs"][0][field] = value

    result = validate_hevc444_delivery(
        report,
        tracks=FIXTURE_TRACKS,
        profiles=PROFILES,
        direct_report_paths=report_paths,
    )

    assert result["ok"] is False
    assert result["entry_checks"]["standard:9000"]["ok"] is False


def test_accepts_ten_direct_av1_420_dual_delivery_entries() -> None:
    report, report_paths = valid_av1_420_fixtures()

    result = validate_av1_420_delivery(
        report,
        tracks=FIXTURE_TRACKS,
        profiles=PROFILES,
        direct_report_paths=report_paths,
    )

    assert result["ok"] is True
    assert result["reported_full_decode_ok"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pixel_format", "gbrp"),
        ("color_range", "pc"),
        ("containers", ["mp4"]),
    ],
)
def test_rejects_wrong_av1_420_release_contract(field: str, value: object) -> None:
    report, report_paths = valid_av1_420_fixtures()
    report[field] = value

    result = validate_av1_420_delivery(
        report,
        tracks=FIXTURE_TRACKS,
        profiles=PROFILES,
        direct_report_paths=report_paths,
    )

    assert result["ok"] is False


def valid_report() -> dict:
    def song(song_id: str) -> dict:
        return {
            "song_id": song_id,
            "alignment": {
                "requested_mode": "forced",
                "status": "ok",
                "audio_kind": "msst-karaoke-vocals",
                "original_mix_cross_check": {"status": "ok"},
                "gate_ok": True,
            },
            "project_validation": {"ok": True},
        }

    return {
        "schema_version": "karaoke-timing-report/v1",
        "ok": True,
        "songs": [song(track["song_id"]) for track in FIXTURE_TRACKS],
    }


def test_accepts_complete_forced_double_alignment_gate() -> None:
    result = validate_timing_report(valid_report(), FIXTURE_TRACKS)

    assert result["ok"] is True
    assert result["expected_songs_present"] is True


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("ok",), False),
        (("schema_version",), "karaoke-timing-report/v0"),
        (("songs", 0, "alignment", "requested_mode"), "auto"),
        (("songs", 0, "alignment", "status"), "failed"),
        (("songs", 0, "alignment", "audio_kind"), "original-mix"),
        (("songs", 0, "alignment", "original_mix_cross_check", "status"), "failed"),
        (("songs", 0, "alignment", "gate_ok"), False),
        (("songs", 0, "project_validation", "ok"), False),
    ],
)
def test_rejects_failed_timing_gate(path: tuple, value: object) -> None:
    report = deepcopy(valid_report())
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    assert validate_timing_report(report, FIXTURE_TRACKS)["ok"] is False


def test_rejects_missing_expected_song() -> None:
    report = valid_report()
    report["songs"].pop()

    assert validate_timing_report(report, FIXTURE_TRACKS)["ok"] is False
