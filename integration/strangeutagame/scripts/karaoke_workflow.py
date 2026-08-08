#!/usr/bin/env python3
"""Stage and run an isolated single-song karaoke production workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

try:
    from scripts.inspect_karaoke_media import (
        parse_audio_stream,
        parse_duration,
        parse_video_stream,
    )
    from scripts.karaoke_common.artwork import prepare_auto_artwork
    from scripts.karaoke_common.editable_sug import export_editable_sug
    from scripts.karaoke_common.ffmpeg_tools import (
        resolve_ffmpeg as resolve_ffmpeg_tool,
    )
    from scripts.karaoke_common.media_metadata import resolve_display_metadata
    from scripts.karaoke_language import language_identity
    from scripts.render_karaoke_direct_av1_420_album import (
        DirectAV1420RenderError,
        validate_current_wide_compositions,
    )
    from scripts.render_karaoke_track import SHARED_FONT_DIR, SHARED_FONT_FILE
    from scripts.render_vinyl_karaoke import validate_ass_for_render
except ImportError:  # pragma: no cover - direct script entry points
    from inspect_karaoke_media import (  # type: ignore[no-redef]
        parse_audio_stream,
        parse_duration,
        parse_video_stream,
    )
    from karaoke_common.artwork import prepare_auto_artwork  # type: ignore[no-redef]
    from karaoke_common.editable_sug import export_editable_sug  # type: ignore[no-redef]
    from karaoke_common.ffmpeg_tools import (  # type: ignore[no-redef]
        resolve_ffmpeg as resolve_ffmpeg_tool,
    )
    from karaoke_common.media_metadata import resolve_display_metadata  # type: ignore[no-redef]
    from karaoke_language import language_identity  # type: ignore[no-redef]
    from render_karaoke_direct_av1_420_album import (  # type: ignore[no-redef]
        DirectAV1420RenderError,
        validate_current_wide_compositions,
    )
    from render_karaoke_track import (  # type: ignore[no-redef]
        SHARED_FONT_DIR,
        SHARED_FONT_FILE,
    )
    from render_vinyl_karaoke import validate_ass_for_render  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACK_RENDERER_SCRIPT = REPO_ROOT / "scripts" / "render_karaoke_track.py"
WORKFLOW_REPORT_NAME = "workflow-report.json"
TEST_ROOT_MARKER = ".karaoke-workflow-test-root"
VISUAL_STYLES = ("vinyl", "spectrum")


def inspect_av1_420_media(ffmpeg: Path, media: Path) -> dict[str, Any]:
    """Load the release verifier only when a real media gate is requested."""

    try:
        from scripts.finalize_karaoke_release import inspect_av1_420_media as inspect
    except ImportError:  # pragma: no cover - direct script entry point
        from finalize_karaoke_release import (  # type: ignore[no-redef]
            inspect_av1_420_media as inspect,
        )
    return inspect(ffmpeg, media)


class KaraokeWorkflowError(RuntimeError):
    """Raised when a workflow stage cannot safely continue."""


@dataclass(frozen=True)
class WorkflowConfig:
    sug: Path
    audio: Path
    composition: Path | None
    canonical_vinyl: Path | None
    output_dir: Path
    language: str
    layout: str
    title: str
    artist: str
    album_title: str | None
    album_artist: str | None
    cover: Path | None = None
    background: Path | None = None
    cover_source_audio: Path | None = None
    metadata_source_audio: Path | None = None
    fonts_dir: Path = SHARED_FONT_DIR
    font_file: Path = SHARED_FONT_FILE
    smoke_duration: float | None = None
    pronunciation_validation: str = "optional"
    visual_style: str = "vinyl"
    color_policy: str = "cover"
    singer_colors: tuple[str, ...] = ()
    spectrum_color: str | None = None
    progress_color: str | None = None
    cover_url: str = ""
    allow_network: bool = False
    ffmpeg: Path | None = None
    lossless_companion: bool = False
    full_decode: bool = False
    canonical_deliverables: tuple[Path, ...] = ()
    timing_overrides: Path | None = None
    timing_override_song_id: str | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _nearest_deliverable_root(path: Path) -> Path | None:
    resolved = path.resolve()
    parents = (resolved, *resolved.parents)
    for candidate in parents:
        if candidate.parent.name.casefold() == "deliverables":
            return candidate
    return None


def _has_test_root_marker(path: Path) -> bool:
    for candidate in (path, *path.parents):
        if (candidate / TEST_ROOT_MARKER).is_file():
            return True
    return False


def validate_visual_contract(config: WorkflowConfig) -> None:
    if (config.timing_overrides is None) != (
        config.timing_override_song_id is None
    ):
        raise KaraokeWorkflowError(
            "timing_overrides and timing_override_song_id must be provided together"
        )
    if config.visual_style not in VISUAL_STYLES:
        raise KaraokeWorkflowError(
            f"unsupported visual style: {config.visual_style!r}"
        )
    if config.color_policy not in {"cover", "project"}:
        raise KaraokeWorkflowError(
            f"unsupported color policy: {config.color_policy!r}"
        )
    if config.visual_style == "vinyl" and (
        config.spectrum_color is not None or config.progress_color is not None
    ):
        raise KaraokeWorkflowError(
            "--spectrum-color/--progress-color require --visual-style=spectrum"
        )


def validate_output_dir(config: WorkflowConfig) -> Path:
    output_dir = config.output_dir.expanduser().resolve()
    if output_dir.exists():
        raise KaraokeWorkflowError(f"output directory already exists: {output_dir}")

    canonical_roots = {
        root.expanduser().resolve() for root in config.canonical_deliverables
    }
    canonical_roots.add((REPO_ROOT / "deliverables").resolve())
    output_canonical_root = _nearest_deliverable_root(output_dir)
    if output_canonical_root is not None:
        canonical_roots.add(output_canonical_root)
    sources = [
        config.sug,
        config.audio,
        config.cover_source_audio or config.audio,
    ]
    for optional_source in (config.composition, config.cover, config.background):
        if optional_source is not None:
            sources.append(optional_source)
    for source in sources:
        root = _nearest_deliverable_root(source)
        if root is not None:
            canonical_roots.add(root)

    if not _has_test_root_marker(output_dir.parent):
        for root in canonical_roots:
            if _is_relative_to(output_dir, root):
                raise KaraokeWorkflowError(
                    f"output directory is inside canonical deliverables: {root}"
                )
    return output_dir


def _assert_output_path(path: Path, output_dir: Path) -> Path:
    resolved = path.resolve()
    if not _is_relative_to(resolved, output_dir.resolve()):
        raise KaraokeWorkflowError(f"workflow output escapes output directory: {resolved}")
    return resolved


def resolve_ffmpeg(explicit: Path | None) -> Path:
    try:
        return resolve_ffmpeg_tool(explicit, root=REPO_ROOT)
    except RuntimeError as error:
        raise KaraokeWorkflowError(str(error)) from error


def build_probe_command(ffmpeg: Path, path: Path) -> list[str]:
    return [
        str(ffmpeg),
        "-hide_banner",
        "-i",
        str(path),
    ]


def build_full_decode_command(ffmpeg: Path, path: Path) -> list[str]:
    return [
        str(ffmpeg),
        "-v",
        "error",
        "-xerror",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-f",
        "null",
        "-",
    ]


def build_video_stream_hash_command(ffmpeg: Path, path: Path) -> list[str]:
    return [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-c",
        "copy",
        "-f",
        "hash",
        "-hash",
        "sha256",
        "-",
    ]


def build_ass_command(
    config: WorkflowConfig,
    *,
    generated_vinyl: Path | None,
    ass_path: Path,
    report_path: Path,
    output_path: Path,
    duration: float,
) -> list[str]:
    command = [
        sys.executable,
        str(TRACK_RENDERER_SCRIPT),
        "--sug",
        str(config.sug.resolve()),
        "--audio",
        str(config.audio.resolve()),
        "--composition",
        str(config.composition.resolve()),
        "--fonts-dir",
        str(config.fonts_dir.resolve()),
        "--font-file",
        str(config.font_file.resolve()),
        "--output",
        str(output_path.resolve()),
        "--ass-output",
        str(ass_path.resolve()),
        "--report-output",
        str(report_path.resolve()),
        "--start",
        "0",
        "--duration",
        f"{duration:.3f}",
        "--layout",
        config.layout,
        "--visual-style",
        config.visual_style,
        "--color-policy",
        config.color_policy,
    ]
    if config.timing_overrides is not None:
        command.extend(
            [
                "--timing-overrides",
                str(config.timing_overrides.expanduser().resolve()),
                "--song-id",
                str(config.timing_override_song_id),
            ]
        )
    if config.visual_style == "vinyl":
        if generated_vinyl is None:
            raise KaraokeWorkflowError("vinyl workflow did not provide generated artwork")
        command.extend(
            ["--vinyl", str(generated_vinyl.resolve()), "--vinyl-motion", "rotate"]
        )
    elif generated_vinyl is not None:
        raise KaraokeWorkflowError("spectrum workflow must not receive vinyl artwork")
    for singer_color in config.singer_colors:
        command.extend(["--singer-color", singer_color])
    if config.visual_style == "spectrum":
        if config.spectrum_color is not None:
            command.extend(["--spectrum-color", config.spectrum_color])
        if config.progress_color is not None:
            command.extend(["--progress-color", config.progress_color])
    command.extend(
        [
        "--pronunciation-validation",
        config.pronunciation_validation,
        "--ass-only",
        ]
    )
    return command


def build_render_command(
    config: WorkflowConfig,
    *,
    generated_vinyl: Path | None,
    ass_path: Path,
    report_path: Path,
    output_path: Path,
    duration: float,
    lossless_output: Path | None = None,
) -> list[str]:
    if config.lossless_companion and lossless_output is None:
        raise KaraokeWorkflowError(
            "--lossless-companion requires an explicit workflow MKV output path"
        )
    if not config.lossless_companion and lossless_output is not None:
        raise KaraokeWorkflowError(
            "lossless output path is forbidden unless --lossless-companion is set"
        )
    command = build_ass_command(
        config,
        generated_vinyl=generated_vinyl,
        ass_path=ass_path,
        report_path=report_path,
        output_path=output_path,
        duration=duration,
    )
    command.remove("--ass-only")
    command.extend(["--video-encoder", "av1_nvenc", "--av1-cq", "38"])
    if lossless_output is not None:
        command.extend(["--lossless-output", str(lossless_output.resolve())])
    return command


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _probe_with_ffmpeg(
    ffmpeg: Path,
    path: Path,
    *,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    command = build_probe_command(ffmpeg, path)
    completed = runner(command)
    diagnostic = completed.stdout + "\n" + completed.stderr
    stream_lines = [line.strip() for line in diagnostic.splitlines() if "Stream #" in line]
    video_line = next((line for line in stream_lines if "Video:" in line), None)
    audio_line = next((line for line in stream_lines if "Audio:" in line), None)
    return {
        "command": command,
        "duration_seconds": parse_duration(diagnostic),
        "video_stream": parse_video_stream(video_line) if video_line else None,
        "audio_stream": parse_audio_stream(audio_line) if audio_line else None,
        "bt709": "bt709" in diagnostic.casefold(),
        "diagnostic_tail": diagnostic[-2000:],
    }


def _input_identity(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise KaraokeWorkflowError(f"required input does not exist: {resolved}")
    return {
        "path": str(resolved),
        "size": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def _duration_from_probe(probe: dict[str, Any]) -> float:
    try:
        duration = float(probe["duration_seconds"])
    except (KeyError, TypeError, ValueError) as error:
        raise KaraokeWorkflowError("audio FFmpeg probe did not report a duration") from error
    if duration <= 0:
        raise KaraokeWorkflowError("audio duration must be positive")
    return duration


def validate_lossless_source(audio_path: Path, probe: dict[str, Any]) -> str:
    audio_stream = probe.get("audio_stream")
    codec = audio_stream.get("codec") if isinstance(audio_stream, dict) else None
    suffix = audio_path.suffix.casefold()
    is_flac = suffix == ".flac" and codec == "flac"
    is_pcm_wav = suffix in {".wav", ".wave"} and isinstance(codec, str) and codec.startswith("pcm_")
    if not (is_flac or is_pcm_wav):
        raise KaraokeWorkflowError(
            "--lossless-companion requires actual FLAC or PCM WAV audio; "
            f"got suffix={suffix!r}, codec={codec!r}"
        )
    return str(codec)


def _video_stream_sha256(
    ffmpeg: Path,
    path: Path,
    *,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
) -> str:
    completed = runner(build_video_stream_hash_command(ffmpeg, path))
    diagnostic = completed.stdout + "\n" + completed.stderr
    match = re.search(r"SHA256=([0-9A-Fa-f]{64})", diagnostic)
    if completed.returncode != 0 or match is None:
        raise KaraokeWorkflowError(
            f"could not hash copied video stream for {path}: {completed.stderr[-1200:]}"
        )
    return match.group(1).upper()


def _ruby_records(value: Any, path: str = "") -> list[str]:
    records: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            here = f"{path}/{key}"
            if (
                key.casefold().startswith("ruby")
                and key not in {"ruby_enabled", "ruby_policy"}
                and child not in (None, "", [], {})
            ):
                records.append(here)
            records.extend(_ruby_records(child, here))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            records.extend(_ruby_records(child, f"{path}/{index}"))
    return records


def enforce_language_contract(config: WorkflowConfig, ass_path: Path) -> dict[str, Any]:
    sug = json.loads(config.sug.read_text(encoding="utf-8"))
    ass = ass_path.read_text(encoding="utf-8-sig")
    source_ruby = _ruby_records(sug)
    ass_ruby = [
        line
        for line in ass.splitlines()
        if line.startswith("Dialogue:") and (",Ruby," in line or ",RubyGlow," in line)
    ]
    if config.language in {"zh", "en"} and (source_ruby or ass_ruby):
        raise KaraokeWorkflowError(
            f"zero-ruby contract failed: source={len(source_ruby)}, ass={len(ass_ruby)}"
        )
    identity = language_identity(config.language)
    return {
        "language": config.language,
        "language_identity": identity,
        "layout": config.layout,
        "ruby_policy": "disabled" if config.language in {"zh", "en"} else "reviewed",
        "ruby_enabled": bool(identity["ruby_enabled"]),
        "source_ruby_records": len(source_ruby),
        "ass_ruby_events": len(ass_ruby),
        "pronunciation_validation": config.pronunciation_validation,
    }


def _write_report(output_dir: Path, report: dict[str, Any]) -> None:
    report_path = _assert_output_path(output_dir / WORKFLOW_REPORT_NAME, output_dir)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_vinyl_provenance(
    artwork: dict[str, Any],
    generated_vinyl: Path,
) -> dict[str, Any]:
    generated_vinyl_sha256 = sha256_file(generated_vinyl)
    renderer_source_sha256 = sha256_file(
        REPO_ROOT / "scripts" / "render_vinyl_karaoke.py"
    )
    recorded_generator_sha256 = artwork.get("vinyl_generator_sha256")
    if recorded_generator_sha256 is None:
        recorded_generator_sha256 = artwork.get("render_vinyl_karaoke_sha256")
    if artwork.get("vinyl_sha256") != generated_vinyl_sha256:
        raise KaraokeWorkflowError("generated vinyl hash does not match artwork.json")
    if recorded_generator_sha256 != renderer_source_sha256:
        raise KaraokeWorkflowError("artwork renderer identity does not match current source")
    if not artwork.get("vinyl_style_version"):
        raise KaraokeWorkflowError("artwork.json is missing vinyl_style_version")
    motion_contract = artwork.get("vinyl_motion_contract")
    if not isinstance(motion_contract, dict) or motion_contract.get("default") != "rotate":
        raise KaraokeWorkflowError("current vinyl motion contract must default to rotate")
    return {
        "vinyl_generator_sha256": renderer_source_sha256,
        "vinyl_sha256": generated_vinyl_sha256,
        "vinyl_style_version": artwork["vinyl_style_version"],
        "vinyl_motion_contract": motion_contract,
    }


def validate_workflow_composition(config: WorkflowConfig) -> dict[str, Any]:
    """Apply the album renderer's current wide composition identity gate."""

    if config.composition is None:
        raise KaraokeWorkflowError("composition must be prepared before layout gating")
    task = SimpleNamespace(profile="wide", composition_path=config.composition)
    try:
        results = validate_current_wide_compositions(
            [task], visual_style=config.visual_style
        )
    except DirectAV1420RenderError as error:
        raise KaraokeWorkflowError(str(error)) from error
    if len(results) != 1:
        raise KaraokeWorkflowError("current wide composition gate returned no result")
    return results[0]


def validate_renderer_report(
    config: WorkflowConfig,
    report_path: Path,
    *,
    generated_vinyl: Path | None,
) -> dict[str, Any]:
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise KaraokeWorkflowError(f"invalid renderer report: {error}") from error
    video = payload.get("video") if isinstance(payload, dict) else None
    ass = payload.get("ass") if isinstance(payload, dict) else None
    if not isinstance(video, dict):
        raise KaraokeWorkflowError("renderer report has no video object")
    checks: dict[str, bool] = {
        "visual_style": video.get("visual_style") == config.visual_style,
    }
    color_plan = ass.get("color_plan") if isinstance(ass, dict) else None
    requires_color_plan = (
        (isinstance(ass, dict) and "color_plan" in ass)
        or config.color_policy == "cover"
        or bool(config.singer_colors)
    )
    visual_colors = (
        color_plan.get("visual") if isinstance(color_plan, dict) else None
    )
    color_plan_sha256 = (
        color_plan.get("color_plan_sha256")
        if isinstance(color_plan, dict)
        else None
    )
    if requires_color_plan:
        checks.update(
            {
                "color_plan_schema": isinstance(color_plan, dict)
                and color_plan.get("schema_version") == "karaoke-color-plan/v1",
                "color_plan_hash": isinstance(color_plan_sha256, str)
                and bool(color_plan_sha256)
                and video.get("color_plan_sha256") == color_plan_sha256,
            }
        )
    if config.visual_style == "vinyl":
        vinyl_asset = video.get("vinyl_asset")
        checks.update(
            {
                "vinyl_motion_rotate": video.get("vinyl_motion") == "rotate",
                "vinyl_asset_present": isinstance(vinyl_asset, dict),
                "vinyl_path": (
                    generated_vinyl is not None
                    and isinstance(vinyl_asset, dict)
                    and vinyl_asset.get("path") == str(generated_vinyl.resolve())
                ),
                "vinyl_sha256": (
                    generated_vinyl is not None
                    and isinstance(vinyl_asset, dict)
                    and vinyl_asset.get("sha256") == sha256_file(generated_vinyl)
                ),
            }
        )
    else:
        progress_bar = video.get("progress_bar")
        checks.update(
            {
                "vinyl_motion_absent": video.get("vinyl_motion") is None,
                "vinyl_asset_absent": video.get("vinyl_asset") is None,
                "spectrum_geometry": video.get("spectrum_geometry")
                == {"x": 800, "y": 290, "width": 1040, "height": 220},
                "spectrum_bar_count": video.get("spectrum_bar_count") == 80,
                "spectrum_clip_safe_geometry": video.get(
                    "spectrum_clip_safe_geometry"
                )
                == {"x": 736, "y": 226, "width": 1168, "height": 348},
                "spectrum_bar_top_clearance": video.get(
                    "spectrum_bar_top_clearance_px"
                )
                == 8,
                "spectrum_bar_bottom_clearance": video.get(
                    "spectrum_bar_bottom_clearance_px"
                )
                == 8,
                "spectrum_glow_top_padding": video.get(
                    "spectrum_glow_top_padding_px"
                )
                == 56,
                "spectrum_glow_bottom_padding": video.get(
                    "spectrum_glow_bottom_padding_px"
                )
                == 56,
                "peak_hold": isinstance(video.get("peak_hold"), dict)
                and video["peak_hold"].get("enabled") is True,
                "progress_time_hidden": isinstance(video.get("progress_bar"), dict)
                and video["progress_bar"].get("show_time") is False,
                "spectrum_color_plan": not requires_color_plan
                or (
                    isinstance(visual_colors, dict)
                    and video.get("spectrum_color")
                    == visual_colors.get("spectrum_color")
                ),
                "progress_color_plan": not requires_color_plan
                or (
                    isinstance(visual_colors, dict)
                    and isinstance(progress_bar, dict)
                    and progress_bar.get("color")
                    == visual_colors.get("progress_color")
                ),
            }
        )
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise KaraokeWorkflowError(
            "renderer visual-style report gate failed: " + ", ".join(failed)
        )
    return {"visual_style": config.visual_style, "checks": checks}


def _critical_ass_report_facts(report: dict[str, Any]) -> dict[str, Any]:
    """Extract stable singer/secondary/ruby facts from either report wrapper."""

    nested = report.get("ass")
    ass = nested if isinstance(nested, dict) else report

    def line_facts(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        keys = (
            "line_index",
            "secondary_line_index",
            "source_line_index",
            "phrase_index",
            "text",
            "voice_role",
            "singer_group",
            "effective_singer_id",
            "effective_singer_ids",
            "effective_singer_runs",
            "hot_primary_ass",
            "ruby",
        )
        return [
            {key: item.get(key) for key in keys if key in item}
            for item in value
            if isinstance(item, dict)
        ]

    top_keys = (
        "language_identity",
        "singer_color_mapping",
        "color_plan",
        "sug_hash",
        "ruby_enabled",
        "ruby_spans",
        "ruby_review",
        "ruby_consistency_gate",
        "pronunciation_validation",
    )
    return {
        **{key: ass.get(key) for key in top_keys if key in ass},
        "lines": line_facts(ass.get("lines")),
        "secondary_lines": line_facts(ass.get("secondary_lines")),
    }


def validate_ass_report_parity(
    preflight_report: dict[str, Any],
    final_report: dict[str, Any],
) -> dict[str, Any]:
    preflight = _critical_ass_report_facts(preflight_report)
    final = _critical_ass_report_facts(final_report)
    if preflight != final:
        raise KaraokeWorkflowError(
            "preflight and final singer/secondary/ruby report facts differ"
        )
    encoded = json.dumps(
        preflight, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "status": "ok",
        "critical_facts_sha256": hashlib.sha256(encoded).hexdigest(),
        "singer_color_mapping_count": len(preflight.get("singer_color_mapping", [])),
        "line_count": len(preflight["lines"]),
        "secondary_line_count": len(preflight["secondary_lines"]),
    }


def _full_decode_report(
    *,
    requested: bool,
    completed: subprocess.CompletedProcess[str] | None = None,
) -> dict[str, Any]:
    if completed is None:
        return {
            "requested": requested,
            "performed": False,
            "required": False,
            "recommended": False,
            "reason": "pending" if requested else "not-requested",
        }
    return {
        "requested": requested,
        "performed": True,
        "required": False,
        "recommended": False,
        "reason": "explicit-opt-in",
        "returncode": completed.returncode,
    }


def run_workflow(
    config: WorkflowConfig,
    *,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _run_command,
    artwork_builder: Callable[..., dict[str, Any]] | None = None,
    artwork_preparer: Callable[..., dict[str, Any]] = prepare_auto_artwork,
    media_verifier: Callable[[Path, Path], dict[str, Any]] = inspect_av1_420_media,
    ass_validator: Callable[[Path, str], dict[str, Any]] = validate_ass_for_render,
) -> dict[str, Any]:
    validate_visual_contract(config)
    display_metadata = resolve_display_metadata(
        audio_path=config.audio,
        metadata_source_audio=config.metadata_source_audio,
        title=config.title,
        artist=config.artist,
        album_title=config.album_title,
        album_artist=config.album_artist,
    )
    config = replace(
        config,
        album_title=display_metadata["album_title"],
        album_artist=display_metadata["album_artist"],
    )
    output_dir = validate_output_dir(config)
    is_vinyl = config.visual_style == "vinyl"
    input_paths = {
        "sug": config.sug,
        "delivery_audio": config.audio,
        "fonts_dir": config.fonts_dir,
        "font_file": config.font_file,
    }
    for name, path in (
        ("composition_override", config.composition),
        ("cover", config.cover),
        ("background", config.background),
        ("cover_source_audio", config.cover_source_audio),
        ("metadata_source_audio", config.metadata_source_audio),
    ):
        if path is not None:
            input_paths[name] = path
    identities = {
        name: _input_identity(path)
        for name, path in input_paths.items()
        if name != "fonts_dir"
    }
    timing_override_identity: dict[str, Any] | None = None
    if config.timing_overrides is not None:
        timing_override_identity = {
            **_input_identity(config.timing_overrides),
            "song_id": config.timing_override_song_id,
        }
        identities["timing_overrides"] = timing_override_identity
    if not config.fonts_dir.is_dir():
        raise KaraokeWorkflowError(f"fonts directory does not exist: {config.fonts_dir}")

    ffmpeg = resolve_ffmpeg(config.ffmpeg)
    output_dir.mkdir(parents=True)
    report: dict[str, Any] = {
        "schema_version": "karaoke-workflow/v1",
        "status": "running",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "language": config.language,
        "layout": config.layout,
        "visual_style": config.visual_style,
        "vinyl_motion": "rotate" if is_vinyl else None,
        "full_decode": _full_decode_report(requested=config.full_decode),
        "lossless_companion": {
            "requested": config.lossless_companion,
            "performed": False,
            "reason": (
                "requested"
                if config.lossless_companion
                else "not-requested"
            ),
        },
        "inputs": identities,
        "stages": [],
        "outputs": {},
        "display_metadata": display_metadata,
    }
    if timing_override_identity is not None:
        report["timing_override"] = timing_override_identity
    try:
        composition_gate: dict[str, Any] | None = None
        if config.composition is not None:
            composition_gate = validate_workflow_composition(config)

        if artwork_builder is None:
            prepared = artwork_preparer(
                output_dir=output_dir,
                audio_path=config.audio.resolve(),
                cover_source_audio=(
                    config.cover_source_audio.resolve()
                    if config.cover_source_audio is not None
                    else None
                ),
                cover_path=config.cover,
                background_path=config.background,
                composition_override=config.composition,
                fonts_dir=config.fonts_dir.resolve(),
                title=config.title,
                artist=config.artist,
                album_title=config.album_title,
                album_artist=config.album_artist,
                visual_style=config.visual_style,
            )
            artwork_report = prepared["report"]
            generated_vinyl = prepared["vinyl"]
        else:
            if config.composition is None:
                raise KaraokeWorkflowError(
                    "legacy artwork_builder cannot prepare an automatic composition"
                )
            generated_vinyl = None
            legacy_artwork: dict[str, Any] | None = None
            if is_vinyl:
                artwork_dir = _assert_output_path(
                    output_dir / "artwork-current", output_dir
                )
                cover_source_audio = (config.cover_source_audio or config.audio).resolve()
                legacy_artwork = artwork_builder(
                    cover_source_audio,
                    artwork_dir,
                    config.title,
                    config.artist,
                    config.cover_url,
                    config.fonts_dir.resolve(),
                    allow_network=config.allow_network,
                    album_title=config.album_title,
                    album_artist=config.album_artist,
                )
                generated_vinyl = artwork_dir / "vinyl.png"
            metadata = json.loads(
                config.composition.with_suffix(".json").read_text(encoding="utf-8")
            )
            artwork_report = {
                "schema_version": "karaoke-auto-artwork/v1",
                "selection": "explicit-advanced-override",
                "visual_style": config.visual_style,
                "composition": _input_identity(config.composition),
                "layout": metadata,
                "vinyl": (
                    _input_identity(generated_vinyl)
                    if generated_vinyl is not None
                    else None
                ),
            }
            if legacy_artwork is not None:
                artwork_report.update(legacy_artwork)

        config = replace(config, composition=Path(prepared["composition"]) if artwork_builder is None else config.composition)
        assert config.composition is not None
        identities["composition"] = _input_identity(config.composition)
        report["inputs"] = identities
        report["auto_artwork"] = artwork_report
        report["stages"].append(
            {
                "name": "auto-artwork",
                "status": "ok",
                "selection": artwork_report["selection"],
                "visual_style": config.visual_style,
                "layout": artwork_report["layout"],
                "vinyl_generated": generated_vinyl is not None,
            }
        )
        if composition_gate is None:
            composition_gate = validate_workflow_composition(config)
        report["stages"].append(
            {
                "name": "current-wide-composition",
                "status": "ok",
                "visual_style": config.visual_style,
                "gate": composition_gate,
            }
        )
        probe_paths = {
            "audio": config.audio,
            "composition": config.composition,
        }
        probes = {
            name: _probe_with_ffmpeg(ffmpeg, path.resolve(), runner=runner)
            for name, path in probe_paths.items()
        }
        full_duration = _duration_from_probe(probes["audio"])
        lossless_source_codec = (
            validate_lossless_source(config.audio, probes["audio"])
            if config.lossless_companion
            else None
        )
        duration = config.smoke_duration or full_duration
        if duration <= 0 or duration > full_duration + 0.05:
            raise KaraokeWorkflowError(
                f"render duration must be in (0, {full_duration:.3f}] seconds"
            )
        report["stages"].append(
            {"name": "inventory", "status": "ok", "probes": probes}
        )

        if is_vinyl:
            if generated_vinyl is None or not generated_vinyl.is_file():
                raise KaraokeWorkflowError(
                    "current run did not generate vinyl.png"
                )
            provenance = validate_vinyl_provenance(artwork_report, generated_vinyl)
            asset_record = {
                "generator": "scripts.karaoke_common.artwork.prepare_auto_artwork",
                **provenance,
                "source": artwork_report.get("cover_source"),
                "delivery_audio": identities["delivery_audio"],
                "generated_vinyl": _input_identity(generated_vinyl),
                "silently_reused": False,
            }
            report["stages"].append(
                {
                    "name": "generate-current-vinyl",
                    "status": "ok",
                    **asset_record,
                }
            )

        preflight_ass_path = _assert_output_path(
            output_dir / "karaoke-preflight.ass", output_dir
        )
        ass_path = _assert_output_path(output_dir / "karaoke.ass", output_dir)
        ass_report_path = _assert_output_path(output_dir / "ass-report.json", output_dir)
        output_path = _assert_output_path(output_dir / "karaoke-av1.mp4", output_dir)
        lossless_output = (
            _assert_output_path(
                output_dir / "karaoke-av1-lossless.mkv", output_dir
            )
            if config.lossless_companion
            else None
        )
        render_report_path = _assert_output_path(
            output_dir / "render-report.json", output_dir
        )
        ass_command = build_ass_command(
            config,
            generated_vinyl=generated_vinyl,
            ass_path=preflight_ass_path,
            report_path=ass_report_path,
            output_path=output_path,
            duration=duration,
        )
        ass_result = runner(ass_command)
        if ass_result.returncode != 0:
            raise KaraokeWorkflowError(f"ASS production failed: {ass_result.stderr[-2000:]}")
        if not preflight_ass_path.is_file() or not ass_report_path.is_file():
            raise KaraokeWorkflowError("ASS production did not create its declared outputs")
        preflight_ass_gate = ass_validator(
            preflight_ass_path, "HarmonyOS Sans SC"
        )
        if not preflight_ass_gate.get("ok"):
            raise KaraokeWorkflowError(
                f"preflight ASS gate failed: {preflight_ass_gate.get('errors')}"
            )
        preflight_renderer_report = json.loads(
            ass_report_path.read_text(encoding="utf-8")
        )
        preflight_contract = enforce_language_contract(config, preflight_ass_path)
        preflight_ass_sha256 = sha256_file(preflight_ass_path)
        report["stages"].append(
            {
                "name": "ass-report",
                "status": "ok",
                "contract": preflight_contract,
                "preflight_ass_sha256": preflight_ass_sha256,
                "ass_gate": preflight_ass_gate,
            }
        )

        render_command = build_render_command(
            config,
            generated_vinyl=generated_vinyl,
            ass_path=ass_path,
            report_path=render_report_path,
            output_path=output_path,
            duration=duration,
            lossless_output=lossless_output,
        )
        rendered = runner(render_command)
        if rendered.returncode != 0:
            raise KaraokeWorkflowError(f"AV1 render failed: {rendered.stderr[-2000:]}")
        if (
            not output_path.is_file()
            or not ass_path.is_file()
            or not render_report_path.is_file()
        ):
            raise KaraokeWorkflowError("AV1 render did not create its declared outputs")
        if lossless_output is not None and not lossless_output.is_file():
            raise KaraokeWorkflowError(
                "requested lossless companion was not created"
            )
        final_contract = enforce_language_contract(config, ass_path)
        final_ass_gate = ass_validator(ass_path, "HarmonyOS Sans SC")
        if not final_ass_gate.get("ok"):
            raise KaraokeWorkflowError(
                f"final ASS gate failed: {final_ass_gate.get('errors')}"
            )
        final_ass_sha256 = sha256_file(ass_path)
        if final_ass_sha256 != preflight_ass_sha256:
            raise KaraokeWorkflowError(
                "preflight and final ASS identities differ for the same SUG/config"
            )
        visual_report_gate = validate_renderer_report(
            config,
            render_report_path,
            generated_vinyl=generated_vinyl,
        )
        final_renderer_report = json.loads(
            render_report_path.read_text(encoding="utf-8")
        )
        report_parity = validate_ass_report_parity(
            preflight_renderer_report,
            final_renderer_report,
        )
        report["stages"].append(
            {
                "name": "ass-render-parity",
                "status": "ok",
                "preflight_ass_sha256": preflight_ass_sha256,
                "final_ass_sha256": final_ass_sha256,
                "contract": final_contract,
                "preflight_ass_gate": preflight_ass_gate,
                "final_ass_gate": final_ass_gate,
                "report_parity": report_parity,
            }
        )
        render_stage = {
            "name": "render-av1-mp4",
            "status": "ok",
            "duration_seconds": duration,
            "mode": "smoke" if config.smoke_duration is not None else "full",
            "visual_style": config.visual_style,
            "vinyl_motion": "rotate" if is_vinyl else None,
            "visual_report_gate": visual_report_gate,
        }
        if generated_vinyl is not None:
            render_stage["vinyl_sha256"] = sha256_file(generated_vinyl)
        report["stages"].append(render_stage)

        final_probe = _probe_with_ffmpeg(ffmpeg, output_path, runner=runner)
        existing_gate = media_verifier(ffmpeg, output_path)
        video_stream = final_probe.get("video_stream") or {}
        duration_drift = abs(
            float(final_probe.get("duration_seconds") or -1.0) - duration
        )
        media_gate = {
            "codec_av1": existing_gate.get("codec_av1") is True,
            "pixel_format_yuv420p": existing_gate.get("pixel_format_yuv420p") is True,
            "resolution_1920x1080": existing_gate.get("resolution_1920x1080") is True,
            "cfr_30fps": existing_gate.get("cfr_30fps") is True,
            "bt709": final_probe.get("bt709") is True,
            "audio_present": final_probe.get("audio_stream") is not None,
            "duration_within_100ms": duration_drift <= 0.1,
            "parsed_video_codec": video_stream.get("codec") == "av1",
        }
        if not all(media_gate.values()):
            raise KaraokeWorkflowError(f"final media gate failed: {media_gate}")
        decoded = None
        if config.full_decode:
            decoded = runner(build_full_decode_command(ffmpeg, output_path))
            report["full_decode"] = _full_decode_report(
                requested=True,
                completed=decoded,
            )
            if decoded.returncode != 0:
                raise KaraokeWorkflowError(
                    f"full null decode failed: {decoded.stderr[-2000:]}"
                )
        if lossless_output is not None:
            lossless_probe = _probe_with_ffmpeg(
                ffmpeg, lossless_output, runner=runner
            )
            lossless_video = lossless_probe.get("video_stream") or {}
            lossless_audio = lossless_probe.get("audio_stream") or {}
            lossless_duration_drift = abs(
                float(lossless_probe.get("duration_seconds") or -1.0) - duration
            )
            mp4_video_sha256 = _video_stream_sha256(
                ffmpeg, output_path, runner=runner
            )
            mkv_video_sha256 = _video_stream_sha256(
                ffmpeg, lossless_output, runner=runner
            )
            lossless_gate = {
                "video_codec_av1": lossless_video.get("codec") == "av1",
                "audio_codec_flac": lossless_audio.get("codec") == "flac",
                "duration_within_100ms": lossless_duration_drift <= 0.1,
                "video_stream_matches_mp4": mp4_video_sha256 == mkv_video_sha256,
            }
            if not all(lossless_gate.values()):
                raise KaraokeWorkflowError(
                    f"lossless companion gate failed: {lossless_gate}"
                )
            lossless_decoded = None
            if config.full_decode:
                lossless_decoded = runner(
                    build_full_decode_command(ffmpeg, lossless_output)
                )
                if lossless_decoded.returncode != 0:
                    raise KaraokeWorkflowError(
                        "lossless companion full null decode failed: "
                        f"{lossless_decoded.stderr[-2000:]}"
                    )
            report["lossless_companion"] = {
                "requested": True,
                "performed": True,
                "path": str(lossless_output),
                "source_codec": lossless_source_codec,
                "checks": lossless_gate,
                "video_stream_sha256": mkv_video_sha256,
                "full_decode": _full_decode_report(
                    requested=config.full_decode,
                    completed=lossless_decoded,
                ),
            }
        editable_sug = export_editable_sug(config.sug, config.audio, output_dir)
        report["stages"].append(
            {"name": "editable-sug", "status": "ok", **editable_sug}
        )
        report["outputs"] = {
            "preflight_ass": _input_identity(preflight_ass_path),
            "ass": _input_identity(ass_path),
            "ass_report": _input_identity(ass_report_path),
            "render_report": _input_identity(render_report_path),
            "video": _input_identity(output_path),
            "editable_sug": _input_identity(Path(editable_sug["path"])),
        }
        if generated_vinyl is not None:
            report["outputs"]["generated_vinyl"] = _input_identity(generated_vinyl)
        if lossless_output is not None:
            report["outputs"]["lossless_video"] = _input_identity(lossless_output)
        report["stages"].append(
            {
                "name": "ffmpeg-identity",
                "status": "ok",
                "probe": final_probe,
                "media_gate": media_gate,
                "existing_verifier": existing_gate,
                "full_decode": report["full_decode"],
            }
        )
        report["status"] = "ok"
        return report
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        _write_report(output_dir, report)


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sug", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument(
        "--cover-source-audio",
        type=Path,
        help=(
            "audio used only to extract artwork cover data; defaults to --audio"
        ),
    )
    parser.add_argument(
        "--metadata-source-audio",
        type=Path,
        help="audio whose tags provide default album title and album artist",
    )
    parser.add_argument(
        "--composition",
        type=Path,
        help=(
            "advanced explicit wide-layout override; omitted builds the current "
            "composition inside --output-dir"
        ),
    )
    parser.add_argument(
        "--cover",
        type=Path,
        help="explicit cover image; otherwise use the cover audio's embedded image",
    )
    parser.add_argument(
        "--background",
        type=Path,
        help="explicit background image; otherwise derive it from the selected cover",
    )
    parser.add_argument(
        "--visual-style",
        choices=VISUAL_STYLES,
        default="vinyl",
        help="choose exactly one right-side visual effect (default: vinyl)",
    )
    parser.add_argument(
        "--color-policy",
        choices=("cover", "project"),
        default="cover",
        help=(
            "cover assigns the ordered cover palette to active singers; "
            "project preserves SUG singer colors as a compatibility rollback"
        ),
    )
    parser.add_argument(
        "--singer-color",
        action="append",
        default=[],
        metavar="SINGER_ID=#RRGGBB",
        help=(
            "explicit singer colour override; repeat once per singer and let the "
            "renderer enforce syntax and precedence"
        ),
    )
    parser.add_argument(
        "--vinyl",
        dest="canonical_vinyl",
        type=Path,
        help=(
            "legacy compatibility input; current runs always generate vinyl inside "
            "--output-dir and never reuse this file"
        ),
    )
    parser.add_argument("--spectrum-color")
    parser.add_argument("--progress-color")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--artist", required=True)
    parser.add_argument("--album-title")
    parser.add_argument("--album-artist")
    parser.add_argument("--fonts-dir", type=Path, default=SHARED_FONT_DIR)
    parser.add_argument("--font-file", type=Path, default=SHARED_FONT_FILE)
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--smoke-duration", type=float)
    duration.add_argument(
        "--full-duration",
        action="store_true",
        help="render the complete probed audio duration (the default)",
    )
    parser.add_argument("--cover-url", default="")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument(
        "--lossless-companion",
        action="store_true",
        help=(
            "explicitly request karaoke-av1-lossless.mkv; only actual FLAC or "
            "PCM WAV sources are accepted"
        ),
    )
    parser.add_argument(
        "--full-decode",
        action="store_true",
        help="explicitly run full MP4/MKV null-decode diagnostics",
    )
    parser.add_argument(
        "--canonical-deliverables",
        type=Path,
        action="append",
        default=[],
    )


def config_from_args(
    args: argparse.Namespace,
    *,
    language: str,
    layout: str,
    pronunciation_validation: str,
) -> WorkflowConfig:
    config = WorkflowConfig(
        sug=args.sug,
        audio=args.audio,
        cover_source_audio=args.cover_source_audio,
        metadata_source_audio=args.metadata_source_audio,
        composition=args.composition,
        canonical_vinyl=args.canonical_vinyl,
        output_dir=args.output_dir,
        language=language,
        layout=layout,
        title=args.title,
        artist=args.artist,
        album_title=args.album_title,
        album_artist=args.album_artist,
        cover=args.cover,
        background=args.background,
        fonts_dir=args.fonts_dir,
        font_file=args.font_file,
        smoke_duration=args.smoke_duration,
        pronunciation_validation=pronunciation_validation,
        visual_style=args.visual_style,
        color_policy=args.color_policy,
        singer_colors=tuple(args.singer_color),
        spectrum_color=args.spectrum_color,
        progress_color=args.progress_color,
        cover_url=args.cover_url,
        allow_network=args.allow_network,
        ffmpeg=args.ffmpeg,
        lossless_companion=args.lossless_companion,
        full_decode=args.full_decode,
        canonical_deliverables=tuple(args.canonical_deliverables),
    )
    validate_visual_contract(config)
    return config


def print_result(report: dict[str, Any]) -> int:
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
