from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import karaoke_workflow as workflow
from scripts.karaoke_common.subtitle_video import (
    build_av1_encoder_smoke_command,
    build_background_composite_command,
    build_transparent_overlay_command,
    parse_available_av1_encoders,
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
    assert command[command.index("-movflags") + 1] == "+write_colr"
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
        video_encoder="av1_nvenc",
    )
    graph = command[command.index("-filter_complex") + 1]

    assert "tpad=stop_mode=add:stop_duration=20.000" in graph
    assert "trim=duration=20.000" in graph
    assert "atrim=start=0.000:end=20.000" in graph
    assert "pad=1920:1080" in graph
    assert command[command.index("-c:v") + 1] == "av1_nvenc"
    assert command[command.index("-tag:v") + 1] == "av01"
    assert command[command.index("-c:a") + 1] == "aac"
    assert "-shortest" not in command


def test_av1_encoder_discovery_prefers_nvenc_then_libaom():
    output = """
 V....D libaom-av1           libaom AV1
 V....D av1_nvenc            NVIDIA NVENC AV1
"""

    assert parse_available_av1_encoders(output) == (
        "av1_nvenc",
        "libaom-av1",
    )


def test_libaom_command_does_not_receive_nvenc_options(tmp_path: Path):
    command = build_background_composite_command(
        ffmpeg=tmp_path / "ffmpeg.exe",
        background_video=tmp_path / "footage.mp4",
        audio_path=tmp_path / "song.flac",
        ass_path=tmp_path / "karaoke.ass",
        fonts_dir=tmp_path / "fonts",
        output_path=tmp_path / "composite.mp4",
        start_seconds=0.0,
        duration_seconds=2.0,
        video_encoder="libaom-av1",
    )

    assert command[command.index("-c:v") + 1] == "libaom-av1"
    assert "-crf" in command
    assert "-row-mt" in command
    assert "-cq" not in command
    assert "-multipass" not in command
    assert "-spatial-aq" not in command


def test_encoder_smoke_command_uses_selected_encoder(tmp_path: Path):
    command = build_av1_encoder_smoke_command(
        tmp_path / "ffmpeg.exe",
        video_encoder="libaom-av1",
    )

    assert command[command.index("-c:v") + 1] == "libaom-av1"
    assert command[-2:] == ["null", "-"]


def test_background_render_retries_libaom_after_nvenc_failure(tmp_path: Path):
    output = tmp_path / "composite.mp4"
    render_encoders: list[str] = []

    def runner(command):
        command = list(command)
        if "-encoders" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                " V....D av1_nvenc\n V....D libaom-av1\n",
                "",
            )
        encoder = command[command.index("-c:v") + 1]
        if command[-1] == "-":
            return subprocess.CompletedProcess(command, 0, "", "")
        render_encoders.append(encoder)
        partial = Path(command[-1])
        partial.write_bytes(b"failed" if encoder == "av1_nvenc" else b"av1")
        return subprocess.CompletedProcess(
            command,
            1 if encoder == "av1_nvenc" else 0,
            "",
            "nvenc unavailable" if encoder == "av1_nvenc" else "",
        )

    _completed, selected, attempts = workflow.render_background_with_av1_fallback(
        ffmpeg=tmp_path / "ffmpeg.exe",
        background_video=tmp_path / "footage.mp4",
        audio_path=tmp_path / "song.flac",
        ass_path=tmp_path / "karaoke.ass",
        fonts_dir=tmp_path / "fonts",
        output_path=output,
        start_seconds=0.0,
        duration_seconds=2.0,
        runner=runner,
    )

    assert selected == "libaom-av1"
    assert render_encoders == ["av1_nvenc", "libaom-av1"]
    assert [attempt["render_returncode"] for attempt in attempts] == [1, 0]
    assert output.read_bytes() == b"av1"
    assert not list(tmp_path.glob(".*.partial.mp4"))


def test_transparent_overlay_media_gate_requires_prores_alpha_contract():
    final_probe = {
        "duration_seconds": 3.0,
        "video_stream": {
            "codec": "prores",
            "codec_tag": "ap4h",
            "pixel_format": "yuva444p10le",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
        },
        "audio_stream": None,
        "bt709": True,
    }

    gate = workflow.build_overlay_media_gate(
        final_probe=final_probe,
        duration_seconds=3.0,
        transparent=True,
    )

    assert all(gate.values())
    final_probe["video_stream"]["codec_tag"] = "apcn"
    failed_gate = workflow.build_overlay_media_gate(
        final_probe=final_probe,
        duration_seconds=3.0,
        transparent=True,
    )
    assert failed_gate["prores_4444_tag_ap4h"] is False


def test_background_overlay_media_gate_requires_av1_aac_contract():
    final_probe = {
        "duration_seconds": 3.0,
        "video_stream": {
            "codec": "av1",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
        },
        "audio_stream": {"codec": "aac"},
        "bt709": True,
    }
    verified = {
        "codec_av1": True,
        "codec_tag_av01": True,
        "profile_main": True,
        "pixel_format_yuv420p": True,
        "yuv_limited_range": True,
        "resolution_1920x1080": True,
        "cfr_30fps": True,
        "aac_audio": True,
        "aac_lc_profile": True,
    }

    gate = workflow.build_overlay_media_gate(
        final_probe=final_probe,
        duration_seconds=3.0,
        transparent=False,
        existing_gate=verified,
    )

    assert all(gate.values())
    verified["aac_lc_profile"] = False
    failed_gate = workflow.build_overlay_media_gate(
        final_probe=final_probe,
        duration_seconds=3.0,
        transparent=False,
        existing_gate=verified,
    )
    assert failed_gate["aac_lc_profile"] is False
