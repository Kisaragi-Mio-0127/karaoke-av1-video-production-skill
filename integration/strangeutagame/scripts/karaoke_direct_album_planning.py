#!/usr/bin/env python3
"""Language- and codec-neutral planning for direct track and album renderers."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

try:
    from .karaoke_album import AlbumManifest, AlbumTrack, load_album_manifest
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_album import (  # type: ignore[no-redef]
        AlbumManifest,
        AlbumTrack,
        load_album_manifest,
    )

try:
    from .karaoke_common.ffmpeg_tools import resolve_ffmpeg
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_common.ffmpeg_tools import resolve_ffmpeg  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("standard", "wide")
VISUAL_STYLES = (
    "vinyl",
    "spectrum",
    "spectrum-line",
    "spectrum-mirror",
    "spectrum-dots",
    "spectrum-ribbon",
)
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

__all__ = [
    "RenderTask",
    "default_ffmpeg",
    "find_latest_ass",
    "find_latest_sug",
    "load_album_manifest",
    "plan_tasks",
    "resolve_font_paths",
    "resolve_path",
    "select_profiles",
    "select_visual_styles",
    "select_tracks",
]


class RenderTask:
    """Shared source and publication paths for one song/profile render."""

    def __init__(
        self,
        *,
        album: AlbumManifest,
        root: Path,
        track: AlbumTrack,
        profile: str,
        visual_style: str = "vinyl",
        sug_path: Path,
        ass_source: Path | None,
        composition_path: Path,
        vinyl_path: Path | None,
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
        self.visual_style = visual_style
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
        if self.visual_style == "vinyl":
            return self.track.artifact_slug
        return f"{self.track.artifact_slug}_{self.visual_style}"


def resolve_path(value: Path | str, *, base: Path = REPO_ROOT) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def default_ffmpeg() -> Path:
    """Resolve the preferred FFmpeg executable."""

    return resolve_ffmpeg(root=REPO_ROOT)


def _validate_ass_file(path: Path, profile: str) -> dict[str, Any]:
    """Compatibility ASS structure gate shared by direct renderer lanes."""

    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception as error:  # pragma: no cover - result-gate failure
        return {"ok": False, "path": str(path), "errors": [f"read_failed: {error}"]}
    errors: list[str] = []
    expected_layout = PROFILE_LAYOUTS[profile]
    if f"; Layout: {expected_layout}" not in text:
        errors.append(f"layout_mismatch: expected {expected_layout}")
    if not re.search(r"(?im)^PlayResX:\\s*1920\\s*$", text):
        errors.append("missing_PlayResX_1920")
    if not re.search(r"(?im)^PlayResY:\\s*1080\\s*$", text):
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


def _split_selectors(values: Iterable[str] | None) -> list[str]:
    selectors: list[str] = []
    for value in values or ():
        selectors.extend(item.strip() for item in str(value).split(",") if item.strip())
    return selectors


def select_tracks(
    album: AlbumManifest,
    selectors: Iterable[str] | None = None,
) -> tuple[AlbumTrack, ...]:
    """Select tracks by song id, title, artifact slug, timing stem, or audio stem."""

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
        if matches:
            selected_ids.update(str(track.song_id) for track in matches)
        else:
            unknown.append(selector)
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


def select_visual_styles(value: str | None) -> tuple[str, ...]:
    """Expand the batch visual-style selector in stable render order."""

    selected = value or "vinyl"
    if selected == "both":
        return ("vinyl", "spectrum")
    if selected == "all":
        return VISUAL_STYLES
    if selected not in VISUAL_STYLES:
        raise ValueError(
            "unknown --visual-style value: "
            f"{selected}; expected vinyl, spectrum, spectrum-line, "
            "spectrum-mirror, spectrum-dots, spectrum-ribbon, both, or all"
        )
    return (selected,)


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
    return [
        path.resolve()
        for path in directory.rglob(f"*{suffix}")
        if path.is_file()
        and not _is_temporary_name(path)
        and any(token in path.stem.casefold() for token in tokens)
    ]


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
    result = _latest_file(
        _matching_files(timing_dir, track, ".sug"),
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
    candidates = _matching_files(timing_dir, track, ".ass")
    profile_dir = (timing_dir / profile).resolve()
    profile_candidates = [
        path
        for path in candidates
        if profile_dir == path.parent or profile_dir in path.parents
    ]
    if profile_candidates:
        candidates = profile_candidates
    return _latest_file(candidates, exact_names=(f"{track.timing_stem}.ass",))


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
    """Resolve the shared font directory and the measurement font."""

    del root  # Reserved for callers with project-relative font policies.
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
    visual_style: str = "vinyl",
) -> tuple[Path, Path | None]:
    track_artwork = artwork_root / track.artifact_slug
    profile_artwork = (
        artwork_root / "wide" / track.artifact_slug
        if profile == "wide"
        else track_artwork
    )
    if visual_style in {
        "spectrum",
        "spectrum-line",
        "spectrum-mirror",
        "spectrum-dots",
        "spectrum-ribbon",
    }:
        suffix = visual_style
        preferred = (
            artwork_root / f"wide-{suffix}" / track.artifact_slug / "composition.png"
            if profile == "wide"
            else artwork_root / suffix / track.artifact_slug / "composition.png"
        )
        fallback = profile_artwork / f"composition_{suffix.replace('-', '_')}.png"
        composition = preferred if preferred.is_file() else fallback
        return composition.resolve(), None
    if visual_style != "vinyl":
        raise ValueError(f"unsupported visual style: {visual_style}")
    composition = profile_artwork / "composition.png"
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
    visual_styles: Sequence[str] = ("vinyl",),
    timing_dir: Path | None = None,
    artwork_root: Path | None = None,
    fonts_dir: Path | None = None,
    font_file: Path | None = None,
    duration_seconds: float | None = None,
) -> tuple[RenderTask, ...]:
    """Build and validate codec-neutral direct-render task inputs."""

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
        for visual_style in visual_styles:
            if visual_style not in VISUAL_STYLES:
                raise ValueError(f"unsupported visual style: {visual_style}")
            for track in tracks:
                sug_path = find_latest_sug(timing_root, track)
                ass_source = find_latest_ass(timing_root, track, profile)
                composition_path, vinyl_path = _artwork_paths(
                    artwork_root, track, profile, visual_style
                )
                required_paths = [track.audio_path, sug_path, composition_path]
                if vinyl_path is not None:
                    required_paths.append(vinyl_path)
                missing.extend(str(path) for path in required_paths if not path.is_file())
                validation_root = root / "validation" / profile
                tasks.append(
                    RenderTask(
                        album=album,
                        root=root,
                        track=track,
                        profile=profile,
                        visual_style=visual_style,
                        sug_path=sug_path,
                        ass_source=ass_source,
                        composition_path=composition_path,
                        vinyl_path=vinyl_path,
                        fonts_dir=resolved_fonts_dir,
                        font_file=resolved_font_file,
                        ass_output=(
                            timing_root / profile / f"{track.timing_stem}.ass"
                        ).resolve(),
                        video_output=(
                            root
                            / "video"
                            / "hevc444"
                            / profile
                            / f"{track.artifact_slug}.mp4"
                        ).resolve(),
                        direct_report=(
                            validation_root
                            / f"{track.artifact_slug}_direct_hevc444_render_report.json"
                        ).resolve(),
                        ass_report=(
                            validation_root / f"{track.artifact_slug}_ass_report.json"
                        ).resolve(),
                        duration_seconds=(
                            float(duration_seconds)
                            if duration_seconds is not None
                            else track.expected_duration_ms / 1000.0
                        ),
                    )
                )
    if missing:
        raise FileNotFoundError(
            "missing direct-render inputs:\n"
            + "\n".join(dict.fromkeys(missing))
        )
    return tuple(tasks)
