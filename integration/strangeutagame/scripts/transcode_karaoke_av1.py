#!/usr/bin/env python3
"""Create compact AV1 delivery copies of the five karaoke masters."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import imageio_ffmpeg

try:
    from .karaoke_album import DEFAULT_MANIFEST_PATH, load_album_manifest
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_album import (  # type: ignore[no-redef]
        DEFAULT_MANIFEST_PATH,
        load_album_manifest,
    )

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO_ROOT / "deliverables" / "karaoke"
PROFILES = ("standard", "wide")
# Stable release profile; change only through an explicit profile migration.
DEFAULT_AV1_CQ = 44
DEFAULT_AV1_PRESET = "p7"


def encoder_command(
    ffmpeg: Path,
    source: Path,
    output: Path,
    *,
    cq: int,
) -> list[str]:
    return [
        str(ffmpeg),
        "-hide_banner",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-map_metadata",
        "0",
        "-c:v",
        "av1_nvenc",
        "-preset",
        DEFAULT_AV1_PRESET,
        "-tune",
        "hq",
        "-rc",
        "vbr",
        "-cq",
        str(cq),
        "-b:v",
        "0",
        "-multipass",
        "fullres",
        "-rc-lookahead",
        "32",
        "-spatial-aq",
        "1",
        "-temporal-aq",
        "1",
        "-aq-strength",
        "8",
        "-g",
        "240",
        "-pix_fmt",
        "yuv420p",
        "-tag:v",
        "av01",
        "-c:a",
        "copy",
        "-fps_mode",
        "passthrough",
        "-movflags",
        "+faststart",
        str(output),
    ]


def transcode_one(
    ffmpeg: Path,
    source: Path,
    output: Path,
    *,
    cq: int,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    partial.unlink(missing_ok=True)
    started = time.perf_counter()
    completed = subprocess.run(
        encoder_command(ffmpeg, source, partial, cq=cq),
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"AV1 encode failed for {source.name}:\n{completed.stderr[-3000:]}"
        )
    partial.replace(output)
    source_size = source.stat().st_size
    output_size = output.stat().st_size
    return {
        "source": source,
        "output": output,
        "source_size_bytes": source_size,
        "output_size_bytes": output_size,
        "size_ratio": round(output_size / source_size, 6),
        "reduction_percent": round((1 - output_size / source_size) * 100, 2),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument(
        "--cq",
        type=int,
        default=DEFAULT_AV1_CQ,
        help=(
            "NVENC constant-quality target; higher is smaller "
            f"(default: {DEFAULT_AV1_CQ}; preset fixed to {DEFAULT_AV1_PRESET})"
        ),
    )
    parser.add_argument("--workers", type=int, default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if not 0 <= args.cq <= 63:
        raise ValueError("--cq must be between 0 and 63")
    if args.workers < 1:
        raise ValueError("--workers must be at least 1")

    album = load_album_manifest(args.manifest)
    root = (args.root or album.deliverable_dir).resolve()
    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    tasks = [
        (
            profile,
            track,
            root / "video" / profile / f"{track.artifact_slug}.mp4",
            root / "video" / "av1" / profile / f"{track.artifact_slug}.mp4",
        )
        for profile in PROFILES
        for track in album.tracks
    ]
    missing = [str(source) for _, _, source, _ in tasks if not source.is_file()]
    if missing:
        raise FileNotFoundError(f"missing karaoke masters: {missing}")

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(args.workers, len(tasks))) as executor:
        futures = {
            executor.submit(
                transcode_one,
                ffmpeg,
                source,
                output,
                cq=args.cq,
            ): (profile, track)
            for profile, track, source, output in tasks
        }
        for future in as_completed(futures):
            profile, track = futures[future]
            result = future.result()
            result.update(
                {
                    "profile": profile,
                    "song_id": track.song_id,
                    "title": track.title,
                    "artifact_slug": track.artifact_slug,
                }
            )
            results.append(result)
            print(
                json.dumps(
                    {
                        "profile": profile,
                        "title": track.title,
                        "artifact_slug": track.artifact_slug,
                        "output_size_bytes": result["output_size_bytes"],
                        "reduction_percent": result["reduction_percent"],
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )

    outputs = []
    for result in sorted(
        results, key=lambda item: (item["profile"], item["artifact_slug"])
    ):
        outputs.append(
            {
                **{
                    key: value
                    for key, value in result.items()
                    if key not in {"source", "output"}
                },
                "source": result["source"].relative_to(root).as_posix(),
                "output": result["output"].relative_to(root).as_posix(),
            }
        )
    report = {
        "schema_version": "karaoke-av1/v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "encoder": "av1_nvenc",
        "container": "mp4",
        "codec_tag": "av01",
        "pixel_format": "yuv420p",
        "audio": "copy",
        "settings": {
            "cq": args.cq,
            "preset": DEFAULT_AV1_PRESET,
            "tune": "hq",
            "multipass": "fullres",
            "lookahead": 32,
            "spatial_aq": True,
            "temporal_aq": True,
            "gop_frames": 240,
        },
        "outputs": outputs,
    }
    report_path = root / "validation" / "av1_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "pass", "report": str(report_path)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
