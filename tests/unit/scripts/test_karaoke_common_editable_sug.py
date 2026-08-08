from __future__ import annotations

import json
from pathlib import Path

from scripts.karaoke_common import editable_sug, media_metadata


def test_export_editable_sug_rewrites_and_verifies_media_path(tmp_path: Path):
    audio = tmp_path / "source" / "mix.flac"
    audio.parent.mkdir()
    audio.write_bytes(b"audio")
    source = tmp_path / "timing" / "song.sug"
    source.parent.mkdir()
    source.write_text(
        json.dumps({"version": "2.0", "sentences": [{"characters": []}]}),
        encoding="utf-8",
    )

    report = editable_sug.export_editable_sug(source, audio, tmp_path / "render")

    destination = Path(report["path"])
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert (destination.parent / document["media_path"]).resolve() == audio.resolve()
    assert report["sentence_count"] == 1


def test_display_metadata_prefers_override_then_tags_then_track(monkeypatch):
    monkeypatch.setattr(
        media_metadata,
        "read_album_tags",
        lambda _path: {"album_title": "Tagged Album", "album_artist": "Tagged Artist"},
    )
    tagged = media_metadata.resolve_display_metadata(
        audio_path=Path("mix.flac"), title="Song", artist="Singer",
        album_title=None, album_artist=None,
    )
    explicit = media_metadata.resolve_display_metadata(
        audio_path=Path("mix.flac"), title="Song", artist="Singer",
        album_title="Override Album", album_artist="Override Artist",
    )

    assert tagged["album_title"] == "Tagged Album"
    assert tagged["album_artist"] == "Tagged Artist"
    assert explicit["album_title"] == "Override Album"
    assert explicit["album_artist"] == "Override Artist"
