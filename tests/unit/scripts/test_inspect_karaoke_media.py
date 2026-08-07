from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import inspect_karaoke_media as inspector


def _burn_ready_ass(path: Path) -> None:
    path.write_text(
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,HarmonyOS Sans SC,58,&H00FFFFFF,&H00FFFFFF,&H00000000,"
        "&H80000000,1,0,0,0,100,100,0,0,1,3,0,3,980,80,100,1\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,歌詞\n",
        encoding="utf-8",
    )


def test_parser_exposes_partial_manifest_gate():
    args = inspector.make_parser().parse_args(
        ["--manifest", "album.json", "--allow-partial-manifest"]
    )

    assert args.allow_partial_manifest is True


def test_inspect_track_requires_real_lyrics_libass_font_probe(
    tmp_path: Path,
    monkeypatch,
):
    audio = tmp_path / "song.mp3"
    video = tmp_path / "song.mp4"
    ass = tmp_path / "song.ass"
    audio.write_bytes(b"audio")
    video.write_bytes(b"video")
    _burn_ready_ass(ass)
    probe_calls: list[Path] = []

    def fake_font_probe(*_args, ass_path: Path | None = None, **_kwargs):
        assert ass_path is not None
        probe_calls.append(ass_path)
        return {
            "ok": True,
            "filter": "subtitles",
            "probe_kind": "real_lyrics",
            "ass_path": str(ass_path),
        }

    monkeypatch.setattr(inspector, "probe_libass_font", fake_font_probe)
    monkeypatch.setattr(inspector, "audio_duration", lambda _path: 1.0)
    monkeypatch.setattr(
        inspector,
        "embedded_cover",
        lambda _path: (None, {"present": False}),
    )
    monkeypatch.setattr(
        inspector,
        "probe_media",
        lambda *_args, **_kwargs: {
            "path": "song.mp4",
            "exists": True,
            "duration_seconds": 1.0,
            "video_stream": {
                "codec": "hevc",
                "codec_tag": "hvc1",
                "profile": "Rext",
                "width": 1920,
                "height": 1080,
                "pixel_format": "yuv444p",
                "fps": 30.0,
                "raw": "yuv444p(pc, bt709), 1920x1080, 30 fps",
            },
            "audio_stream": {"codec": "aac"},
        },
    )
    args = SimpleNamespace(
        video=video,
        ass=ass,
        video_dir=None,
        timing_dir=tmp_path,
        slug="fixture",
        deliverable_root=tmp_path / "deliverable",
        project_root=tmp_path,
        duration_tolerance=0.1,
    )
    font_info = {
        "directory": str(tmp_path / "fonts"),
        "family": "HarmonyOS Sans SC",
        "regular": {},
        "bold": {},
    }

    report = inspector.inspect_track(
        args=args,
        track={
            "audio": audio,
            "title": "Song",
            "artist": "Artist",
            "basename": "song",
        },
        ffmpeg=tmp_path / "ffmpeg.exe",
        all_tracks=False,
        font_info=font_info,
        libass_font_probe={"ok": True, "filter": "subtitles"},
    )

    assert report["status"] == "pass"
    assert report["checks"]["real_lyrics_libass_font"] is True
    assert probe_calls == [ass.resolve()]
