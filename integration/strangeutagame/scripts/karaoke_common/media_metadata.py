"""Resolve display metadata from audio tags with explicit override support."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _first_tag(tags: Any, *names: str) -> str | None:
    if tags is None:
        return None
    folded = {str(key).casefold(): value for key, value in tags.items()}
    for name in names:
        value = folded.get(name.casefold())
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        text = str(value).strip() if value is not None else ""
        if text:
            return text
    return None


def read_album_tags(path: Path) -> dict[str, str | None]:
    """Read normalized album display tags without treating missing tags as errors."""

    from mutagen import File as MutagenFile

    resolved = path.expanduser().resolve()
    try:
        media = MutagenFile(str(resolved), easy=True)
    except Exception:
        media = None
    tags = getattr(media, "tags", None) if media is not None else None
    return {
        "album_title": _first_tag(tags, "album"),
        "album_artist": _first_tag(tags, "albumartist", "album artist", "artist"),
    }


def resolve_display_metadata(
    *,
    audio_path: Path,
    title: str,
    artist: str,
    album_title: str | None,
    album_artist: str | None,
    metadata_source_audio: Path | None = None,
) -> dict[str, str]:
    """Resolve album title/artist as override, audio tags, then track fallback."""

    source = (metadata_source_audio or audio_path).expanduser().resolve()
    tags = read_album_tags(source)
    resolved_title = (album_title or "").strip() or tags["album_title"] or title
    resolved_artist = (
        (album_artist or "").strip() or tags["album_artist"] or artist
    )
    return {
        "title": title,
        "artist": artist,
        "album_title": resolved_title,
        "album_artist": resolved_artist,
        "source_audio": str(source),
        "album_title_source": (
            "explicit" if (album_title or "").strip() else
            "audio-tag" if tags["album_title"] else "track-fallback"
        ),
        "album_artist_source": (
            "explicit" if (album_artist or "").strip() else
            "audio-tag" if tags["album_artist"] else "track-fallback"
        ),
    }
