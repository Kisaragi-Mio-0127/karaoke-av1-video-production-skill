import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.karaoke_album import (
    EXPECTED_TRACK_COUNT,
    AlbumManifestError,
    load_album_manifest,
    project_relative,
    validate_exact_five_track_collection,
)


def _write_manifest(tmp_path: Path, track_count: int) -> Path:
    manifest = tmp_path / "album.json"
    document = {
        "schema_version": "karaoke-album/v1",
        "album": {"title": "Generic Album", "artist": "Generic Artist"},
        "paths": {
            "audio_directory": "audio",
            "font_package": "fonts",
            "deliverable_directory": "deliverables",
        },
        "tracks": [
            {
                "disc": 1,
                "track_number": index,
                "song_id": f"generic-song-{index}",
                "title": f"Generic Title {index}",
                "artist": "Generic Artist",
                "artifact_slug": f"generic-track-{index}",
                "audio_file": f"generic-track-{index}.flac",
                "audio_sha256": f"{index:x}" * 64,
                "expected_duration_ms": 10_000 + index,
                "expected_cues": index,
                "language": "en",
            }
            for index in range(1, track_count + 1)
        ],
    }
    manifest.write_text(json.dumps(document), encoding="utf-8")
    return manifest


def test_manifest_path_is_required() -> None:
    with pytest.raises(TypeError, match="path"):
        load_album_manifest()  # type: ignore[call-arg]


@pytest.mark.parametrize("track_count", [1, EXPECTED_TRACK_COUNT])
def test_manifest_loader_supports_single_track_and_album(
    tmp_path: Path, track_count: int
) -> None:
    manifest = _write_manifest(tmp_path, track_count)

    album = load_album_manifest(manifest)

    assert len(album.tracks) == track_count
    assert [track.song_id for track in album.tracks] == [
        f"generic-song-{index}" for index in range(1, track_count + 1)
    ]
    assert album.tracks[0].numbered_video_filename == "01 Generic Title 1.mp4"


def test_exact_five_track_gate_rejects_missing_and_duplicate_tracks(
    tmp_path: Path,
) -> None:
    album = load_album_manifest(_write_manifest(tmp_path, EXPECTED_TRACK_COUNT))

    with pytest.raises(AlbumManifestError, match="exactly 5"):
        validate_exact_five_track_collection(album.tracks[:-1])

    duplicate = replace(album.tracks[-1], song_id=album.tracks[0].song_id)
    with pytest.raises(AlbumManifestError, match="duplicate song_id"):
        validate_exact_five_track_collection((*album.tracks[:-1], duplicate))


def test_project_relative_falls_back_to_absolute_path_across_windows_drives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = (tmp_path / "artifact.mp4").resolve()

    def cross_volume(*_args, **_kwargs):
        raise ValueError("path is on mount C:, start on mount D:")

    monkeypatch.setattr("scripts.karaoke_album.os.path.relpath", cross_volume)

    assert project_relative(target, tmp_path) == target.as_posix()
