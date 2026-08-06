#!/usr/bin/env python3
"""Render an album directly from SUG/ASS and artwork to HEVC 4:4:4.

The script deliberately does not consume an existing video master.  A task is
assembled from the manifest's original MP3, the profile-specific composition,
the track vinyl artwork, and the newest SUG timing source.  The preview
renderer generates the profile ASS and performs the final render in one
``hevc_nvenc`` invocation using full-range YUV 4:4:4 (``yuv444p``).

Every generated artifact is first written beside its project deliverable with
a unique ``.partial`` name.  Media and report gates run before any artifact is
published with ``os.replace``.  This keeps an interrupted or failed encode
from replacing a previously published deliverable.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .karaoke_album import (
        DEFAULT_MANIFEST_PATH,
        AlbumManifest,
        AlbumTrack,
        load_album_manifest,
        project_relative,
        sha256_file,
    )
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_album import (  # type: ignore[no-redef]
        DEFAULT_MANIFEST_PATH,
        AlbumManifest,
        AlbumTrack,
        load_album_manifest,
        project_relative,
        sha256_file,
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
PREVIEW_SCRIPT = REPO_ROOT / "scripts" / "karaoke_review_preview.py"
PROFILES = ("standard", "wide")
FONT_FAMILY = "HarmonyOS Sans SC"
SHARED_FONT_DIR = REPO_ROOT / "assets" / "fonts" / "HarmonyOS-Sans"
PROFILE_LAYOUTS = {
    "standard": "standard-v7",
    "wide": "wide-bottom",
}
DEFAULT_FONT_NAMES = (
    "HarmonyOS_Sans_SC_Regular.ttf",
    "HarmonyOS Sans SC Regular.ttf",
)
_TEMP_MARKERS = (".partial", ".tmp", ".temp")


class DirectHEVC444RenderError(RuntimeError):
    """Raised when a direct HEVC 4:4:4 task cannot pass publication gates."""


# Compatibility for callers of the earlier AV1-only implementation.
DirectAV1RenderError = DirectHEVC444RenderError


class RenderTask:
    """All immutable source and publication paths for one song/profile pair."""

    def __init__(
        self,
        *,
        album: AlbumManifest,
        root: Path,
        track: AlbumTrack,
        profile: str,
        sug_path: Path,
        ass_source: Path | None,
        composition_path: Path,
        vinyl_path: Path,
        fonts_dir: Path,
        font_file: Path,
        ass_output: Path,
        video_output: Path,
        direct_report: Path,
        ass_report: Path,
        duration_seconds: float,
    ) -> None:
        self.album = album
        self.root = root
        self.track = track
        self.profile = profile
        self.sug_path = sug_path
        self.ass_source = ass_source
        self.composition_path = composition_path
        self.vinyl_path = vinyl_path
        self.fonts_dir = fonts_dir
        self.font_file = font_file
        self.ass_output = ass_output
        self.video_output = video_output
        self.direct_report = direct_report
        self.ass_report = ass_report
        self.duration_seconds = duration_seconds

    @property
    def layout(self) -> str:
        return PROFILE_LAYOUTS[self.profile]

    @property
    def report_stem(self) -> str:
        return self.track.artifact_slug


def _resolve_path(value: Path | str, *, base: Path = REPO_ROOT) -> Path:
    """Resolve a CLI path relative to the project when it is not absolute."""

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _temporary_path(target: Path, *, token: str | None = None) -> Path:
    """Return a unique temporary path in the same directory as ``target``."""

    suffix = token or uuid.uuid4().hex
    return target.with_name(f".{target.stem}.{suffix}.partial{target.suffix}")


def _remove_if_present(path: Path) -> None:
    with suppress(FileNotFoundError):
        path.unlink()


def _split_selectors(values: Iterable[str] | None) -> list[str]:
    selectors: list[str] = []
    for value in values or ():
        selectors.extend(item.strip() for item in str(value).split(",") if item.strip())
    return selectors


def select_tracks(
    album: AlbumManifest,
    selectors: Iterable[str] | None = None,
) -> tuple[AlbumTrack, ...]:
    """Select manifest tracks by song id, title, artifact slug, or timing stem."""

    requested = _split_selectors(selectors)
    if not requested:
        return tuple(album.tracks)

    selected_ids: set[str] = set()
    unknown: list[str] = []
    for selector in requested:
        normalized = selector.casefold()
        matches = [
            track
            for track in album.tracks
            if normalized
            in {
                str(track.song_id).casefold(),
                track.title.casefold(),
                track.artifact_slug.casefold(),
                track.timing_stem.casefold(),
                Path(track.audio_file).stem.casefold(),
            }
        ]
        if not matches:
            unknown.append(selector)
            continue
        selected_ids.update(str(track.song_id) for track in matches)

    if unknown:
        available = ", ".join(
            f"{track.song_id}:{track.artifact_slug}" for track in album.tracks
        )
        raise ValueError(
            f"unknown --song selector(s): {', '.join(unknown)}; available: {available}"
        )
    return tuple(track for track in album.tracks if str(track.song_id) in selected_ids)


def select_profiles(values: Iterable[str] | None) -> tuple[str, ...]:
    """Return selected profiles in stable build order."""

    requested = _split_selectors(values)
    if not requested:
        return PROFILES
    unknown = [value for value in requested if value not in PROFILES]
    if unknown:
        raise ValueError(
            f"unknown --profile value(s): {', '.join(unknown)}; "
            f"expected one of {', '.join(PROFILES)}"
        )
    requested_set = set(requested)
    return tuple(profile for profile in PROFILES if profile in requested_set)


def _is_temporary_name(path: Path) -> bool:
    lowered = path.name.casefold()
    return any(marker in lowered for marker in _TEMP_MARKERS)


def _track_tokens(track: AlbumTrack) -> tuple[str, ...]:
    return tuple(
        token.casefold()
        for token in (
            track.timing_stem,
            str(track.song_id),
            track.artifact_slug,
            track.title,
            Path(track.audio_file).stem,
        )
        if token
    )


def _matching_files(directory: Path, track: AlbumTrack, suffix: str) -> list[Path]:
    if not directory.is_dir():
        return []
    tokens = _track_tokens(track)
    result: list[Path] = []
    for path in directory.rglob(f"*{suffix}"):
        if not path.is_file() or _is_temporary_name(path):
            continue
        if any(token in path.stem.casefold() for token in tokens):
            result.append(path.resolve())
    return result


def _latest_file(
    candidates: Iterable[Path],
    *,
    exact_names: Iterable[str] = (),
) -> Path | None:
    paths = list(dict.fromkeys(path.resolve() for path in candidates))
    if not paths:
        return None
    exact = {name.casefold() for name in exact_names}
    exact_paths = [path for path in paths if path.name.casefold() in exact]
    if exact_paths:
        paths = exact_paths
    return max(paths, key=lambda path: (path.stat().st_mtime_ns, str(path)))


def find_latest_sug(timing_dir: Path, track: AlbumTrack) -> Path:
    """Find the newest matching SUG, preferring the manifest timing stem."""

    candidates = _matching_files(timing_dir, track, ".sug")
    result = _latest_file(
        candidates,
        exact_names=(f"{track.timing_stem}.sug",),
    )
    if result is None:
        raise FileNotFoundError(
            f"no SUG timing source for {track.song_id}:{track.artifact_slug} "
            f"under {timing_dir}"
        )
    return result


def find_latest_ass(
    timing_dir: Path,
    track: AlbumTrack,
    profile: str,
) -> Path | None:
    """Find the newest existing ASS for provenance, preferring this profile."""

    candidates = _matching_files(timing_dir, track, ".ass")
    profile_dir = (timing_dir / profile).resolve()
    profile_candidates = [
        path
        for path in candidates
        if profile_dir == path.parent or profile_dir in path.parents
    ]
    if profile_candidates:
        candidates = profile_candidates
    return _latest_file(
        candidates,
        exact_names=(f"{track.timing_stem}.ass",),
    )


def _font_file_from_directory(fonts_dir: Path) -> Path:
    for name in DEFAULT_FONT_NAMES:
        candidate = fonts_dir / name
        if candidate.is_file():
            return candidate.resolve()

    font_files = sorted(
        path.resolve() for path in fonts_dir.rglob("*.ttf") if path.is_file()
    )
    regular = [
        path
        for path in font_files
        if "regular" in path.stem.casefold() and "harmony" in path.stem.casefold()
    ]
    if regular:
        return regular[0]
    if font_files:
        return font_files[0]
    raise FileNotFoundError(f"no TTF font found under {fonts_dir}")


def resolve_font_paths(
    root: Path,
    *,
    fonts_dir: Path | None = None,
    font_file: Path | None = None,
    font_package: Path | None = None,
) -> tuple[Path, Path]:
    """Resolve the project-local HarmonyOS Sans directory and measurement font."""

    resolved_fonts_dir = (fonts_dir or SHARED_FONT_DIR).resolve()
    if not resolved_fonts_dir.is_dir():
        package_hint = f"; font package: {font_package}" if font_package else ""
        raise FileNotFoundError(
            f"HarmonyOS Sans fonts directory does not exist: "
            f"{resolved_fonts_dir}{package_hint}"
        )
    resolved_font_file = (
        font_file.resolve()
        if font_file is not None
        else _font_file_from_directory(resolved_fonts_dir)
    )
    if not resolved_font_file.is_file():
        raise FileNotFoundError(f"font file does not exist: {resolved_font_file}")
    return resolved_fonts_dir, resolved_font_file


def _artwork_paths(
    artwork_root: Path,
    track: AlbumTrack,
    profile: str,
) -> tuple[Path, Path]:
    track_artwork = artwork_root / track.artifact_slug
    profile_artwork = (
        artwork_root / "wide" / track.artifact_slug
        if profile == "wide"
        else track_artwork
    )
    composition = profile_artwork / "composition.png"
    # Wide compositions are profile-specific, while the vinyl source is the
    # track-level asset generated from the same cover artwork.
    vinyl = track_artwork / "vinyl.png"
    if not vinyl.is_file():
        profile_vinyl = profile_artwork / "vinyl.png"
        if profile_vinyl.is_file():
            vinyl = profile_vinyl
    return composition.resolve(), vinyl.resolve()


def plan_tasks(
    album: AlbumManifest,
    *,
    root: Path,
    tracks: Sequence[AlbumTrack],
    profiles: Sequence[str],
    timing_dir: Path | None = None,
    artwork_root: Path | None = None,
    fonts_dir: Path | None = None,
    font_file: Path | None = None,
    duration_seconds: float | None = None,
) -> tuple[RenderTask, ...]:
    """Build and validate the ten (or selected) direct-render task inputs."""

    root = root.resolve()
    timing_root = (timing_dir or root / "timing").resolve()
    artwork_root = (artwork_root or root / "artwork").resolve()
    resolved_fonts_dir, resolved_font_file = resolve_font_paths(
        root,
        fonts_dir=fonts_dir,
        font_file=font_file,
        font_package=album.font_package,
    )
    tasks: list[RenderTask] = []
    missing: list[str] = []
    for profile in profiles:
        if profile not in PROFILES:
            raise ValueError(f"unsupported profile: {profile}")
        for track in tracks:
            sug_path = find_latest_sug(timing_root, track)
            ass_source = find_latest_ass(timing_root, track, profile)
            composition_path, vinyl_path = _artwork_paths(
                artwork_root,
                track,
                profile,
            )
            audio_path = track.audio_path
            missing.extend(
                str(path)
                for path in (audio_path, sug_path, composition_path, vinyl_path)
                if not path.is_file()
            )
            profile_timing = timing_root / profile
            ass_output = profile_timing / f"{track.timing_stem}.ass"
            video_output = (
                root / "video" / "hevc444" / profile / f"{track.artifact_slug}.mp4"
            )
            validation_root = root / "validation" / profile
            direct_report = validation_root / (
                f"{track.artifact_slug}_direct_hevc444_render_report.json"
            )
            ass_report = validation_root / f"{track.artifact_slug}_ass_report.json"
            tasks.append(
                RenderTask(
                    album=album,
                    root=root,
                    track=track,
                    profile=profile,
                    sug_path=sug_path,
                    ass_source=ass_source,
                    composition_path=composition_path,
                    vinyl_path=vinyl_path,
                    fonts_dir=resolved_fonts_dir,
                    font_file=resolved_font_file,
                    ass_output=ass_output.resolve(),
                    video_output=video_output.resolve(),
                    direct_report=direct_report.resolve(),
                    ass_report=ass_report.resolve(),
                    duration_seconds=(
                        float(duration_seconds)
                        if duration_seconds is not None
                        else track.expected_duration_ms / 1000.0
                    ),
                )
            )
    if missing:
        unique_missing = list(dict.fromkeys(missing))
        raise FileNotFoundError(
            "missing direct-render inputs:\n" + "\n".join(unique_missing)
        )
    return tuple(tasks)


def _flag_value(command: Sequence[str], flag: str) -> str:
    try:
        return str(command[command.index(flag) + 1])
    except (ValueError, IndexError) as error:
        raise DirectAV1RenderError(f"preview command is missing {flag}") from error


def build_preview_command(
    task: RenderTask,
    *,
    temporary_video: Path,
    temporary_ass: Path,
    temporary_report: Path,
    preview_script: Path = PREVIEW_SCRIPT,
    ass_only: bool = False,
) -> list[str]:
    """Build the exact preview-render command for one task."""

    command = [
        sys.executable,
        str(preview_script.resolve()),
        "--sug",
        str(task.sug_path),
        "--audio",
        str(task.track.audio_path),
        "--composition",
        str(task.composition_path),
        "--vinyl",
        str(task.vinyl_path),
        "--fonts-dir",
        str(task.fonts_dir),
        "--font-file",
        str(task.font_file),
        "--output",
        str(temporary_video),
        "--ass-output",
        str(temporary_ass),
        "--report-output",
        str(temporary_report),
        "--start",
        "0",
        "--duration",
        f"{task.duration_seconds:.3f}",
        "--layout",
        task.profile,
        # This is the only permitted full-render video encoder for this lane.
        "--video-encoder",
        "hevc_nvenc_444",
        "--hevc-cq",
        "30",
    ]
    if ass_only:
        command.append("--ass-only")
    return command


def run_preview(
    command: Sequence[str],
    *,
    cwd: Path = REPO_ROOT,
) -> subprocess.CompletedProcess[str]:
    """Run the existing renderer without allowing a shell to reinterpret paths."""

    print("$", subprocess.list2cmdline([str(value) for value in command]), flush=True)
    return subprocess.run(
        list(command),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _validate_ass_file(path: Path, profile: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception as error:  # pragma: no cover - exercised through result gate
        return {"ok": False, "path": str(path), "errors": [f"read_failed: {error}"]}

    errors: list[str] = []
    expected_layout = PROFILE_LAYOUTS[profile]
    if f"; Layout: {expected_layout}" not in text:
        errors.append(f"layout_mismatch: expected {expected_layout}")
    if not re.search(r"(?im)^PlayResX:\s*1920\s*$", text):
        errors.append("missing_PlayResX_1920")
    if not re.search(r"(?im)^PlayResY:\s*1080\s*$", text):
        errors.append("missing_PlayResY_1080")
    style_lines = [line for line in text.splitlines() if line.startswith("Style:")]
    if not style_lines:
        errors.append("no_styles")
    if any(FONT_FAMILY not in line for line in style_lines):
        errors.append(f"styles_not_using_{FONT_FAMILY}")
    dialogue_count = sum(
        1 for line in text.splitlines() if line.lstrip().startswith("Dialogue:")
    )
    if dialogue_count == 0:
        errors.append("no_dialogue_events")
    return {
        "ok": not errors,
        "path": str(path),
        "errors": errors,
        "font_family": FONT_FAMILY,
        "layout": expected_layout,
        "dialogue_count": dialogue_count,
    }


def default_ffmpeg() -> Path:
    """Resolve the project environment's ffmpeg for the HEVC media gate."""

    try:
        import imageio_ffmpeg
    except ImportError as error:  # pragma: no cover - dependency failure
        raise DirectAV1RenderError(
            "imageio-ffmpeg is required for HEVC 4:4:4 validation"
        ) from error
    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    if not ffmpeg.is_file():
        raise FileNotFoundError(f"ffmpeg executable does not exist: {ffmpeg}")
    return ffmpeg


def verify_hevc444_output(
    output: Path,
    *,
    ffmpeg: Path | None = None,
) -> dict[str, Any]:
    """Probe the rendered file and require HEVC Rext/hvc1 YUV 4:4:4."""

    checks: dict[str, Any] = {
        "exists": output.is_file(),
        "non_empty": output.is_file() and output.stat().st_size > 0,
    }
    if not checks["non_empty"]:
        return {"ok": False, "path": str(output), "checks": checks, "probe": ""}

    executable = (ffmpeg or default_ffmpeg()).resolve()
    completed = subprocess.run(
        [str(executable), "-hide_banner", "-i", str(output)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    probe = f"{completed.stdout}\n{completed.stderr}"
    video_line = re.search(r"Video:\s+([^\r\n]+)", probe, re.IGNORECASE)
    checks.update(
        {
            "codec_hevc": bool(re.search(r"Video:\s+hevc\b", probe, re.IGNORECASE)),
            "codec_tag_hvc1": "(hvc1 /" in probe.casefold(),
            "not_h264": not bool(
                re.search(r"Video:\s+(?:h264|h\.264)\b", probe, re.IGNORECASE)
            ),
            "not_av1": not bool(re.search(r"Video:\s+av1\b", probe, re.IGNORECASE)),
            "resolution_1920x1080": "1920x1080" in probe,
            "pixel_format_yuv444p": bool(
                re.search(r"Video:.*\byuv444p\b", probe, re.IGNORECASE)
            ),
            "profile_rext": bool(
                re.search(r"Video:\s+hevc.*\(Rext\)", probe, re.IGNORECASE)
            ),
            "full_range": bool(re.search(r"\byuv444p\(pc,", probe, re.IGNORECASE)),
            "cfr_30fps": bool(re.search(r"\b30\s+fps\b", probe)),
            "aac_audio": bool(re.search(r"Audio:\s+aac\b", probe, re.IGNORECASE)),
        }
    )
    return {
        "ok": all(checks.values()),
        "path": str(output),
        "size_bytes": output.stat().st_size,
        "checks": checks,
        "video_line": video_line.group(0) if video_line else None,
        "probe_returncode": completed.returncode,
        "probe_stderr_tail": completed.stderr[-1200:],
    }


verify_av1_output = verify_hevc444_output


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise DirectAV1RenderError(
            f"invalid renderer report {path}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise DirectAV1RenderError(f"renderer report must be an object: {path}")
    return value


def _validate_preview_report(
    report: dict[str, Any],
    *,
    ass_only: bool,
) -> None:
    expected_statuses = {"ass-ready"} if ass_only else {"ok"}
    if report.get("status") not in expected_statuses:
        raise DirectAV1RenderError(
            f"unexpected preview report status: {report.get('status')!r}"
        )
    ass = report.get("ass")
    if not isinstance(ass, dict) or not ass.get("ass"):
        raise DirectAV1RenderError("preview report has no ASS artifact")
    if ass_only:
        return
    video = report.get("video")
    if not isinstance(video, dict) or video.get("video_encoder") != "hevc_nvenc_444":
        raise DirectAV1RenderError(
            "preview report did not confirm video_encoder=hevc_nvenc_444"
        )
    if video.get("pixel_format") != "yuv444p":
        raise DirectAV1RenderError(
            "preview report did not confirm pixel_format=yuv444p"
        )


def _source_record(task: RenderTask) -> dict[str, str | None]:
    def relative(path: Path | None) -> str | None:
        return project_relative(path, REPO_ROOT) if path is not None else None

    def identity(path: Path | None) -> str | None:
        return sha256_file(path) if path is not None and path.is_file() else None

    return {
        "audio": relative(task.track.audio_path),
        "audio_sha256": identity(task.track.audio_path),
        "composition": relative(task.composition_path),
        "composition_sha256": identity(task.composition_path),
        "vinyl": relative(task.vinyl_path),
        "vinyl_sha256": identity(task.vinyl_path),
        "sug": relative(task.sug_path),
        "sug_sha256": identity(task.sug_path),
        "latest_ass": relative(task.ass_source),
        "latest_ass_sha256": identity(task.ass_source),
    }


def _normalise_report(
    report: dict[str, Any],
    task: RenderTask,
    *,
    ass_only: bool,
    video_path: Path,
    ass_path: Path,
    media: dict[str, Any] | None,
    video_size_bytes: int | None = None,
    video_sha256: str | None = None,
    render_elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    """Make preview report paths stable and attach direct-render provenance."""

    result = copy.deepcopy(report)
    ass_report = result.setdefault("ass", {})
    if not isinstance(ass_report, dict):
        ass_report = {}
        result["ass"] = ass_report
    ass_report["ass"] = str(ass_path.resolve())
    result.update(
        {
            "profile": task.profile,
            "song_id": str(task.track.song_id),
            "title": task.track.title,
            "artist": task.track.artist,
            "artifact_slug": task.track.artifact_slug,
            "render_mode": "ass-only" if ass_only else "direct-hevc444",
            "intermediate_h264": False,
            "intermediate_av1": False,
            "source_chain": (
                "original MP3 + composition + vinyl + latest SUG/ASS -> "
                "hevc_nvenc/yuv444p"
                if not ass_only
                else "latest SUG/ASS -> profile ASS"
            ),
            "sources": _source_record(task),
        }
    )
    if not ass_only:
        video_report = result.setdefault("video", {})
        if not isinstance(video_report, dict):
            video_report = {}
            result["video"] = video_report
        video_report["video"] = str(video_path.resolve())
        video_report["video_encoder"] = "hevc_nvenc_444"
        video_report["pixel_format"] = "yuv444p"
        video_report["bytes"] = (
            video_size_bytes
            if video_size_bytes is not None
            else video_path.stat().st_size
        )
        video_report["media_checks"] = media
        result["output_sha256"] = video_sha256
        result["render_elapsed_seconds"] = render_elapsed_seconds
    return result


def _publish_atomically(pairs: Sequence[tuple[Path, Path]]) -> None:
    """Publish already-validated project-local artifacts one by one atomically."""

    for temporary, target in pairs:
        if not temporary.is_file():
            raise DirectAV1RenderError(
                f"validated temporary artifact disappeared: {temporary}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(temporary), str(target))


def render_one(
    task: RenderTask,
    *,
    ass_only: bool = False,
    preview_script: Path = PREVIEW_SCRIPT,
    ffmpeg: Path | None = None,
    runner: Callable[[Sequence[str]], Any] | None = None,
    verifier: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Render one task and publish only after ASS/report/media validation."""

    token = uuid.uuid4().hex
    temporary_video = _temporary_path(task.video_output, token=token)
    temporary_ass = _temporary_path(task.ass_output, token=token)
    report_target = task.ass_report if ass_only else task.direct_report
    temporary_report = _temporary_path(report_target, token=token)
    command = build_preview_command(
        task,
        temporary_video=temporary_video,
        temporary_ass=temporary_ass,
        temporary_report=temporary_report,
        preview_script=preview_script,
        ass_only=ass_only,
    )
    command_text = " ".join(str(value) for value in command).casefold()
    if (
        "h264" in command_text
        or "video/standard" in command_text
        or "video/wide" in command_text
        or "video/av1" in command_text
        or "video/legacy" in command_text
    ):
        raise DirectAV1RenderError(
            "direct HEVC 4:4:4 command contains a forbidden video master input"
        )

    started = time.perf_counter()
    try:
        completed = (runner or run_preview)(command)
        returncode = getattr(completed, "returncode", 0)
        if returncode != 0:
            stderr = str(getattr(completed, "stderr", ""))
            stdout = str(getattr(completed, "stdout", ""))
            raise DirectAV1RenderError(
                f"preview render failed for {task.profile}:{task.track.artifact_slug} "
                f"(returncode={returncode})\n{stderr[-2500:] or stdout[-1000:]}"
            )
        if not temporary_ass.is_file() or temporary_ass.stat().st_size == 0:
            raise DirectAV1RenderError(f"preview did not create ASS: {temporary_ass}")
        ass_gate = _validate_ass_file(temporary_ass, task.profile)
        if not ass_gate["ok"]:
            raise DirectAV1RenderError(f"ASS validation failed: {ass_gate}")
        if not temporary_report.is_file():
            raise DirectAV1RenderError(
                f"preview did not create report: {temporary_report}"
            )
        preview_report = _read_json(temporary_report)
        _validate_preview_report(preview_report, ass_only=ass_only)

        media: dict[str, Any] | None = None
        if not ass_only:
            if not temporary_video.is_file() or temporary_video.stat().st_size == 0:
                raise DirectAV1RenderError(
                    f"preview did not create video: {temporary_video}"
                )
            media = (verifier or verify_hevc444_output)(temporary_video, ffmpeg=ffmpeg)
            if not media.get("ok"):
                raise DirectAV1RenderError(
                    f"HEVC 4:4:4 media validation failed: {media}"
                )

        elapsed_seconds = round(time.perf_counter() - started, 3)
        output_sha256 = sha256_file(temporary_video) if not ass_only else None
        normalised = _normalise_report(
            preview_report,
            task,
            ass_only=ass_only,
            video_path=task.video_output,
            ass_path=task.ass_output,
            media=media,
            video_size_bytes=temporary_video.stat().st_size if not ass_only else None,
            video_sha256=output_sha256,
            render_elapsed_seconds=elapsed_seconds,
        )
        temporary_report.write_text(
            json.dumps(normalised, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        pairs = [(temporary_ass, task.ass_output), (temporary_report, report_target)]
        if not ass_only:
            pairs.append((temporary_video, task.video_output))
        _publish_atomically(pairs)

        result: dict[str, Any] = {
            "status": "ass-ready" if ass_only else "ok",
            "profile": task.profile,
            "song_id": str(task.track.song_id),
            "title": task.track.title,
            "artifact_slug": task.track.artifact_slug,
            "render_mode": "ass-only" if ass_only else "direct-hevc444",
            "ass": str(task.ass_output),
            "report": str(report_target),
            "elapsed_seconds": elapsed_seconds,
        }
        if not ass_only:
            result.update(
                {
                    "video": str(task.video_output),
                    "output_size_bytes": task.video_output.stat().st_size,
                    "sha256": output_sha256,
                    "media": media,
                }
            )
        return result
    finally:
        for temporary in (temporary_video, temporary_ass, temporary_report):
            _remove_if_present(temporary)


def build_hevc444_report(
    results: Sequence[dict[str, Any]],
    *,
    root: Path,
) -> dict[str, Any]:
    """Build the aggregate HEVC 4:4:4 report for a complete ten-output batch."""

    if len(results) != len(PROFILES) * 5:
        raise DirectAV1RenderError(
            "aggregate HEVC 4:4:4 report requires exactly 10 direct-render results"
        )
    output_keys = {
        (str(result["profile"]), str(result["song_id"])) for result in results
    }
    if len(output_keys) != len(results):
        raise DirectAV1RenderError(
            "aggregate HEVC 4:4:4 report contains duplicate profile/song entries"
        )
    song_ids = sorted({str(result["song_id"]) for result in results})
    profile_song_ids = {
        profile: {
            str(result["song_id"]) for result in results if result["profile"] == profile
        }
        for profile in PROFILES
    }
    if len(song_ids) != 5 or any(len(ids) != 5 for ids in profile_song_ids.values()):
        raise DirectAV1RenderError(
            "aggregate HEVC 4:4:4 report must contain five songs in both profiles"
        )
    if profile_song_ids[PROFILES[0]] != profile_song_ids[PROFILES[1]]:
        raise DirectAV1RenderError(
            "aggregate HEVC 4:4:4 report profile song collections must match"
        )

    outputs: list[dict[str, Any]] = []
    for result in sorted(
        results,
        key=lambda item: (str(item["profile"]), str(item["artifact_slug"])),
    ):
        output_path = Path(str(result["video"])).resolve()
        sources = result.get("sources", {})
        outputs.append(
            {
                "profile": result["profile"],
                "song_id": str(result["song_id"]),
                "title": result["title"],
                "artifact_slug": result["artifact_slug"],
                "source": f"original audio + artwork + timing/{result['profile']}/ASS",
                "source_paths": sources,
                "output": project_relative(output_path, root),
                "direct_hevc444_render_report": project_relative(
                    Path(str(result["report"])), root
                ),
                "render_mode": "direct-hevc444",
                "intermediate_h264": False,
                "intermediate_av1": False,
                "audio": "aac-lc-320k",
                "output_size_bytes": result["output_size_bytes"],
                "sha256": result["sha256"],
                "elapsed_seconds": result["elapsed_seconds"],
            }
        )
    return {
        "schema_version": "karaoke-hevc444/v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "encoder": "hevc_nvenc",
        "container": "mp4",
        "codec_tag": "hvc1",
        "profile": "Rext",
        "pixel_format": "yuv444p",
        "color_range": "pc",
        "color_matrix": "bt709",
        "audio": "AAC-LC 320 kb/s for direct renders",
        "direct_render": {
            "song_ids": song_ids,
            "source_chain": (
                "original audio + artwork + timing/{profile}/ASS -> hevc_nvenc/yuv444p"
            ),
            "intermediate_h264": False,
            "intermediate_av1": False,
            "reports": (
                "validation/{standard,wide}/{track}_direct_hevc444_render_report.json"
            ),
        },
        "settings": {
            "preset": "p7",
            "tune": "hq",
            "rate_control": "vbr",
            "cq": 30,
            "multipass": "fullres",
            "lookahead": 32,
            "spatial_aq": True,
            "temporal_aq": True,
            "aq_strength": 8,
            "gop_frames": 240,
        },
        "outputs": outputs,
    }


build_av1_report = build_hevc444_report


def collect_existing_results(
    tasks: Sequence[RenderTask],
    *,
    ffmpeg: Path | None = None,
) -> list[dict[str, Any]]:
    """Revalidate published direct renders for an aggregate-only pass."""

    results: list[dict[str, Any]] = []
    for task in tasks:
        if not task.direct_report.is_file():
            raise DirectAV1RenderError(
                f"missing direct HEVC 4:4:4 render report: {task.direct_report}"
            )
        report = _read_json(task.direct_report)
        _validate_preview_report(report, ass_only=False)
        expected_identity = {
            "profile": task.profile,
            "song_id": str(task.track.song_id),
            "artifact_slug": task.track.artifact_slug,
            "render_mode": "direct-hevc444",
            "intermediate_h264": False,
            "intermediate_av1": False,
        }
        for key, expected in expected_identity.items():
            if report.get(key) != expected:
                raise DirectAV1RenderError(
                    f"published report identity mismatch for {task.profile}:"
                    f"{task.track.artifact_slug}: {key}={report.get(key)!r}, "
                    f"expected {expected!r}"
                )
        if report.get("sources") != _source_record(task):
            raise DirectAV1RenderError(
                f"published source chain is stale for {task.profile}:"
                f"{task.track.artifact_slug}"
            )
        ass_gate = _validate_ass_file(task.ass_output, task.profile)
        if not ass_gate.get("ok"):
            raise DirectAV1RenderError(
                f"published ASS validation failed for {task.profile}:"
                f"{task.track.artifact_slug}: {ass_gate}"
            )
        media = verify_hevc444_output(task.video_output, ffmpeg=ffmpeg)
        if not media.get("ok"):
            raise DirectAV1RenderError(
                f"published HEVC 4:4:4 validation failed for {task.profile}:"
                f"{task.track.artifact_slug}: {media}"
            )
        output_sha256 = sha256_file(task.video_output)
        recorded_sha256 = report.get("output_sha256")
        if recorded_sha256 is not None and recorded_sha256 != output_sha256:
            raise DirectAV1RenderError(
                f"published HEVC 4:4:4 hash differs from its render report: "
                f"{task.video_output}"
            )
        results.append(
            {
                "status": "ok",
                "profile": task.profile,
                "song_id": str(task.track.song_id),
                "title": task.track.title,
                "artifact_slug": task.track.artifact_slug,
                "render_mode": "direct-hevc444",
                "video": str(task.video_output),
                "report": str(task.direct_report),
                "output_size_bytes": task.video_output.stat().st_size,
                "sha256": output_sha256,
                "elapsed_seconds": report.get("render_elapsed_seconds"),
                "sources": _source_record(task),
                "media": media,
            }
        )
    return results


def write_json_atomically(path: Path, value: dict[str, Any]) -> None:
    temporary = _temporary_path(path)
    try:
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _publish_atomically([(temporary, path)])
    finally:
        _remove_if_present(temporary)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="album.json manifest; the default owns the five-track album",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="deliverable root; defaults to the manifest's deliverable directory",
    )
    parser.add_argument(
        "--song",
        "--songs",
        dest="songs",
        action="append",
        default=[],
        metavar="ID|TITLE|SLUG",
        help="select a song; repeat or comma-separate selectors (default: all five)",
    )
    parser.add_argument(
        "--profile",
        "--profiles",
        dest="profiles",
        action="append",
        choices=PROFILES,
        default=[],
        help="select standard or wide; repeat for both (default: both)",
    )
    parser.add_argument(
        "--ass-only",
        action="store_true",
        help="generate and publish profile ASS plus ass_report without rendering video",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help=(
            "revalidate all 10 published direct HEVC 4:4:4 renders and rebuild "
            "hevc444_report.json"
        ),
    )
    parser.add_argument("--timing-dir", type=Path, default=None)
    parser.add_argument("--artwork-dir", type=Path, default=None)
    parser.add_argument("--fonts-dir", type=Path, default=None)
    parser.add_argument("--font-file", type=Path, default=None)
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="override manifest duration in seconds for every selected task",
    )
    parser.add_argument(
        "--preview-script",
        type=Path,
        default=PREVIEW_SCRIPT,
        help="karaoke_review_preview.py entry point",
    )
    parser.add_argument("--ffmpeg", type=Path, default=None)
    return parser


def _serialisable_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key not in {"media"}}


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        if args.duration is not None and args.duration <= 0:
            raise ValueError("--duration must be positive")
        if args.ass_only and args.report_only:
            raise ValueError("--ass-only and --report-only are mutually exclusive")
        album = load_album_manifest(_resolve_path(args.manifest))
        root = (
            _resolve_path(args.root) if args.root else album.deliverable_dir.resolve()
        )
        tracks = select_tracks(album, args.songs)
        profiles = select_profiles(args.profiles)
        timing_dir = _resolve_path(args.timing_dir) if args.timing_dir else None
        artwork_dir = _resolve_path(args.artwork_dir) if args.artwork_dir else None
        fonts_dir = _resolve_path(args.fonts_dir) if args.fonts_dir else None
        font_file = _resolve_path(args.font_file) if args.font_file else None
        preview_script = _resolve_path(args.preview_script)
        if not args.report_only and not preview_script.is_file():
            raise FileNotFoundError(f"preview script does not exist: {preview_script}")
        ffmpeg = _resolve_path(args.ffmpeg) if args.ffmpeg else None
        if ffmpeg is not None and not ffmpeg.is_file():
            raise FileNotFoundError(f"ffmpeg executable does not exist: {ffmpeg}")

        tasks = plan_tasks(
            album,
            root=root,
            tracks=tracks,
            profiles=profiles,
            timing_dir=timing_dir,
            artwork_root=artwork_dir,
            fonts_dir=fonts_dir,
            font_file=font_file,
            duration_seconds=args.duration,
        )
        results: list[dict[str, Any]] = []
        if args.report_only:
            if len(tracks) != len(album.tracks) or set(profiles) != set(PROFILES):
                raise ValueError(
                    "--report-only requires all five songs and both profiles"
                )
            results = collect_existing_results(tasks, ffmpeg=ffmpeg)
        else:
            for task in tasks:
                result = render_one(
                    task,
                    ass_only=args.ass_only,
                    preview_script=preview_script,
                    ffmpeg=ffmpeg,
                )
                # Keep the source map for the aggregate report without exposing
                # implementation objects to the JSON summary.
                result["sources"] = _source_record(task)
                results.append(result)
                print(
                    json.dumps(_serialisable_summary(result), ensure_ascii=False),
                    flush=True,
                )

        aggregate_report: Path | None = None
        complete_selection = (
            not args.ass_only
            and len(tracks) == len(album.tracks)
            and set(profiles) == set(PROFILES)
        )
        if complete_selection:
            aggregate_report = root / "validation" / "hevc444_report.json"
            write_json_atomically(
                aggregate_report,
                build_hevc444_report(results, root=root),
            )
        summary = {
            "status": "ass-ready" if args.ass_only else "pass",
            "task_count": len(results),
            "profiles": list(profiles),
            "songs": [str(track.song_id) for track in tracks],
            "aggregate_report": str(aggregate_report) if aggregate_report else None,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
