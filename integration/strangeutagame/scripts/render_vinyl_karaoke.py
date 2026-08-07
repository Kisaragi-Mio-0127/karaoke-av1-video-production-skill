#!/usr/bin/env python3
"""Render album-cover + spinning-vinyl karaoke from explicit track metadata.

Timing/ASS remains an external input owned by the timing workflow.  This
module has no import-time album authority: the CLI resolves an explicitly
selected manifest track and library callers supply track/album metadata.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Windows PowerShell sessions may expose a legacy GBK stream.  Media metadata
# contains Japanese titles, so keep CLI diagnostics lossless regardless of the
# host console code page.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from mutagen import File as MutagenFile
except ImportError:  # pragma: no cover - the media venv supplies mutagen
    MutagenFile = None  # type: ignore[assignment]

try:
    import imageio_ffmpeg
except ImportError:  # pragma: no cover - the media venv supplies imageio-ffmpeg
    imageio_ffmpeg = None  # type: ignore[assignment]

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
except ImportError:  # pragma: no cover - the media venv supplies Pillow
    Image = ImageDraw = ImageEnhance = ImageFilter = ImageFont = ImageOps = None  # type: ignore[assignment]

try:
    from .karaoke_album import (
        load_album_manifest,
        project_relative,
    )
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_album import (  # type: ignore[no-redef]
        load_album_manifest,
        project_relative,
    )

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED_FONT_DIR = REPO_ROOT / "assets" / "fonts" / "HarmonyOS-Sans"


def track_dict(track: Any) -> dict[str, Any]:
    """Convert a manifest track to the renderer's small working record."""

    return {
        "audio": track.audio_path,
        "title": track.title,
        "artist": track.artist,
        "basename": track.artifact_slug,
        "song_id": track.song_id,
        "track_number": track.track_number,
        "timing_stem": track.timing_stem,
        "report_stem": track.report_stem,
        "expected_cues": track.expected_cues,
    }


CANVAS_SIZE = (1920, 1080)
VINYL_SIZE = 860
VINYL_STYLE_VERSION = "direction-neutral-concentric-grooves/v3/backplate-absent"


class WaitingForASSError(RuntimeError):
    """Raised when the timing agent has not supplied the required ASS yet."""


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def run_capture(executable: Path | str, args: Iterable[str]) -> CommandResult:
    command = [str(executable), *[str(arg) for arg in args]]
    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
    )
    return CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=_decode(completed.stdout),
        stderr=_decode(completed.stderr),
    )


def default_ffmpeg() -> Path:
    if imageio_ffmpeg is None:
        raise RuntimeError("imageio-ffmpeg is not importable in the active Python environment")
    return Path(imageio_ffmpeg.get_ffmpeg_exe())


def _has_named_filter(output: str, name: str) -> bool:
    return any(name in line.strip().split() for line in output.splitlines())


def _has_named_encoder(output: str, name: str) -> bool:
    return any(re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", line) for line in output.splitlines())


def probe_ffmpeg_capabilities(ffmpeg: Path | str) -> dict[str, Any]:
    """Probe the exact FFmpeg binary before assembling a render command."""

    ffmpeg = Path(ffmpeg)
    version = run_capture(ffmpeg, ["-hide_banner", "-version"])
    filters = run_capture(ffmpeg, ["-hide_banner", "-filters"])
    encoders = run_capture(ffmpeg, ["-hide_banner", "-encoders"])
    buildconf = run_capture(ffmpeg, ["-hide_banner", "-buildconf"])

    filter_output = filters.stdout + "\n" + filters.stderr
    encoder_output = encoders.stdout + "\n" + encoders.stderr
    buildconf_output = buildconf.stdout + "\n" + buildconf.stderr
    has_subtitles = _has_named_filter(filter_output, "subtitles")
    has_ass = _has_named_filter(filter_output, "ass")

    if has_subtitles:
        subtitle_filter = "subtitles"
    elif has_ass:
        subtitle_filter = "ass"
    else:
        subtitle_filter = None

    h264_candidates = [
        "libx264",
        "h264_nvenc",
        "h264_qsv",
        "h264_amf",
    ]
    audio_candidates = ["aac", "aac_mf", "libfdk_aac", "libmp3lame"]
    available_h264 = [name for name in h264_candidates if _has_named_encoder(encoder_output, name)]
    available_av1 = [
        name
        for name in ("av1_nvenc", "libaom-av1")
        if _has_named_encoder(encoder_output, name)
    ]
    available_audio = [name for name in audio_candidates if _has_named_encoder(encoder_output, name)]

    return {
        "path": str(ffmpeg),
        "version": next((line for line in version.stdout.splitlines() if line.startswith("ffmpeg version")), "unknown"),
        "libass": "libass" in buildconf_output.lower() or has_subtitles or has_ass,
        "subtitle_filters": {"subtitles": has_subtitles, "ass": has_ass},
        "subtitle_filter_selected": subtitle_filter,
        "h264_encoders": available_h264,
        "av1_encoders": available_av1,
        "audio_encoders": available_audio,
        "checks": {
            "subtitles_or_ass_filter": subtitle_filter is not None,
            "libass": "libass" in buildconf_output.lower() or has_subtitles or has_ass,
            "h264": bool(available_h264),
            "aac": _has_named_encoder(encoder_output, "aac") or _has_named_encoder(encoder_output, "aac_mf"),
        },
        "raw_probe_errors": {
            "version": version.returncode,
            "filters": filters.returncode,
            "encoders": encoders.returncode,
            "buildconf": buildconf.returncode,
        },
    }


def audio_duration(audio_path: Path) -> float:
    if MutagenFile is None:
        raise RuntimeError("mutagen is required to read the source audio duration")
    media = MutagenFile(str(audio_path))
    if media is None or getattr(media, "info", None) is None:
        raise RuntimeError(f"mutagen could not read audio metadata: {audio_path}")
    duration = float(getattr(media.info, "length", 0.0))
    if duration <= 0:
        raise RuntimeError(f"audio duration is not positive: {audio_path}")
    return duration


def embedded_cover(audio_path: Path) -> tuple[bytes | None, dict[str, Any]]:
    """Return the first embedded image, preserving its source bytes."""

    if MutagenFile is None:
        return None, {"present": False, "reason": "mutagen_unavailable"}
    media = MutagenFile(str(audio_path))
    pictures: list[Any] = []
    if media is not None:
        pictures.extend(getattr(media, "pictures", None) or ())
    tags = getattr(media, "tags", None) if media is not None else None
    if tags is None and not pictures:
        return None, {"present": False, "reason": "no_tags_or_pictures"}

    getall = getattr(tags, "getall", None)
    if callable(getall):
        pictures.extend(getall("APIC"))
    if not pictures and tags is not None:
        pictures.extend(value for key, value in tags.items() if str(key).upper().startswith("APIC"))

    for picture in pictures:
        data = getattr(picture, "data", None)
        if data:
            mime = str(getattr(picture, "mime", "image/jpeg"))
            source = (
                "embedded_flac_picture"
                if picture in (getattr(media, "pictures", None) or ())
                else "embedded_id3_apic"
            )
            return bytes(data), {
                "present": True,
                "source": source,
                "mime": mime,
                "bytes": len(data),
                "description": str(getattr(picture, "desc", "")),
            }
    return None, {"present": False, "reason": "no_embedded_image"}


def fetch_cover(url: str) -> tuple[bytes, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "StrangeUtaGame-media-builder/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read()
        mime = response.headers.get_content_type() or "image/jpeg"
    if not data:
        raise RuntimeError(f"cover URL returned an empty response: {url}")
    return data, {
        "present": True,
        "source": "network_url",
        "mime": mime,
        "bytes": len(data),
    }


def _source_extension(mime: str) -> str:
    mime = mime.lower()
    if "png" in mime:
        return ".png"
    if "webp" in mime:
        return ".webp"
    return ".jpg"


def inspect_font_dir(fonts_dir: Path) -> dict[str, Any]:
    """Inspect the explicitly supplied HarmonyOS Sans distribution directory."""

    if ImageFont is None:
        raise RuntimeError("Pillow is required to inspect the supplied font directory")
    fonts_dir = fonts_dir.resolve()
    if not fonts_dir.is_dir():
        raise RuntimeError(f"HarmonyOS Sans directory does not exist: {fonts_dir}")
    font_paths = sorted(
        path
        for path in fonts_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".ttf", ".otf", ".ttc"}
    )
    records: list[dict[str, Any]] = []
    for path in font_paths:
        try:
            font = ImageFont.truetype(str(path), size=16)
            family, style = font.getname()
        except OSError:
            continue
        records.append({
            "file": path.name,
            "path": str(path),
            "family": family,
            "style": style,
        })
    harmony_records = [record for record in records if "harmonyos sans" in record["family"].casefold()]
    if not harmony_records:
        raise RuntimeError(f"no HarmonyOS Sans family found under --fonts-dir: {fonts_dir}")

    def rank(record: dict[str, Any]) -> tuple[int, int, str]:
        family = record["family"].casefold()
        file_name = record["file"].casefold()
        # SC is the CJK cut needed for the Japanese title/artist; prefer it
        # over the Latin-only family when both are distributed.
        cjk_rank = 0 if " sc" in family or "_sc" in file_name else 1
        regular_rank = 0 if "regular" in record["style"].casefold() or "regular" in file_name else 1
        return cjk_rank, regular_rank, record["file"]

    regular = sorted(harmony_records, key=rank)[0]
    same_family = [record for record in harmony_records if record["family"] == regular["family"]]
    bold_candidates = [
        record
        for record in same_family
        if "bold" in record["style"].casefold() or "bold" in record["file"].casefold()
    ]
    bold = sorted(bold_candidates or same_family, key=rank)[0]
    return {
        "directory": str(fonts_dir),
        "family": regular["family"],
        "regular": regular,
        "bold": bold,
        "files": records,
        "source": "HarmonyOS-Sans.zip",
        "source_url": "https://developer.huawei.com/images/download/general/HarmonyOS-Sans.zip",
    }


def load_font(size: int, font_info: dict[str, Any], bold: bool = False):
    if ImageFont is None:
        raise RuntimeError("Pillow is required to build artwork")
    selected = font_info["bold"] if bold else font_info["regular"]
    try:
        return ImageFont.truetype(selected["path"], size=size)
    except OSError as exc:
        raise RuntimeError(f"could not load HarmonyOS Sans file: {selected['path']}") from exc


def fit_font(draw: Any, text: str, max_width: int, start_size: int, font_info: dict[str, Any], bold: bool = False):
    for size in range(start_size, 15, -2):
        font = load_font(size, font_info, bold=bold)
        if draw.textlength(text, font=font) <= max_width:
            return font
    return load_font(15, font_info, bold=bold)


def fit_cover(image: Any, size: tuple[int, int]) -> Any:
    return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def _draw_background(cover: Any) -> Any:
    background = fit_cover(cover, CANVAS_SIZE)
    background = background.filter(ImageFilter.GaussianBlur(radius=24))
    background = ImageEnhance.Color(background).enhance(0.72)
    background = ImageEnhance.Contrast(background).enhance(0.78)
    background = ImageEnhance.Brightness(background).enhance(0.30)
    canvas = background.convert("RGBA")

    shade = Image.new("RGBA", CANVAS_SIZE, (4, 6, 14, 118))
    shade_draw = ImageDraw.Draw(shade)
    width, height = CANVAS_SIZE
    for y in range(height):
        # A restrained top/bottom vignette keeps the cover readable without
        # requiring a video-side blend filter.
        edge = min(y / height, 1 - y / height) * 2
        alpha = int(145 - 44 * max(0.0, edge))
        shade_draw.line((0, y, width, y), fill=(4, 6, 14, alpha))
    canvas.alpha_composite(shade)

    return canvas


def build_background(cover: Any) -> Any:
    """Build the current cover-derived 1920x1080 background."""

    return _draw_background(cover)


def _draw_envelope(
    canvas: Any,
    cover: Any,
    title: str,
    artist: str,
    font_info: dict[str, Any],
    album_title: str = "",
    album_artist: str = "",
) -> None:
    x, y = 125, 120
    envelope_width, envelope_height = 690, 730
    shadow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (x + 14, y + 20, x + envelope_width + 14, y + envelope_height + 20),
        radius=34,
        fill=(0, 0, 0, 180),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(radius=22)))

    sleeve = Image.new("RGBA", (envelope_width, envelope_height), (0, 0, 0, 0))
    sleeve_draw = ImageDraw.Draw(sleeve)
    sleeve_draw.rounded_rectangle(
        (0, 0, envelope_width - 1, envelope_height - 1),
        radius=34,
        fill=(243, 241, 235, 255),
        outline=(255, 255, 255, 140),
        width=3,
    )
    sleeve_cover = fit_cover(cover, (envelope_width - 58, envelope_width - 58))
    sleeve.paste(sleeve_cover, (29, 29))
    sleeve_draw.rectangle(
        (29, envelope_width - 29, envelope_width - 29, envelope_height - 29),
        fill=(238, 235, 228, 255),
    )
    small_font = load_font(22, font_info, bold=True)
    sleeve_draw.text(
        (42, envelope_width - 18),
        album_title,
        font=small_font,
        fill=(39, 42, 48, 235),
    )
    sleeve_draw.text((42, envelope_width + 13), album_artist, font=load_font(18, font_info), fill=(82, 84, 89, 225))
    canvas.alpha_composite(sleeve, (x, y))

    draw = ImageDraw.Draw(canvas)
    label_font = load_font(20, font_info, bold=True)
    draw.text((x, y + envelope_height + 38), "STUDIO KARAOKE / 30 FPS", font=label_font, fill=(234, 232, 227, 190))
    title_font = fit_font(draw, title, 820, 64, font_info, bold=True)
    draw.text((x, y + envelope_height + 76), title, font=title_font, fill=(255, 255, 255, 255))
    artist_font = fit_font(draw, artist, 820, 31, font_info, bold=False)
    draw.text((x, y + envelope_height + 153), artist, font=artist_font, fill=(204, 207, 215, 235))


def _draw_vinyl(cover: Any) -> Any:
    vinyl = Image.new("RGBA", (VINYL_SIZE, VINYL_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(vinyl)
    center = VINYL_SIZE // 2
    radius = VINYL_SIZE // 2 - 16
    draw.ellipse((center - radius, center - radius, center + radius, center + radius), fill=(9, 10, 14, 255))

    # Concentric grooves are intentionally low contrast.  They catch the
    # slow movement in the final video without becoming a distracting moiré.
    groove_layer = Image.new("RGBA", vinyl.size, (0, 0, 0, 0))
    groove_draw = ImageDraw.Draw(groove_layer)
    for groove_radius in range(radius - 8, 155, -9):
        alpha = 42 if groove_radius % 18 else 74
        groove_draw.ellipse(
            (center - groove_radius, center - groove_radius, center + groove_radius, center + groove_radius),
            outline=(92, 96, 108, alpha),
            width=2,
        )
    vinyl = Image.alpha_composite(vinyl, groove_layer)
    # Keep the rotating surface direction-neutral. Partial highlight or shadow
    # arcs sweep around during rotation and read as a missing/differently
    # coloured section, so the record uses complete concentric grooves only.

    label_size = 278
    label = fit_cover(cover, (label_size, label_size))
    mask = Image.new("L", (label_size, label_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, label_size - 1, label_size - 1), fill=255)
    vinyl.paste(label, (center - label_size // 2, center - label_size // 2), mask)
    detail_layer = Image.new("RGBA", vinyl.size, (0, 0, 0, 0))
    detail_draw = ImageDraw.Draw(detail_layer)
    detail_draw.ellipse(
        (center - label_size // 2, center - label_size // 2, center + label_size // 2, center + label_size // 2),
        outline=(255, 255, 255, 120),
        width=3,
    )
    detail_draw.ellipse(
        (center - 11, center - 11, center + 11, center + 11),
        fill=(224, 225, 229, 230),
    )
    detail_draw.ellipse(
        (center - 4, center - 4, center + 4, center + 4),
        fill=(18, 20, 25, 255),
    )
    return Image.alpha_composite(vinyl, detail_layer)


def build_vinyl(cover: Any) -> Any:
    """Build the current direction-neutral rotating vinyl asset."""

    return _draw_vinyl(cover)


def build_artwork(
    audio_path: Path,
    artwork_dir: Path,
    title: str,
    artist: str,
    cover_url: str,
    fonts_dir: Path,
    allow_network: bool = True,
    album_title: str = "",
    album_artist: str = "",
) -> dict[str, Any]:
    if Image is None or ImageOps is None:
        raise RuntimeError("Pillow is required to build artwork")

    embedded, source_info = embedded_cover(audio_path)
    if embedded is not None:
        cover_bytes = embedded
    elif allow_network:
        cover_bytes, source_info = fetch_cover(cover_url)
    else:
        raise RuntimeError("no embedded cover and network fallback was disabled")

    try:
        cover = ImageOps.exif_transpose(Image.open(io.BytesIO(cover_bytes))).convert("RGB")
    except Exception as exc:  # Pillow raises several image-format-specific errors
        raise RuntimeError(f"cover bytes are not a readable image: {exc}") from exc
    font_info = inspect_font_dir(fonts_dir)

    artwork_dir.mkdir(parents=True, exist_ok=True)
    source_ext = _source_extension(str(source_info.get("mime", "image/jpeg")))
    source_path = artwork_dir / f"cover_source{source_ext}"
    source_path.write_bytes(cover_bytes)
    cover_path = artwork_dir / "cover.jpg"
    cover.save(cover_path, format="JPEG", quality=95, optimize=True)

    background = _draw_background(cover)
    background_path = artwork_dir / "background.png"
    background.convert("RGB").save(background_path, format="PNG", optimize=True)

    composition = background.copy()
    _draw_envelope(
        composition,
        cover,
        title,
        artist,
        font_info,
        album_title,
        album_artist,
    )
    composition_path = artwork_dir / "composition.png"
    composition.convert("RGB").save(composition_path, format="PNG", optimize=True)

    vinyl = _draw_vinyl(cover)
    vinyl_path = artwork_dir / "vinyl.png"
    vinyl.save(vinyl_path, format="PNG", optimize=True)
    renderer_source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    vinyl_sha256 = hashlib.sha256(vinyl_path.read_bytes()).hexdigest()

    metadata = {
        "schema_version": 1,
        "vinyl_style_version": VINYL_STYLE_VERSION,
        "vinyl_generator_sha256": renderer_source_sha256,
        # Compatibility alias for reports written before the shared direct-
        # renderer provenance contract was finalized.
        "render_vinyl_karaoke_sha256": renderer_source_sha256,
        "vinyl_sha256": vinyl_sha256,
        "vinyl_backplate": None,
        "vinyl_backplate_present": False,
        # Compatibility field for older report readers. False is intentional:
        # the compact panel behind the rotating vinyl is no longer preserved.
        "vinyl_backplate_preserved": False,
        "vinyl_motion_contract": {
            "default": "rotate",
            "allowed": ["static", "rotate"],
            "rotation_period_seconds": 8.0,
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "track": {
            "title": title,
            "artist": artist,
            "audio": project_relative(audio_path, REPO_ROOT),
        },
        "album": {"title": album_title, "artist": album_artist},
        "cover_url": cover_url,
        "source": source_info,
        "source_sha256": hashlib.sha256(cover_bytes).hexdigest(),
        "source_file": source_path.name,
        "normalized_cover": cover_path.name,
        "cover_dimensions": {"width": cover.width, "height": cover.height},
        "generated_files": {
            "background": background_path.name,
            "composition": composition_path.name,
            "vinyl": vinyl_path.name,
        },
        "font": font_info,
        "design": {
            "canvas": {"width": CANVAS_SIZE[0], "height": CANVAS_SIZE[1]},
            "vinyl_size": VINYL_SIZE,
            "rotation_period_seconds": 8.0,
            "background": "embedded cover, Gaussian blur, color/contrast reduction and dark overlay",
        },
    }
    metadata_path = artwork_dir / "artwork.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def _casefold_path_map(paths: Iterable[Path]) -> dict[str, Path]:
    return {path.name.casefold(): path for path in paths}


def timing_directories(repo_root: Path, slug: str, timing_dir: Path | None) -> list[Path]:
    if timing_dir is not None:
        return [timing_dir]
    return [
        repo_root / "timing",
        repo_root / "deliverables" / slug / "timing",
    ]


def ass_candidates(
    repo_root: Path,
    slug: str,
    audio_path: Path,
    title: str,
    timing_dir: Path | None = None,
) -> list[Path]:
    directories = [path for path in timing_directories(repo_root, slug, timing_dir) if path.exists()]
    files: list[Path] = []
    for directory in directories:
        files.extend(sorted(path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() == ".ass"))
    unique: dict[str, Path] = {}
    for path in files:
        unique[str(path.resolve()).casefold()] = path
    candidates = list(unique.values())

    exact_names = {
        f"{audio_path.stem}.ass".casefold(),
        f"{title}.ass".casefold(),
        f"{slug}.ass".casefold(),
    }
    exact = _casefold_path_map(candidates)
    exact_matches = [exact[name] for name in exact_names if name in exact]
    if exact_matches:
        return sorted(exact_matches, key=lambda path: str(path))

    tokens = [token.casefold() for token in (audio_path.stem, title, slug) if token]
    scored = sorted(
        candidates,
        key=lambda path: sum(token in path.stem.casefold() for token in tokens),
        reverse=True,
    )
    if scored and sum(token in scored[0].stem.casefold() for token in tokens) > 0:
        best_score = sum(token in scored[0].stem.casefold() for token in tokens)
        return [path for path in scored if sum(token in path.stem.casefold() for token in tokens) == best_score]
    return candidates


def find_ass(
    repo_root: Path,
    slug: str,
    audio_path: Path,
    title: str,
    timing_dir: Path | None = None,
) -> Path | None:
    candidates = ass_candidates(repo_root, slug, audio_path, title, timing_dir)
    if len(candidates) == 1:
        return candidates[0]
    return None


def escape_filter_path(path: Path) -> str:
    # FFmpeg's filter parser treats the Windows drive colon as an option
    # separator.  Forward slashes plus an escaped colon work for both the
    # Windows build used here and POSIX CI.
    value = path.resolve().as_posix().replace("'", r"\'")
    return value.replace(":", r"\:")


def escape_filter_value(value: str) -> str:
    return value.replace("\\", r"\\").replace("'", r"\'")


def normalize_font_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def subtitle_filter_expression(
    *,
    subtitle_filter: str,
    ass_path: Path,
    fonts_dir: Path,
    font_family: str,
) -> str:
    return (
        f"{subtitle_filter}=filename='{escape_filter_path(ass_path)}'"
        f":fontsdir='{escape_filter_path(fonts_dir)}'"
        f":force_style='FontName={escape_filter_value(font_family)}'"
    )


def _ass_time_seconds(value: str) -> float:
    hours, minutes, seconds = value.strip().split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _first_ass_dialogue_time(text: str) -> float | None:
    """Return the first real lyric event time from an ASS document."""

    starts: list[float] = []
    for line in text.splitlines():
        if not line.lstrip().startswith("Dialogue:"):
            continue
        fields = line.split(":", 1)[1].split(",", 3)
        if len(fields) < 4 or not fields[3].strip():
            continue
        with contextlib.suppress(ValueError, IndexError):
            starts.append(_ass_time_seconds(fields[1]))
    return min(starts) if starts else None


def _parse_ass_color(value: object) -> dict[str, str] | None:
    """Parse an ASS ``&HAABBGGRR`` color and expose its RGB value."""

    raw = str(value or "").strip().upper().removesuffix("&")
    match = re.fullmatch(r"&H([0-9A-F]{6}|[0-9A-F]{8})", raw)
    if match is None:
        return None
    digits = match.group(1)
    if len(digits) == 6:
        digits = "00" + digits
    bgr = digits[2:]
    return {
        "ass": f"&H{digits}",
        "alpha": digits[:2],
        "bgr": bgr,
        "rgb": f"#{bgr[4:6]}{bgr[2:4]}{bgr[:2]}",
    }


def validate_ass_for_render(ass_path: Path, font_family: str) -> dict[str, Any]:
    """Gate the timing agent's ASS before it can reach a final video render."""

    try:
        text = ass_path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        return {"ok": False, "path": str(ass_path), "errors": [f"read_failed: {exc}"]}

    errors: list[str] = []
    forbidden_tokens = [token for token in ("|<", "#|", r"\sing_") if token in text]
    if forbidden_tokens:
        errors.append(f"forbidden_karaoke_tokens: {forbidden_tokens}")
    play_res = {
        "x": int(match.group(1)) if (match := re.search(r"(?im)^PlayResX:\s*(\d+)", text)) else None,
        "y": int(match.group(1)) if (match := re.search(r"(?im)^PlayResY:\s*(\d+)", text)) else None,
    }
    if play_res != {"x": 1920, "y": 1080}:
        errors.append(f"play_resolution_must_be_1920x1080: {play_res}")
    layout_match = re.search(r"(?im)^;\s*Layout:\s*([^\r\n]+)", text)
    layout = layout_match.group(1).strip() if layout_match else None
    role_aware_layout = layout in {
        "standard-v7",
        "wide-bottom",
        "wide-bottom-zh",
        "wide-bottom-en",
    }

    styles: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.lstrip().startswith("Style:"):
            continue
        fields = line.split(":", 1)[1].split(",")
        if len(fields) < 19:
            errors.append(f"malformed_style: {line[:120]}")
            continue
        try:
            style = {
                "name": fields[0].strip(),
                "font_family": fields[1].strip(),
                "font_size": float(fields[2].strip()),
                "primary_color": fields[3].strip() if len(fields) > 3 else None,
                "secondary_color": fields[4].strip() if len(fields) > 4 else None,
                "scale_x": float(fields[11].strip()) if len(fields) > 11 else None,
                "spacing": float(fields[13].strip()) if len(fields) > 13 else None,
                "alignment": int(fields[18].strip()),
                "margin_left": int(fields[19].strip()) if len(fields) > 19 else None,
                "margin_right": int(fields[20].strip()) if len(fields) > 20 else None,
                "margin_vertical": int(fields[21].strip()) if len(fields) > 21 else None,
                "line": line_number,
            }
        except ValueError:
            errors.append(f"unreadable_style_fields: {line[:120]}")
            continue
        styles.append(style)

    dialogue_count = 0
    dialogues: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.lstrip().startswith("Dialogue:"):
            continue
        dialogue_count += 1
        fields = line.split(":", 1)[1].split(",", 9)
        if len(fields) < 10:
            errors.append(f"malformed_dialogue: line {line_number}")
            continue
        try:
            layer = int(fields[0].strip())
        except ValueError:
            layer = None
            errors.append(f"unreadable_dialogue_layer: line {line_number}")
        dialogues.append(
            {
                "line": line_number,
                "layer": layer,
                "start": fields[1].strip(),
                "end": fields[2].strip(),
                "style": fields[3].strip(),
                "name": fields[4].strip(),
                "text": fields[9],
            }
        )

    wanted_font = normalize_font_name(font_family)
    wrong_fonts = [style for style in styles if normalize_font_name(style["font_family"]) != wanted_font]
    if not styles:
        errors.append("no_ASS_styles")
    if wrong_fonts:
        errors.append(f"styles_not_using_{font_family}: {wrong_fonts}")

    style_names = {style["name"] for style in styles}
    dialogue_style_names = {dialogue["style"] for dialogue in dialogues}
    secondary_style_names = {"Secondary", "SecondaryGlow"}
    secondary_declared = bool(
        style_names & secondary_style_names
        or dialogue_style_names & secondary_style_names
    )

    if role_aware_layout:
        if layout in {"wide-bottom", "wide-bottom-zh"}:
            size_ranges = {
                "Glow": (108.0, 108.0),
                "Main": (108.0, 108.0),
                "RubyGlow": (51.0, 51.0),
                "Ruby": (51.0, 51.0),
                "CueDim": (39.0, 39.0),
                "CueHot": (39.0, 39.0),
            }
        elif layout == "wide-bottom-en":
            size_ranges = {
                "Glow": (96.0, 96.0),
                "Main": (96.0, 96.0),
                "RubyGlow": (51.0, 51.0),
                "Ruby": (51.0, 51.0),
                "CueDim": (39.0, 39.0),
                "CueHot": (39.0, 39.0),
            }
        else:
            size_ranges = {
                "Glow": (38.0, 60.0),
                "Main": (38.0, 60.0),
                "RubyGlow": (18.0, 30.0),
                "Ruby": (18.0, 30.0),
                "CueDim": (18.0, 48.0),
                "CueHot": (18.0, 48.0),
            }
        if secondary_declared:
            size_ranges.update(
                {
                    "SecondaryGlow": (60.0, 60.0),
                    "Secondary": (60.0, 60.0),
                }
            )
        missing_styles = sorted(set(size_ranges) - {style["name"] for style in styles})
        if missing_styles:
            errors.append(f"missing_layout_styles: {missing_styles}")
        bad_sizes = [
            style
            for style in styles
            if style["name"] not in size_ranges
            or not size_ranges[style["name"]][0]
            <= style["font_size"]
            <= size_ranges[style["name"]][1]
        ]
    else:
        bad_sizes = [
            style
            for style in styles
            if (
                style["name"] in secondary_style_names
                and not 60.0 <= style["font_size"] <= 60.0
            )
            or (
                style["name"] not in secondary_style_names
                and not 56.0 <= style["font_size"] <= 60.0
            )
        ]
    if bad_sizes:
        error_name = (
            "font_size_outside_layout_role_range"
            if role_aware_layout
            else "font_size_outside_56_to_60px"
        )
        errors.append(f"{error_name}: {bad_sizes}")

    if role_aware_layout:
        inline_size_ranges = dict(size_ranges)
        if layout == "wide-bottom-zh":
            inline_size_ranges.update({"Glow": (75.0, 108.0), "Main": (75.0, 108.0)})
        elif layout == "wide-bottom-en":
            inline_size_ranges.update({"Glow": (54.0, 96.0), "Main": (54.0, 96.0)})
        if secondary_declared:
            inline_size_ranges.update(
                {"SecondaryGlow": (36.0, 60.0), "Secondary": (36.0, 60.0)}
            )
    else:
        inline_size_ranges = (
            {"SecondaryGlow": (36.0, 60.0), "Secondary": (36.0, 60.0)}
            if secondary_declared
            else {}
        )

    number_pattern = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
    fs_pattern = re.compile(rf"\\fs(?![A-Za-z])({number_pattern})")
    fsp_pattern = re.compile(rf"\\fsp({number_pattern})")
    fscx_pattern = re.compile(rf"\\fscx({number_pattern})")
    inline_font_sizes: list[dict[str, Any]] = []
    bad_inline_sizes: list[dict[str, Any]] = []
    missing_dynamic_sizes: list[dict[str, Any]] = []
    letter_spacing: list[dict[str, Any]] = []
    bad_letter_spacing: list[dict[str, Any]] = []
    inline_scale_x: list[dict[str, Any]] = []
    for dialogue in dialogues:
        style_name = dialogue["style"]
        text_value = dialogue["text"]
        fs_values = [float(match.group(1)) for match in fs_pattern.finditer(text_value)]
        allowed_range = inline_size_ranges.get(style_name)
        for value in fs_values:
            record = {
                "line": dialogue["line"],
                "style": style_name,
                "value": value,
            }
            inline_font_sizes.append(record)
            if allowed_range is not None and not allowed_range[0] <= value <= allowed_range[1]:
                bad_inline_sizes.append({**record, "allowed": allowed_range})
        if (
            layout in {"wide-bottom-zh", "wide-bottom-en"}
            and style_name in {"Glow", "Main"}
            and not fs_values
        ):
            missing_dynamic_sizes.append(
                {"line": dialogue["line"], "style": style_name}
            )
        for match in fsp_pattern.finditer(text_value):
            value = float(match.group(1))
            record = {"line": dialogue["line"], "style": style_name, "value": value}
            letter_spacing.append(record)
            if value < 0:
                bad_letter_spacing.append(record)
        for match in fscx_pattern.finditer(text_value):
            inline_scale_x.append(
                {
                    "line": dialogue["line"],
                    "style": style_name,
                    "value": float(match.group(1)),
                }
            )

    if bad_inline_sizes:
        errors.append(f"inline_font_size_outside_layout_role_range: {bad_inline_sizes}")
    if missing_dynamic_sizes:
        errors.append(f"missing_dynamic_main_font_size_override: {missing_dynamic_sizes}")

    def right_middle_or_lower(style: dict[str, Any]) -> bool:
        if style["name"] in secondary_style_names:
            return style["alignment"] == 8
        if style["alignment"] in {3, 6}:
            return True
        # ASS alignment 2 is bottom-center.  With a deliberately asymmetric
        # 980/80 margin pair its anchor is in the right-hand lower panel.
        return (
            style["alignment"] == 2
            and style["margin_left"] is not None
            and style["margin_right"] is not None
            and style["margin_left"] >= 900
            and style["margin_left"] - style["margin_right"] >= 600
        )

    if role_aware_layout:
        expected_alignments = {
            "Glow": 7,
            "Main": 7,
            "RubyGlow": 8,
            "Ruby": 8,
            "CueDim": 5,
            "CueHot": 5,
        }
        if secondary_declared:
            expected_alignments.update({"SecondaryGlow": 8, "Secondary": 8})
        bad_alignment = [
            style
            for style in styles
            if expected_alignments.get(style["name"]) != style["alignment"]
        ]
    else:
        bad_alignment = [style for style in styles if not right_middle_or_lower(style)]
    if bad_alignment:
        error_name = (
            "subtitle_alignment_wrong_for_layout_role"
            if role_aware_layout
            else "subtitle_not_right_middle_or_lower"
        )
        errors.append(f"{error_name}: {bad_alignment}")
    inline_fonts = [value.strip() for value in re.findall(r"\\fn([^\\}]+)", text)]
    bad_inline_fonts = [value for value in inline_fonts if normalize_font_name(value) != wanted_font]
    if bad_inline_fonts:
        errors.append(f"inline_fonts_not_using_{font_family}: {bad_inline_fonts}")

    position_pattern = re.compile(
        rf"\\pos\(({number_pattern}),\s*({number_pattern})\)"
    )
    positions: list[tuple[float, float]] = []
    position_records: list[dict[str, Any]] = []
    for dialogue in dialogues:
        for match in position_pattern.finditer(dialogue["text"]):
            x, y = float(match.group(1)), float(match.group(2))
            positions.append((x, y))
            position_records.append(
                {"line": dialogue["line"], "style": dialogue["style"], "x": x, "y": y}
            )

    secondary_events = [
        dialogue for dialogue in dialogues if dialogue["style"] in secondary_style_names
    ]
    secondary_positions = [
        record for record in position_records if record["style"] in secondary_style_names
    ]
    secondary_without_position = [
        {"line": dialogue["line"], "style": dialogue["style"]}
        for dialogue in secondary_events
        if not any(record["line"] == dialogue["line"] for record in secondary_positions)
    ]
    secondary_bad_positions = [
        record
        for record in secondary_positions
        if not (160 <= record["x"] <= 1760 and 0 <= record["y"] <= 96)
    ]
    secondary_bad_layers = [
        {"line": dialogue["line"], "layer": dialogue["layer"]}
        for dialogue in secondary_events
        if dialogue["layer"] not in {7, 8}
    ]
    secondary_bad_inline_alignments = [
        {"line": dialogue["line"], "alignment": int(value)}
        for dialogue in secondary_events
        for value in re.findall(r"\\an(\d+)", dialogue["text"])
        if int(value) != 8
    ]
    if secondary_without_position:
        errors.append(f"secondary_events_missing_position: {secondary_without_position}")
    if secondary_bad_positions:
        errors.append(f"secondary_positions_outside_top_safe_area: {secondary_bad_positions}")
    if secondary_bad_layers:
        errors.append(f"secondary_events_not_on_dedicated_layers: {secondary_bad_layers}")
    if secondary_bad_inline_alignments:
        errors.append(
            "secondary_inline_alignment_not_top_center: "
            f"{secondary_bad_inline_alignments}"
        )

    def position_is_allowed(record: dict[str, Any]) -> bool:
        if record["style"] in secondary_style_names:
            return 160 <= record["x"] <= 1760 and 0 <= record["y"] <= 96
        if role_aware_layout:
            return 0 <= record["x"] <= 1920 and 450 <= record["y"] <= 1020
        return record["x"] >= 960 and record["y"] >= 500

    bad_positions = [
        (record["x"], record["y"])
        for record in position_records
        if not position_is_allowed(record)
    ]
    if bad_positions:
        error_name = (
            "explicit_positions_outside_layout_bounds"
            if role_aware_layout
            else "explicit_positions_not_right_middle_or_lower"
        )
        errors.append(f"{error_name}: {bad_positions}")
    if dialogue_count == 0:
        errors.append("no_dialogue_events")
    undefined_dialogue_styles = sorted(
        dialogue_style_names - style_names
    )
    if undefined_dialogue_styles:
        errors.append(f"dialogue_styles_not_defined: {undefined_dialogue_styles}")

    secondary_style_pair_ok = not secondary_declared or (
        secondary_style_names <= style_names
    )
    if secondary_declared and not secondary_style_pair_ok:
        errors.append(
            "secondary_styles_must_be_paired: "
            f"{sorted(secondary_style_names - style_names)}"
        )

    secondary_style_sizes_ok = not any(
        style["name"] in secondary_style_names
        and not 60.0 <= style["font_size"] <= 60.0
        for style in styles
    )
    secondary_style_alignment_ok = not any(
        style["name"] in secondary_style_names and style["alignment"] != 8
        for style in styles
    )
    secondary_inline_sizes_ok = not any(
        record["style"] in secondary_style_names for record in bad_inline_sizes
    )
    secondary_gate_ok = (
        secondary_style_pair_ok
        and secondary_style_sizes_ok
        and secondary_style_alignment_ok
        and secondary_inline_sizes_ok
        and not secondary_bad_positions
        and not secondary_bad_layers
        and not secondary_bad_inline_alignments
        and not secondary_without_position
    )

    styles_by_name = {style["name"]: style for style in styles}
    highlight_style_names = ("Main", "Glow", "CueHot")
    parsed_highlight_colors: dict[str, dict[str, str]] = {}
    highlight_color_errors: list[dict[str, Any]] = []
    for style_name in highlight_style_names:
        style = styles_by_name.get(style_name)
        if style is None:
            continue
        parsed = _parse_ass_color(style["primary_color"])
        if parsed is None:
            highlight_color_errors.append(
                {"style": style_name, "value": style["primary_color"]}
            )
        else:
            parsed_highlight_colors[style_name] = parsed
    cue_hot_secondary = None
    cue_hot_style = styles_by_name.get("CueHot")
    if cue_hot_style is not None:
        cue_hot_secondary = _parse_ass_color(cue_hot_style["secondary_color"])
        if cue_hot_secondary is None:
            highlight_color_errors.append(
                {"style": "CueHot.SecondaryColour", "value": cue_hot_style["secondary_color"]}
            )
    highlight_color_consistent = True
    if role_aware_layout:
        highlight_color_consistent = (
            len(parsed_highlight_colors) == len(highlight_style_names)
            and not highlight_color_errors
            and len(
                {parsed["bgr"] for parsed in parsed_highlight_colors.values()}
            )
            == 1
            and cue_hot_secondary is not None
            and cue_hot_secondary["bgr"]
            == parsed_highlight_colors.get("CueHot", {}).get("bgr")
        )
    if role_aware_layout and highlight_color_errors:
        errors.append(f"unreadable_highlight_colors: {highlight_color_errors}")
    if role_aware_layout and not highlight_color_consistent:
        errors.append(
            "highlight_colors_not_consistent: "
            f"{ {name: parsed.get('rgb') for name, parsed in parsed_highlight_colors.items()} }"
        )

    project_highlight_bgr = None
    project_highlight_bgr_values = {
        parsed["bgr"] for parsed in parsed_highlight_colors.values()
    }
    if (
        len(parsed_highlight_colors) == len(highlight_style_names)
        and len(project_highlight_bgr_values) == 1
        and highlight_color_consistent
    ):
        project_highlight_bgr = next(iter(project_highlight_bgr_values))

    singer_event_style_names = {
        "Main",
        "Glow",
        "CueHot",
        "Secondary",
        "SecondaryGlow",
    }
    inline_primary_pattern = re.compile(
        # Prefer the eight-digit AABBGGRR form before six-digit BBGGRR;
        # otherwise regex alternation truncates an eight-digit colour after
        # its first six digits.
        r"\\(?:1c|c)(&H(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6})&?)"
    )
    inline_secondary_pattern = re.compile(
        r"\\2c(&H(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6})&?)"
    )
    singer_event_colors: list[dict[str, Any]] = []
    for dialogue in dialogues:
        if dialogue["style"] not in singer_event_style_names:
            continue
        # Static outro/title events may reuse the lyric styles without a
        # singer assignment.  CueHot is itself singer-routed even though its
        # countdown dots intentionally carry no karaoke timing tag.
        if (
            dialogue["style"] != "CueHot"
            and re.search(r"\\k(?:f|o)?\d", dialogue["text"]) is None
        ):
            continue
        primary_match = inline_primary_pattern.search(dialogue["text"])
        secondary_match = inline_secondary_pattern.search(dialogue["text"])
        parsed = _parse_ass_color(primary_match.group(1)) if primary_match else None
        parsed_secondary = (
            _parse_ass_color(secondary_match.group(1)) if secondary_match else None
        )
        singer_event_colors.append(
            {
                "line": dialogue["line"],
                "style": dialogue["style"],
                "ass": parsed["ass"] if parsed else None,
                "rgb": parsed["rgb"] if parsed else None,
                "bgr": parsed["bgr"] if parsed else None,
                "secondary_bgr": (
                    parsed_secondary["bgr"] if parsed_secondary else None
                ),
                "inline": primary_match is not None,
                "inline_secondary": secondary_match is not None,
                "event_key": (
                    dialogue["start"],
                    dialogue["end"],
                    dialogue["name"],
                    re.sub(r"\{[^}]*\}", "", dialogue["text"]),
                ),
            }
        )
    inline_singer_color_mode = any(record["inline"] for record in singer_event_colors)
    invalid_inline_singer_colors = (
        [record for record in singer_event_colors if record["bgr"] is None]
        if inline_singer_color_mode
        else []
    )
    if invalid_inline_singer_colors:
        errors.append(
            "singer_events_missing_readable_inline_primary_color: "
            f"{invalid_inline_singer_colors}"
        )

    singer_event_color_mismatches: list[dict[str, Any]] = []
    if inline_singer_color_mode:
        records_by_style: dict[str, dict[tuple[str, str, str, str], list[dict[str, Any]]]] = {}
        for record in singer_event_colors:
            records_by_style.setdefault(record["style"], {}).setdefault(
                record["event_key"], []
            ).append(record)

        for main_style, glow_style in (
            ("Main", "Glow"),
            ("Secondary", "SecondaryGlow"),
        ):
            main_records = records_by_style.get(main_style, {})
            glow_records = records_by_style.get(glow_style, {})
            for event_key in sorted(set(main_records) | set(glow_records)):
                mains = main_records.get(event_key, [])
                glows = glow_records.get(event_key, [])
                if len(mains) != len(glows):
                    singer_event_color_mismatches.append(
                        {
                            "styles": [main_style, glow_style],
                            "event_key": event_key,
                            "reason": "unpaired_render_events",
                            "lines": [
                                [record["line"] for record in mains],
                                [record["line"] for record in glows],
                            ],
                        }
                    )
                    continue
                for main_record, glow_record in zip(mains, glows, strict=True):
                    if main_record["bgr"] != glow_record["bgr"]:
                        singer_event_color_mismatches.append(
                            {
                                "styles": [main_style, glow_style],
                                "event_key": event_key,
                                "reason": "paired_primary_colors_differ",
                                "lines": [main_record["line"], glow_record["line"]],
                                "bgr": [main_record["bgr"], glow_record["bgr"]],
                            }
                        )

        for record in singer_event_colors:
            style = record["style"]
            style_secondary = _parse_ass_color(
                styles_by_name.get(style, {}).get("secondary_color")
            )
            effective_secondary_bgr = (
                record["secondary_bgr"]
                or (style_secondary["bgr"] if style_secondary else None)
            )
            if style == "CueHot":
                if not record["inline_secondary"]:
                    singer_event_color_mismatches.append(
                        {
                            "style": style,
                            "line": record["line"],
                            "reason": "missing_inline_secondary_hot_color",
                        }
                    )
                elif record["bgr"] != record["secondary_bgr"]:
                    singer_event_color_mismatches.append(
                        {
                            "style": style,
                            "line": record["line"],
                            "reason": "cue_hot_primary_secondary_colors_differ",
                            "bgr": [record["bgr"], record["secondary_bgr"]],
                        }
                    )
            elif effective_secondary_bgr != "FFFFFF":
                singer_event_color_mismatches.append(
                    {
                        "style": style,
                        "line": record["line"],
                        "reason": "unhighlighted_color_must_be_white",
                        "bgr": effective_secondary_bgr,
                    }
                )
    if singer_event_color_mismatches:
        errors.append(
            "singer_event_colors_not_consistent: "
            f"{singer_event_color_mismatches}"
        )

    secondary_highlight_required = bool(secondary_events)
    parsed_secondary_highlight_colors: dict[str, dict[str, str]] = {}
    secondary_highlight_color_errors: list[dict[str, Any]] = []
    if secondary_highlight_required:
        for style_name in ("Secondary", "SecondaryGlow"):
            style = styles_by_name.get(style_name)
            parsed = _parse_ass_color(style["primary_color"]) if style else None
            if parsed is None:
                secondary_highlight_color_errors.append(
                    {
                        "style": style_name,
                        "value": style["primary_color"] if style else None,
                    }
                )
            else:
                parsed_secondary_highlight_colors[style_name] = parsed

    secondary_event_colors = [
        record
        for record in singer_event_colors
        if record["style"] in secondary_style_names
    ]
    secondary_highlight_is_white = secondary_highlight_required and (
        any(record["bgr"] == "FFFFFF" for record in secondary_event_colors)
        if inline_singer_color_mode
        else any(
            parsed["bgr"] == "FFFFFF"
            for parsed in parsed_secondary_highlight_colors.values()
        )
    )
    global_secondary_color_consistent = (
        len(parsed_secondary_highlight_colors) == 2
        and not secondary_highlight_color_errors
        and len(
            {parsed["bgr"] for parsed in parsed_secondary_highlight_colors.values()}
        ) == 1
    )
    secondary_highlight_consistent = not secondary_highlight_required or (
        not secondary_highlight_is_white
        and (
            not invalid_inline_singer_colors
            if inline_singer_color_mode
            else global_secondary_color_consistent
        )
    )
    secondary_highlight_matches_project = (
        not secondary_highlight_required
        or (
            secondary_highlight_consistent
            and project_highlight_bgr is not None
            and all(
                parsed["bgr"] == project_highlight_bgr
                for parsed in parsed_secondary_highlight_colors.values()
            )
        )
    )
    if secondary_highlight_color_errors:
        errors.append(
            "unreadable_secondary_highlight_colors: "
            f"{secondary_highlight_color_errors}"
        )
    if secondary_highlight_is_white:
        errors.append("secondary_hot_highlight_must_not_be_white")
    if (
        secondary_highlight_required
        and not secondary_highlight_consistent
        and not invalid_inline_singer_colors
    ):
        errors.append(
            "secondary_highlight_colors_not_consistent: "
            f"{ {name: parsed['bgr'] for name, parsed in parsed_secondary_highlight_colors.items()} }"
        )

    natural_advance_violations: list[dict[str, Any]] = []
    if layout == "wide-bottom-en":
        for style_name in ("Main", "Glow"):
            style = styles_by_name.get(style_name)
            if style is None or style["scale_x"] != 100.0 or style["spacing"] != 0.0:
                natural_advance_violations.append(
                    {
                        "style": style_name,
                        "scale_x": style["scale_x"] if style else None,
                        "spacing": style["spacing"] if style else None,
                    }
                )
        natural_advance_violations.extend(
            record for record in inline_scale_x if record["value"] != 100.0
        )
    natural_advance_ok = not natural_advance_violations
    if natural_advance_violations:
        errors.append(
            "english_wide_requires_natural_advance: "
            f"{natural_advance_violations}"
        )

    english_main_events = [
        dialogue for dialogue in dialogues if dialogue["style"] in {"Main", "Glow"}
    ]
    english_bad_spacing_scope = [
        record for record in letter_spacing if record["style"] not in {"Main", "Glow"}
    ]
    english_spacing_ok = True
    if layout == "wide-bottom-en":
        english_spacing_ok = bool(english_main_events) and not (
            bad_letter_spacing or english_bad_spacing_scope
        )
        if bad_letter_spacing:
            errors.append(f"english_wide_letter_spacing_negative: {bad_letter_spacing}")
        if english_bad_spacing_scope:
            errors.append(
                "english_wide_letter_spacing_outside_main_glyphs: "
                f"{english_bad_spacing_scope}"
            )
        if not english_main_events:
            errors.append("english_wide_has_no_main_glyph_events")
    elif bad_letter_spacing:
        errors.append(f"letter_spacing_negative: {bad_letter_spacing}")

    highlight_color = None
    highlight_color_ass = None
    if "Main" in parsed_highlight_colors:
        highlight_color = parsed_highlight_colors["Main"]["rgb"]
        highlight_color_ass = parsed_highlight_colors["Main"]["ass"]
    start_times = []
    for dialogue in dialogues:
        with contextlib.suppress(ValueError, IndexError):
            start_times.append(_ass_time_seconds(dialogue["start"]))
    font_size_profile_ok = not bad_sizes and not bad_inline_sizes and not missing_dynamic_sizes
    letter_spacing_ok = english_spacing_ok and not bad_letter_spacing
    return {
        "ok": not errors,
        "path": str(ass_path),
        "errors": errors,
        "forbidden_tokens": forbidden_tokens,
        "font_family_required": font_family,
        "layout": layout,
        "font_families_seen": sorted({style["font_family"] for style in styles}),
        "font_sizes": sorted({style["font_size"] for style in styles}),
        "inline_font_sizes": inline_font_sizes,
        "alignments": sorted({style["alignment"] for style in styles}),
        "positions": positions,
        "dialogue_count": dialogue_count,
        "first_dialogue_start_seconds": min(start_times) if start_times else None,
        "highlight_color": highlight_color,
        "highlight_color_ass": highlight_color_ass,
        "highlight_colors": {
            name: parsed["rgb"] for name, parsed in parsed_highlight_colors.items()
        },
        "letter_spacing": {
            "required": False,
            "values": sorted({record["value"] for record in letter_spacing}),
            "positive": bool(letter_spacing)
            and all(record["value"] > 0 for record in letter_spacing),
            "non_negative": letter_spacing_ok,
            "scope": (
                "english-word-internal-main-glyphs"
                if layout == "wide-bottom-en" and letter_spacing
                else "natural-font-advance"
                if layout == "wide-bottom-en"
                else "none"
            ),
        },
        "natural_advance": {
            "required": layout == "wide-bottom-en",
            "ok": natural_advance_ok,
            "violations": natural_advance_violations,
        },
        "secondary": {
            "present": secondary_declared,
            "style_pair": secondary_style_pair_ok,
            "event_count": len(secondary_events),
            "font_sizes": sorted(
                {
                    record["value"]
                    for record in inline_font_sizes
                    if record["style"] in secondary_style_names
                }
            ),
            "positions": [
                (record["x"], record["y"]) for record in secondary_positions
            ],
            "top_safe_area": {
                "left_px": 160,
                "right_px": 1760,
                "top_px": 0,
                "bottom_px": 96,
            },
            "excluded_from_main_lane_phase": True,
            "excluded_from_main_cue_pairing": True,
            "excluded_from_ruby": True,
            "highlight_color_required": secondary_highlight_required,
            "highlight_colors": {
                name: parsed["rgb"]
                for name, parsed in parsed_secondary_highlight_colors.items()
            },
            "highlight_color_consistency": secondary_highlight_consistent,
            "matches_main_highlight": secondary_highlight_matches_project,
            "inline_singer_color_mode": inline_singer_color_mode,
            "event_highlight_colors": secondary_event_colors,
        },
        "gate": {
            "no_forbidden_karaoke_tokens": not forbidden_tokens,
            "harmonyos_sans_sc": not wrong_fonts and not bad_inline_fonts,
            "font_size_profile": font_size_profile_ok,
            "inline_font_size_profile": not bad_inline_sizes and not missing_dynamic_sizes,
            "layout_geometry": not bad_alignment and not bad_positions,
            "secondary_styles": secondary_gate_ok,
            "highlight_color_consistency": highlight_color_consistent,
            "secondary_highlight_color_consistency": secondary_highlight_consistent,
            "inline_singer_event_colors": not invalid_inline_singer_colors
            and not singer_event_color_mismatches,
            "letter_spacing": letter_spacing_ok,
            "natural_advance": natural_advance_ok,
        },
    }


def probe_libass_font(
    ffmpeg: Path,
    fonts_dir: Path,
    font_family: str,
    subtitle_filter: str = "subtitles",
    *,
    ass_path: Path | None = None,
) -> dict[str, Any]:
    """Verify libass resolves the requested family from the supplied directory.

    With ``ass_path`` the probe renders the actual lyric ASS and seeks to its
    first dialogue.  This is the publication gate used by render/inspect paths;
    the synthetic fallback remains available for artwork-only capability checks
    and compatibility with older callers.
    """

    if subtitle_filter not in {"subtitles", "ass"}:
        return {"ok": False, "reason": "no_ass_filter", "family": font_family}
    fonts_dir = fonts_dir.resolve()
    if not fonts_dir.is_dir():
        return {
            "ok": False,
            "reason": "fonts_dir_missing",
            "directory": str(fonts_dir),
            "family": font_family,
            "probe_kind": "real_lyrics" if ass_path is not None else "synthetic",
        }
    probe_kind = "real_lyrics" if ass_path is not None else "synthetic"
    probe_time = 0.0
    probe_duration = 0.2
    if ass_path is not None:
        ass_path = ass_path.resolve()
        try:
            ass_text = ass_path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            return {
                "ok": False,
                "reason": f"lyrics_ass_read_failed: {exc}",
                "directory": str(fonts_dir),
                "family": font_family,
                "ass_path": str(ass_path),
                "probe_kind": probe_kind,
            }
        probe_time = _first_ass_dialogue_time(ass_text) or 0.0
        if _first_ass_dialogue_time(ass_text) is None:
            return {
                "ok": False,
                "reason": "lyrics_ass_has_no_dialogue",
                "directory": str(fonts_dir),
                "family": font_family,
                "ass_path": str(ass_path),
                "probe_kind": probe_kind,
            }
        probe_duration = max(0.2, probe_time + 0.2)
    with tempfile.TemporaryDirectory(prefix="strange-uta-libass-") as temporary:
        if ass_path is None:
            ass_path = Path(temporary) / "font_probe.ass"
            ass_path.write_text(
                "[Script Info]\n"
                "ScriptType: v4.00+\n"
                "PlayResX: 64\n"
                "PlayResY: 64\n"
                "[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                f"Style: Default,{font_family},18,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,1,0,7,2,2,2,1\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:00.10,Default,,0,0,0,,font probe\n",
                encoding="utf-8",
            )
        subtitle = (
            f"{subtitle_filter}=filename='{escape_filter_path(ass_path)}'"
            f":fontsdir='{escape_filter_path(fonts_dir)}'"
            f":force_style='FontName={escape_filter_value(font_family)}'"
        )
        result = run_capture(
            ffmpeg,
            [
                "-hide_banner",
                "-loglevel",
                "debug",
                "-f",
                "lavfi",
                 "-i",
                 f"color=c=black:s=1920x1080:r=30:d={probe_duration:.3f}",
                 "-ss",
                 f"{probe_time:.3f}",
                 "-vf",
                 subtitle,
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
        )
        debug = result.stdout + "\n" + result.stderr
        fontselect_lines = [line.strip() for line in debug.splitlines() if "fontselect:" in line.lower()]
        normalized_family = normalize_font_name(font_family)
        selected_lines = [
            line for line in fontselect_lines
            if "->" in line and normalized_family in normalize_font_name(line.split("->", 1)[1])
        ]
        return {
            "ok": result.returncode == 0 and bool(selected_lines),
            "filter": subtitle_filter,
            "directory": str(fonts_dir),
            "family": font_family,
            "ass_path": str(ass_path) if probe_kind == "real_lyrics" else None,
            "probe_kind": probe_kind,
            "probe_time_seconds": probe_time,
            "returncode": result.returncode,
            "fontselect": selected_lines[-1] if selected_lines else (fontselect_lines[-1] if fontselect_lines else None),
            "stderr_tail": result.stderr[-1200:],
            "reason": None if result.returncode == 0 and selected_lines else "libass_did_not_select_requested_family",
        }


def _vinyl_filter(*, vinyl_motion: str, rotation_period: float) -> str:
    if vinyl_motion == "static":
        return "[1:v]format=rgba[vinyl]"
    if vinyl_motion == "rotate":
        return (
            "[1:v]format=rgba,"
            f"rotate=2*PI*t/{rotation_period:.6f}:ow=iw:oh=ih:"
            "fillcolor=black@0:bilinear=1[vinyl]"
        )
    raise ValueError(f"unsupported vinyl motion: {vinyl_motion}")


def burn_frame_probe(
    *,
    ffmpeg: Path,
    background_path: Path,
    vinyl_path: Path,
    ass_path: Path,
    output_path: Path,
    fonts_dir: Path,
    font_family: str,
    subtitle_filter: str,
    probe_time: float,
    vinyl_motion: str,
    rotation_period: float,
) -> dict[str, Any]:
    """Burn one real ASS frame for visual review before full-length encoding."""

    subtitle = subtitle_filter_expression(
        subtitle_filter=subtitle_filter,
        ass_path=ass_path,
        fonts_dir=fonts_dir,
        font_family=font_family,
    )
    filter_complex = (
        "[0:v]format=rgba[bg];"
        f"{_vinyl_filter(vinyl_motion=vinyl_motion, rotation_period=rotation_period)};"
        "[bg][vinyl]overlay=1030:110:format=auto[scene];"
        f"[scene]{subtitle}[v]"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_capture(
        ffmpeg,
        [
            "-hide_banner",
            "-y",
            "-loglevel",
            "debug",
            "-loop",
            "1",
            "-framerate",
            "30",
            "-i",
            str(background_path),
            "-loop",
            "1",
            "-framerate",
            "30",
            "-i",
            str(vinyl_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-ss",
            f"{probe_time:.3f}",
            "-frames:v",
            "1",
            "-f",
            "image2",
            str(output_path),
        ],
    )
    zoom_path = output_path.with_name(f"{output_path.stem}_zoom{output_path.suffix}")
    if result.returncode == 0 and output_path.exists() and Image is not None:
        with Image.open(output_path) as image:
            image.crop((900, 0, 1920, 1080)).save(zoom_path, format="PNG")
    fontselect_lines = [line.strip() for line in (result.stdout + "\n" + result.stderr).splitlines() if "fontselect:" in line.lower()]
    return {
        "ok": result.returncode == 0 and output_path.exists(),
        "path": str(output_path),
        "zoom_path": str(zoom_path) if zoom_path.exists() else None,
        "zoom_crop": {"left": 900, "top": 0, "right": 1920, "bottom": 1080},
        "probe_time_seconds": probe_time,
        "vinyl_motion": vinyl_motion,
        "rotation_period_seconds": (
            rotation_period if vinyl_motion == "rotate" else None
        ),
        "fontselect": fontselect_lines[-1] if fontselect_lines else None,
        "returncode": result.returncode,
        "stderr_tail": result.stderr[-1200:],
    }


def _video_codec_args(encoder: str) -> list[str]:
    if encoder == "av1_nvenc":
        return [
            "-c:v",
            encoder,
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
            "-lookahead",
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
    if encoder == "libaom-av1":
        return ["-c:v", encoder, "-crf", "30", "-b:v", "0", "-cpu-used", "4", "-row-mt", "1"]
    if encoder == "libx264":
        return ["-c:v", encoder, "-preset", "medium", "-crf", "19"]
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p5", "-cq", "19", "-b:v", "0"]
    if encoder in {"h264_qsv", "h264_amf"}:
        return ["-c:v", encoder, "-quality", "quality"]
    if encoder == "mpeg4":
        return ["-c:v", encoder, "-q:v", "3"]
    return ["-c:v", encoder]


def _audio_codec_args(encoder: str) -> list[str]:
    return [
        "-c:a",
        encoder,
        "-profile:a",
        "aac_low",
        "-b:a",
        "320k",
        "-ar",
        "44100",
        "-ac",
        "2",
    ]


def render_video(
    *,
    ffmpeg: Path,
    background_path: Path,
    vinyl_path: Path,
    audio_path: Path,
    ass_path: Path,
    output_path: Path,
    title: str,
    artist: str,
    duration: float,
    capabilities: dict[str, Any],
    fonts_dir: Path,
    font_family: str,
    libass_font_probe: dict[str, Any],
    rotation_period: float,
    vinyl_motion: str,
    force: bool,
    video_encoder: str | None = None,
) -> dict[str, Any]:
    subtitle_filter = capabilities.get("subtitle_filter_selected")
    if not subtitle_filter:
        raise RuntimeError("FFmpeg has neither subtitles nor ass/libass filter")
    if not ass_path.exists():
        raise WaitingForASSError(f"ASS does not exist: {ass_path}")
    if output_path.exists() and not force:
        raise RuntimeError(f"output exists; pass --force to replace it: {output_path}")

    available_h264 = list(capabilities.get("h264_encoders", []))
    available_av1 = list(capabilities.get("av1_encoders", []))
    if video_encoder is not None:
        if video_encoder not in {*available_h264, *available_av1, "mpeg4"}:
            raise RuntimeError(f"requested video encoder is unavailable: {video_encoder}")
        video_candidates = [video_encoder]
    else:
        video_candidates = available_h264 + ["mpeg4"]
    available_audio = list(capabilities.get("audio_encoders", []))
    audio_candidates = [
        name for name in ("aac", "aac_mf", "libfdk_aac") if name in available_audio
    ]
    if not audio_candidates:
        raise RuntimeError("formal MP4 delivery requires an AAC-LC encoder")

    if not libass_font_probe.get("ok") or libass_font_probe.get("probe_kind") != "real_lyrics":
        raise RuntimeError(
            "real lyric libass/font gate failed: "
            f"{libass_font_probe}"
        )
    subtitle = (
        f"{subtitle_filter}=filename='{escape_filter_path(ass_path)}'"
        f":fontsdir='{escape_filter_path(fonts_dir)}'"
        f":force_style='FontName={escape_filter_value(font_family)}'"
    )
    filter_complex = (
        "[0:v]format=rgba[bg];"
        f"{_vinyl_filter(vinyl_motion=vinyl_motion, rotation_period=rotation_period)};"
        "[bg][vinyl]overlay=1030:110:format=auto[scene];"
        f"[scene]{subtitle}[v]"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(f".{output_path.stem}.{os.getpid()}.partial{output_path.suffix}")
    attempts: list[dict[str, Any]] = []
    try:
        for video_encoder in video_candidates:
            for audio_encoder in audio_candidates:
                if partial.exists():
                    partial.unlink()
                command = [
                    str(ffmpeg),
                    "-hide_banner",
                    "-y",
                    "-loglevel",
                    "error",
                    "-loop",
                    "1",
                    "-framerate",
                    "30",
                    "-i",
                    str(background_path),
                    "-loop",
                    "1",
                    "-framerate",
                    "30",
                    "-i",
                    str(vinyl_path),
                    "-i",
                    str(audio_path),
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[v]",
                    "-map",
                    "2:a:0",
                    "-t",
                    f"{duration:.6f}",
                    "-r",
                    "30",
                    "-fps_mode",
                    "cfr",
                    *_video_codec_args(video_encoder),
                    "-pix_fmt",
                    "yuv420p",
                    *_audio_codec_args(audio_encoder),
                    "-metadata",
                    f"title={title}",
                    "-metadata",
                    f"artist={artist}",
                    "-metadata",
                    "comment=StrangeUtaGame vinyl karaoke render",
                    "-movflags",
                    "+faststart",
                    str(partial),
                ]
                result = run_capture(ffmpeg, command[1:])
                attempt = {
                    "video_encoder": video_encoder,
                    "audio_encoder": audio_encoder,
                    "returncode": result.returncode,
                    "stderr_tail": result.stderr[-1200:],
                }
                attempts.append(attempt)
                if result.returncode == 0 and partial.exists() and partial.stat().st_size > 0:
                    if output_path.exists():
                        output_path.unlink()
                    os.replace(partial, output_path)
                    return {
                        "output": project_relative(output_path, REPO_ROOT),
                        "duration_target_seconds": duration,
                        "video_encoder": video_encoder,
                        "audio_encoder": audio_encoder,
                        "attempts": attempts,
                        "filter": subtitle_filter,
                        "fps": "30 CFR",
                        "pixel_format": "yuv420p",
                        "resolution": "1920x1080",
                        "fonts_dir": project_relative(fonts_dir, REPO_ROOT),
                        "font_family": font_family,
                        "libass_font_probe": libass_font_probe,
                        "vinyl_motion": vinyl_motion,
                        "rotation_period_seconds": (
                            rotation_period if vinyl_motion == "rotate" else None
                        ),
                    }
        raise RuntimeError("all video/audio encoder attempts failed; see attempts in command output")
    finally:
        if partial.exists():
            partial.unlink()


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    track_group = parser.add_mutually_exclusive_group()
    track_group.add_argument(
        "--all-tracks",
        dest="all_tracks",
        action="store_true",
        help="build/render the complete manifest track collection (default)",
    )
    track_group.add_argument(
        "--single-track",
        dest="all_tracks",
        action="store_false",
        help="render one explicitly selected track",
    )
    parser.set_defaults(all_tracks=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="explicit album.json manifest that owns the track collection",
    )
    parser.add_argument(
        "--allow-partial-manifest",
        action="store_true",
        help="allow an explicitly supplied manifest with fewer than five tracks",
    )
    parser.add_argument(
        "--track",
        dest="song_id",
        help="manifest song_id required with --single-track",
    )
    parser.add_argument(
        "--slug",
        help="optional deliverable directory override; defaults to the manifest",
    )
    parser.add_argument("--timing-dir", type=Path, help="ASS search directory; no files are written here")
    parser.add_argument("--ass", type=Path, help="explicit ASS input; no ASS is generated")
    parser.add_argument("--artwork-dir", type=Path, help="defaults to deliverables/<slug>/artwork; --all-tracks adds one subdirectory per track")
    parser.add_argument("--output", type=Path, help="single-track output; --all-tracks uses video/<track>.mp4")
    parser.add_argument(
        "--fonts-dir",
        type=Path,
        default=SHARED_FONT_DIR,
        help="HarmonyOS Sans directory used by Pillow and libass; defaults to assets/fonts/HarmonyOS-Sans",
    )
    parser.add_argument(
        "--cover-url",
        default="",
        help="explicit network fallback URL; embedded cover remains preferred",
    )
    parser.add_argument("--no-network", action="store_true", help="fail instead of fetching a missing cover")
    parser.add_argument("--ffmpeg", type=Path, help="override imageio-ffmpeg binary")
    parser.add_argument("--rotation-period", type=float, default=8.0, help="seconds per vinyl revolution")
    parser.add_argument(
        "--vinyl-motion",
        choices=("static", "rotate"),
        default="rotate",
        help="keep the vinyl still or rotate it (default: rotate)",
    )
    parser.add_argument("--artwork-only", action="store_true", help="build artwork and stop before ASS/render checks")
    parser.add_argument("--frame-probe-only", action="store_true", help="burn review frames after ASS/font gates, but do not render full videos")
    parser.add_argument("--force", action="store_true", help="replace an existing output video")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    album = load_album_manifest(
        args.manifest,
        require_five_tracks=not args.allow_partial_manifest,
    )
    if args.vinyl_motion == "rotate" and args.rotation_period <= 0:
        print("ERROR: --rotation-period must be positive", file=sys.stderr)
        return 1
    if args.all_tracks and args.ass:
        print("ERROR: --ass is single-track only; let --all-tracks discover one ASS per track", file=sys.stderr)
        return 1

    slug_override = args.slug
    if slug_override:
        deliverable_root = (REPO_ROOT / "deliverables" / slug_override).resolve()
    else:
        deliverable_root = album.deliverable_dir
    args.slug = slug_override or album.title
    artwork_root = (args.artwork_dir or deliverable_root / "artwork").resolve()
    fonts_dir = args.fonts_dir.resolve()
    video_root = (deliverable_root / "video").resolve()
    args.timing_dir = (
        args.timing_dir or album.deliverable_dir / "timing"
    ).resolve()
    if args.all_tracks:
        track_specs = [track_dict(track) for track in album.tracks]
    else:
        matches = [track for track in album.tracks if track.song_id == args.song_id]
        if len(matches) != 1:
            print(
                "ERROR: --single-track requires exactly one explicit --track song_id",
                file=sys.stderr,
            )
            return 1
        track_specs = [track_dict(matches[0])]
    if not args.all_tracks and len(track_specs) != 1:
        print("ERROR: --single-track must create exactly one track task", file=sys.stderr)
        return 1
    for track in track_specs:
        track["audio"] = Path(track["audio"]).expanduser().resolve()
        if not track["audio"].exists():
            print(f"ERROR: source audio does not exist: {track['audio']}", file=sys.stderr)
            return 1

    try:
        font_info = inspect_font_dir(fonts_dir)
        built_tracks: list[dict[str, Any]] = []
        for track in track_specs:
            artwork_dir = artwork_root / track["basename"] if args.all_tracks else artwork_root
            metadata = build_artwork(
                track["audio"],
                artwork_dir,
                track["title"],
                track["artist"],
                args.cover_url,
                fonts_dir,
                allow_network=bool(args.cover_url) and not args.no_network,
                album_title=album.title,
                album_artist=album.artist,
            )
            built_tracks.append({
                **track,
                "artwork_dir": artwork_dir,
                "duration": audio_duration(track["audio"]),
                "artwork": metadata,
            })
        if args.artwork_only:
            print(json.dumps({
                "status": "artwork_ready",
                "tracks": [
                    {"title": track["title"], "artist": track["artist"], "duration_seconds": track["duration"], "artwork": track["artwork"]}
                    for track in built_tracks
                ],
            }, ensure_ascii=False, indent=2))
            return 0

        ffmpeg = (args.ffmpeg or default_ffmpeg()).resolve()
        capabilities = probe_ffmpeg_capabilities(ffmpeg)
        libass_font_probe = probe_libass_font(
            ffmpeg,
            fonts_dir,
            font_info["family"],
            capabilities.get("subtitle_filter_selected") or "subtitles",
        )
        if not libass_font_probe.get("ok"):
            raise RuntimeError(f"libass did not load the requested HarmonyOS Sans family: {libass_font_probe}")
        results: list[dict[str, Any]] = []
        ready_tracks: list[dict[str, Any]] = []
        for track in built_tracks:
            audio_path = track["audio"]
            ass_path = find_ass(REPO_ROOT, args.slug, audio_path, track["title"], args.timing_dir)
            if ass_path is None:
                candidates = ass_candidates(REPO_ROOT, args.slug, audio_path, track["title"], args.timing_dir)
                results.append({
                    "status": "waiting_for_ass",
                    "title": track["title"],
                    "artist": track["artist"],
                    "audio": str(audio_path),
                    "duration_seconds": track["duration"],
                    "searched_candidates": [str(path) for path in candidates],
                })
                continue
            ass_gate = validate_ass_for_render(ass_path, font_info["family"])
            if not ass_gate["ok"]:
                results.append({
                    "status": "waiting_for_burn_ready_ass",
                    "title": track["title"],
                    "artist": track["artist"],
                    "audio": str(audio_path),
                    "ass": str(ass_path),
                    "duration_seconds": track["duration"],
                    "ass_gate": ass_gate,
                })
                continue
            track_libass_font_probe = probe_libass_font(
                ffmpeg,
                fonts_dir,
                font_info["family"],
                capabilities.get("subtitle_filter_selected") or "subtitles",
                ass_path=ass_path,
            )
            if not track_libass_font_probe.get("ok"):
                results.append({
                    "status": "waiting_for_libass_font",
                    "title": track["title"],
                    "artist": track["artist"],
                    "audio": str(audio_path),
                    "ass": str(ass_path),
                    "duration_seconds": track["duration"],
                    "ass_gate": ass_gate,
                    "libass_font_probe": track_libass_font_probe,
                })
                continue
            ready_tracks.append({
                **track,
                "ass": ass_path,
                "ass_gate": ass_gate,
                "libass_font_probe": track_libass_font_probe,
            })
        blocked = [result for result in results if result["status"] != "ass_ready"]
        if blocked:
            if any(result["status"] == "waiting_for_ass" for result in blocked):
                blocked_status = "waiting_for_ass"
            elif any(
                result["status"] == "waiting_for_libass_font" for result in blocked
            ):
                blocked_status = "waiting_for_libass_font"
            else:
                blocked_status = "waiting_for_burn_ready_ass"
            if args.all_tracks:
                print(json.dumps({
                    "status": blocked_status,
                    "tracks": results,
                    "capabilities": capabilities,
                    "font": font_info,
                    "libass_font_probe": libass_font_probe,
                }, ensure_ascii=False, indent=2))
                return 2
            raise WaitingForASSError(
                "burn-ready ASS gate is blocked; waiting for binbu timing output. "
                f"details={blocked[0]}"
            )

        # No full video is attempted until every track passes the ASS gate and
        # has a real burned frame available for visual review.
        for track in ready_tracks:
            probe_time = track["ass_gate"]["first_dialogue_start_seconds"]
            frame_path = track["artwork_dir"] / "frame_probe.png"
            frame_probe = burn_frame_probe(
                ffmpeg=ffmpeg,
                background_path=track["artwork_dir"] / "composition.png",
                vinyl_path=track["artwork_dir"] / "vinyl.png",
                ass_path=track["ass"],
                output_path=frame_path,
                fonts_dir=fonts_dir,
                font_family=font_info["family"],
                subtitle_filter=capabilities["subtitle_filter_selected"],
                probe_time=max(0.0, float(probe_time or 0.0) + 0.05),
                vinyl_motion=args.vinyl_motion,
                rotation_period=args.rotation_period,
            )
            if not frame_probe["ok"]:
                raise RuntimeError(f"ASS frame probe failed for {track['title']}: {frame_probe}")
            track["frame_probe"] = frame_probe
        if args.frame_probe_only:
            print(json.dumps({
                "status": "frame_probes_ready",
                "tracks": [
                    {
                        "title": track["title"],
                        "artist": track["artist"],
                        "ass": str(track["ass"]),
                        "ass_gate": track["ass_gate"],
                        "frame_probe": track["frame_probe"],
                    }
                    for track in ready_tracks
                ],
                "font": font_info,
                "libass_font_probe": libass_font_probe,
            }, ensure_ascii=False, indent=2))
            return 0

        for track in ready_tracks:
            audio_path = track["audio"]
            ass_path = track["ass"]
            output_path = (args.output.resolve() if args.output and not args.all_tracks else video_root / f"{track['basename']}.mp4")
            result = render_video(
                ffmpeg=ffmpeg,
                background_path=track["artwork_dir"] / "composition.png",
                vinyl_path=track["artwork_dir"] / "vinyl.png",
                audio_path=audio_path,
                ass_path=ass_path,
                output_path=output_path,
                title=track["title"],
                artist=track["artist"],
                duration=track["duration"],
                capabilities=capabilities,
                fonts_dir=fonts_dir,
                font_family=font_info["family"],
                libass_font_probe=track["libass_font_probe"],
                rotation_period=args.rotation_period,
                vinyl_motion=args.vinyl_motion,
                force=args.force,
            )
            results.append({
                "status": "rendered",
                "title": track["title"],
                "artist": track["artist"],
                "audio": str(audio_path),
                "ass": str(ass_path),
                "ass_gate": track["ass_gate"],
                "frame_probe": track["frame_probe"],
                "artwork": track["artwork"],
                **result,
            })
        print(json.dumps({
            "status": "rendered",
            "tracks": results,
            "capabilities": capabilities,
        }, ensure_ascii=False, indent=2))
        return 0
    except WaitingForASSError as exc:
        print(f"WAITING_FOR_ASS: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # CLI should report a recoverable build failure cleanly.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
