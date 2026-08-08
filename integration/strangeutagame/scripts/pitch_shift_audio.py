"""Pitch-shift one complete audio mix with Rubber Band R3 Finer.

The pipeline deliberately uses float WAV intermediates, keeps tempo unchanged,
and publishes the audio plus its verification report as one rollback-safe pair.
It never separates vocals and never derives a supposed lossless result from a
lossy intermediate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .karaoke_common.ffmpeg_tools import resolve_ffmpeg, resolve_ffprobe
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_common.ffmpeg_tools import (  # type: ignore[no-redef]
        resolve_ffmpeg,
        resolve_ffprobe,
    )

ROOT = Path(__file__).resolve().parents[1]

SAFE_METADATA_ALIASES = {
    "title": ("title",),
    "artist": ("artist",),
    "album": ("album",),
    "album_artist": ("album_artist", "albumartist", "album artist"),
    "date": ("date", "year"),
    "track": ("track", "tracknumber", "track_number"),
    "disc": ("disc", "discnumber", "disc_number"),
    "comment": ("comment", "comments"),
    "genre": ("genre",),
    "composer": ("composer",),
    "copyright": ("copyright",),
}
ID3_TEXT_FRAMES = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "album_artist": "TPE2",
    "date": "TDRC",
    "track": "TRCK",
    "disc": "TPOS",
    "genre": "TCON",
    "composer": "TCOM",
    "copyright": "TCOP",
}


def executable(explicit: Path | None, env_name: str, command: str) -> Path:
    """Resolve the requested member of the project FFmpeg tool pair."""

    try:
        if command == "ffmpeg":
            return resolve_ffmpeg(explicit, root=ROOT)
        if command == "ffprobe":
            return resolve_ffprobe(explicit, root=ROOT)
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    raise SystemExit(f"Unsupported executable: {command} ({env_name})")


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def checked(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    result = run(command, capture=capture)
    if result.returncode:
        detail = (result.stdout or "").strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {command[0]}\n{detail}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def version(path: Path, *arguments: str) -> str:
    result = checked([str(path), *arguments], capture=True)
    return (result.stdout or "").splitlines()[0].strip()


def probe(ffprobe: Path, path: Path) -> dict[str, Any]:
    result = checked(
        [
            str(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_fmt,sample_rate,channels,duration,duration_ts,time_base",
            "-of",
            "json",
            str(path),
        ],
        capture=True,
    )
    streams = json.loads(result.stdout or "{}").get("streams") or []
    if len(streams) != 1:
        raise RuntimeError(f"Expected exactly one selected audio stream in {path}")
    return streams[0]


def probe_container(ffprobe: Path, path: Path) -> dict[str, Any]:
    """Return container tags and stream dispositions used for safe inheritance."""

    result = checked(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format_tags:stream=index,codec_type,codec_name:stream_disposition=attached_pic",
            "-of",
            "json",
            str(path),
        ],
        capture=True,
    )
    return json.loads(result.stdout or "{}")


def inherited_metadata(container_probe: dict[str, Any]) -> dict[str, str]:
    """Select descriptive tags without copying encoder or technical metadata."""

    raw_tags = (container_probe.get("format") or {}).get("tags") or {}
    tags = {str(key).casefold(): str(value) for key, value in raw_tags.items()}
    inherited: dict[str, str] = {}
    for output_key, aliases in SAFE_METADATA_ALIASES.items():
        value = next((tags[alias] for alias in aliases if tags.get(alias)), None)
        if value is not None:
            inherited[output_key] = value
    return inherited


def attached_picture_indices(container_probe: dict[str, Any]) -> list[int]:
    """Select only explicitly attached pictures, never source audio/video content."""

    return [
        int(stream["index"])
        for stream in container_probe.get("streams") or []
        if stream.get("codec_type") == "video"
        and (stream.get("disposition") or {}).get("attached_pic") == 1
    ]


def build_encode_command(
    ffmpeg: Path,
    shifted_audio: Path,
    source: Path,
    candidate: Path,
    target: Path,
    post_gain: float,
    source_container_probe: dict[str, Any],
) -> tuple[list[str], dict[str, str], list[int]]:
    """Build an encode that maps shifted audio plus safe descriptive metadata."""

    metadata = inherited_metadata(source_container_probe)
    pictures = (
        attached_picture_indices(source_container_probe)
        if target.suffix.lower() == ".flac"
        else []
    )
    command = [
        str(ffmpeg), "-nostdin", "-y", "-v", "warning", "-i", str(shifted_audio),
    ]
    if pictures:
        command += ["-i", str(source)]
    command += ["-map", "0:a:0"]
    for picture_index in pictures:
        command += ["-map", f"1:{picture_index}"]
    command += ["-map_metadata", "-1"]
    for key, value in metadata.items():
        command += ["-metadata", f"{key}={value}"]
    command += ["-af", f"volume={post_gain:.6f}dB", *output_codec(target)]
    if pictures:
        command += ["-c:v", "copy"]
        for output_index in range(len(pictures)):
            command += [f"-disposition:v:{output_index}", "attached_pic"]
    command.append(str(candidate))
    return command, metadata, pictures


def _syncsafe(value: int) -> bytes:
    if not 0 <= value < (1 << 28):
        raise RuntimeError("ID3 metadata is too large")
    return bytes(
        ((value >> 21) & 0x7F, (value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F)
    )


def write_wav_id3_metadata(path: Path, metadata: dict[str, str]) -> None:
    """Append an ID3v2.4 RIFF chunk for WAV tags absent from LIST/INFO."""

    if not metadata:
        return
    with path.open("rb") as stream:
        if stream.read(4) != b"RIFF" or stream.read(4) == b"":
            raise RuntimeError(f"Cannot add WAV metadata to a non-RIFF file: {path}")
        if stream.read(4) != b"WAVE":
            raise RuntimeError(f"Cannot add WAV metadata to a non-WAVE file: {path}")
    frames: list[bytes] = []
    for key, frame_id in ID3_TEXT_FRAMES.items():
        if value := metadata.get(key):
            payload = b"\x03" + value.encode("utf-8")
            frames.append(frame_id.encode("ascii") + _syncsafe(len(payload)) + b"\0\0" + payload)
    if value := metadata.get("comment"):
        payload = b"\x03eng\0" + value.encode("utf-8")
        frames.append(b"COMM" + _syncsafe(len(payload)) + b"\0\0" + payload)
    if not frames:
        return
    body = b"".join(frames)
    tag = b"ID3\x04\0\0" + _syncsafe(len(body)) + body
    chunk = b"id3 " + len(tag).to_bytes(4, "little") + tag
    if len(tag) % 2:
        chunk += b"\0"
    with path.open("ab") as stream:
        stream.write(chunk)
    riff_size = path.stat().st_size - 8
    if riff_size > 0xFFFFFFFF:
        raise RuntimeError("WAV metadata would exceed the RIFF size limit")
    with path.open("r+b") as stream:
        stream.seek(4)
        stream.write(riff_size.to_bytes(4, "little"))


def max_volume_dbfs(ffmpeg: Path, path: Path) -> float:
    result = checked(
        [
            str(ffmpeg),
            "-v",
            "info",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "NUL" if os.name == "nt" else "/dev/null",
        ],
        capture=True,
    )
    matches = re.findall(r"max_volume:\s*(-?(?:inf|\d+(?:\.\d+)?))\s*dB", result.stdout or "", re.I)
    if not matches or matches[-1].lower() == "-inf":
        raise RuntimeError(f"Could not measure a finite peak in {path}")
    return float(matches[-1])


def scan_finite_pcm(ffmpeg: Path, path: Path) -> tuple[float, int]:
    """Decode to float32 and return exact peak dBFS and sample-value count."""

    process = subprocess.Popen(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-f",
            "f32le",
            "-c:a",
            "pcm_f32le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    peak = 0.0
    sample_values = 0
    remainder = b""
    while chunk := process.stdout.read(1024 * 1024):
        chunk = remainder + chunk
        aligned = len(chunk) - (len(chunk) % 4)
        remainder = chunk[aligned:]
        values = array("f")
        values.frombytes(chunk[:aligned])
        if sys.byteorder != "little":
            values.byteswap()
        for value in values:
            if not math.isfinite(value):
                process.kill()
                process.wait()
                raise RuntimeError(f"Non-finite PCM sample detected in {path}")
            peak = max(peak, abs(value))
        sample_values += len(values)
    error_text = process.stderr.read().decode("utf-8", errors="replace")
    returncode = process.wait()
    if returncode:
        raise RuntimeError(f"PCM validation decode failed ({returncode}): {error_text.strip()}")
    if remainder:
        raise RuntimeError("PCM validation produced a partial float32 sample")
    if sample_values == 0 or peak == 0.0:
        raise RuntimeError(f"Decoded audio is empty or silent: {path}")
    return 20.0 * math.log10(peak), sample_values


def duration_seconds(metadata: dict[str, Any]) -> float:
    return float(metadata.get("duration") or 0.0)


def output_codec(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".flac":
        return ["-c:a", "flac", "-sample_fmt", "s16", "-compression_level", "12"]
    if suffix == ".wav":
        return ["-c:a", "pcm_s24le"]
    raise SystemExit("Output must use .flac or .wav")


def is_formal_lossless_source(metadata: dict[str, Any]) -> bool:
    """Accept only probed FLAC or PCM audio for the lossless pitch lane."""

    codec = str(metadata.get("codec_name") or "").casefold()
    return codec == "flac" or codec.startswith("pcm_")


def promote_transaction(
    candidate: Path,
    temporary_report: Path,
    target: Path,
    report_path: Path,
    target_rollback: Path | None,
    report_rollback: Path | None,
) -> None:
    """Promote audio and report together, restoring only completed steps."""

    target_backed_up = False
    report_backed_up = False
    target_promoted = False
    report_promoted = False
    try:
        if target_rollback:
            os.replace(target, target_rollback)
            target_backed_up = True
        if report_rollback:
            os.replace(report_path, report_rollback)
            report_backed_up = True
        os.replace(candidate, target)
        target_promoted = True
        os.replace(temporary_report, report_path)
        report_promoted = True
    except Exception:
        if report_promoted and report_path.exists():
            report_path.unlink()
        if target_promoted and target.exists():
            target.unlink()
        if report_backed_up and report_rollback and report_rollback.exists():
            os.replace(report_rollback, report_path)
        if target_backed_up and target_rollback and target_rollback.exists():
            os.replace(target_rollback, target)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="R3 Finer pitch shift with optional formant preservation")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--semitones", type=float, required=True)
    parser.add_argument("--target-peak-dbfs", type=float, default=-1.0)
    parser.add_argument("--initial-headroom-db", type=float, default=3.0)
    parser.add_argument("--max-headroom-attempts", type=int, default=4)
    parser.add_argument("--no-formant", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--ffprobe", type=Path)
    parser.add_argument("--rubberband", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.input.resolve()
    target = args.output.resolve()
    if not source.is_file():
        raise SystemExit(f"Input does not exist: {source}")
    if source == target:
        raise SystemExit("Input and output paths must differ")
    if target.exists() and not target.is_file():
        raise SystemExit(f"Output path exists but is not a file: {target}")
    if target.exists() and not args.force:
        raise SystemExit(f"Output exists; pass --force to replace it: {target}")
    if args.initial_headroom_db < 0 or args.max_headroom_attempts < 1:
        raise SystemExit("Headroom must be non-negative and attempts must be positive")

    ffmpeg = executable(args.ffmpeg, "FFMPEG", "ffmpeg")
    ffprobe = executable(args.ffprobe, "FFPROBE", "ffprobe")
    rubberband = executable(args.rubberband, "RUBBERBAND", "rubberband")
    source_probe = probe(ffprobe, source)
    source_container_probe = probe_container(ffprobe, source)
    if not is_formal_lossless_source(source_probe):
        codec = str(source_probe.get("codec_name") or "unknown")
        raise SystemExit(
            "Formal pitch shifting requires a genuinely lossless FLAC or PCM "
            f"source; probed codec is {codec!r}"
        )
    rubberband_version = version(rubberband, "--version")
    match = re.search(r"(\d+)(?:\.\d+)*", rubberband_version)
    if not match or int(match.group(1)) < 3:
        raise SystemExit(f"Rubber Band 3.x or newer is required, got: {rubberband_version}")

    target.parent.mkdir(parents=True, exist_ok=True)
    report_path = (args.report or target.with_suffix(target.suffix + ".pitch-shift.json")).resolve()
    if report_path in {source, target}:
        raise SystemExit("Report path must differ from both input and output audio")
    if report_path.exists() and not report_path.is_file():
        raise SystemExit(f"Report path exists but is not a file: {report_path}")
    if report_path.exists() and not args.force:
        raise SystemExit(f"Report exists; pass --force to replace it: {report_path}")
    if report_path.anchor.casefold() != target.anchor.casefold():
        raise SystemExit("Report and output must be on the same filesystem volume")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.parent.stat().st_dev != target.parent.stat().st_dev:
        raise SystemExit("Report and output must be on the same filesystem volume")
    command_logs: list[dict[str, Any]] = []
    work = Path(tempfile.mkdtemp(prefix=".pitch-shift-", dir=target.parent))
    candidate = work / target.name
    try:
        # Keep every intermediate beside the destination. This allows final
        # promotion to use same-volume atomic replacement semantics.
        input_float = work / "input-f32.wav"
        shifted_float = work / "shifted-r3-f32.wav"
        used_headroom = float(args.initial_headroom_db)
        rubberband_output = ""
        # Rubber Band reports clipping without reducing gain itself. Retry the
        # source decode with additional headroom instead of clipping the R3 run.
        for attempt in range(1, args.max_headroom_attempts + 1):
            decode_command = [
                str(ffmpeg), "-nostdin", "-y", "-v", "warning", "-i", str(source),
                "-map", "0:a:0", "-vn", "-sn", "-dn", "-af", f"volume=-{used_headroom:g}dB",
                "-c:a", "pcm_f32le", str(input_float),
            ]
            checked(decode_command)
            rb_command = [str(rubberband), "-3"]
            if not args.no_formant:
                rb_command.append("-F")
            rb_command += ["-p", f"{args.semitones:g}", str(input_float), str(shifted_float)]
            rb_result = checked(rb_command, capture=True)
            rubberband_output = rb_result.stdout or ""
            command_logs.append(
                {
                    "attempt": attempt,
                    "input_headroom_db": used_headroom,
                    "rubberband_flags": rb_command[1:-2],
                    "reported_unreduced_clipping": "but not reducing gain" in rubberband_output,
                }
            )
            if "but not reducing gain" not in rubberband_output:
                break
            used_headroom += 3.0
        else:
            raise RuntimeError("Rubber Band still reported unreduced clipping after all attempts")

        # Measure the R3 output before final encoding, then apply only the gain
        # needed to meet the requested peak ceiling.
        shifted_peak, shifted_sample_values = scan_finite_pcm(ffmpeg, shifted_float)
        post_gain = float(args.target_peak_dbfs) - shifted_peak
        encode_command, metadata, inherited_pictures = build_encode_command(
            ffmpeg,
            shifted_float,
            source,
            candidate,
            target,
            post_gain,
            source_container_probe,
        )
        checked(encode_command)
        if target.suffix.lower() == ".wav":
            write_wav_id3_metadata(candidate, metadata)

        # Validate the encoded candidate before any accepted output is moved.
        output_probe = probe(ffprobe, candidate)
        output_container_probe = probe_container(ffprobe, candidate)
        actual_metadata = inherited_metadata(output_container_probe)
        missing_metadata = {
            key: value for key, value in metadata.items() if actual_metadata.get(key) != value
        }
        if missing_metadata:
            raise RuntimeError(f"Output did not preserve metadata: {missing_metadata}")
        output_pictures = attached_picture_indices(output_container_probe)
        if len(output_pictures) != len(inherited_pictures):
            raise RuntimeError("Output did not preserve all attached FLAC pictures")
        if source_probe.get("sample_rate") != output_probe.get("sample_rate"):
            raise RuntimeError("Sample rate changed")
        if source_probe.get("channels") != output_probe.get("channels"):
            raise RuntimeError("Channel count changed")
        tolerance = 1.0 / float(source_probe["sample_rate"])
        drift = abs(duration_seconds(source_probe) - duration_seconds(output_probe))
        if drift > tolerance + 1e-9:
            raise RuntimeError(f"Duration drift {drift:.9f}s exceeds one sample")
        final_peak, final_sample_values = scan_finite_pcm(ffmpeg, candidate)
        if final_peak > float(args.target_peak_dbfs) + 0.05:
            raise RuntimeError(f"Output peak {final_peak:.2f} dBFS exceeds requested ceiling")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target_rollback = (
            target.with_name(f".{target.name}.{stamp}.rollback")
            if target.exists()
            else None
        )
        report_rollback = (
            report_path.with_name(f".{report_path.name}.{stamp}.rollback")
            if report_path.exists()
            else None
        )
        report = {
            "schema_version": "r3-formant-pitch-shift/v1",
            "source": {
                "name": source.name,
                "sha256": sha256(source),
                "probe": source_probe,
                "formal_lossless": True,
            },
            "output": {
                "name": target.name,
                "sha256": sha256(candidate),
                "probe": output_probe,
                "peak_dbfs": final_peak,
                "decoded_sample_values": final_sample_values,
                "nonfinite_sample_values": 0,
                "inherited_metadata": metadata,
                "inherited_attached_picture_streams": inherited_pictures,
                "output_attached_picture_streams": output_pictures,
            },
            "transform": {
                "semitones": args.semitones,
                "frequency_ratio": math.pow(2.0, args.semitones / 12.0),
                "tempo_ratio": 1.0,
                "engine": "R3 Finer",
                "formant_preserved": not args.no_formant,
                "input_headroom_db": used_headroom,
                "shifted_peak_dbfs": shifted_peak,
                "shifted_decoded_sample_values": shifted_sample_values,
                "post_gain_db": post_gain,
                "target_peak_dbfs": args.target_peak_dbfs,
            },
            "tools": {
                "ffmpeg": version(ffmpeg, "-version"),
                "ffprobe": version(ffprobe, "-version"),
                "rubberband": rubberband_version,
            },
            "attempts": command_logs,
            "duration_drift_seconds": drift,
            "rollback": {
                "audio": target_rollback.name if target_rollback else None,
                "report": report_rollback.name if report_rollback else None,
            },
        }
        temporary_report = work / "report.json"
        temporary_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        promote_transaction(
            candidate,
            temporary_report,
            target,
            report_path,
            target_rollback,
            report_rollback,
        )
        print(json.dumps({"status": "ok", "output": str(target), "report": str(report_path)}, ensure_ascii=False))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
