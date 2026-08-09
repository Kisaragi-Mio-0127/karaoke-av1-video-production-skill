from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from scripts import karaoke_workflow as workflow
from scripts.karaoke_common.subtitle_video import (
    build_background_composite_command,
    build_transparent_overlay_command,
)
from scripts.run_karaoke_japanese_workflow import make_parser


def test_subtitle_overlay_parser_uses_project_colors_and_accepts_background_video():
    args = make_parser().parse_args(
        [
            "--sug",
            "song.sug",
            "--audio",
            "song.flac",
            "--output-dir",
            "out",
            "--title",
            "Title",
            "--artist",
            "Artist",
            "--output-mode",
            "subtitle-overlay",
            "--background-video",
            "footage.mp4",
        ]
    )

    config = workflow.config_from_args(
        args,
        language="ja",
        layout="wide",
        pronunciation_validation=args.pronunciation_validation,
    )

    assert config.output_mode == "subtitle-overlay"
    assert config.color_policy == "project"
    assert config.background_video == Path("footage.mp4")


def test_background_video_requires_subtitle_overlay(tmp_path: Path):
    config = workflow.WorkflowConfig(
        sug=tmp_path / "song.sug",
        audio=tmp_path / "song.flac",
        composition=None,
        canonical_vinyl=None,
        output_dir=tmp_path / "out",
        language="ja",
        layout="wide",
        title="Title",
        artist="Artist",
        album_title=None,
        album_artist=None,
        background_video=tmp_path / "footage.mp4",
    )

    with pytest.raises(workflow.KaraokeWorkflowError, match="requires"):
        workflow.validate_visual_contract(config)

    overlay = replace(config, output_mode="subtitle-overlay", color_policy="project")
    workflow.validate_visual_contract(overlay)


def test_transparent_overlay_command_selects_prores_4444_alpha(tmp_path: Path):
    command = build_transparent_overlay_command(
        ffmpeg=tmp_path / "ffmpeg.exe",
        ass_path=tmp_path / "karaoke.ass",
        fonts_dir=tmp_path / "fonts",
        output_path=tmp_path / "overlay.mov",
        duration_seconds=12.5,
    )

    assert command[command.index("-c:v") + 1] == "prores_ks"
    assert command[command.index("-profile:v") + 1] == "4"
    assert command[command.index("-pix_fmt") + 1] == "yuva444p10le"
    assert "-an" in command
    assert "alpha=1" in command[command.index("-vf") + 1]


def test_background_composite_command_trims_and_black_pads(tmp_path: Path):
    command = build_background_composite_command(
        ffmpeg=tmp_path / "ffmpeg.exe",
        background_video=tmp_path / "footage.mp4",
        audio_path=tmp_path / "song.flac",
        ass_path=tmp_path / "karaoke.ass",
        fonts_dir=tmp_path / "fonts",
        output_path=tmp_path / "composite.mp4",
        start_seconds=0.0,
        duration_seconds=20.0,
    )
    graph = command[command.index("-filter_complex") + 1]

    assert "tpad=stop_mode=add:stop_duration=20.000" in graph
    assert "trim=duration=20.000" in graph
    assert "atrim=start=0.000:end=20.000" in graph
    assert "pad=1920:1080" in graph
    assert command[command.index("-c:v") + 1] == "av1_nvenc"
    assert command[command.index("-c:a") + 1] == "aac"
