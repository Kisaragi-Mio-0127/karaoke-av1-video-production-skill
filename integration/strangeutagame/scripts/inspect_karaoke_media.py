#!/usr/bin/env python3
"""Inspect karaoke media and write a machine-readable validation report.

This inspector is intentionally read-only with respect to audio, timing and
source files.  It writes only the requested deliverable validation JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from karaoke_album import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    load_album_manifest,
    project_relative,
)
from render_vinyl_karaoke import (  # noqa: E402
    DEFAULT_ARTIST,
    DEFAULT_AUDIO,
    DEFAULT_SLUG,
    DEFAULT_TITLE,
    REPO_ROOT,
    ass_candidates,
    audio_duration,
    default_ffmpeg,
    embedded_cover,
    find_ass,
    inspect_font_dir,
    probe_ffmpeg_capabilities,
    probe_libass_font,
    run_capture,
    timing_directories,
    track_dict,
    validate_ass_for_render,
)


def parse_duration(text: str) -> float | None:
    match = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", text)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _stream_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if "Stream #" in line and ("Video:" in line or "Audio:" in line)
    ]


def parse_video_stream(line: str) -> dict[str, Any]:
    payload = line.split("Video:", 1)[1]
    codec = payload.split(",", 1)[0].strip().split(" ", 1)[0]
    pixel_match = re.search(
        r"\b([a-zA-Z][a-zA-Z0-9_]+)(?:\([^)]*\))?,\s*(\d{3,5})x(\d{3,5})", payload
    )
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s+fps\b", payload)
    return {
        "raw": line,
        "codec": codec,
        "codec_tag": (
            tag_match.group(1).lower()
            if (tag_match := re.search(r"\(\s*([a-z0-9]+)\s*/", payload, re.IGNORECASE))
            else None
        ),
        "profile": (
            profile_match.group(1)
            if (profile_match := re.search(r"\(\s*([^()]+?)\s*\)", payload))
            else None
        ),
        "pixel_format": pixel_match.group(1) if pixel_match else None,
        "color_range": (
            range_match.group(1).lower()
            if (
                range_match := re.search(
                    r"\b[a-z0-9_]+\(\s*(pc|tv)\b", payload, re.IGNORECASE
                )
            )
            else None
        ),
        "width": int(pixel_match.group(2)) if pixel_match else None,
        "height": int(pixel_match.group(3)) if pixel_match else None,
        "fps": float(fps_match.group(1)) if fps_match else None,
    }


def parse_audio_stream(line: str) -> dict[str, Any]:
    payload = line.split("Audio:", 1)[1]
    codec = payload.split(",", 1)[0].strip().split(" ", 1)[0]
    sample_rate_match = re.search(r"(\d{4,6})\s*Hz", payload)
    channel_match = re.search(
        r"\b(mono|stereo|\d+(?:\.\d+)?\s*channels?)\b", payload, re.IGNORECASE
    )
    bitrate_match = re.search(r"(\d+(?:\.\d+)?)\s*kb/s", payload)
    return {
        "raw": line,
        "codec": codec,
        "sample_rate_hz": int(sample_rate_match.group(1))
        if sample_rate_match
        else None,
        "channels": channel_match.group(1) if channel_match else None,
        "bitrate_kbps": float(bitrate_match.group(1)) if bitrate_match else None,
    }


def probe_media(
    ffmpeg: Path,
    path: Path,
    project_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": project_relative(path, project_root),
            "exists": False,
            "duration_seconds": None,
            "video_stream": None,
            "audio_stream": None,
        }
    result = run_capture(ffmpeg, ["-hide_banner", "-i", str(path)])
    text = result.stdout + "\n" + result.stderr
    video_line = next((line for line in _stream_lines(text) if "Video:" in line), None)
    audio_line = next((line for line in _stream_lines(text) if "Audio:" in line), None)
    return {
        "path": project_relative(path, project_root),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "probe_returncode": result.returncode,
        "duration_seconds": parse_duration(text),
        "video_stream": parse_video_stream(video_line) if video_line else None,
        "audio_stream": parse_audio_stream(audio_line) if audio_line else None,
        "probe_stderr_tail": result.stderr[-1200:],
    }


def hash_artifact(path: Path, project_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Record a deterministic hash for a generated visual review artifact."""

    if not path.exists():
        return {
            "path": project_relative(path, project_root),
            "exists": False,
            "size_bytes": None,
            "sha256": None,
        }
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": project_relative(path, project_root),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "sha256": digest.hexdigest().upper(),
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    track_group = parser.add_mutually_exclusive_group()
    track_group.add_argument(
        "--all-tracks",
        dest="all_tracks",
        action="store_true",
        help="inspect the complete manifest track collection (default)",
    )
    track_group.add_argument(
        "--single-track",
        dest="all_tracks",
        action="store_false",
        help="inspect one explicitly selected track",
    )
    parser.set_defaults(all_tracks=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--allow-partial-manifest",
        action="store_true",
        help="allow an explicitly supplied manifest with fewer than five tracks",
    )
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--video", type=Path)
    parser.add_argument(
        "--video-dir",
        type=Path,
        help="directory containing <track>.mp4 files for --all-tracks",
    )
    parser.add_argument("--ass", type=Path)
    parser.add_argument("--timing-dir", type=Path)
    parser.add_argument("--slug", default=DEFAULT_SLUG)
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--artist", default=DEFAULT_ARTIST)
    parser.add_argument(
        "--fonts-dir",
        type=Path,
        help="HarmonyOS Sans directory; defaults to deliverables/<slug>/artwork/fonts",
    )
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--duration-tolerance", type=float, default=0.10)
    return parser


def inspect_track(
    *,
    args: argparse.Namespace,
    track: dict[str, Any],
    ffmpeg: Path,
    all_tracks: bool,
    font_info: dict[str, Any],
    libass_font_probe: dict[str, Any],
) -> dict[str, Any]:
    audio_path = Path(track["audio"]).expanduser().resolve()
    if all_tracks:
        video_dir = (args.video_dir or args.deliverable_root / "video").resolve()
        video_path = video_dir / f"{track['basename']}.mp4"
        timing_dir = args.timing_dir.resolve() if args.timing_dir else None
        timing_stem = track.get("timing_stem")
        ass_path = (
            timing_dir / f"{timing_stem}.ass"
            if timing_dir is not None and timing_stem
            else find_ass(
                REPO_ROOT, args.slug, audio_path, track["title"], args.timing_dir
            )
        )
    else:
        video_path = (
            args.video or args.deliverable_root / "video" / f"{track['basename']}.mp4"
        ).resolve()
        ass_path = (
            args.ass.resolve()
            if args.ass
            else find_ass(
                REPO_ROOT, args.slug, audio_path, track["title"], args.timing_dir
            )
        )
    ass_options = ass_candidates(
        REPO_ROOT, args.slug, audio_path, track["title"], args.timing_dir
    )
    if ass_path is not None:
        ass_options = [
            ass_path,
            *[candidate for candidate in ass_options if candidate != ass_path],
        ]
    ass_gate = (
        validate_ass_for_render(ass_path, font_info["family"])
        if ass_path and ass_path.exists() and font_info.get("family")
        else {
            "ok": False,
            "path": str(ass_path) if ass_path else None,
            "errors": ["ASS missing or HarmonyOS Sans family unavailable"],
        }
    )
    track_libass_font_probe = libass_font_probe
    if ass_path and ass_path.exists() and font_info.get("family"):
        track_libass_font_probe = probe_libass_font(
            ffmpeg,
            Path(font_info["directory"]),
            str(font_info["family"]),
            str(libass_font_probe.get("filter") or "subtitles"),
            ass_path=ass_path,
        )

    source_audio: dict[str, Any]
    try:
        source_audio = {
            "path": str(audio_path),
            "exists": audio_path.exists(),
            "duration_seconds": audio_duration(audio_path)
            if audio_path.exists()
            else None,
        }
        cover_bytes, cover_info = (
            embedded_cover(audio_path)
            if audio_path.exists()
            else (None, {"present": False})
        )
        source_audio["embedded_cover"] = {
            **cover_info,
            "bytes": len(cover_bytes) if cover_bytes else cover_info.get("bytes"),
        }
    except Exception as exc:
        source_audio = {
            "path": str(audio_path),
            "exists": audio_path.exists(),
            "error": str(exc),
            "duration_seconds": None,
        }

    video = probe_media(ffmpeg, video_path, args.project_root)
    artwork_root = (args.deliverable_root / "artwork").resolve()
    artwork_dir = artwork_root / track["basename"] if all_tracks else artwork_root
    frame_probe = hash_artifact(artwork_dir / "frame_probe.png", args.project_root)
    audio_expected = source_audio.get("duration_seconds")
    video_duration = video.get("duration_seconds")
    duration_difference = (
        (video_duration - audio_expected)
        if video_duration is not None and audio_expected is not None
        else None
    )
    video_stream = video.get("video_stream") or {}
    output_audio_stream = video.get("audio_stream")
    checks = {
        "harmonyos_sans_loaded": bool(track_libass_font_probe.get("ok")),
        "real_lyrics_libass_font": bool(
            track_libass_font_probe.get("ok")
            and track_libass_font_probe.get("probe_kind") == "real_lyrics"
        ),
        "ass_available": ass_path is not None and ass_path.exists(),
        "burn_ready_ass": bool(ass_gate.get("ok")),
        "video_exists": bool(video.get("exists")),
        "has_audio": output_audio_stream is not None,
        "codec_hevc": str(video_stream.get("codec", "")).lower() == "hevc",
        "codec_tag_hvc1": str(video_stream.get("codec_tag", "")).lower() == "hvc1",
        "profile_rext": str(video_stream.get("profile", "")).lower() == "rext",
        "resolution_1920x1080": video_stream.get("width") == 1920
        and video_stream.get("height") == 1080,
        "pixel_format_yuv444p": video_stream.get("pixel_format") == "yuv444p",
        "yuv_full_range": bool(
            re.search(
                r"\byuv444p\(\s*pc(?:[,)]|\s)",
                video_stream.get("raw", ""),
                re.IGNORECASE,
            )
        ),
        "cfr_30fps": video_stream.get("fps") is not None
        and abs(float(video_stream["fps"]) - 30.0) < 0.01,
        "aac_audio": str((output_audio_stream or {}).get("codec", "")).lower() == "aac",
        "duration_within_tolerance": duration_difference is not None
        and abs(duration_difference) <= args.duration_tolerance,
    }
    all_media_checks = all(
        checks[name]
        for name in (
            "video_exists",
            "has_audio",
            "codec_hevc",
            "codec_tag_hvc1",
            "profile_rext",
            "resolution_1920x1080",
            "pixel_format_yuv444p",
            "yuv_full_range",
            "cfr_30fps",
            "aac_audio",
            "duration_within_tolerance",
            "real_lyrics_libass_font",
        )
    )
    if not checks["ass_available"]:
        status = "waiting_for_ass"
    elif not checks["burn_ready_ass"]:
        status = "waiting_for_burn_ready_ass"
    elif not checks["video_exists"]:
        status = "waiting_for_video"
    else:
        status = "pass" if all_media_checks else "fail"

    report = {
        "track": {"title": track["title"], "artist": track["artist"]},
        "audio_path": project_relative(audio_path, args.project_root),
        "status": status,
        "font": {
            "family": font_info["family"],
            "directory": font_info["directory"],
            "regular": font_info["regular"],
            "bold": font_info["bold"],
        },
        "libass_font_probe": track_libass_font_probe,
        "source_audio": source_audio,
        "ass": {
            "path": project_relative(ass_path, args.project_root) if ass_path else None,
            "exists": bool(ass_path and ass_path.exists()),
            "searched_directories": [
                project_relative(path, args.project_root)
                for path in timing_directories(REPO_ROOT, args.slug, args.timing_dir)
            ],
            "candidate_paths": [
                project_relative(path, args.project_root) for path in ass_options
            ],
            "ownership": "由渲染流程生成；本脚本仅读取",
            "burn_ready_gate": ass_gate,
        },
        "video": video,
        "frame_probe": frame_probe,
        "stream_parameters": {
            "expected": {
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "frame_rate_mode": "CFR",
                "pixel_format": "yuv444p (full-range YUV 4:4:4)",
                "color_range": "pc",
                "video_family": "HEVC Rext via hevc_nvenc",
                "codec_tag": "hvc1",
                "audio_family": "AAC",
            },
            "actual_video": video_stream,
            "actual_audio": output_audio_stream,
        },
        "duration": {
            "audio_seconds": audio_expected,
            "video_seconds": video_duration,
            "difference_seconds": duration_difference,
            "tolerance_seconds": args.duration_tolerance,
        },
        "checks": checks,
        "commands": {
            "inspect": "python scripts/inspect_karaoke_media.py",
            "render": (
                "python scripts/render_vinyl_karaoke.py --single-track --audio "
                f'"{project_relative(audio_path, args.project_root)}" '
                f'--title "{track["title"]}" --artist "{track["artist"]}" --force'
            ),
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    album = load_album_manifest(
        args.manifest,
        require_five_tracks=not args.allow_partial_manifest,
    )
    if args.all_tracks and (args.ass or args.video):
        print(
            "ERROR: --ass/--video are single-track options; use --all-tracks discovery",
            file=sys.stderr,
        )
        return 1
    deliverable_root = (
        album.deliverable_dir
        if args.slug == DEFAULT_SLUG
        else (REPO_ROOT / "deliverables" / args.slug).resolve()
    )
    args.deliverable_root = deliverable_root
    args.project_root = album.project_root
    args.timing_dir = (args.timing_dir or album.deliverable_dir / "timing").resolve()
    report_path = (
        args.report or deliverable_root / "validation" / "media_report.json"
    ).resolve()
    ffmpeg = (args.ffmpeg or default_ffmpeg()).resolve()
    capabilities = probe_ffmpeg_capabilities(ffmpeg)
    fonts_dir = (args.fonts_dir or deliverable_root / "artwork" / "fonts").resolve()
    try:
        font_info = inspect_font_dir(fonts_dir)
        libass_font_probe = probe_libass_font(
            ffmpeg,
            fonts_dir,
            font_info["family"],
            capabilities.get("subtitle_filter_selected") or "subtitles",
        )
    except Exception as exc:
        font_info = {
            "directory": str(fonts_dir),
            "family": None,
            "regular": None,
            "bold": None,
            "files": [],
        }
        libass_font_probe = {
            "ok": False,
            "directory": str(fonts_dir),
            "reason": str(exc),
        }
    if args.all_tracks:
        track_specs = [track_dict(track) for track in album.tracks]
    else:
        track_specs = [
            {
                "audio": args.audio,
                "title": args.title,
                "artist": args.artist,
                "basename": args.title,
            }
        ]
    reports = [
        inspect_track(
            args=args,
            track=track,
            ffmpeg=ffmpeg,
            all_tracks=args.all_tracks,
            font_info=font_info,
            libass_font_probe=libass_font_probe,
        )
        for track in track_specs
    ]
    statuses = [report["status"] for report in reports]
    if not libass_font_probe.get("ok"):
        status = "fail"
    elif "waiting_for_ass" in statuses:
        status = "waiting_for_ass"
    elif "waiting_for_burn_ready_ass" in statuses:
        status = "waiting_for_burn_ready_ass"
    elif "waiting_for_video" in statuses:
        status = "waiting_for_video"
    elif all(item == "pass" for item in statuses):
        status = "pass"
    else:
        status = "fail"
    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "track_count": len(reports),
        "ffmpeg": capabilities,
        "font": font_info,
        "libass_font_probe": libass_font_probe,
        "tracks": reports,
    }
    if len(reports) == 1:
        # Keep the single-track report convenient for shell users while the
        # tracks list gives the album-level report one stable shape.
        report.update(reports[0])
        report["status"] = status
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": status,
                "report": str(report_path),
                "checks": [item["checks"] for item in reports]
                if args.all_tracks
                else reports[0]["checks"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
