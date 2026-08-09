"""Build FFmpeg commands for subtitle-only and supplied-video karaoke output."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from scripts.render_vinyl_karaoke import escape_filter_path

CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1080
FRAME_RATE = 30
AV1_ENCODER_PREFERENCE = ("av1_nvenc", "libaom-av1")


def create_transparent_canvas(path: Path) -> None:
    """Create the renderer input used only while generating ASS."""

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0)).save(
        path, format="PNG", optimize=True
    )


def _subtitle_filter(ass_path: Path, fonts_dir: Path) -> str:
    return (
        f"ass=filename='{escape_filter_path(ass_path)}'"
        f":fontsdir='{escape_filter_path(fonts_dir)}':alpha=1"
    )


def build_av1_encoder_list_command(ffmpeg: Path) -> list[str]:
    """Return the command used to discover AV1 encoders in the selected FFmpeg."""

    return [str(ffmpeg), "-hide_banner", "-encoders"]


def parse_available_av1_encoders(output: str) -> tuple[str, ...]:
    """Return supported AV1 encoders in the workflow preference order."""

    tokens_by_line = [set(line.split()) for line in output.splitlines()]
    return tuple(
        encoder
        for encoder in AV1_ENCODER_PREFERENCE
        if any(encoder in tokens for tokens in tokens_by_line)
    )


def _av1_codec_args(video_encoder: str) -> list[str]:
    if video_encoder == "av1_nvenc":
        return [
            "-c:v",
            video_encoder,
            "-preset",
            "p7",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            "38",
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
        ]
    if video_encoder == "libaom-av1":
        return [
            "-c:v",
            video_encoder,
            "-crf",
            "30",
            "-b:v",
            "0",
            "-cpu-used",
            "4",
            "-row-mt",
            "1",
            "-g",
            "240",
        ]
    raise ValueError(f"unsupported AV1 encoder: {video_encoder}")


def build_av1_encoder_smoke_command(
    ffmpeg: Path, *, video_encoder: str
) -> list[str]:
    """Build a one-frame initialization probe for one AV1 encoder."""

    return [
        str(ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:r=1:d=0.1",
        "-frames:v",
        "1",
        "-pix_fmt",
        "yuv420p",
        *_av1_codec_args(video_encoder),
        "-f",
        "null",
        "-",
    ]


def build_transparent_overlay_command(
    *,
    ffmpeg: Path,
    ass_path: Path,
    fonts_dir: Path,
    output_path: Path,
    duration_seconds: float,
) -> list[str]:
    """Return a ProRes 4444 command for a silent transparent subtitle layer."""

    duration = max(0.1, float(duration_seconds))
    canvas = (
        f"color=c=black@0.0:s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:"
        f"r={FRAME_RATE}:d={duration:.3f},format=rgba"
    )
    return [
        str(ffmpeg),
        "-nostdin",
        "-n",
        "-f",
        "lavfi",
        "-i",
        canvas,
        "-vf",
        f"{_subtitle_filter(ass_path, fonts_dir)},format=yuva444p10le",
        "-map",
        "0:v:0",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-an",
        "-r",
        str(FRAME_RATE),
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4",
        "-pix_fmt",
        "yuva444p10le",
        "-vendor",
        "apl0",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-movflags",
        "+write_colr",
        str(output_path),
    ]


def build_background_composite_command(
    *,
    ffmpeg: Path,
    background_video: Path,
    audio_path: Path,
    ass_path: Path,
    fonts_dir: Path,
    output_path: Path,
    start_seconds: float,
    duration_seconds: float,
    video_encoder: str,
) -> list[str]:
    """Return an AV1 command that trims long video and pads short video with black."""

    start = max(0.0, float(start_seconds))
    duration = max(0.1, float(duration_seconds))
    end = start + duration
    subtitle = _subtitle_filter(ass_path, fonts_dir)
    graph = (
        "[0:v:0]setpts=PTS-STARTPTS,"
        f"scale={CANVAS_WIDTH}:{CANVAS_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={CANVAS_WIDTH}:{CANVAS_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={FRAME_RATE},tpad=stop_mode=add:stop_duration={duration:.3f},"
        f"trim=duration={duration:.3f},{subtitle},format=yuv420p[v];"
        f"[1:a:0]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a]"
    )
    return [
        str(ffmpeg),
        "-nostdin",
        "-n",
        "-i",
        str(background_video),
        "-i",
        str(audio_path),
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-r",
        str(FRAME_RATE),
        "-pix_fmt",
        "yuv420p",
        *_av1_codec_args(video_encoder),
        "-tag:v",
        "av01",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-c:a",
        "aac",
        "-profile:a",
        "aac_low",
        "-b:a",
        "320k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
