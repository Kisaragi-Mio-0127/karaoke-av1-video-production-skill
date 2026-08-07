"""Offline contracts for cover colors across one-click and batch renderers."""

from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
INTEGRATION_ROOT = ROOT / "integration" / "strangeutagame"
SCRIPTS = INTEGRATION_ROOT / "scripts"
WORKFLOW_SOURCE = SCRIPTS / "karaoke_workflow.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

album_renderer = importlib.import_module("render_karaoke_direct_av1_420_album")


class WorkflowGateError(RuntimeError):
    pass


def _function_source(name: str) -> str:
    source = WORKFLOW_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def _workflow_report_validator():
    namespace = {
        "Any": Any,
        "json": json,
        "KaraokeWorkflowError": WorkflowGateError,
        "Path": Path,
        "WorkflowConfig": object,
        "sha256_file": lambda _path: "vinyl-hash",
    }
    exec(_function_source("validate_renderer_report"), namespace)
    return namespace["validate_renderer_report"]


def _workflow_report(visual_style: str) -> dict[str, object]:
    color_plan = {
        "schema_version": "karaoke-color-plan/v1",
        "color_plan_sha256": "plan-hash",
        "visual": {
            "spectrum_color": "#123456",
            "progress_color": "#ABCDEF",
        },
    }
    if visual_style == "vinyl":
        video = {
            "visual_style": "vinyl",
            "vinyl_motion": "rotate",
            "vinyl_asset": {
                "path": str(Path("generated-vinyl.png").resolve()),
                "sha256": "vinyl-hash",
            },
            "color_plan_sha256": "plan-hash",
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
            "spectrum_color": "#123456",
            "color_plan_sha256": "plan-hash",
            "progress_bar": {"show_time": False, "color": "#ABCDEF"},
        }
    return {"ass": {"color_plan": color_plan}, "video": video}


def _validate_workflow_report(
    tmp_path: Path,
    report: dict[str, object],
    *,
    visual_style: str,
    color_policy: str,
    singer_colors: tuple[str, ...] = (),
) -> dict[str, bool]:
    report_path = tmp_path / "workflow-renderer-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    generated_vinyl = (
        Path("generated-vinyl.png") if visual_style == "vinyl" else None
    )
    result = _workflow_report_validator()(
        SimpleNamespace(
            visual_style=visual_style,
            color_policy=color_policy,
            singer_colors=singer_colors,
        ),
        report_path,
        generated_vinyl=generated_vinyl,
    )
    return result["checks"]


def _parser_argument_default(option: str) -> object:
    source = (SCRIPTS / "karaoke_review_preview.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    make_parser = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "make_parser"
    )
    call = next(
        node
        for node in ast.walk(make_parser)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == option
    )
    default = next(keyword.value for keyword in call.keywords if keyword.arg == "default")
    assert isinstance(default, ast.Constant)
    return default.value


def _spectrum_report() -> dict[str, object]:
    color_plan = {
        "schema_version": "karaoke-color-plan/v1",
        "color_plan_sha256": "plan-hash",
        "visual": {
            "spectrum_color": "#123456",
            "progress_color": "#ABCDEF",
        },
    }
    return {
        "status": "ok",
        "ass": {
            "ass": "karaoke.ass",
            "color_plan": color_plan,
        },
        "video": {
            "visual_style": "spectrum",
            "video_encoder": "av1_nvenc",
            "pixel_format": "yuv420p",
            "av1_cq": 38,
            "av1_preset": "p7",
            "preferred_output": "compatibility-mp4",
            "audio_codec": "aac",
            "audio_profile": "aac_low",
            "audio_bitrate": "320k",
            "color_plan_sha256": "plan-hash",
            "spectrum_color": "#123456",
            "progress_bar": {"show_time": False, "color": "#ABCDEF"},
        },
    }


def test_one_click_command_and_report_gate_share_color_plan_contract():
    command_source = _function_source("build_ass_command")
    gate_source = _function_source("validate_renderer_report")

    assert '"--color-policy"' in command_source
    assert "config.color_policy" in command_source
    assert '"karaoke-color-plan/v1"' in gate_source
    assert 'video.get("color_plan_sha256")' in gate_source
    assert 'video.get("spectrum_color")' in gate_source
    assert 'progress_bar.get("color")' in gate_source
    assert 'config.color_policy == "cover"' in gate_source
    assert "bool(config.singer_colors)" in gate_source
    assert "if requires_color_plan" in gate_source


def test_one_click_repeatable_singer_colors_reach_preflight_and_final_commands():
    workflow_source = WORKFLOW_SOURCE.read_text(encoding="utf-8")
    source = _function_source("build_ass_command")
    render_source = _function_source("build_render_command")
    config_source = _function_source("config_from_args")

    assert '"--singer-color"' in workflow_source
    assert 'action="append"' in workflow_source
    assert "for singer_color in config.singer_colors" in source
    assert 'command.extend(["--singer-color", singer_color])' in source
    assert "build_ass_command(" in render_source
    assert "singer_colors=tuple(args.singer_color)" in config_source


def test_one_click_defaults_cover_but_exposes_project_rollback():
    source = WORKFLOW_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    workflow_config = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WorkflowConfig"
    )
    color_policy = next(
        node
        for node in workflow_config.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "color_policy"
    )

    assert isinstance(color_policy.value, ast.Constant)
    assert color_policy.value.value == "cover"
    assert 'choices=("cover", "project")' in source
    assert 'default="cover"' in source


@pytest.mark.parametrize("visual_style", ["vinyl", "spectrum"])
def test_workflow_project_override_accepts_matching_plan(
    tmp_path: Path,
    visual_style: str,
):
    checks = _validate_workflow_report(
        tmp_path,
        _workflow_report(visual_style),
        visual_style=visual_style,
        color_policy="project",
        singer_colors=("lead=#112233",),
    )

    assert checks["color_plan_schema"] is True
    assert checks["color_plan_hash"] is True


@pytest.mark.parametrize("visual_style", ["vinyl", "spectrum"])
@pytest.mark.parametrize("hash_state", ["empty", "missing", "mismatch"])
def test_workflow_required_plan_rejects_bad_hash(
    tmp_path: Path,
    visual_style: str,
    hash_state: str,
):
    report = _workflow_report(visual_style)
    color_plan = report["ass"]["color_plan"]  # type: ignore[index]
    video = report["video"]
    if hash_state == "empty":
        color_plan["color_plan_sha256"] = ""  # type: ignore[index]
        video["color_plan_sha256"] = ""  # type: ignore[index]
    elif hash_state == "missing":
        color_plan.pop("color_plan_sha256")  # type: ignore[union-attr]
        video.pop("color_plan_sha256")  # type: ignore[union-attr]
    else:
        video["color_plan_sha256"] = "different"  # type: ignore[index]

    with pytest.raises(WorkflowGateError, match="color_plan_hash"):
        _validate_workflow_report(
            tmp_path,
            report,
            visual_style=visual_style,
            color_policy="project",
            singer_colors=("lead=#112233",),
        )


@pytest.mark.parametrize("visual_style", ["vinyl", "spectrum"])
def test_workflow_required_plan_rejects_missing_plan(
    tmp_path: Path,
    visual_style: str,
):
    report = _workflow_report(visual_style)
    report["ass"].pop("color_plan")  # type: ignore[union-attr]
    report["video"].pop("color_plan_sha256")  # type: ignore[union-attr]

    with pytest.raises(WorkflowGateError, match="color_plan_schema"):
        _validate_workflow_report(
            tmp_path,
            report,
            visual_style=visual_style,
            color_policy="project",
            singer_colors=("lead=#112233",),
        )


@pytest.mark.parametrize("visual_style", ["vinyl", "spectrum"])
def test_workflow_project_without_override_allows_only_absent_plan(
    tmp_path: Path,
    visual_style: str,
):
    report = _workflow_report(visual_style)
    report["ass"].pop("color_plan")  # type: ignore[union-attr]
    report["video"].pop("color_plan_sha256")  # type: ignore[union-attr]
    checks = _validate_workflow_report(
        tmp_path,
        report,
        visual_style=visual_style,
        color_policy="project",
    )
    assert "color_plan_hash" not in checks

    malformed = _workflow_report(visual_style)
    malformed["ass"]["color_plan"]["color_plan_sha256"] = ""  # type: ignore[index]
    malformed["video"]["color_plan_sha256"] = ""  # type: ignore[index]
    with pytest.raises(WorkflowGateError, match="color_plan_hash"):
        _validate_workflow_report(
            tmp_path,
            malformed,
            visual_style=visual_style,
            color_policy="project",
        )


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("spectrum", "spectrum_color_plan"),
        ("progress", "progress_color_plan"),
    ],
)
def test_workflow_spectrum_rejects_plan_visual_mismatch(
    tmp_path: Path,
    field: str,
    expected: str,
):
    report = _workflow_report("spectrum")
    if field == "spectrum":
        report["video"]["spectrum_color"] = "#000000"  # type: ignore[index]
    else:
        report["video"]["progress_bar"]["color"] = "#000000"  # type: ignore[index]

    with pytest.raises(WorkflowGateError, match=expected):
        _validate_workflow_report(
            tmp_path,
            report,
            visual_style="spectrum",
            color_policy="project",
            singer_colors=("lead=#112233",),
        )


def test_bottom_level_review_renderer_defaults_to_cover_policy():
    assert _parser_argument_default("--color-policy") == "cover"


def test_batch_report_gate_checks_the_same_color_plan():
    report = _spectrum_report()

    album_renderer.validate_preview_report(
        report,
        av1_cq=38,
        visual_style="spectrum",
    )
    report["video"]["color_plan_sha256"] = "different"  # type: ignore[index]
    with pytest.raises(
        album_renderer.DirectAV1420RenderError,
        match="preview color-plan mismatch: hash",
    ):
        album_renderer.validate_preview_report(
            report,
            av1_cq=38,
            visual_style="spectrum",
        )


def test_batch_repeatable_singer_colors_reach_every_render_task():
    args = album_renderer.make_parser().parse_args(
        [
            "--singer-color",
            "lead=#112233",
            "--singer-color",
            "guest=#AABBCC",
        ]
    )
    source = (SCRIPTS / "render_karaoke_direct_av1_420_album.py").read_text(
        encoding="utf-8"
    )

    assert args.singer_color == ["lead=#112233", "guest=#AABBCC"]
    assert "for singer_color in singer_colors" in source
    assert "singer_colors=args.singer_color" in source
