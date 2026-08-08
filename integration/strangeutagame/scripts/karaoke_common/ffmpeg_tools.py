"""Resolve the FFmpeg tool pair used by karaoke workflows."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


class FFmpegToolError(RuntimeError):
    """Raised when a requested FFmpeg-family executable is unavailable."""


def project_root() -> Path:
    """Return the StrangeUtaGame checkout containing the installed scripts."""

    return Path(__file__).resolve().parents[2]


def _project_tool(name: str, root: Path) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return root / "tools" / "ffmpeg" / "bin" / f"{name}{suffix}"


def _existing(candidate: Path | None) -> Path | None:
    if candidate is None:
        return None
    expanded = candidate.expanduser()
    return expanded.resolve() if expanded.is_file() else None


def _resolve(
    name: str,
    *,
    explicit: Path | None = None,
    root: Path | None = None,
    sibling: Path | None = None,
) -> Path | None:
    configured = os.environ.get(name.upper(), "").strip()
    candidates = (
        explicit,
        Path(configured) if configured else None,
        _project_tool(name, (root or project_root()).resolve()),
        sibling,
        Path(discovered) if (discovered := shutil.which(name)) else None,
    )
    for candidate in candidates:
        if resolved := _existing(candidate):
            return resolved
    return None


def resolve_ffmpeg(
    explicit: Path | None = None,
    *,
    root: Path | None = None,
    allow_imageio_fallback: bool = True,
) -> Path:
    """Resolve FFmpeg, preferring explicit and project-owned executables."""

    if resolved := _resolve("ffmpeg", explicit=explicit, root=root):
        return resolved
    if allow_imageio_fallback:
        try:
            import imageio_ffmpeg

            if resolved := _existing(Path(imageio_ffmpeg.get_ffmpeg_exe())):
                return resolved
        except (ImportError, RuntimeError, OSError):
            pass
    raise FFmpegToolError(
        "Cannot find ffmpeg; install it at tools/ffmpeg/bin, pass --ffmpeg, "
        "or set FFMPEG"
    )


def resolve_ffprobe(
    explicit: Path | None = None,
    *,
    root: Path | None = None,
    ffmpeg: Path | None = None,
) -> Path:
    """Resolve FFprobe from the same project tool pair when possible."""

    suffix = ".exe" if os.name == "nt" else ""
    sibling = ffmpeg.with_name(f"ffprobe{suffix}") if ffmpeg else None
    if resolved := _resolve("ffprobe", explicit=explicit, root=root, sibling=sibling):
        return resolved
    raise FFmpegToolError(
        "Cannot find ffprobe; install it beside ffmpeg at tools/ffmpeg/bin, "
        "pass --ffprobe, or set FFPROBE"
    )


def prepend_ffmpeg_to_path(
    environment: dict[str, str] | None = None,
    *,
    root: Path | None = None,
) -> tuple[dict[str, str], Path]:
    """Return an environment where subprocesses can invoke literal ``ffmpeg``."""

    env = dict(os.environ if environment is None else environment)
    ffmpeg = resolve_ffmpeg(root=root)
    env["PATH"] = str(ffmpeg.parent) + os.pathsep + env.get("PATH", "")
    return env, ffmpeg
