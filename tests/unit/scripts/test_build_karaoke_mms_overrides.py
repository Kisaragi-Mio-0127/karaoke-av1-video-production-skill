from __future__ import annotations

import sys
from pathlib import Path

import pytest

BUNDLE = Path(__file__).resolve().parents[3] / "integration" / "strangeutagame"
if str(BUNDLE) not in sys.path:
    sys.path.insert(0, str(BUNDLE))

from scripts import karaoke_album as _karaoke_album  # noqa: E402

_karaoke_album.DEFAULT_MANIFEST_PATH = (
    BUNDLE.parents[1] / "examples" / "album.example.json"
)
_load_public_manifest = _karaoke_album.load_album_manifest


def _load_example_manifest(path, *, require_five_tracks=True):
    del require_five_tracks
    return _load_public_manifest(path, require_five_tracks=False)


_karaoke_album.load_album_manifest = _load_example_manifest

from scripts.build_karaoke_mms_overrides import build_overrides  # noqa: E402

GLYPH = "\u6b4c"
SONG_ID = "song-ja"


def _audit(*, lines: list[dict] | None = None) -> dict:
    if lines is None:
        lines = [
            {
                "line_index": 0,
                "text": GLYPH,
                "timed_character_indices": [0],
                "dual_audio_comparisons": [
                    {
                        "character_index": 0,
                        "character": GLYPH,
                        "current_ms": 1_000,
                        "vocal_mms_ms": 1_010,
                        "mix_mms_ms": 1_015,
                        "vocal_minus_mix_ms": -5,
                        "vocal_score": 0.9,
                        "mix_score": 0.8,
                    }
                ],
            }
        ]
    return {
        "model": "torchaudio.pipelines.MMS_FA",
        "model_path": "models/mms-local.pt",
        "model_sha256": "a" * 64,
        "lyric_source_path": "sources/frozen-lyrics.json",
        "lyric_source_sha256": "b" * 64,
        "manifest_path": "album.json",
        "manifest_sha256": "c" * 64,
        "lyric_corrections_path": "sources/corrections.json",
        "lyric_corrections_sha256": "d" * 64,
        "songs": [
            {
                "song_id": SONG_ID,
                "language": "ja",
                "sug_path": "timing/song.sug",
                "sug_sha256": "e" * 64,
                "vocals_path": "evidence/Vocals.wav",
                "vocals_sha256": "f" * 64,
                "mix_path": "audio/mix.flac",
                "mix_sha256": "1" * 64,
                "lines": lines,
            }
        ],
    }


def test_character_overrides_are_non_rendering_evidence_and_provenance_is_bound():
    result = build_overrides(
        _audit(),
        {"songs": {}},
        audit_relative_path="audit/mms.json",
        line_windows={(SONG_ID, 0): (900, 2_000)},
        line_texts={(SONG_ID, 0): GLYPH},
        target_song_ids=(SONG_ID,),
        audit_sha256="2" * 64,
    )

    line = result["songs"][SONG_ID]["lines"]["0"]
    assert line["character_overrides_ms"]
    assert line["character_overrides_ms_semantics"] == {
        "role": "evidence",
        "applied_to_render": False,
    }
    provenance = result["mms_provenance"]
    assert provenance["audit_sha256"] == "2" * 64
    assert provenance["manifest_sha256"] == "c" * 64
    assert provenance["song_inputs"][SONG_ID]["sug_sha256"] == "e" * 64
    assert provenance["policy"]["character_overrides_ms"]["applied_to_render"] is False


def test_empty_audit_lines_are_rejected_instead_of_vacuously_passing():
    with pytest.raises(ValueError, match="contains no timing lines"):
        build_overrides(
            _audit(lines=[]),
            {"songs": {}},
            audit_relative_path="audit/mms.json",
            line_windows={},
            line_texts={},
            target_song_ids=(SONG_ID,),
        )
