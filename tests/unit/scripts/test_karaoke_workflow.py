from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import karaoke_workflow as workflow
from scripts import render_karaoke_direct_av1_420_album as direct_renderer
from scripts import render_vinyl_karaoke as renderer
from scripts.run_karaoke_japanese_workflow import make_parser as japanese_parser


def _config(
    tmp_path: Path,
    *,
    language: str = "ja",
    visual_style: str = "vinyl",
) -> workflow.WorkflowConfig:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    sug = inputs / "song.sug"
    sug.write_text('{"sentences": []}', encoding="utf-8")
    audio = inputs / "song.flac"
    audio.write_bytes(b"audio")
    composition = inputs / "composition.png"
    composition.write_bytes(b"composition")
    generator = direct_renderer.WIDE_ARTWORK_GENERATOR
    version_match = re.search(
        r'^WIDE_LAYOUT_VERSION\s*=\s*[\'\"]([^\'\"]+)[\'\"]',
        generator.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert version_match is not None
    composition.with_suffix(".json").write_text(
        json.dumps(
            {
                "layout_version": version_match.group(1),
                "layout_generator_sha256": direct_renderer.sha256_file(generator),
                "composition_sha256": direct_renderer.sha256_file(composition),
                "visual_style": visual_style,
                "sleeve": (
                    {"x": 40, "y": 30, "width": 340, "height": 402}
                    if visual_style == "vinyl"
                    else {"x": 40, "y": 30, "width": 460, "height": 522}
                ),
                "title_block_x": 430 if visual_style == "vinyl" else 800,
                "title_block_y": {"label": 120, "title": 155, "artist": 220},
                "title_bounds": [
                    428 if visual_style == "vinyl" else 798,
                    123,
                    858 if visual_style == "vinyl" else 1228,
                    253,
                ],
                "secondary_overlay_contract": {
                    "anchor_y": 12,
                    "font_size_px": 60,
                    "safe_bounds": [0, 0, 1920, 96],
                    "outline_px": 3,
                    "glow_px": 8,
                    "reserved_bounds": [0, 0, 1920, 107],
                },
                "secondary_reserved_bounds": [0, 0, 1920, 107],
                "title_secondary_clearance_px": 16,
                "title_secondary_collision": False,
                "bottom_panel": [20, 576, 1900, 1050],
                "right_panel": None,
                "right_panel_visible": False,
                "outer_right_panel": None,
                "outer_right_panel_visible": False,
                "vinyl_backplate": None,
                "vinyl_backplate_present": False,
                "vinyl_backplate_preserved": False,
            }
        ),
        encoding="utf-8",
    )
    vinyl = inputs / "vinyl.png"
    vinyl.write_bytes(b"old-vinyl")
    fonts = inputs / "fonts"
    fonts.mkdir()
    font_file = fonts / "font.ttf"
    font_file.write_bytes(b"font")
    ffmpeg = inputs / "ffmpeg.exe"
    ffmpeg.write_bytes(b"ffmpeg")
    return workflow.WorkflowConfig(
        sug=sug,
        audio=audio,
        composition=composition,
        canonical_vinyl=vinyl if visual_style == "vinyl" else None,
        output_dir=tmp_path / "fresh-output",
        language=language,
        layout={"ja": "wide", "zh": "wide-zh", "en": "wide-en"}[language],
        title="Title",
        artist="Artist",
        album_title="Album",
        album_artist="Album Artist",
        fonts_dir=fonts,
        font_file=font_file,
        smoke_duration=5.0,
        pronunciation_validation="optional" if language == "ja" else "off",
        visual_style=visual_style,
        ffmpeg=ffmpeg,
    )


def _renderer_report(
    config: workflow.WorkflowConfig,
    *,
    generated_vinyl: Path | None,
) -> dict[str, object]:
    color_plan = {
        "schema_version": "karaoke-color-plan/v1",
        "color_plan_sha256": "test-color-plan",
        "visual": {
            "spectrum_color": config.spectrum_color or "#123456",
            "progress_color": config.progress_color or "#654321",
        },
    }
    if config.visual_style == "vinyl":
        assert generated_vinyl is not None
        video = {
            "visual_style": "vinyl",
            "vinyl_motion": "rotate",
            "vinyl_asset": {
                "path": str(generated_vinyl.resolve()),
                "sha256": workflow.sha256_file(generated_vinyl),
            },
            "color_plan_sha256": color_plan["color_plan_sha256"],
        }
    elif config.visual_style == "spectrum-mirror":
        video = {
            "visual_style": "spectrum-mirror",
            "vinyl_motion": None,
            "vinyl_asset": None,
            "spectrum_geometry": {"x": 800, "y": 290, "width": 1040, "height": 220},
            "spectrum_clip_safe_geometry": {
                "x": 736,
                "y": 226,
                "width": 1168,
                "height": 348,
            },
            "spectrum_mode": "glowing-symmetric-40-point-ripple",
            "spectrum_mirror_points": 40,
            "spectrum_mirror_frequency_points": 38,
            "spectrum_mirror_center_y": 400,
            "spectrum_mirror_center_gap_px": 0,
            "spectrum_mirror_exact_symmetry": True,
            "spectrum_mirror_antialias": "4x-ssaa-lanczos",
            "spectrum_mirror_height_depth_bits": 16,
            "spectrum_mirror_stems_to_center": True,
            "spectrum_mirror_stem_width_px": 2,
            "spectrum_mirror_stem_alpha": 0.55,
            "spectrum_color": color_plan["visual"]["spectrum_color"],
            "color_plan_sha256": color_plan["color_plan_sha256"],
            "progress_bar": {
                "show_time": False,
                "color": color_plan["visual"]["progress_color"],
            },
        }
    elif config.visual_style == "spectrum-dots":
        video = {
            "visual_style": "spectrum-dots",
            "vinyl_motion": None,
            "vinyl_asset": None,
            "spectrum_geometry": {"x": 800, "y": 290, "width": 1040, "height": 220},
            "spectrum_clip_safe_geometry": {
                "x": 736,
                "y": 226,
                "width": 1168,
                "height": 348,
            },
            "spectrum_mode": "glowing-52-column-dot-matrix",
            "spectrum_dot_columns": 52,
            "spectrum_dot_rows": 10,
            "spectrum_dot_cell_size_px": 20,
            "spectrum_dot_gap_px": 8,
            "spectrum_dot_vertical_padding_px": 10,
            "spectrum_dot_trail_decay": 0.93,
            "spectrum_color": color_plan["visual"]["spectrum_color"],
            "color_plan_sha256": color_plan["color_plan_sha256"],
            "progress_bar": {
                "show_time": False,
                "color": color_plan["visual"]["progress_color"],
            },
        }
    elif config.visual_style == "spectrum-ribbon":
        video = {
            "visual_style": "spectrum-ribbon",
            "vinyl_motion": None,
            "vinyl_asset": None,
            "spectrum_geometry": {"x": 800, "y": 290, "width": 1040, "height": 220},
            "spectrum_clip_safe_geometry": {
                "x": 736,
                "y": 226,
                "width": 1168,
                "height": 348,
            },
            "spectrum_mode": "dual-color-40-point-neon-ribbon-trails",
            "spectrum_baseline_y": 510,
            "spectrum_baseline_visible": False,
            "spectrum_ribbon_points": 40,
            "spectrum_ribbon_frequency_points": 38,
            "spectrum_ribbon_zero_boundary_points": 2,
            "spectrum_ribbon_antialias": "4x-ssaa-lanczos",
            "spectrum_ribbon_trail_frames": 7,
            "spectrum_ribbon_trail_color_source": "progress-color",
            "spectrum_ribbon_stems_visible": False,
            "spectrum_color": color_plan["visual"]["spectrum_color"],
            "color_plan_sha256": color_plan["color_plan_sha256"],
            "progress_bar": {
                "show_time": False,
                "color": color_plan["visual"]["progress_color"],
            },
        }
    else:
        video = {
            "visual_style": "spectrum",
            "vinyl_motion": None,
            "vinyl_asset": None,
            "spectrum_geometry": {"x": 800, "y": 290, "width": 1040, "height": 220},
            "spectrum_bar_count": 80,
            "spectrum_clip_safe_geometry": {
                "x": 736,
                "y": 226,
                "width": 1168,
                "height": 348,
            },
            "spectrum_bar_top_clearance_px": 8,
            "spectrum_bar_bottom_clearance_px": 8,
            "spectrum_glow_top_padding_px": 56,
            "spectrum_glow_bottom_padding_px": 56,
            "peak_hold": {"enabled": True},
            "spectrum_color": color_plan["visual"]["spectrum_color"],
            "color_plan_sha256": color_plan["color_plan_sha256"],
            "progress_bar": {
                "show_time": False,
                "color": color_plan["visual"]["progress_color"],
            },
        }
    return {"ass": {"color_plan": color_plan}, "video": video}


def test_ass_report_parity_compares_singer_secondary_and_ruby_facts():
    ass = {
        "singer_color_mapping": [
            {"singer_id": "lead", "effective_color": "#112233"}
        ],
        "sug_hash": "sug-generation",
        "ruby_spans": [{"text": "雨", "reading": "あめ"}],
        "lines": [
            {
                "source_line_index": 0,
                "effective_singer_id": "lead",
                "effective_singer_ids": ["lead"],
                "ruby": [{"text": "雨", "reading": "あめ"}],
            }
        ],
        "secondary_lines": [
            {
                "source_line_index": 1,
                "voice_role": "harmony",
                "effective_singer_id": "harmony",
                "effective_singer_ids": ["harmony"],
                "hot_primary_ass": "&H00332211",
                "ruby": [],
            }
        ],
    }

    parity = workflow.validate_ass_report_parity(
        {"status": "ass-ready", "ass": ass},
        {"status": "ok", "ass": json.loads(json.dumps(ass))},
    )

    assert parity["status"] == "ok"
    assert parity["singer_color_mapping_count"] == 1
    assert parity["secondary_line_count"] == 1

    changed = json.loads(json.dumps(ass))
    changed["secondary_lines"][0]["effective_singer_id"] = "wrong"
    with pytest.raises(workflow.KaraokeWorkflowError, match="report facts differ"):
        workflow.validate_ass_report_parity({"ass": ass}, {"ass": changed})


def test_commands_pass_pronunciation_layout_and_regenerated_vinyl(tmp_path: Path):
    config = replace(
        _config(tmp_path, language="en"),
        singer_colors=("lead=#112233", "harmony=#AABBCC"),
    )
    generated = tmp_path / "new" / "vinyl.png"
    command = workflow.build_render_command(
        config,
        generated_vinyl=generated,
        ass_path=tmp_path / "out.ass",
        report_path=tmp_path / "report.json",
        output_path=tmp_path / "out.mp4",
        duration=5.0,
    )

    assert command[command.index("--vinyl") + 1] == str(generated.resolve())
    assert command[command.index("--pronunciation-validation") + 1] == "off"
    assert command[command.index("--vinyl-motion") + 1] == "rotate"
    assert command[command.index("--layout") + 1] == "wide-en"
    assert command[command.index("--video-encoder") + 1] == "av1_nvenc"
    assert str(config.canonical_vinyl.resolve()) not in command
    assert "--lossless-output" not in command
    assert [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--singer-color"
    ] == ["lead=#112233", "harmony=#AABBCC"]


def test_standard_render_retries_libaom_after_nvenc_failure(tmp_path: Path):
    config = _config(tmp_path, language="ja", visual_style="spectrum")
    output = tmp_path / "out.mp4"
    report = tmp_path / "render-report.json"
    ass = tmp_path / "karaoke.ass"
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
        if command[-1] == "-":
            return subprocess.CompletedProcess(command, 0, "", "")
        encoder = command[command.index("--video-encoder") + 1]
        render_encoders.append(encoder)
        output_path = Path(command[command.index("--output") + 1])
        report_path = Path(command[command.index("--report-output") + 1])
        output_path.write_bytes(b"failed" if encoder == "av1_nvenc" else b"av1")
        if encoder == "libaom-av1":
            report_path.write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            1 if encoder == "av1_nvenc" else 0,
            "",
            "nvenc failed" if encoder == "av1_nvenc" else "",
        )

    _completed, selected, attempts = workflow.render_standard_with_av1_fallback(
        ffmpeg=tmp_path / "ffmpeg.exe",
        config=config,
        generated_vinyl=None,
        ass_path=ass,
        report_path=report,
        output_path=output,
        duration_seconds=2.0,
        lossless_output=None,
        runner=runner,
    )

    assert selected == "libaom-av1"
    assert render_encoders == ["av1_nvenc", "libaom-av1"]
    assert [attempt["render_returncode"] for attempt in attempts] == [1, 0]
    assert output.read_bytes() == b"av1"
    assert report.is_file()


def test_preflight_and_final_commands_share_explicit_singer_colors(tmp_path: Path):
    config = replace(
        _config(tmp_path),
        singer_colors=("lead=#112233", "guest=#445566"),
    )
    command_kwargs = {
        "generated_vinyl": tmp_path / "generated-vinyl.png",
        "ass_path": tmp_path / "out.ass",
        "report_path": tmp_path / "report.json",
        "output_path": tmp_path / "out.mp4",
        "duration": 5.0,
    }

    preflight = workflow.build_ass_command(config, **command_kwargs)
    final = workflow.build_render_command(config, **command_kwargs)

    def singer_colors(command: list[str]) -> list[str]:
        return [
            command[index + 1]
            for index, value in enumerate(command)
            if value == "--singer-color"
        ]

    assert singer_colors(preflight) == ["lead=#112233", "guest=#445566"]
    assert singer_colors(final) == singer_colors(preflight)


def test_preflight_and_final_commands_share_explicit_timing_overrides(
    tmp_path: Path,
):
    overrides = tmp_path / "private" / "timing_overrides.json"
    overrides.parent.mkdir()
    overrides.write_text('{"schema_version":"karaoke-timing-overrides/v2"}')
    config = replace(
        _config(tmp_path),
        timing_overrides=overrides,
        timing_override_song_id="song-1",
    )
    command_kwargs = {
        "generated_vinyl": tmp_path / "generated-vinyl.png",
        "ass_path": tmp_path / "out.ass",
        "report_path": tmp_path / "report.json",
        "output_path": tmp_path / "out.mp4",
        "duration": 5.0,
    }

    preflight = workflow.build_ass_command(config, **command_kwargs)
    final = workflow.build_render_command(config, **command_kwargs)

    for command in (preflight, final):
        assert command[command.index("--timing-overrides") + 1] == str(
            overrides.resolve()
        )
        assert command[command.index("--song-id") + 1] == "song-1"


def test_timing_override_config_must_be_paired(tmp_path: Path):
    config = replace(
        _config(tmp_path),
        timing_overrides=tmp_path / "timing_overrides.json",
    )

    with pytest.raises(workflow.KaraokeWorkflowError, match="provided together"):
        workflow.validate_visual_contract(config)


def test_render_commands_keep_visual_styles_exclusive_and_pass_spectrum_colors(
    tmp_path: Path,
):
    config = replace(
        _config(tmp_path, language="en", visual_style="spectrum"),
        spectrum_color="#123456",
        progress_color="#ABCDEF",
    )
    command = workflow.build_render_command(
        config,
        generated_vinyl=None,
        ass_path=tmp_path / "out.ass",
        report_path=tmp_path / "report.json",
        output_path=tmp_path / "out.mp4",
        duration=5.0,
    )

    assert command[command.index("--visual-style") + 1] == "spectrum"
    assert command[command.index("--spectrum-color") + 1] == "#123456"
    assert command[command.index("--progress-color") + 1] == "#ABCDEF"
    assert "--vinyl" not in command
    assert "--vinyl-motion" not in command

    with pytest.raises(workflow.KaraokeWorkflowError, match="must not receive vinyl"):
        workflow.build_render_command(
            config,
            generated_vinyl=tmp_path / "vinyl.png",
            ass_path=tmp_path / "bad.ass",
            report_path=tmp_path / "bad.json",
            output_path=tmp_path / "bad.mp4",
            duration=5.0,
        )


def test_render_command_adds_lossless_output_only_when_explicitly_requested(
    tmp_path: Path,
):
    config = replace(_config(tmp_path), lossless_companion=True)
    lossless_output = tmp_path / "output" / "karaoke-av1-lossless.mkv"

    command = workflow.build_render_command(
        config,
        generated_vinyl=tmp_path / "new" / "vinyl.png",
        ass_path=tmp_path / "out.ass",
        report_path=tmp_path / "report.json",
        output_path=tmp_path / "out.mp4",
        duration=5.0,
        lossless_output=lossless_output,
    )

    assert command[command.index("--lossless-output") + 1] == str(
        lossless_output.resolve()
    )


def test_language_entry_contracts_require_language_and_default_optional():
    ja = japanese_parser().parse_args(
        [
            "--sug", "a.sug", "--audio", "a.flac", "--composition", "c.png",
            "--vinyl", "v.png", "--output-dir", "out", "--title", "t",
            "--artist", "a", "--album-title", "at", "--album-artist", "aa",
        ]
    )
    assert ja.pronunciation_validation == "optional"
    assert ja.lossless_companion is False
    assert ja.full_decode is False
    assert ja.cover_source_audio is None
    assert ja.visual_style == "vinyl"
    assert not hasattr(ja, "timing_overrides")
    assert workflow.config_from_args(
        ja,
        language="ja",
        layout="wide",
        pronunciation_validation=ja.pronunciation_validation,
    ).full_decode is False
    spectrum = japanese_parser().parse_args(
        [
            "--sug", "a.sug", "--audio", "a.flac", "--composition", "c.png",
            "--visual-style", "spectrum", "--spectrum-color", "#112233",
            "--progress-color", "#445566", "--output-dir", "out", "--title", "t",
            "--artist", "a", "--album-title", "at", "--album-artist", "aa",
        ]
    )
    spectrum_config = workflow.config_from_args(
        spectrum,
        language="ja",
        layout="wide",
        pronunciation_validation=spectrum.pronunciation_validation,
    )
    assert spectrum_config.visual_style == "spectrum"
    assert spectrum_config.canonical_vinyl is None
    assert spectrum_config.spectrum_color == "#112233"
    assert spectrum_config.progress_color == "#445566"

    vinyl_without_asset = japanese_parser().parse_args(
        [
            "--sug", "a.sug", "--audio", "a.flac", "--composition", "c.png",
            "--output-dir", "out", "--title", "t", "--artist", "a",
            "--album-title", "at", "--album-artist", "aa",
        ]
    )
    vinyl_config = workflow.config_from_args(
        vinyl_without_asset,
        language="ja",
        layout="wide",
        pronunciation_validation=vinyl_without_asset.pronunciation_validation,
    )
    assert vinyl_config.canonical_vinyl is None
    assert "off" in japanese_parser()._option_string_actions[
        "--pronunciation-validation"
    ].choices
    opted_in = japanese_parser().parse_args(
        [
            "--sug", "a.sug", "--audio", "a.flac", "--composition", "c.png",
            "--vinyl", "v.png", "--output-dir", "out", "--title", "t",
            "--artist", "a", "--album-title", "at", "--album-artist", "aa",
            "--cover-source-audio", "original.flac",
            "--lossless-companion",
            "--full-decode",
            "--singer-color", "lead=#112233",
            "--singer-color", "guest=#AABBCC",
        ]
    )
    assert opted_in.lossless_companion is True
    assert opted_in.full_decode is True
    assert opted_in.cover_source_audio == Path("original.flac")
    opted_in_config = workflow.config_from_args(
        opted_in,
        language="ja",
        layout="wide",
        pronunciation_validation=opted_in.pronunciation_validation,
    )
    assert opted_in_config.full_decode is True
    assert opted_in_config.singer_colors == (
        "lead=#112233",
        "guest=#AABBCC",
    )


def test_one_click_renderer_report_rejects_color_plan_hash_mismatch(tmp_path: Path):
    config = _config(tmp_path, language="en", visual_style="spectrum")
    report = _renderer_report(config, generated_vinyl=None)
    report["video"]["color_plan_sha256"] = "stale-plan"  # type: ignore[index]
    report_path = tmp_path / "renderer-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(workflow.KaraokeWorkflowError, match="color_plan_hash"):
        workflow.validate_renderer_report(
            config,
            report_path,
            generated_vinyl=None,
        )


def _renderer_report_case(
    tmp_path: Path,
    *,
    visual_style: str,
    color_policy: str,
    singer_colors: tuple[str, ...] = (),
) -> tuple[workflow.WorkflowConfig, dict[str, object], Path | None]:
    config = replace(
        _config(tmp_path, language="en", visual_style=visual_style),
        color_policy=color_policy,
        singer_colors=singer_colors,
    )
    generated_vinyl = None
    if visual_style == "vinyl":
        generated_vinyl = tmp_path / "generated-vinyl.png"
        generated_vinyl.write_bytes(b"generated-vinyl")
    return (
        config,
        _renderer_report(config, generated_vinyl=generated_vinyl),
        generated_vinyl,
    )


def _validate_renderer_report_case(
    tmp_path: Path,
    config: workflow.WorkflowConfig,
    report: dict[str, object],
    generated_vinyl: Path | None,
) -> dict[str, object]:
    report_path = tmp_path / "renderer-report-case.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    result = workflow.validate_renderer_report(
        config,
        report_path,
        generated_vinyl=generated_vinyl,
    )
    return result["checks"]


def test_spectrum_mirror_renderer_report_accepts_current_contract(tmp_path: Path):
    config, report, generated_vinyl = _renderer_report_case(
        tmp_path,
        visual_style="spectrum-mirror",
        color_policy="cover",
    )

    checks = _validate_renderer_report_case(
        tmp_path, config, report, generated_vinyl
    )

    assert checks["spectrum_mirror_mode"] is True
    assert checks["spectrum_mirror_exact_symmetry"] is True
    assert checks["spectrum_mirror_stems_to_center"] is True


def test_spectrum_dots_renderer_report_accepts_current_contract(tmp_path: Path):
    config, report, generated_vinyl = _renderer_report_case(
        tmp_path,
        visual_style="spectrum-dots",
        color_policy="cover",
    )

    checks = _validate_renderer_report_case(
        tmp_path, config, report, generated_vinyl
    )

    assert checks["spectrum_dot_mode"] is True
    assert checks["spectrum_dot_columns"] is True
    assert checks["spectrum_dot_trail_decay"] is True


def test_spectrum_ribbon_renderer_report_accepts_current_contract(
    tmp_path: Path,
):
    config, report, generated_vinyl = _renderer_report_case(
        tmp_path,
        visual_style="spectrum-ribbon",
        color_policy="cover",
    )

    checks = _validate_renderer_report_case(
        tmp_path, config, report, generated_vinyl
    )

    assert checks["spectrum_ribbon_mode"] is True
    assert checks["spectrum_ribbon_trail_frames"] is True
    assert checks["spectrum_ribbon_stems_hidden"] is True


@pytest.mark.parametrize("visual_style", ["vinyl", "spectrum"])
def test_project_singer_override_accepts_complete_matching_color_plan(
    tmp_path: Path,
    visual_style: str,
):
    config, report, generated_vinyl = _renderer_report_case(
        tmp_path,
        visual_style=visual_style,
        color_policy="project",
        singer_colors=("lead=#112233",),
    )

    checks = _validate_renderer_report_case(
        tmp_path, config, report, generated_vinyl
    )

    assert checks["color_plan_schema"] is True
    assert checks["color_plan_hash"] is True


@pytest.mark.parametrize("visual_style", ["vinyl", "spectrum"])
@pytest.mark.parametrize("hash_state", ["empty", "missing", "mismatch"])
def test_required_color_plan_rejects_invalid_hash(
    tmp_path: Path,
    visual_style: str,
    hash_state: str,
):
    config, report, generated_vinyl = _renderer_report_case(
        tmp_path,
        visual_style=visual_style,
        color_policy="project",
        singer_colors=("lead=#112233",),
    )
    color_plan = report["ass"]["color_plan"]  # type: ignore[index]
    video = report["video"]  # type: ignore[assignment]
    if hash_state == "empty":
        color_plan["color_plan_sha256"] = ""  # type: ignore[index]
        video["color_plan_sha256"] = ""  # type: ignore[index]
    elif hash_state == "missing":
        color_plan.pop("color_plan_sha256")  # type: ignore[union-attr]
        video.pop("color_plan_sha256")  # type: ignore[union-attr]
    else:
        video["color_plan_sha256"] = "stale-plan"  # type: ignore[index]

    with pytest.raises(workflow.KaraokeWorkflowError, match="color_plan_hash"):
        _validate_renderer_report_case(
            tmp_path, config, report, generated_vinyl
        )


@pytest.mark.parametrize("visual_style", ["vinyl", "spectrum"])
@pytest.mark.parametrize(
    ("color_policy", "singer_colors"),
    [("cover", ()), ("project", ("lead=#112233",))],
)
def test_required_color_plan_rejects_missing_plan(
    tmp_path: Path,
    visual_style: str,
    color_policy: str,
    singer_colors: tuple[str, ...],
):
    config, report, generated_vinyl = _renderer_report_case(
        tmp_path,
        visual_style=visual_style,
        color_policy=color_policy,
        singer_colors=singer_colors,
    )
    report["ass"].pop("color_plan")  # type: ignore[union-attr]
    report["video"].pop("color_plan_sha256")  # type: ignore[union-attr]

    with pytest.raises(workflow.KaraokeWorkflowError, match="color_plan_schema"):
        _validate_renderer_report_case(
            tmp_path, config, report, generated_vinyl
        )


@pytest.mark.parametrize("visual_style", ["vinyl", "spectrum"])
def test_project_without_override_allows_absent_plan(
    tmp_path: Path,
    visual_style: str,
):
    config, report, generated_vinyl = _renderer_report_case(
        tmp_path,
        visual_style=visual_style,
        color_policy="project",
    )
    report["ass"].pop("color_plan")  # type: ignore[union-attr]
    report["video"].pop("color_plan_sha256")  # type: ignore[union-attr]

    checks = _validate_renderer_report_case(
        tmp_path, config, report, generated_vinyl
    )

    assert "color_plan_schema" not in checks
    assert "color_plan_hash" not in checks


@pytest.mark.parametrize("visual_key", ["spectrum_color", "progress_color"])
def test_spectrum_rejects_visual_color_plan_mismatch(
    tmp_path: Path,
    visual_key: str,
):
    config, report, generated_vinyl = _renderer_report_case(
        tmp_path,
        visual_style="spectrum",
        color_policy="project",
        singer_colors=("lead=#112233",),
    )
    if visual_key == "spectrum_color":
        report["video"]["spectrum_color"] = "#000000"  # type: ignore[index]
        expected = "spectrum_color_plan"
    else:
        report["video"]["progress_bar"]["color"] = "#000000"  # type: ignore[index]
        expected = "progress_color_plan"

    with pytest.raises(workflow.KaraokeWorkflowError, match=expected):
        _validate_renderer_report_case(
            tmp_path, config, report, generated_vinyl
        )


@pytest.mark.parametrize("visual_style", ["vinyl", "spectrum"])
def test_project_without_override_still_validates_present_plan(
    tmp_path: Path,
    visual_style: str,
):
    config, report, generated_vinyl = _renderer_report_case(
        tmp_path,
        visual_style=visual_style,
        color_policy="project",
    )
    report["ass"]["color_plan"]["color_plan_sha256"] = ""  # type: ignore[index]
    report["video"]["color_plan_sha256"] = ""  # type: ignore[index]

    with pytest.raises(workflow.KaraokeWorkflowError, match="color_plan_hash"):
        _validate_renderer_report_case(
            tmp_path, config, report, generated_vinyl
        )


def test_shared_workflow_does_not_import_language_specific_packages():
    source = Path(workflow.__file__).read_text(encoding="utf-8")
    assert "karaoke_japanese" not in source
    assert "karaoke_zh_en" not in source


def test_output_dir_rejects_existing_and_canonical_deliverables(tmp_path: Path):
    config = _config(tmp_path)
    config.output_dir.mkdir()
    with pytest.raises(workflow.KaraokeWorkflowError, match="already exists"):
        workflow.validate_output_dir(config)

    canonical = tmp_path / "deliverables" / "song"
    canonical.mkdir(parents=True)
    config = workflow.WorkflowConfig(
        **{**config.__dict__, "output_dir": canonical / "new"}
    )
    with pytest.raises(workflow.KaraokeWorkflowError, match="canonical deliverables"):
        workflow.validate_output_dir(config)


@pytest.mark.parametrize("language", ["ja", "zh", "en"])
@pytest.mark.parametrize("metadata_state", ["missing", "stale"])
def test_all_language_workflows_reject_noncurrent_wide_composition(
    tmp_path: Path,
    language: str,
    metadata_state: str,
):
    config = _config(tmp_path, language=language)
    metadata_path = config.composition.with_suffix(".json")
    if metadata_state == "missing":
        metadata_path.unlink()
        expected = "metadata is missing"
    else:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["layout_version"] = "stale-layout/v0"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        expected = "stale wide composition"

    with pytest.raises(workflow.KaraokeWorkflowError, match=expected):
        workflow.run_workflow(config)


def test_workflow_regenerates_vinyl_passes_it_and_records_rotate(tmp_path: Path):
    config = _config(tmp_path)
    cover_source_audio = tmp_path / "inputs" / "original-with-cover.flac"
    cover_source_audio.write_bytes(b"original-audio-with-cover")
    config = replace(config, cover_source_audio=cover_source_audio)
    commands: list[list[str]] = []
    artwork_inputs: list[Path] = []
    ass_gate_paths: list[Path] = []

    def ass_validator(path: Path, font_family: str):
        ass_gate_paths.append(path)
        return {"ok": path.is_file(), "errors": [], "font": font_family}

    def artwork_builder(artwork_audio, artwork_dir, *_args, **_kwargs):
        artwork_inputs.append(Path(artwork_audio))
        artwork_dir.mkdir(parents=True)
        generated = artwork_dir / "vinyl.png"
        generated.write_bytes(b"new-current-vinyl")
        source_hash = hashlib.sha256(
            (workflow.REPO_ROOT / "scripts" / "render_vinyl_karaoke.py").read_bytes()
        ).hexdigest()
        return {
            "vinyl_style_version": "test-current/v1",
            "vinyl_generator_sha256": source_hash,
            "vinyl_sha256": hashlib.sha256(generated.read_bytes()).hexdigest(),
            "vinyl_motion_contract": {"default": "rotate"},
            "source": {"source": "test"},
            "source_sha256": "cover-hash",
        }

    def runner(command):
        command = [str(value) for value in command]
        commands.append(command)
        if "-encoders" in command:
            return subprocess.CompletedProcess(
                command, 0, " V....D av1_nvenc\n V....D libaom-av1\n", ""
            )
        if command[-1] == "-":
            return subprocess.CompletedProcess(command, 0, "", "")
        if "-hide_banner" in command and "-i" in command:
            target = Path(command[command.index("-i") + 1])
            if target.suffix == ".mp4":
                stderr = (
                    "Duration: 00:00:05.00\n"
                    "Stream #0:0: Video: av1 (Main) (av01 / 0x31307661), "
                    "yuv420p(tv, bt709), 1920x1080, 30 fps\n"
                    "Stream #0:1: Audio: aac (LC), 44100 Hz, stereo, 320 kb/s\n"
                )
            elif target.suffix == ".flac":
                stderr = "Duration: 00:00:10.00\nStream #0:0: Audio: flac, 44100 Hz, stereo\n"
            else:
                stderr = "Stream #0:0: Video: png, rgba, 1920x1080, 30 fps\n"
            return subprocess.CompletedProcess(command, 1, "", stderr)
        if "--ass-only" in command:
            Path(command[command.index("--ass-output") + 1]).write_text(
                "[Events]\n", encoding="utf-8"
            )
            Path(command[command.index("--report-output") + 1]).write_text(
                json.dumps(
                    {
                        "ass": _renderer_report(
                            config,
                            generated_vinyl=(
                                config.output_dir / "artwork-current" / "vinyl.png"
                            ),
                        )["ass"]
                    }
                ),
                encoding="utf-8",
            )
        elif "--video-encoder" in command:
            Path(command[command.index("--output") + 1]).write_bytes(b"mp4")
            Path(command[command.index("--ass-output") + 1]).write_text(
                "[Events]\n", encoding="utf-8"
            )
            Path(command[command.index("--report-output") + 1]).write_text(
                json.dumps(
                    _renderer_report(
                        config,
                        generated_vinyl=(
                            config.output_dir / "artwork-current" / "vinyl.png"
                        ),
                    )
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, "{}", "")

    report = workflow.run_workflow(
        config,
        runner=runner,
        artwork_builder=artwork_builder,
        ass_validator=ass_validator,
        media_verifier=lambda _ffmpeg, _output: {
            "codec_av1": True,
            "pixel_format_yuv420p": True,
            "resolution_1920x1080": True,
            "cfr_30fps": True,
        },
    )

    generated = config.output_dir / "artwork-current" / "vinyl.png"
    render_command = next(command for command in commands if "--video-encoder" in command)
    assert artwork_inputs == [cover_source_audio.resolve()]
    assert render_command[render_command.index("--vinyl") + 1] == str(generated.resolve())
    assert render_command[render_command.index("--audio") + 1] == str(
        config.audio.resolve()
    )
    assert all(str(cover_source_audio.resolve()) not in command for command in commands)
    assert report["vinyl_motion"] == "rotate"
    assert report["status"] == "ok"
    assert ass_gate_paths == [
        config.output_dir / "karaoke-preflight.ass",
        config.output_dir / "karaoke.ass",
    ]
    parity_stage = next(
        stage for stage in report["stages"] if stage["name"] == "ass-render-parity"
    )
    assert parity_stage["preflight_ass_gate"]["ok"] is True
    assert parity_stage["final_ass_gate"]["ok"] is True
    assert parity_stage["report_parity"]["status"] == "ok"
    assert report["lossless_companion"] == {
        "requested": False,
        "performed": False,
        "reason": "not-requested",
    }
    assert report["full_decode"] == {
        "requested": False,
        "performed": False,
        "required": False,
        "recommended": False,
        "reason": "not-requested",
    }
    assert not any(
        "-f" in command
        and command[command.index("-f") + 1] == "null"
        for command in commands
    )
    vinyl_stage = next(
        stage for stage in report["stages"]
        if stage["name"] == "generate-current-vinyl"
    )
    assert vinyl_stage["silently_reused"] is False
    assert report["inputs"]["cover_source_audio"]["sha256"] == hashlib.sha256(
        cover_source_audio.read_bytes()
    ).hexdigest()
    assert report["inputs"]["delivery_audio"]["sha256"] == hashlib.sha256(
        config.audio.read_bytes()
    ).hexdigest()
    assert (
        report["inputs"]["cover_source_audio"]["sha256"]
        != report["inputs"]["delivery_audio"]["sha256"]
    )
    assert (config.output_dir / workflow.WORKFLOW_REPORT_NAME).is_file()


def test_mocked_spectrum_workflow_skips_artwork_and_passes_spectrum_report_gate(
    tmp_path: Path,
):
    config = replace(
        _config(tmp_path, language="en", visual_style="spectrum"),
        spectrum_color="#123456",
        progress_color="#654321",
    )
    commands: list[list[str]] = []
    ass_paths: list[Path] = []

    def fail_artwork_builder(*_args, **_kwargs):
        raise AssertionError("spectrum workflow must not call artwork_builder")

    def runner(command):
        command = [str(value) for value in command]
        commands.append(command)
        if "-encoders" in command:
            return subprocess.CompletedProcess(
                command, 0, " V....D av1_nvenc\n V....D libaom-av1\n", ""
            )
        if command[-1] == "-":
            return subprocess.CompletedProcess(command, 0, "", "")
        if "-hide_banner" in command and "-i" in command:
            target = Path(command[command.index("-i") + 1])
            stderr = (
                "Duration: 00:00:05.00\n"
                "Stream #0:0: Video: av1 (Main) (av01 / 0x31307661), "
                "yuv420p(tv, bt709), 1920x1080, 30 fps\n"
                "Stream #0:1: Audio: aac (LC), 44100 Hz, stereo, 320 kb/s\n"
                if target.suffix == ".mp4"
                else "Duration: 00:00:10.00\nStream #0:0: Audio: flac, 44100 Hz, stereo\n"
            )
            return subprocess.CompletedProcess(command, 1, "", stderr)
        if "--ass-only" in command or "--video-encoder" in command:
            ass_path = Path(command[command.index("--ass-output") + 1])
            ass_paths.append(ass_path)
            ass_path.write_text("[Events]\n", encoding="utf-8")
            report_path = Path(command[command.index("--report-output") + 1])
            renderer_report = _renderer_report(config, generated_vinyl=None)
            report_path.write_text(
                json.dumps(
                    {"ass": renderer_report["ass"]}
                    if "--ass-only" in command
                    else renderer_report
                ),
                encoding="utf-8",
            )
            if "--video-encoder" in command:
                Path(command[command.index("--output") + 1]).write_bytes(b"mp4")
        return subprocess.CompletedProcess(command, 0, "{}", "")

    report = workflow.run_workflow(
        config,
        runner=runner,
        artwork_builder=fail_artwork_builder,
        ass_validator=lambda path, _font: {"ok": path.is_file(), "errors": []},
        media_verifier=lambda _ffmpeg, _output: {
            "codec_av1": True,
            "pixel_format_yuv420p": True,
            "resolution_1920x1080": True,
            "cfr_30fps": True,
        },
    )

    assert ass_paths == [
        config.output_dir / "karaoke-preflight.ass",
        config.output_dir / "karaoke.ass",
    ]
    assert ass_paths[0] != ass_paths[1]
    assert workflow.sha256_file(ass_paths[1]) == next(
        stage["preflight_ass_sha256"]
        for stage in report["stages"]
        if stage["name"] == "ass-render-parity"
    )
    assert all("--vinyl" not in command for command in commands)
    assert "generated_vinyl" not in report["outputs"]
    assert report["vinyl_motion"] is None
    render_stage = next(
        stage for stage in report["stages"] if stage["name"] == "render-av1-mp4"
    )
    assert all(render_stage["visual_report_gate"]["checks"].values())
    assert report["status"] == "ok"


def test_lossless_source_gate_accepts_only_actual_flac_or_pcm_wav():
    assert workflow.validate_lossless_source(
        Path("song.flac"), {"audio_stream": {"codec": "flac"}}
    ) == "flac"
    assert workflow.validate_lossless_source(
        Path("song.wav"), {"audio_stream": {"codec": "pcm_s24le"}}
    ) == "pcm_s24le"
    with pytest.raises(workflow.KaraokeWorkflowError, match="actual FLAC or PCM WAV"):
        workflow.validate_lossless_source(
            Path("song.flac"), {"audio_stream": {"codec": "aac"}}
        )


@pytest.mark.parametrize("full_decode", [False, True])
def test_opt_in_lossless_companion_is_created_verified_and_reported(
    tmp_path: Path,
    full_decode: bool,
):
    config = replace(
        _config(tmp_path),
        lossless_companion=True,
        full_decode=full_decode,
    )
    commands: list[list[str]] = []

    def artwork_builder(_audio, artwork_dir, *_args, **_kwargs):
        artwork_dir.mkdir(parents=True)
        generated = artwork_dir / "vinyl.png"
        generated.write_bytes(b"new-current-vinyl")
        source_hash = hashlib.sha256(
            (workflow.REPO_ROOT / "scripts" / "render_vinyl_karaoke.py").read_bytes()
        ).hexdigest()
        return {
            "vinyl_style_version": "test-current/v1",
            "vinyl_generator_sha256": source_hash,
            "vinyl_sha256": hashlib.sha256(generated.read_bytes()).hexdigest(),
            "vinyl_motion_contract": {"default": "rotate"},
            "source": {"source": "test"},
            "source_sha256": "cover-hash",
        }

    def runner(command):
        command = [str(value) for value in command]
        commands.append(command)
        if "-encoders" in command:
            return subprocess.CompletedProcess(
                command, 0, " V....D av1_nvenc\n V....D libaom-av1\n", ""
            )
        if "-f" in command and command[command.index("-f") + 1] == "hash":
            return subprocess.CompletedProcess(
                command, 0, f"SHA256={'A' * 64}\n", ""
            )
        if command[-1] == "-":
            return subprocess.CompletedProcess(command, 0, "", "")
        if "-hide_banner" in command and "-i" in command:
            target = Path(command[command.index("-i") + 1])
            if target.suffix == ".mp4":
                stderr = (
                    "Duration: 00:00:05.00\n"
                    "Stream #0:0: Video: av1 (Main) (av01 / 0x31307661), "
                    "yuv420p(tv, bt709), 1920x1080, 30 fps\n"
                    "Stream #0:1: Audio: aac (LC), 44100 Hz, stereo, 320 kb/s\n"
                )
            elif target.suffix == ".mkv":
                stderr = (
                    "Duration: 00:00:05.00\n"
                    "Stream #0:0: Video: av1 (Main), yuv420p(tv, bt709), "
                    "1920x1080, 30 fps\n"
                    "Stream #0:1: Audio: flac, 44100 Hz, stereo\n"
                )
            elif target.suffix == ".flac":
                stderr = (
                    "Duration: 00:00:10.00\n"
                    "Stream #0:0: Audio: flac, 44100 Hz, stereo\n"
                )
            else:
                stderr = "Stream #0:0: Video: png, rgba, 1920x1080, 30 fps\n"
            return subprocess.CompletedProcess(command, 1, "", stderr)
        if "--ass-only" in command:
            Path(command[command.index("--ass-output") + 1]).write_text(
                "[Events]\n", encoding="utf-8"
            )
            Path(command[command.index("--report-output") + 1]).write_text(
                json.dumps(
                    {
                        "ass": _renderer_report(
                            config,
                            generated_vinyl=(
                                config.output_dir / "artwork-current" / "vinyl.png"
                            ),
                        )["ass"]
                    }
                ),
                encoding="utf-8",
            )
        elif "--video-encoder" in command:
            Path(command[command.index("--output") + 1]).write_bytes(b"mp4")
            Path(command[command.index("--lossless-output") + 1]).write_bytes(b"mkv")
            Path(command[command.index("--ass-output") + 1]).write_text(
                "[Events]\n", encoding="utf-8"
            )
            Path(command[command.index("--report-output") + 1]).write_text(
                json.dumps(
                    _renderer_report(
                        config,
                        generated_vinyl=(
                            config.output_dir / "artwork-current" / "vinyl.png"
                        ),
                    )
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, "{}", "")

    report = workflow.run_workflow(
        config,
        runner=runner,
        artwork_builder=artwork_builder,
        ass_validator=lambda path, _font: {"ok": path.is_file(), "errors": []},
        media_verifier=lambda _ffmpeg, _output: {
            "codec_av1": True,
            "pixel_format_yuv420p": True,
            "resolution_1920x1080": True,
            "cfr_30fps": True,
        },
    )

    render_command = next(command for command in commands if "--video-encoder" in command)
    assert render_command[render_command.index("--lossless-output") + 1].endswith(
        "karaoke-av1-lossless.mkv"
    )
    assert report["lossless_companion"]["requested"] is True
    assert report["lossless_companion"]["performed"] is True
    assert report["lossless_companion"]["source_codec"] == "flac"
    assert all(report["lossless_companion"]["checks"].values())
    assert report["full_decode"]["performed"] is full_decode
    assert report["lossless_companion"]["full_decode"]["performed"] is full_decode
    decode_commands = [
        command for command in commands
        if (
        "-f" in command
        and command[command.index("-f") + 1] == "null"
        )
    ]
    assert len(decode_commands) == (2 if full_decode else 0)
    if full_decode:
        assert report["full_decode"]["returncode"] == 0
        assert report["lossless_companion"]["full_decode"]["returncode"] == 0
    assert report["outputs"]["lossless_video"]["path"].endswith(
        "karaoke-av1-lossless.mkv"
    )


def test_real_build_artwork_metadata_satisfies_workflow_provenance_contract(
    tmp_path: Path,
    monkeypatch,
):
    cover = renderer.Image.new("RGB", (64, 64), (180, 120, 80))
    import io

    cover_bytes = io.BytesIO()
    cover.save(cover_bytes, format="PNG")
    monkeypatch.setattr(
        renderer,
        "embedded_cover",
        lambda _audio: (
            cover_bytes.getvalue(),
            {"present": True, "source": "test", "mime": "image/png"},
        ),
    )
    monkeypatch.setattr(
        renderer,
        "inspect_font_dir",
        lambda _fonts: {"family": "test", "regular": {}, "bold": {}, "files": []},
    )
    monkeypatch.setattr(
        renderer,
        "_draw_background",
        lambda _cover: renderer.Image.new("RGBA", renderer.CANVAS_SIZE, (0, 0, 0, 255)),
    )
    monkeypatch.setattr(renderer, "_draw_envelope", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        renderer,
        "_draw_vinyl",
        lambda _cover: renderer.Image.new("RGBA", (32, 32), (10, 10, 10, 255)),
    )
    audio = tmp_path / "audio.flac"
    audio.write_bytes(b"audio")
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    artwork_dir = tmp_path / "artwork"

    metadata = renderer.build_artwork(
        audio,
        artwork_dir,
        "Title",
        "Artist",
        "",
        fonts,
        allow_network=False,
    )
    provenance = workflow.validate_vinyl_provenance(
        metadata, artwork_dir / "vinyl.png"
    )
    direct_gate = direct_renderer.validate_current_vinyl_assets(
        [SimpleNamespace(vinyl_path=artwork_dir / "vinyl.png")]
    )

    assert provenance["vinyl_generator_sha256"] == metadata["vinyl_generator_sha256"]
    assert provenance["vinyl_sha256"] == metadata["vinyl_sha256"]
    assert provenance["vinyl_motion_contract"]["default"] == "rotate"
    assert direct_gate[0]["status"] == "pass"
    assert direct_gate[0]["generator_hash_metadata_field"] == "vinyl_generator_sha256"
