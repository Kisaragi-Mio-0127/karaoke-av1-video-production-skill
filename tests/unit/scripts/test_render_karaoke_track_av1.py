from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import render_karaoke_track as renderer


def test_libaom_av1_render_command_uses_release_yuv420p(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, list[str]] = {}
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"fixture")

    def fake_run(command, **_kwargs):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"av1 fixture")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(renderer, "resolve_ffmpeg", lambda **_kwargs: ffmpeg)
    monkeypatch.setattr(renderer.subprocess, "run", fake_run)
    output = tmp_path / "probe.mp4"

    report = renderer.render_karaoke_video(
        ass_path=tmp_path / "lyrics.ass",
        audio_path=tmp_path / "audio.mp3",
        composition_path=tmp_path / "composition.png",
        vinyl_path=tmp_path / "vinyl.png",
        fonts_dir=tmp_path,
        output_path=output,
        start_seconds=0,
        duration_seconds=1,
        video_encoder="libaom-av1",
    )

    command = captured["command"]
    assert command[command.index("-c:v") + 1] == "libaom-av1"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-colorspace") + 1] == "bt709"
    assert command[command.index("-color_range") + 1] == "tv"
    assert report["pixel_format"] == "yuv420p"
