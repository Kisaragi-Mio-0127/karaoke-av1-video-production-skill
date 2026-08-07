"""Shared album manifest loading for the karaoke build scripts.

The karaoke pipeline receives track metadata from an explicitly selected album
manifest.  This module owns the small amount of path and validation logic
needed by the timing, media and release stages so those stages cannot silently
drift back to a hand-maintained subset of tracks.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .karaoke_language import DEFAULT_LANGUAGE, normalize_language
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_language import (  # type: ignore[no-redef]
        DEFAULT_LANGUAGE,
        normalize_language,
    )

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TRACK_COUNT = 5
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_WINDOWS_DELIVERY_TRANSLATION = str.maketrans(
    {
        "?": "？",
        ":": "：",
        "*": "＊",
        '"': "＂",
        "<": "＜",
        ">": "＞",
        "|": "｜",
        "/": "／",
        "\\": "＼",
    }
)


class AlbumManifestError(ValueError):
    """Raised when an album manifest cannot be used by the pipeline."""


def delivery_display_title(track: Any) -> str:
    """Return one Windows-safe display title shared by folders and archives."""

    if isinstance(track, Mapping):
        raw_title = track.get("display_title") or track.get("title")
    else:
        raw_title = getattr(track, "display_title", None) or getattr(track, "title", None)
    title = str(raw_title or "").translate(_WINDOWS_DELIVERY_TRANSLATION).rstrip(" .")
    if not title:
        raise AlbumManifestError("track delivery title must not be empty")
    return title


def numbered_video_filename(track: Any) -> str:
    """Return the canonical numbered MP4 filename for a delivery track."""

    number = track.get("track_number") if isinstance(track, Mapping) else track.track_number
    return f"{int(number):02d} {delivery_display_title(track)}.mp4"


@dataclass(frozen=True)
class AlbumTrack:
    """A validated track record with paths resolved from its manifest."""

    disc: int
    track_number: int
    song_id: str
    title: str
    artist: str
    artifact_slug: str
    audio_file: str
    audio_sha256: str
    expected_duration_ms: int
    expected_cues: int | None
    manifest_path: Path
    audio_directory: str
    deliverable_directory: str
    language: str = DEFAULT_LANGUAGE

    @property
    def manifest_dir(self) -> Path:
        return self.manifest_path.parent

    @property
    def audio_path(self) -> Path:
        return (self.manifest_dir / self.audio_directory / self.audio_file).resolve()

    @property
    def deliverable_dir(self) -> Path:
        return (self.manifest_dir / self.deliverable_directory).resolve()

    @property
    def timing_stem(self) -> str:
        return f"{self.song_id}_{self.artifact_slug}"

    @property
    def report_stem(self) -> str:
        return self.artifact_slug

    @property
    def numbered_video_filename(self) -> str:
        return numbered_video_filename(self)

    @property
    def audio_name(self) -> str:
        """Compatibility alias used by the timing/source report code."""

        return self.audio_file

    def as_dict(self) -> dict[str, Any]:
        """Return the manifest-shaped record without resolved local paths."""

        return {
            "disc": self.disc,
            "track_number": self.track_number,
            "song_id": self.song_id,
            "title": self.title,
            "artist": self.artist,
            "artifact_slug": self.artifact_slug,
            "audio_file": self.audio_file,
            "audio_sha256": self.audio_sha256,
            "expected_duration_ms": self.expected_duration_ms,
            "expected_cues": self.expected_cues,
            "language": self.language,
        }

@dataclass(frozen=True)
class AlbumManifest:
    """Validated album metadata and its track collection."""

    path: Path
    album: dict[str, Any]
    paths: dict[str, str]
    tracks: tuple[AlbumTrack, ...]

    @property
    def project_root(self) -> Path:
        """Infer the repository root while keeping custom manifests portable."""

        manifest_dir = self.path.parent
        for candidate in (manifest_dir, *manifest_dir.parents):
            if (candidate / "src").is_dir() and (candidate / "scripts").is_dir():
                return candidate.resolve()
        return manifest_dir.resolve()

    @property
    def source_dir(self) -> Path:
        return self.path.parent.resolve()

    @property
    def audio_dir(self) -> Path:
        return (self.source_dir / self.paths["audio_directory"]).resolve()

    @property
    def font_package(self) -> Path:
        return (self.source_dir / self.paths["font_package"]).resolve()

    @property
    def deliverable_dir(self) -> Path:
        return (self.source_dir / self.paths["deliverable_directory"]).resolve()

    @property
    def title(self) -> str:
        return str(self.album["title"])

    @property
    def artist(self) -> str:
        return str(self.album["artist"])

    @property
    def catalog_number(self) -> str:
        return str(self.album.get("catalog_number", ""))


def _as_non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AlbumManifestError(f"{field} must be a non-empty string")
    return value


def _as_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AlbumManifestError(f"{field} must be a positive integer")
    return value


def _relative_manifest_path(
    value: Any, field: str, *, allow_parent: bool = False
) -> str:
    path = _as_non_empty_string(value, field).replace("\\", "/")
    candidate = Path(path)
    if candidate.is_absolute() or (not allow_parent and ".." in candidate.parts):
        raise AlbumManifestError(f"{field} must stay relative to album.json: {value!r}")
    return path


def _validate_paths(raw_paths: Any) -> dict[str, str]:
    if not isinstance(raw_paths, dict):
        raise AlbumManifestError("paths must be an object")
    required = ("audio_directory", "font_package", "deliverable_directory")
    return {
        field: _relative_manifest_path(
            raw_paths.get(field), f"paths.{field}", allow_parent=True
        )
        for field in required
    }


def _validate_track(
    raw: Any,
    manifest_path: Path,
    index: int,
    paths: dict[str, str],
) -> AlbumTrack:
    if not isinstance(raw, dict):
        raise AlbumManifestError(f"tracks[{index}] must be an object")
    prefix = f"tracks[{index}]"
    disc = _as_positive_int(raw.get("disc"), f"{prefix}.disc")
    track_number = _as_positive_int(raw.get("track_number"), f"{prefix}.track_number")
    song_id = _as_non_empty_string(raw.get("song_id"), f"{prefix}.song_id")
    title = _as_non_empty_string(raw.get("title"), f"{prefix}.title")
    artist = _as_non_empty_string(raw.get("artist"), f"{prefix}.artist")
    artifact_slug = _relative_manifest_path(raw.get("artifact_slug"), f"{prefix}.artifact_slug")
    audio_file = _relative_manifest_path(raw.get("audio_file"), f"{prefix}.audio_file")
    audio_sha256 = _as_non_empty_string(raw.get("audio_sha256"), f"{prefix}.audio_sha256")
    if not _SHA256_RE.fullmatch(audio_sha256):
        raise AlbumManifestError(f"{prefix}.audio_sha256 must be a 64-character hex digest")
    expected_duration_ms = _as_positive_int(
        raw.get("expected_duration_ms"), f"{prefix}.expected_duration_ms"
    )
    expected_cues = raw.get("expected_cues")
    if expected_cues is not None and (
        isinstance(expected_cues, bool)
        or not isinstance(expected_cues, int)
        or expected_cues < 0
    ):
        raise AlbumManifestError(f"{prefix}.expected_cues must be a non-negative integer or null")
    try:
        language = normalize_language(raw.get("language"), default=DEFAULT_LANGUAGE)
    except ValueError as error:
        raise AlbumManifestError(f"{prefix}.language is invalid: {error}") from error
    return AlbumTrack(
        disc=disc,
        track_number=track_number,
        song_id=song_id,
        title=title,
        artist=artist,
        artifact_slug=artifact_slug,
        audio_file=audio_file,
        audio_sha256=audio_sha256,
        expected_duration_ms=expected_duration_ms,
        expected_cues=expected_cues,
        manifest_path=manifest_path,
        audio_directory=paths["audio_directory"],
        deliverable_directory=paths["deliverable_directory"],
        language=language,
    )


def validate_exact_five_track_collection(tracks: Iterable[AlbumTrack]) -> tuple[AlbumTrack, ...]:
    """Enforce the release's exact five-track collection contract."""

    collection = tuple(tracks)
    if len(collection) != EXPECTED_TRACK_COUNT:
        raise AlbumManifestError(
            f"album track collection must contain exactly {EXPECTED_TRACK_COUNT} tracks; "
            f"got {len(collection)}"
        )
    for field in ("song_id", "track_number", "artifact_slug", "audio_file"):
        values = [getattr(track, field) for track in collection]
        if len(values) != len(set(values)):
            raise AlbumManifestError(f"album track collection has duplicate {field}")
    track_numbers = sorted(track.track_number for track in collection)
    if track_numbers != list(range(1, EXPECTED_TRACK_COUNT + 1)):
        raise AlbumManifestError(
            "album track collection must have track_number values 1 through 5"
        )
    return tuple(sorted(collection, key=lambda track: (track.disc, track.track_number)))


# Short aliases make the gate easy to discover from scripts and tests.
ensure_exact_five_track_collection = validate_exact_five_track_collection
require_five_track_collection = validate_exact_five_track_collection


def load_album_manifest(
    path: Path | str,
    *,
    require_five_tracks: bool = False,
) -> AlbumManifest:
    """Load and validate an explicitly selected album manifest."""

    manifest_path = Path(path).expanduser().resolve()
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise AlbumManifestError(f"album manifest does not exist: {manifest_path}") from error
    except json.JSONDecodeError as error:
        raise AlbumManifestError(f"album manifest is not valid JSON: {manifest_path}") from error
    if not isinstance(document, dict):
        raise AlbumManifestError("album manifest root must be an object")
    if document.get("schema_version") != "karaoke-album/v1":
        raise AlbumManifestError("album manifest schema_version must be karaoke-album/v1")
    album = document.get("album")
    if not isinstance(album, dict):
        raise AlbumManifestError("album must be an object")
    _as_non_empty_string(album.get("title"), "album.title")
    _as_non_empty_string(album.get("artist"), "album.artist")
    paths = _validate_paths(document.get("paths"))
    raw_tracks = document.get("tracks")
    if not isinstance(raw_tracks, list):
        raise AlbumManifestError("tracks must be an array")
    tracks = tuple(
        _validate_track(raw_track, manifest_path, index, paths)
        for index, raw_track in enumerate(raw_tracks)
    )
    if require_five_tracks:
        tracks = validate_exact_five_track_collection(tracks)
    return AlbumManifest(path=manifest_path, album=album, paths=paths, tracks=tracks)


def project_relative(path: Path | str, project_root: Path | str = PROJECT_ROOT) -> str:
    """Return a stable POSIX relative path for reports and provenance records."""

    resolved = Path(path).expanduser().resolve()
    root = Path(project_root).expanduser().resolve()
    try:
        return Path(os.path.relpath(resolved, root)).as_posix()
    except ValueError:
        # Windows cannot form a relative path across drive letters.  Preserve
        # an unambiguous stable path instead of aborting report generation.
        return resolved.as_posix()


def sha256_file(path: Path) -> str:
    """Hash a file without loading the complete artifact into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
